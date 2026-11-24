//! NDI test patterns (`testpatterns.py`, #302): colour bars, a ramp, a grid, an alpha checker, solid colours, a moving element, or your own
//! picture, with a test tone, sent as NDI sources so the booth can check a wire, a monitor or a switcher without a camera.
//!
//! The pictures and the tone are `relay-ndi`'s (`patterns`); this keeps the burned-in text (name, size and clock, on a dark plate, drawn with
//! the module's own font), the operator's PNG, and one thread per source that sends a frame and its sound, then sleeps to the frame rate.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use relay_ndi::patterns::{self, Patch};
use serde_json::{json, Value};

use crate::font;
use crate::multiview::Clock;
use crate::outputs::{Maker, Sink};
use crate::router::ndi_name;

pub const MAX_IMAGE: usize = 5 * 1024 * 1024;
pub const MAX_PIXELS: usize = 5000 * 5000;
const PNG_MAGIC: &[u8] = b"\x89PNG\r\n\x1a\n";

pub const SIZES: [(&str, usize, usize); 4] = [("640x360", 640, 360), ("1280x720", 1280, 720), ("1920x1080", 1920, 1080), ("3840x2160", 3840, 2160)];

pub fn size_of(name: &str) -> (usize, usize) {
    SIZES.iter().find(|(n, _, _)| *n == name).map_or((1280, 720), |(_, w, h)| (*w, *h))
}

/// How a test source shows up on the network: "Relay - Test <name>".
pub fn test_ndi_name(test: &Value) -> String {
    ndi_name(&format!("Test {}", test["name"].as_str().unwrap_or("")))
}

// ------------------------------------------------------------------------------------------------- the operator's picture
/// "" when `raw` is a PNG Relay will use, else the sentence for the operator.
pub fn check_image(raw: &[u8]) -> &'static str {
    if !raw.starts_with(PNG_MAGIC) {
        return "That is not a PNG picture.";
    }
    if raw.len() > MAX_IMAGE {
        return "That picture is over 5 MB.";
    }
    let Ok(reader) = png::Decoder::new(std::io::Cursor::new(raw)).read_info() else { return "That PNG could not be read." };
    let (w, h) = reader.info().size();
    if (w as usize).saturating_mul(h as usize) > MAX_PIXELS {
        return "That picture is too large.";
    }
    if decode_png(raw).is_none() {
        return "That PNG could not be read.";
    }
    ""
}

struct Rgba {
    w: usize,
    h: usize,
    px: Vec<u8>,
}

fn decode_png(raw: &[u8]) -> Option<Rgba> {
    let mut dec = png::Decoder::new(std::io::Cursor::new(raw));
    dec.set_transformations(png::Transformations::EXPAND | png::Transformations::STRIP_16);
    let mut reader = dec.read_info().ok()?;
    let mut buf = vec![0u8; reader.output_buffer_size()];
    let info = reader.next_frame(&mut buf).ok()?;
    let (w, h) = (info.width as usize, info.height as usize);
    let src = &buf[..info.buffer_size()];
    let mut px = Vec::with_capacity(w * h * 4);
    match info.color_type {
        png::ColorType::Rgba => px.extend_from_slice(src),
        png::ColorType::Rgb => src.as_chunks::<3>().0.iter().for_each(|p| px.extend_from_slice(&[p[0], p[1], p[2], 255])),
        png::ColorType::GrayscaleAlpha => src.as_chunks::<2>().0.iter().for_each(|p| px.extend_from_slice(&[p[0], p[0], p[0], p[1]])),
        png::ColorType::Grayscale => src.iter().for_each(|g| px.extend_from_slice(&[*g, *g, *g, 255])),
        png::ColorType::Indexed => return None,
    }
    Some(Rgba { w, h, px })
}

/// `src` scaled to `dw` by `dh`, bilinear (premultiplied, so a transparent edge does not bleed its colour).
fn resize(src: &Rgba, dw: usize, dh: usize) -> Rgba {
    let mut px = vec![0u8; dw * dh * 4];
    let (sx, sy) = (src.w as f64 / dw as f64, src.h as f64 / dh as f64);
    let at = |x: usize, y: usize| -> [f64; 4] {
        let i = (y.min(src.h - 1) * src.w + x.min(src.w - 1)) * 4;
        let a = f64::from(src.px[i + 3]) / 255.0;
        [f64::from(src.px[i]) * a, f64::from(src.px[i + 1]) * a, f64::from(src.px[i + 2]) * a, a]
    };
    for y in 0..dh {
        let fy = ((y as f64 + 0.5) * sy - 0.5).max(0.0);
        let (y0, ty) = (fy as usize, fy.fract());
        for x in 0..dw {
            let fx = ((x as f64 + 0.5) * sx - 0.5).max(0.0);
            let (x0, tx) = (fx as usize, fx.fract());
            let (p00, p10, p01, p11) = (at(x0, y0), at(x0 + 1, y0), at(x0, y0 + 1), at(x0 + 1, y0 + 1));
            let mut m = [0.0; 4];
            for k in 0..4 {
                m[k] = (p00[k] * (1.0 - tx) + p10[k] * tx) * (1.0 - ty) + (p01[k] * (1.0 - tx) + p11[k] * tx) * ty;
            }
            let o = (y * dw + x) * 4;
            let a = m[3];
            for k in 0..3 {
                px[o + k] = if a > 0.0 { (m[k] / a).round().clamp(0.0, 255.0) as u8 } else { 0 };
            }
            px[o + 3] = (a * 255.0).round() as u8;
        }
    }
    Rgba { w: dw, h: dh, px }
}

/// The picture fitted inside `w` by `h` and centred on a transparent canvas, as BGRA.
fn load_image(raw: &[u8], w: usize, h: usize) -> Option<Vec<u8>> {
    let im = decode_png(raw)?;
    let scale = (w as f64 / im.w as f64).min(h as f64 / im.h as f64);
    let (nw, nh) = (((im.w as f64 * scale).round() as usize).max(1), ((im.h as f64 * scale).round() as usize).max(1));
    let fitted = if (nw, nh) == (im.w, im.h) { im } else { resize(&im, nw, nh) };
    let mut out = vec![0u8; w * h * 4];
    let (ox, oy) = ((w - fitted.w.min(w)) / 2, (h - fitted.h.min(h)) / 2);
    for y in 0..fitted.h.min(h) {
        for x in 0..fitted.w.min(w) {
            let (s, d) = ((y * fitted.w + x) * 4, ((y + oy) * w + x + ox) * 4);
            out[d..d + 4].copy_from_slice(&[fitted.px[s + 2], fitted.px[s + 1], fitted.px[s], fitted.px[s + 3]]);
        }
    }
    Some(out)
}

fn text_scale(px: usize) -> usize {
    (px / font::GLYPH_H).max(1)
}

/// A BGRA picture of `lines` centred on a dark ground: what an "own picture" source shows until a PNG is uploaded.
fn placeholder(w: usize, h: usize, lines: &[&str]) -> Vec<u8> {
    let mut out: Vec<u8> = [24u8, 24, 24, 255].iter().copied().cycle().take(w * h * 4).collect();
    let scale = text_scale((h / 18).max(14));
    let line_h = font::height(scale) * 14 / 10;
    let top = (h as i64) / 2 - (lines.len() * line_h) as i64 / 2;
    for (i, line) in lines.iter().enumerate() {
        let x0 = (w as i64 - font::width(line, scale) as i64) / 2;
        let y0 = top + (i * line_h) as i64;
        font::draw(line, scale, |px, py| {
            let (x, y) = (x0 + px as i64, y0 + py as i64);
            if x >= 0 && y >= 0 && (x as usize) < w && (y as usize) < h {
                let o = (y as usize * w + x as usize) * 4;
                out[o..o + 4].copy_from_slice(&[255, 255, 255, 255]);
            }
        });
    }
    out
}

/// The picture that does not change from frame to frame, BGRA (`h` rows of `w`).
pub fn base_picture(test: &Value, w: usize, h: usize, image: &std::path::Path) -> Vec<u8> {
    let pattern = test["pattern"].as_str().unwrap_or("bars");
    if pattern == "image" {
        return std::fs::read(image).ok().and_then(|raw| load_image(&raw, w, h)).unwrap_or_else(|| placeholder(w, h, &["Upload a PNG for this source"]));
    }
    patterns::base_picture(pattern, w, h)
}

// ------------------------------------------------------------------------------------------------- the burned-in text
/// The text written over the picture: its name, size and clock, in a box on the picture's lower third, on a dark plate so it reads on any
/// pattern. The box is drawn again only when its text changes (once a second).
pub struct Burn {
    name: String,
    w: usize,
    h: usize,
    cache: Option<(String, u64, Vec<u8>, usize, usize)>,
}

impl Burn {
    pub fn new(test: &Value, w: usize, h: usize) -> Burn {
        Burn { name: test["name"].as_str().unwrap_or("").to_owned(), w, h, cache: None }
    }

    /// The patch to blend over this frame: (x, y, width, height, BGRA).
    pub fn patch(&mut self, clock: &str, frame: u64, fps: i64, channel: &str) -> (i64, i64, usize, usize, &[u8]) {
        let key = format!("{clock}|{channel}");
        let second = frame / fps.max(1) as u64;
        if self.cache.as_ref().is_none_or(|c| c.0 != key || c.1 != second) {
            let scale = text_scale((self.h / 22).max(12));
            let lines = [self.name.clone(), format!("{} x {}  {fps} fps", self.w, self.h), if channel.is_empty() { clock.to_owned() } else { format!("{clock}   {channel}") }];
            let line_h = font::height(scale) * 13 / 10;
            let pad = font::height(scale);
            let bw = lines.iter().map(|l| font::width(l, scale)).max().unwrap_or(0) + pad;
            let bh = line_h * lines.len() + pad / 2;
            let mut px: Vec<u8> = [0u8, 0, 0, 200].iter().copied().cycle().take(bw * bh * 4).collect();
            for (i, l) in lines.iter().enumerate() {
                let (x0, y0) = (pad / 2, pad / 4 + i * line_h);
                font::draw(l, scale, |x, y| {
                    let (x, y) = (x0 + x, y0 + y);
                    if x < bw && y < bh {
                        let o = (y * bw + x) * 4;
                        px[o..o + 4].copy_from_slice(&[255, 255, 255, 255]);
                    }
                });
            }
            self.cache = Some((key, second, px, bw, bh));
        }
        let c = self.cache.as_ref().map_or((&[][..], 0, 0), |c| (c.2.as_slice(), c.3, c.4));
        ((self.w as i64 - c.1 as i64) / 2, (self.h as f64 * 0.62) as i64, c.1, c.2, c.0)
    }
}

// ------------------------------------------------------------------------------------------------- running
pub struct Deps {
    pub maker: Arc<dyn Maker>,
    pub settings: Arc<dyn Fn() -> Value + Send + Sync>,
    /// The folder the pictures live in (`<state>/ndi`).
    pub folder: Arc<dyn Fn() -> PathBuf + Send + Sync>,
    pub clock: Arc<dyn Fn() -> Clock + Send + Sync>,
    pub notify: Arc<dyn Fn() + Send + Sync>,
}

#[derive(Clone, Default)]
struct Status {
    state: &'static str,
    error: String,
    fps: f64,
}

struct Run {
    test: Value,
    status: Arc<Mutex<Status>>,
    stop: Arc<AtomicBool>,
    wake: mpsc::Sender<()>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

pub struct Tests {
    d: Arc<Deps>,
    runs: Mutex<HashMap<String, Arc<Run>>>,
}

fn set(status: &Mutex<Status>, state: &'static str, error: &str) {
    if let Ok(mut s) = status.lock() {
        s.state = state;
        s.error = error.chars().take(200).collect();
    }
}

impl Tests {
    pub fn new(d: Deps) -> Tests {
        Tests { d: Arc::new(d), runs: Mutex::default() }
    }

    pub fn image_path(&self, tid: &str) -> PathBuf {
        (self.d.folder)().join("tests").join(format!("{tid}.png"))
    }

    pub fn status(&self) -> Vec<Value> {
        let s = (self.d.settings)();
        let on: Vec<&str> = s["tests_on"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
        let runs = self.runs.lock().map(|r| r.clone()).unwrap_or_default();
        s["tests"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|t| {
                let id = t["id"].as_str().unwrap_or("");
                let st = runs.get(id).and_then(|r| r.status.lock().ok().map(|s| s.clone()));
                let mut o = t.clone();
                o["ndi"] = json!(test_ndi_name(t));
                o["on"] = json!(on.contains(&id));
                o["has_image"] = json!(self.image_path(id).is_file());
                o["state"] = json!(st.as_ref().map_or("off", |s| s.state));
                o["error"] = json!(st.as_ref().map_or("", |s| s.error.as_str()));
                o["fps_now"] = json!(st.as_ref().map_or(0.0, |s| (s.fps * 10.0).round() / 10.0));
                o["cpu"] = json!(0);
                o
            })
            .collect()
    }

    /// The NDI names of the sources that are sending now.
    pub fn names(&self) -> Vec<String> {
        let runs = self.runs.lock().map(|r| r.clone()).unwrap_or_default();
        let mut v: Vec<String> = runs.values().filter(|r| r.status.lock().is_ok_and(|s| s.state == "on")).map(|r| test_ndi_name(&r.test)).collect();
        v.sort();
        v
    }

    /// Start the test sources that are on and stop the rest. One whose settings changed starts again.
    pub fn sync(&self) {
        let s = (self.d.settings)();
        let on: Vec<&str> = s["tests_on"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
        let want: Vec<Value> = s["tests"].as_array().into_iter().flatten().filter(|t| on.contains(&t["id"].as_str().unwrap_or(""))).cloned().collect();
        let gone: Vec<Arc<Run>> = {
            let Ok(mut runs) = self.runs.lock() else { return };
            let ids: Vec<String> = runs.keys().filter(|k| !want.iter().any(|t| t["id"] == k.as_str())).cloned().collect();
            ids.iter().filter_map(|k| runs.remove(k)).collect()
        };
        gone.iter().for_each(|r| Self::stop_run(r));
        for t in want {
            let id = t["id"].as_str().unwrap_or("").to_owned();
            let current = self.runs.lock().ok().and_then(|r| r.get(&id).cloned());
            if let Some(run) = current {
                if run.test == t && run.status.lock().is_ok_and(|s| s.state != "error") {
                    continue;
                }
                if let Some(old) = self.runs.lock().ok().and_then(|mut r| r.remove(&id)) {
                    Self::stop_run(&old);
                }
            }
            let run = self.start(t);
            if let Ok(mut runs) = self.runs.lock() {
                runs.insert(id, run);
            }
        }
        (self.d.notify)();
    }

    fn start(&self, test: Value) -> Arc<Run> {
        let (wake, rx) = mpsc::channel();
        let status = Arc::new(Mutex::new(Status { state: "starting", ..Status::default() }));
        let stop = Arc::new(AtomicBool::new(false));
        let run = Arc::new(Run { test: test.clone(), status: Arc::clone(&status), stop: Arc::clone(&stop), wake, thread: Mutex::default() });
        let d = Arc::clone(&self.d);
        let image = self.image_path(test["id"].as_str().unwrap_or(""));
        let handle = std::thread::Builder::new().name("relay-ndi-test".into()).spawn(move || {
            if let Err(why) = drive(&d, &test, &image, &status, &stop, &rx) {
                tracing::warn!("NDI test source {} stopped: {why}", test["id"].as_str().unwrap_or(""));
                set(&status, "error", &why);
                (d.notify)();
            }
            if status.lock().is_ok_and(|s| s.state == "on") {
                set(&status, "off", "");
                (d.notify)();
            }
        });
        if let (Ok(h), Ok(mut t)) = (handle, run.thread.lock()) {
            *t = Some(h);
        }
        run
    }

    pub fn close(&self) {
        let all: Vec<Arc<Run>> = self.runs.lock().map(|mut r| r.drain().map(|(_, v)| v).collect()).unwrap_or_default();
        all.iter().for_each(|r| Self::stop_run(r));
    }

    /// Start one source again (its picture changed).
    pub fn restart(&self, tid: &str) {
        if let Some(old) = self.runs.lock().ok().and_then(|mut r| r.remove(tid)) {
            Self::stop_run(&old);
        }
        self.sync();
    }

    /// A test source was deleted: its picture goes with it.
    pub fn forget(&self, tid: &str) {
        let _ = std::fs::remove_file(self.image_path(tid));
    }

    fn stop_run(run: &Run) {
        run.stop.store(true, Ordering::SeqCst);
        let _ = run.wake.send(());
        let t = run.thread.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = t {
            if t.thread().id() != std::thread::current().id() {
                let _ = t.join();
            }
        }
    }
}

fn epoch() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// One source: draw a frame, send it with its sound, sleep to the frame rate, until told to stop or sending fails.
fn drive(d: &Deps, t: &Value, image: &std::path::Path, status: &Mutex<Status>, stop: &AtomicBool, wake: &mpsc::Receiver<()>) -> Result<(), String> {
    let (w, h) = size_of(t["size"].as_str().unwrap_or(""));
    let fps = t["fps"].as_i64().unwrap_or(30).clamp(5, 60);
    let pattern = t["pattern"].as_str().unwrap_or("bars").to_owned();
    let base = base_picture(t, w, h, image);
    let mut burn = (t["burn"] == true).then(|| Burn::new(t, w, h));
    let audio = t["audio"].as_str().unwrap_or("");
    let mut tone = (!audio.is_empty()).then(|| patterns::Tone::new(audio, t["level"].as_f64().unwrap_or(-18.0), fps as f64));
    let sink: Arc<dyn Sink> = d.maker.sink(&test_ndi_name(t), w, h, fps as f64).ok_or("NDI would not open this source (is the name already in use?).")?;
    set(status, "on", "");
    (d.notify)();
    let period = Duration::from_secs_f64(1.0 / fps as f64);
    let (mut n, mut count, mut t0) = (0u64, 0u32, Instant::now());
    let result = loop {
        if stop.load(Ordering::SeqCst) {
            break Ok(());
        }
        let start = Instant::now();
        let channel = tone.as_ref().map_or("", |t| t.channel());
        let clock = (d.clock)().text24();
        let patch = burn.as_mut().map(|b| b.patch(&clock, n, fps, channel));
        let frame = patterns::draw_frame(&pattern, &base, w, h, epoch(), n, patch.as_ref().map(|(x, y, pw, ph, px)| Patch { x: *x, y: *y, w: *pw, h: *ph, bgra: px }).as_ref());
        let sound = tone.as_mut().map(|t| t.next_frame());
        let sent = match &sound {
            Some((data, samples)) => sink.send_av(&frame, Some((data, 2, *samples))),
            None => sink.send_av(&frame, None),
        };
        if let Err(e) = sent {
            break Err(e);
        }
        n += 1;
        count += 1;
        if t0.elapsed() >= Duration::from_secs(2) {
            if let Ok(mut s) = status.lock() {
                s.fps = f64::from(count) / t0.elapsed().as_secs_f64();
            }
            (count, t0) = (0, Instant::now());
            (d.notify)();
        }
        if wake.recv_timeout(period.saturating_sub(start.elapsed())).is_ok() {
            continue;
        }
    };
    sink.close();
    result
}
