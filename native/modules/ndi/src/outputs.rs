//! NDI outputs (`outputs.py`, #299): Relay pages sent out as NDI sources.
//!
//! An **output definition** is `{"id", "name", "group", "url", "alpha"}`: a page path on this server, drawn off screen in headless Chromium
//! at the chosen size and frame rate and published as the NDI source "Relay - <name>". Screens (each language, plus the source language) and
//! overlays (the default and every saved one, with alpha) are built in; another module adds its own through what it provides as
//! `ndi.outputs.<id>` (signage, boards), so nothing here names it. The operator switches each output on in admin; `on` is saved, so outputs
//! come back after a restart. Each running output is a task that renders a frame, sends it, and sleeps to its frame rate; the status
//! carries the rate it achieved.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use relay_core::languages;
use relay_core::future::BoxFuture;
use serde_json::{json, Value};

use crate::render::Bgra;
use crate::router::ndi_name;

pub const MAX_ON: usize = 8;
pub const SIZES: [(&str, usize, usize); 2] = [("1280x720", 1280, 720), ("1920x1080", 1920, 1080)];

pub fn size_of(name: &str) -> (usize, usize) {
    SIZES.iter().find(|(n, _, _)| *n == name).map_or((1280, 720), |(_, w, h)| (*w, *h))
}

/// Screens, overlays and public boards from the operator's configuration. A board that needs a sign-in is left out: the renderer has no
/// account. `cfg` reads one top-level config key.
pub fn builtin_defs(cfg: &dyn Fn(&str) -> Option<Value>) -> Vec<Value> {
    let source = cfg("source_language").and_then(|v| v.as_str().map(str::to_owned)).unwrap_or_default();
    let mut out = vec![json!({"id": "screen:source", "name": "Screen (source language)", "group": "Screens", "url": "/screen?stream=transcription", "alpha": false})];
    for t in languages::target_names(&source) {
        let label = languages::target(t).map_or_else(|| t.to_owned(), |l| l.label.to_owned());
        out.push(json!({"id": format!("screen:{t}"), "name": format!("Screen ({label})"), "group": "Screens", "url": format!("/screen?lang={t}"), "alpha": false}));
    }
    out.push(json!({"id": "overlay:default", "name": "Overlay", "group": "Overlays", "url": "/overlay", "alpha": true}));
    for o in cfg("overlays").as_ref().and_then(Value::as_array).into_iter().flatten() {
        if let Some(id) = o["id"].as_str().filter(|i| !i.is_empty()) {
            let name = o["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(id);
            out.push(json!({"id": format!("overlay:{id}"), "name": format!("Overlay ({name})"), "group": "Overlays", "url": format!("/overlay/{id}"), "alpha": true}));
        }
    }
    for b in cfg("boards").as_ref().and_then(Value::as_array).into_iter().flatten() {
        if let (Some(id), true) = (b["id"].as_str().filter(|i| !i.is_empty()), b["public"] == true) {
            let name = b["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(id);
            out.push(json!({"id": format!("board:{id}"), "name": format!("Board ({name})"), "group": "Boards", "url": format!("/board/{id}"), "alpha": false}));
        }
    }
    out
}

/// A page Relay serves: a path on the viewer port, or this machine's own address on another port (signage).
pub fn is_page(url: &str) -> bool {
    (url.starts_with('/') && !url.starts_with("//")) || local_address(url)
}

fn local_address(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("http://127.0.0.1:") else { return false };
    let digits = rest.bytes().take_while(u8::is_ascii_digit).count();
    (1..=5).contains(&digits) && rest[digits..].starts_with('/')
}

/// Where the renderer loads a page: a path goes after the viewer port's `base`; a local address is used as it is.
pub fn page_url(base: &str, url: &str) -> String {
    if local_address(url) {
        url.to_owned()
    } else {
        format!("{base}{url}")
    }
}

// ------------------------------------------------------------------------------------------------- the outside
/// Draws one page. The real one is headless Chromium (`render::Renderer`); a test gives its own.
pub trait Frames: Send + Sync {
    /// The page as it is now, or the last picture again when it has not changed within `wait`.
    fn frame<'a>(&'a self, wait: Duration) -> BoxFuture<'a, Result<Bgra, String>>;
    fn close(self: Box<Self>) -> BoxFuture<'static, ()>;
}

/// An NDI source Relay publishes. The real one is `relay-ndi`'s sender.
pub trait Sink: Send + Sync {
    fn send(&self, bgra: &[u8]) -> Result<(), String>;
    /// A picture with its sound: planar float audio (`channels` rows of `samples`). A sink that carries no sound sends the picture alone.
    fn send_av(&self, bgra: &[u8], audio: Option<(&[f32], usize, usize)>) -> Result<(), String> {
        let _ = audio;
        self.send(bgra)
    }
    fn close(&self);
}

/// An audio-only NDI source the lane bus writes to. The real one is `relay-ndi`'s `AudioSink`, which sends 10 ms at a time on NDI's own clock.
pub trait AudioOut: Send + Sync {
    fn rate(&self) -> u32;
    /// Frames `write` takes now.
    fn free(&self) -> usize;
    fn queued(&self) -> usize;
    /// Queue interleaved float audio; the frames taken.
    fn write(&self, block: &[f32], frames: usize) -> usize;
    fn underruns(&self) -> u64;
    fn close(&self);
}

impl AudioOut for relay_ndi::sink::AudioSink {
    fn rate(&self) -> u32 {
        relay_ndi::sink::AudioSink::rate(self)
    }
    fn free(&self) -> usize {
        relay_ndi::sink::AudioSink::free(self)
    }
    fn queued(&self) -> usize {
        relay_ndi::sink::AudioSink::queued(self)
    }
    fn write(&self, block: &[f32], frames: usize) -> usize {
        relay_ndi::sink::AudioSink::write(self, block, frames)
    }
    fn underruns(&self) -> u64 {
        relay_ndi::sink::AudioSink::underruns(self)
    }
    fn close(&self) {
        relay_ndi::sink::AudioSink::close(self);
    }
}

pub trait Maker: Send + Sync {
    /// An audio-only NDI source, or `None` when NDI would not create it.
    fn audio_out(&self, name: &str, channels: usize, rate: u32) -> Option<Arc<dyn AudioOut>> {
        let _ = (name, channels, rate);
        None
    }
    fn frames<'a>(&'a self, url: &'a str, w: usize, h: usize, alpha: bool) -> BoxFuture<'a, Result<Box<dyn Frames>, String>>;
    /// `None` when NDI would not open it (is the name already in use?).
    fn sink(&self, name: &str, w: usize, h: usize, fps: f64) -> Option<Arc<dyn Sink>>;
    /// `""` when pages can be rendered, else the sentence to show the operator.
    fn available(&self) -> String;
}

pub struct Real;

impl Frames for crate::render::Renderer {
    fn frame<'a>(&'a self, wait: Duration) -> BoxFuture<'a, Result<Bgra, String>> {
        Box::pin(self.frame_within(wait))
    }
    fn close(self: Box<Self>) -> BoxFuture<'static, ()> {
        Box::pin(async move { (*self).close().await })
    }
}

impl Sink for relay_ndi::send::Sender {
    fn send(&self, bgra: &[u8]) -> Result<(), String> {
        relay_ndi::send::Sender::send(self, bgra, None)
    }
    fn send_av(&self, bgra: &[u8], audio: Option<(&[f32], usize, usize)>) -> Result<(), String> {
        relay_ndi::send::Sender::send(self, bgra, audio)
    }
    fn close(&self) {
        relay_ndi::send::Sender::close(self);
    }
}

impl Maker for Real {
    fn audio_out(&self, name: &str, channels: usize, rate: u32) -> Option<Arc<dyn AudioOut>> {
        relay_ndi::sink::AudioSink::open(name, channels, rate).ok().map(|s| Arc::new(s) as Arc<dyn AudioOut>)
    }

    fn frames<'a>(&'a self, url: &'a str, w: usize, h: usize, alpha: bool) -> BoxFuture<'a, Result<Box<dyn Frames>, String>> {
        Box::pin(async move { Ok(Box::new(crate::render::Renderer::open(url, w, h, alpha).await?) as Box<dyn Frames>) })
    }
    fn sink(&self, name: &str, w: usize, h: usize, fps: f64) -> Option<Arc<dyn Sink>> {
        relay_ndi::send::Sender::open(name, w as u32, h as u32, fps, "BGRA").ok().map(|s| Arc::new(s) as Arc<dyn Sink>)
    }
    fn available(&self) -> String {
        crate::render::available().to_owned()
    }
}

/// Reads one top-level config key.
pub type ConfigFn = dyn Fn(&str) -> Option<Value> + Send + Sync;

pub struct Deps {
    pub maker: Arc<dyn Maker>,
    /// The module's saved settings.
    pub settings: Arc<dyn Fn() -> Value + Send + Sync>,
    /// A top-level config key (languages, overlays, boards).
    pub config: Arc<ConfigFn>,
    /// What other modules offer as page outputs.
    pub providers: Arc<dyn Fn() -> Vec<Value> + Send + Sync>,
    /// Where the renderer loads Relay's pages: this server's viewer port, on this machine.
    pub base: Arc<dyn Fn() -> String + Send + Sync>,
    pub notify: Arc<dyn Fn() + Send + Sync>,
}

#[derive(Clone, Default)]
struct Status {
    state: &'static str,
    error: String,
    fps: f64,
}

struct Run {
    def: Value,
    w: usize,
    h: usize,
    fps: i64,
    status: Arc<Mutex<Status>>,
    stop: Arc<tokio::sync::Notify>,
    stopped: Arc<std::sync::atomic::AtomicBool>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

pub struct Outputs {
    d: Arc<Deps>,
    runs: Mutex<HashMap<String, Arc<Run>>>,
}

impl Outputs {
    pub fn new(d: Deps) -> Outputs {
        Outputs { d: Arc::new(d), runs: Mutex::default() }
    }

    /// Every page that can be sent: the built-in ones, then what other modules offer (an id already taken is skipped).
    pub fn defs(&self) -> Vec<Value> {
        let mut out = builtin_defs(&*self.d.config);
        for d in (self.d.providers)() {
            let (Some(id), Some(url)) = (d["id"].as_str().filter(|i| !i.is_empty()), d["url"].as_str().filter(|u| is_page(u))) else { continue };
            if out.iter().any(|o| o["id"] == id) {
                continue;
            }
            let name = d["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(id);
            out.push(json!({"id": id, "name": name, "group": d["group"].as_str().unwrap_or(""), "url": url, "alpha": d["alpha"] == true}));
        }
        out
    }

    pub fn status(&self) -> Vec<Value> {
        let s = (self.d.settings)();
        let on: Vec<&str> = s["out_on"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
        let runs = self.runs.lock().map(|r| r.clone()).unwrap_or_default();
        self.defs()
            .into_iter()
            .map(|mut d| {
                let id = d["id"].as_str().unwrap_or("").to_owned();
                let st = runs.get(&id).and_then(|r| r.status.lock().ok().map(|s| s.clone()));
                d["ndi"] = json!(ndi_name(d["name"].as_str().unwrap_or("")));
                d["on"] = json!(on.contains(&id.as_str()));
                d["state"] = json!(st.as_ref().map_or("off", |s| s.state));
                d["error"] = json!(st.as_ref().map_or("", |s| s.error.as_str()));
                d["fps"] = json!(st.as_ref().map_or(0.0, |s| (s.fps * 10.0).round() / 10.0));
                d["cpu"] = json!(0);
                d
            })
            .collect()
    }

    /// The NDI names of the outputs that are sending now.
    pub fn names(&self) -> Vec<String> {
        let runs = self.runs.lock().map(|r| r.clone()).unwrap_or_default();
        let mut v: Vec<String> = runs.values().filter(|r| r.status.lock().is_ok_and(|s| s.state == "on")).map(|r| ndi_name(r.def["name"].as_str().unwrap_or(""))).collect();
        v.sort();
        v
    }

    /// Start the outputs that are on and stop the rest.
    pub async fn sync(&self) {
        let s = (self.d.settings)();
        let on: Vec<&str> = s["out_on"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
        let want: Vec<Value> = self.defs().into_iter().filter(|d| on.contains(&d["id"].as_str().unwrap_or(""))).collect();
        let (w, h) = size_of(s["out_size"].as_str().unwrap_or(""));
        let fps = s["out_fps"].as_i64().unwrap_or(30);
        let gone: Vec<Arc<Run>> = {
            let Ok(mut runs) = self.runs.lock() else { return };
            let ids: Vec<String> = runs.keys().filter(|k| !want.iter().any(|d| d["id"] == k.as_str())).cloned().collect();
            ids.iter().filter_map(|k| runs.remove(k)).collect()
        };
        for r in gone {
            Self::stop_run(&r).await;
        }
        for d in want {
            let id = d["id"].as_str().unwrap_or("").to_owned();
            let current = self.runs.lock().ok().and_then(|r| r.get(&id).cloned());
            if let Some(run) = &current {
                let errored = run.status.lock().is_ok_and(|s| s.state == "error");
                if (run.w, run.h, run.fps) == (w, h, fps) && run.def["url"] == d["url"] && !errored {
                    continue;
                }
                let old = self.runs.lock().ok().and_then(|mut r| r.remove(&id));
                if let Some(old) = old {
                    Self::stop_run(&old).await;
                }
            }
            let run = Arc::new(Run {
                def: d,
                w,
                h,
                fps,
                status: Arc::new(Mutex::new(Status { state: "starting", ..Status::default() })),
                stop: Arc::new(tokio::sync::Notify::new()),
                stopped: Arc::default(),
                task: Mutex::default(),
            });
            let (d2, r2) = (Arc::clone(&self.d), Arc::clone(&run));
            let handle = tokio::spawn(async move { drive(d2, r2).await });
            if let Ok(mut t) = run.task.lock() {
                *t = Some(handle);
            }
            if let Ok(mut runs) = self.runs.lock() {
                runs.insert(id, run);
            }
        }
        (self.d.notify)();
    }

    pub async fn close(&self) {
        let all: Vec<Arc<Run>> = self.runs.lock().map(|mut r| r.drain().map(|(_, v)| v).collect()).unwrap_or_default();
        for r in all {
            Self::stop_run(&r).await;
        }
    }

    async fn stop_run(run: &Run) {
        run.stopped.store(true, std::sync::atomic::Ordering::SeqCst);
        run.stop.notify_waiters();
        let task = run.task.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = task {
            let _ = tokio::time::timeout(Duration::from_secs(8), t).await;
        }
    }
}

fn set(run: &Run, state: &'static str, error: &str) {
    if let Ok(mut s) = run.status.lock() {
        s.state = state;
        s.error = error.chars().take(200).collect();
    }
}

/// One running output: render, send, sleep to the frame rate, until told to stop or something fails.
async fn drive(d: Arc<Deps>, run: Arc<Run>) {
    let name = ndi_name(run.def["name"].as_str().unwrap_or(""));
    let mut frames: Option<Box<dyn Frames>> = None;
    let mut sink: Option<Arc<dyn Sink>> = None;
    let outcome: Result<(), String> = async {
        let why = d.maker.available();
        if !why.is_empty() {
            return Err(why);
        }
        let url = page_url(&(d.base)(), run.def["url"].as_str().unwrap_or(""));
        frames = Some(d.maker.frames(&url, run.w, run.h, run.def["alpha"] == true).await?);
        let maker = Arc::clone(&d.maker);
        let (n2, w, h, fps) = (name.clone(), run.w, run.h, run.fps as f64);
        sink = tokio::task::spawn_blocking(move || maker.sink(&n2, w, h, fps)).await.map_err(|_| "NDI would not open this output.".to_owned())?;
        let Some(out) = sink.clone() else { return Err("NDI would not open this output (is the name already in use?).".to_owned()) };
        set(&run, "on", "");
        (d.notify)();
        let period = Duration::from_secs_f64(1.0 / run.fps.max(1) as f64);
        let (mut count, mut t0) = (0u32, Instant::now());
        loop {
            if run.stopped.load(std::sync::atomic::Ordering::SeqCst) {
                return Ok(());
            }
            let start = Instant::now();
            let Some(f) = frames.as_ref() else { return Ok(()) };
            let frame = f.frame(period).await?;
            if (frame.width, frame.height) != (run.w, run.h) {
                return Err("Chromium drew the page at the wrong size.".to_owned());
            }
            let o = Arc::clone(&out);
            tokio::task::spawn_blocking(move || o.send(&frame.data)).await.map_err(|_| "NDI stopped.".to_owned())??;
            count += 1;
            if t0.elapsed() >= Duration::from_secs(2) {
                if let Ok(mut s) = run.status.lock() {
                    s.fps = f64::from(count) / t0.elapsed().as_secs_f64();
                }
                (count, t0) = (0, Instant::now());
                (d.notify)();
            }
            let rest = period.saturating_sub(start.elapsed());
            tokio::select! {
                () = run.stop.notified() => return Ok(()),
                () = tokio::time::sleep(rest) => {}
            }
        }
    }
    .await;
    if let Err(why) = outcome {
        tracing::warn!("NDI output {} stopped: {why}", run.def["id"].as_str().unwrap_or(""));
        set(&run, "error", &why);
        (d.notify)();
    }
    if let Some(s) = sink {
        let _ = tokio::task::spawn_blocking(move || s.close()).await;
    }
    if let Some(f) = frames {
        f.close().await;
    }
    if run.status.lock().is_ok_and(|s| s.state == "on") {
        set(&run, "off", "");
        (d.notify)();
    }
}
