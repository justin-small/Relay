//! The page renderer for NDI outputs (`render.py`, #299): a Relay page drawn off screen in headless Chromium and handed over as BGRA frames.
//!
//! Chromium is driven over the DevTools protocol on its own WebSocket, with no browser crate: a small client sends `{id, method, params}`
//! and matches the answers. One browser per output, a throwaway profile, killed when the renderer is dropped. With `alpha` the page background
//! is transparent (a PNG capture), so a lower third keys over video; without it frames are JPEG, which is cheaper for Chromium to encode.
//!
//! Chromium is the operator's install (Chrome, Chromium or Edge); `RELAY_CHROME` names one. Without it the outputs page says so and the
//! router still works.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use serde_json::{json, Value};
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::{Child, Command};
use tokio::sync::{broadcast, mpsc, oneshot};
use tokio_tungstenite::tungstenite::Message;

pub const NO_CHROME: &str = "Page outputs need Chrome or Chromium: install it, or set RELAY_CHROME to its program.";
const LAUNCH_WAIT: Duration = Duration::from_secs(20);
const CALL_WAIT: Duration = Duration::from_secs(15);
const LOAD_WAIT: Duration = Duration::from_secs(15);

/// A BGRA picture, rows packed, the order NDI wants.
pub struct Bgra {
    pub width: usize,
    pub height: usize,
    pub data: Vec<u8>,
}

// ------------------------------------------------------------------------------------------------- finding Chromium
/// Where a Chromium-family browser may be on this machine, best first.
pub fn candidates() -> Vec<PathBuf> {
    let mut out: Vec<PathBuf> = Vec::new();
    if let Some(p) = std::env::var_os("RELAY_CHROME").filter(|p| !p.is_empty()) {
        out.push(PathBuf::from(p));
    }
    for p in [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Chromium.app/Contents/MacOS/Chromium",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/snap/bin/chromium",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ] {
        out.push(PathBuf::from(p));
    }
    if let Some(path) = std::env::var_os("PATH") {
        for dir in std::env::split_paths(&path) {
            for name in ["google-chrome", "chromium", "chromium-browser", "chrome", "msedge"] {
                out.push(dir.join(name));
            }
        }
    }
    out
}

/// The browser to launch, or the sentence to show the operator.
pub fn find_chrome() -> Result<PathBuf, &'static str> {
    candidates().into_iter().find(|p| p.is_file()).ok_or(NO_CHROME)
}

pub fn available() -> &'static str {
    if find_chrome().is_ok() {
        ""
    } else {
        NO_CHROME
    }
}

// ------------------------------------------------------------------------------------------------- the DevTools client
type Pending = Arc<Mutex<HashMap<u64, oneshot::Sender<Result<Value, String>>>>>;

/// A connection to a browser's DevTools WebSocket: calls with answers, and the events it sends.
pub struct Cdp {
    out: mpsc::UnboundedSender<String>,
    pending: Pending,
    next: AtomicU64,
    events: broadcast::Sender<Value>,
    tasks: Vec<tokio::task::JoinHandle<()>>,
}

impl Cdp {
    pub async fn connect(url: &str) -> Result<Cdp, String> {
        let (ws, _) = tokio_tungstenite::connect_async(url).await.map_err(|_| "Chromium would not let Relay connect.".to_owned())?;
        let (mut sink, mut stream) = ws.split();
        let (out, mut rx) = mpsc::unbounded_channel::<String>();
        let pending: Pending = Arc::default();
        let (events, _) = broadcast::channel(256);
        let writer = tokio::spawn(async move {
            while let Some(text) = rx.recv().await {
                if sink.send(Message::text(text)).await.is_err() {
                    break;
                }
            }
        });
        let (p2, e2) = (Arc::clone(&pending), events.clone());
        let reader = tokio::spawn(async move {
            while let Some(Ok(msg)) = stream.next().await {
                let Message::Text(text) = msg else { continue };
                let Ok(v) = serde_json::from_str::<Value>(&text) else { continue };
                if let Some(id) = v["id"].as_u64() {
                    let tx = p2.lock().ok().and_then(|mut p| p.remove(&id));
                    if let Some(tx) = tx {
                        let _ = tx.send(if v["error"].is_object() { Err(v["error"]["message"].as_str().unwrap_or("error").to_owned()) } else { Ok(v["result"].clone()) });
                    }
                } else if v["method"].is_string() {
                    let _ = e2.send(v);
                }
            }
            // The browser went away: nothing waiting for an answer will get one.
            if let Ok(mut p) = p2.lock() {
                for (_, tx) in p.drain() {
                    let _ = tx.send(Err("Chromium closed.".to_owned()));
                }
            }
        });
        Ok(Cdp { out, pending, next: AtomicU64::new(1), events, tasks: vec![writer, reader] })
    }

    /// Run `method`, in `session` when given; the browser's `result`, or its message.
    pub async fn call(&self, method: &str, params: Value, session: Option<&str>) -> Result<Value, String> {
        let id = self.next.fetch_add(1, Ordering::SeqCst);
        let (tx, rx) = oneshot::channel();
        self.pending.lock().map_err(|_| "Chromium closed.".to_owned())?.insert(id, tx);
        let mut msg = json!({"id": id, "method": method, "params": params});
        if let Some(s) = session {
            msg["sessionId"] = json!(s);
        }
        self.out.send(msg.to_string()).map_err(|_| "Chromium closed.".to_owned())?;
        match tokio::time::timeout(CALL_WAIT, rx).await {
            Ok(Ok(r)) => r,
            Ok(Err(_)) => Err("Chromium closed.".to_owned()),
            Err(_) => {
                if let Ok(mut p) = self.pending.lock() {
                    p.remove(&id);
                }
                Err("Chromium did not answer.".to_owned())
            }
        }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<Value> {
        self.events.subscribe()
    }
}

impl Drop for Cdp {
    fn drop(&mut self) {
        for t in &self.tasks {
            t.abort();
        }
    }
}

// ------------------------------------------------------------------------------------------------- frames
fn decode(data: &[u8], alpha: bool) -> Result<Bgra, String> {
    let bad = |_: &dyn std::fmt::Debug| "Chromium sent a picture Relay could not read.".to_owned();
    if alpha {
        let mut reader = png::Decoder::new(std::io::Cursor::new(data)).read_info().map_err(|e| bad(&e))?;
        let mut buf = vec![0u8; reader.output_buffer_size()];
        let info = reader.next_frame(&mut buf).map_err(|e| bad(&e))?;
        let (w, h) = (info.width as usize, info.height as usize);
        let px = &buf[..info.buffer_size()];
        let mut out = Vec::with_capacity(w * h * 4);
        match info.color_type {
            png::ColorType::Rgba => px.as_chunks::<4>().0.iter().for_each(|p| out.extend_from_slice(&[p[2], p[1], p[0], p[3]])),
            png::ColorType::Rgb => px.as_chunks::<3>().0.iter().for_each(|p| out.extend_from_slice(&[p[2], p[1], p[0], 255])),
            _ => return Err(bad(&"color type")),
        }
        Ok(Bgra { width: w, height: h, data: out })
    } else {
        let mut dec = jpeg_decoder::Decoder::new(std::io::Cursor::new(data));
        let px = dec.decode().map_err(|e| bad(&e))?;
        let info = dec.info().ok_or_else(|| bad(&"no info"))?;
        let (w, h) = (usize::from(info.width), usize::from(info.height));
        let mut out = Vec::with_capacity(w * h * 4);
        match info.pixel_format {
            jpeg_decoder::PixelFormat::RGB24 => px.as_chunks::<3>().0.iter().for_each(|p| out.extend_from_slice(&[p[2], p[1], p[0], 255])),
            jpeg_decoder::PixelFormat::L8 => px.iter().for_each(|g| out.extend_from_slice(&[*g, *g, *g, 255])),
            _ => return Err(bad(&"pixel format")),
        }
        Ok(Bgra { width: w, height: h, data: out })
    }
}

/// One headless Chromium page at a fixed size.
pub struct Renderer {
    cdp: Cdp,
    session: String,
    alpha: bool,
    pub width: usize,
    pub height: usize,
    child: Child,
    profile: PathBuf,
    /// The screencast's frames (an opaque page): Chromium pushes one whenever the picture changes, far faster than asking for each.
    cast: Option<tokio::sync::Mutex<broadcast::Receiver<Value>>>,
    last: Mutex<Option<Bgra>>,
}

impl Renderer {
    pub async fn open(url: &str, width: usize, height: usize, alpha: bool) -> Result<Renderer, String> {
        let exe = find_chrome().map_err(str::to_owned)?;
        Self::open_with(&exe, url, width, height, alpha).await
    }

    pub async fn open_with(exe: &Path, url: &str, width: usize, height: usize, alpha: bool) -> Result<Renderer, String> {
        let profile = std::env::temp_dir().join(format!("relay-ndi-chromium-{}-{}", std::process::id(), crate::settings::new_id()));
        let mut child = Command::new(exe)
            .args(["--headless=new", "--remote-debugging-port=0", "--no-first-run", "--no-default-browser-check", "--hide-scrollbars", "--mute-audio", "--disable-extensions", "--disable-background-networking"])
            .arg(format!("--user-data-dir={}", profile.display()))
            .arg(format!("--window-size={width},{height}"))
            .arg("about:blank")
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .map_err(|_| "Chromium would not start.".to_owned())?;
        let stderr = child.stderr.take().ok_or_else(|| "Chromium would not start.".to_owned())?;
        let ws = tokio::time::timeout(LAUNCH_WAIT, async {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if let Some(at) = line.find("ws://") {
                    // Keep reading so the browser never blocks on a full pipe.
                    tokio::spawn(async move { while let Ok(Some(_)) = lines.next_line().await {} });
                    return Some(line[at..].trim().to_owned());
                }
            }
            None
        })
        .await
        .ok()
        .flatten()
        .ok_or_else(|| "Chromium did not say where to connect.".to_owned())?;
        let cdp = Cdp::connect(&ws).await?;
        let mut me = Renderer { cdp, session: String::new(), alpha, width, height, child, profile, cast: None, last: Mutex::new(None) };
        me.setup(url).await?;
        Ok(me)
    }

    async fn setup(&mut self, url: &str) -> Result<(), String> {
        let t = self.cdp.call("Target.createTarget", json!({"url": "about:blank"}), None).await?;
        let target = t["targetId"].as_str().ok_or("Chromium made no page.")?.to_owned();
        let a = self.cdp.call("Target.attachToTarget", json!({"targetId": target, "flatten": true}), None).await?;
        self.session = a["sessionId"].as_str().ok_or("Chromium made no page.")?.to_owned();
        let s = Some(self.session.as_str());
        self.cdp.call("Page.enable", json!({}), s).await?;
        self.cdp.call("Emulation.setDeviceMetricsOverride", json!({"width": self.width, "height": self.height, "deviceScaleFactor": 1, "mobile": false}), s).await?;
        if self.alpha {
            self.cdp.call("Emulation.setDefaultBackgroundColorOverride", json!({"color": {"r": 0, "g": 0, "b": 0, "a": 0}}), s).await?;
        }
        let mut events = self.cdp.subscribe();
        self.cdp.call("Page.navigate", json!({"url": url}), s).await?;
        let session = self.session.clone();
        tokio::time::timeout(LOAD_WAIT, async move {
            while let Ok(ev) = events.recv().await {
                if ev["method"] == "Page.loadEventFired" && ev["sessionId"] == session.as_str() {
                    return;
                }
            }
        })
        .await
        .map_err(|_| "The page did not finish loading.".to_owned())?;
        if !self.alpha {
            let rx = self.cdp.subscribe();
            let params = json!({"format": "jpeg", "quality": 85, "maxWidth": self.width, "maxHeight": self.height, "everyNthFrame": 1});
            if self.cdp.call("Page.startScreencast", params, Some(&self.session)).await.is_ok() {
                self.cast = Some(tokio::sync::Mutex::new(rx));
            }
        }
        Ok(())
    }

    /// The newest screencast frame that arrives within `wait`, decoded. `None` when the page has not changed in that time.
    async fn cast_frame(&self, wait: Duration) -> Option<Bgra> {
        let mut rx = self.cast.as_ref()?.lock().await;
        let deadline = tokio::time::Instant::now() + wait;
        let mut newest: Option<String> = None;
        loop {
            let ev = match tokio::time::timeout_at(deadline, rx.recv()).await {
                Ok(Ok(ev)) => ev,
                Ok(Err(broadcast::error::RecvError::Lagged(_))) => continue,
                Ok(Err(_)) | Err(_) => break,
            };
            if ev["method"] != "Page.screencastFrame" || ev["sessionId"] != self.session.as_str() {
                continue;
            }
            if let Some(n) = ev["params"]["sessionId"].as_i64() {
                let _ = self.cdp.call("Page.screencastFrameAck", json!({"sessionId": n}), Some(&self.session)).await;
            }
            newest = ev["params"]["data"].as_str().map(str::to_owned);
            // Take what is already waiting, so a slow reader gets the newest picture and not an old one.
            if rx.is_empty() {
                break;
            }
        }
        let data = base64::engine::general_purpose::STANDARD.decode(newest?).ok()?;
        tokio::task::spawn_blocking(move || decode(&data, false)).await.ok()?.ok()
    }

    /// The page as it is now, BGRA. A page that has not changed within `wait` gives the last picture again (an NDI output keeps sending).
    pub async fn frame_within(&self, wait: Duration) -> Result<Bgra, String> {
        if self.cast.is_some() {
            if let Some(f) = self.cast_frame(wait).await {
                if let Ok(mut l) = self.last.lock() {
                    *l = Some(Bgra { width: f.width, height: f.height, data: f.data.clone() });
                }
                return Ok(f);
            }
            if let Some(l) = self.last.lock().ok().and_then(|l| l.as_ref().map(|f| Bgra { width: f.width, height: f.height, data: f.data.clone() })) {
                return Ok(l);
            }
        }
        self.frame().await
    }

    /// Ask Chromium for a picture now (a screenshot).
    pub async fn frame(&self) -> Result<Bgra, String> {
        let params = if self.alpha { json!({"format": "png", "fromSurface": true}) } else { json!({"format": "jpeg", "quality": 85, "fromSurface": true}) };
        let r = self.cdp.call("Page.captureScreenshot", params, Some(&self.session)).await?;
        let raw = base64::engine::general_purpose::STANDARD.decode(r["data"].as_str().unwrap_or("")).map_err(|_| "Chromium sent a picture Relay could not read.".to_owned())?;
        let alpha = self.alpha;
        tokio::task::spawn_blocking(move || decode(&raw, alpha)).await.map_err(|_| "Chromium sent a picture Relay could not read.".to_owned())?
    }

    pub async fn close(mut self) {
        let _ = self.cdp.call("Browser.close", json!({}), None).await;
        let _ = tokio::time::timeout(Duration::from_secs(3), self.child.wait()).await;
        let _ = self.child.start_kill();
        let _ = std::fs::remove_dir_all(&self.profile);
    }
}

impl Drop for Renderer {
    fn drop(&mut self) {
        let _ = self.child.start_kill();
        let _ = std::fs::remove_dir_all(&self.profile);
    }
}
