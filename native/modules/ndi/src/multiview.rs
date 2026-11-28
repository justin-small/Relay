//! NDI multiview (`multiview.py`, #297): many NDI sources on one page, composed on the server into a single MJPEG picture.
//!
//! A multiview is composed once however many browsers watch it: it holds a lease on the shared receiver pool for each source in it, draws the
//! boxes at most `view_fps` times a second, and hands the same JPEG to every browser, the way a single-source feed does (`viewer.rs`). Nothing
//! is composed, and every lease is given back, while nobody is watching.
//!
//! Not yet drawn, because they come from other modules: the tally border (needs the tally capability) and the "next service" countdown (needs
//! Planning Center's service time). Both show nothing, and the box says "No service time".

use std::collections::HashMap;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use relay_ndi::picture;
use serde_json::Value;

use crate::analysis::Analyzer;
use crate::font;
use crate::pool::{Lease, Pool};
use crate::viewer::{Feed, Limits};

pub const SIGNAL_LOST: Duration = Duration::from_secs(3);
pub const GAP: i64 = 4;
pub const MAX_METER_BARS: usize = 8;

const BG: [u8; 3] = [14, 14, 16];
const OUTLINE: [u8; 3] = [40, 40, 44];
const TEXT: [u8; 3] = [235, 235, 235];

/// A box of a layout: column, row, columns across, rows down.
pub type Cell = (i64, i64, i64, i64);

/// (columns, rows, the big box).
pub fn layout(name: &str) -> Option<(i64, i64, Option<Cell>)> {
    Some(match name {
        "2x2" => (2, 2, None),
        "3x3" => (3, 3, None),
        "4x4" => (4, 4, None),
        "1+5" => (3, 3, Some((0, 0, 2, 2))),
        "1+7" => (4, 4, Some((0, 0, 3, 3))),
        _ => return None,
    })
}

/// The boxes of a layout as (column, row, columns, rows), the big one first.
pub fn cells(name: &str) -> Vec<(i64, i64, i64, i64)> {
    let Some((cols, rows, big)) = layout(name) else { return Vec::new() };
    let mut out: Vec<(i64, i64, i64, i64)> = big.into_iter().collect();
    let taken = |c: i64, r: i64| big.is_some_and(|(bc, br, bw, bh)| c >= bc && c < bc + bw && r >= br && r < br + bh);
    for r in 0..rows {
        for c in 0..cols {
            if !taken(c, r) {
                out.push((c, r, 1, 1));
            }
        }
    }
    out
}

pub fn countdown_text(seconds: f64) -> String {
    if seconds <= 0.0 {
        return "Starting".to_owned();
    }
    let s = seconds as i64;
    format!("{}:{:02}:{:02}", s / 3600, s / 60 % 60, s % 60)
}

/// Seconds until the clock next reads `at` (HH:MM), today or tomorrow, from a local time of day in seconds.
pub fn seconds_until(at: &str, now_secs_of_day: i64) -> i64 {
    let (h, m) = (at[..2].parse::<i64>().unwrap_or(0), at[3..].parse::<i64>().unwrap_or(0));
    let d = (h * 3600 + m * 60) - now_secs_of_day;
    if d > 0 {
        d
    } else {
        d + 86400
    }
}

/// The wall clock in the Relay time zone.
#[derive(Clone, Copy, Debug)]
pub struct Clock {
    pub secs_of_day: i64,
}

impl Clock {
    /// `07:05:09`, 24 hours.
    pub fn text24(self) -> String {
        format!("{:02}:{:02}:{:02}", self.secs_of_day / 3600 % 24, self.secs_of_day / 60 % 60, self.secs_of_day % 60)
    }

    /// `7:05:09 AM`, the way Python's `%I:%M:%S %p` with the leading zero cut.
    pub fn text(self) -> String {
        let (h, m, s) = (self.secs_of_day / 3600, self.secs_of_day / 60 % 60, self.secs_of_day % 60);
        let h12 = if h % 12 == 0 { 12 } else { h % 12 };
        format!("{h12}:{m:02}:{s:02} {}", if h < 12 { "AM" } else { "PM" })
    }
}

// ------------------------------------------------------------------------------------------------- the canvas
pub struct Canvas {
    pub w: usize,
    pub h: usize,
    pub rgb: Vec<u8>,
}

impl Canvas {
    pub fn new(w: usize, h: usize) -> Canvas {
        Canvas { w, h, rgb: BG.iter().copied().cycle().take(w * h * 3).collect() }
    }

    fn put(&mut self, x: i64, y: i64, c: [u8; 3]) {
        if x >= 0 && y >= 0 && (x as usize) < self.w && (y as usize) < self.h {
            let i = (y as usize * self.w + x as usize) * 3;
            self.rgb[i..i + 3].copy_from_slice(&c);
        }
    }

    pub fn fill(&mut self, x: i64, y: i64, w: i64, h: i64, c: [u8; 3]) {
        for yy in y.max(0)..(y + h).min(self.h as i64) {
            for xx in x.max(0)..(x + w).min(self.w as i64) {
                self.put(xx, yy, c);
            }
        }
    }

    pub fn outline(&mut self, x: i64, y: i64, w: i64, h: i64, c: [u8; 3]) {
        self.fill(x, y, w, 1, c);
        self.fill(x, y + h - 1, w, 1, c);
        self.fill(x, y, 1, h, c);
        self.fill(x + w - 1, y, 1, h, c);
    }

    pub fn text(&mut self, text: &str, x: i64, y: i64, scale: usize, c: [u8; 3]) {
        let mut dots = Vec::new();
        font::draw(text, scale, |px, py| dots.push((px, py)));
        for (px, py) in dots {
            self.put(x + px as i64, y + py as i64, c);
        }
    }

    pub fn centre(&mut self, text: &str, rect: (i64, i64, i64, i64), scale: usize) {
        let (x, y, w, h) = rect;
        let (tw, th) = (font::width(text, scale) as i64, font::height(scale) as i64);
        self.text(text, x + (w - tw) / 2, y + (h - th) / 2, scale, TEXT);
    }

    pub fn label(&mut self, text: &str, rect: (i64, i64, i64, i64), scale: usize) {
        if text.is_empty() {
            return;
        }
        let (x, y, w, _) = rect;
        let (tw, th) = (font::width(text, scale) as i64, font::height(scale) as i64);
        self.fill(x, y, w.min(tw + 14), th + 8, [0, 0, 0]);
        self.text(text, x + 6, y + 4, scale, [255, 255, 255]);
    }

    /// Paste `rgb` (a `w` by `h` picture) at (x, y).
    pub fn paste(&mut self, rgb: &[u8], w: usize, h: usize, x: i64, y: i64) {
        for row in 0..h {
            let yy = y + row as i64;
            if yy < 0 || yy as usize >= self.h {
                continue;
            }
            for col in 0..w {
                let xx = x + col as i64;
                if xx >= 0 && (xx as usize) < self.w {
                    let (s, d) = ((row * w + col) * 3, (yy as usize * self.w + xx as usize) * 3);
                    self.rgb[d..d + 3].copy_from_slice(&rgb[s..s + 3]);
                }
            }
        }
    }

    pub fn jpeg(&self, quality: u8) -> Option<Vec<u8>> {
        let rgbx: Vec<u8> = self.rgb.as_chunks::<3>().0.iter().flat_map(|p| [p[0], p[1], p[2], 255]).collect();
        picture::jpeg(&rgbx, self.w, self.h, "RGBX", self.w, quality)
    }
}

/// `rgb` scaled down (never up) to fit inside `w` by `h`, keeping its shape.
pub fn fit(rgb: picture::Rgb, w: usize, h: usize) -> picture::Rgb {
    if rgb.width <= w && rgb.height <= h || rgb.width == 0 || rgb.height == 0 || w == 0 || h == 0 {
        return rgb;
    }
    let k = (w as f64 / rgb.width as f64).min(h as f64 / rgb.height as f64);
    let (nw, nh) = (((rgb.width as f64 * k) as usize).max(1), ((rgb.height as f64 * k) as usize).max(1));
    let mut out = vec![0u8; nw * nh * 3];
    for y in 0..nh {
        let sy = (y * rgb.height / nh).min(rgb.height - 1);
        for x in 0..nw {
            let sx = (x * rgb.width / nw).min(rgb.width - 1);
            let (s, d) = ((sy * rgb.width + sx) * 3, (y * nw + x) * 3);
            out[d..d + 3].copy_from_slice(&rgb.data[s..s + 3]);
        }
    }
    picture::Rgb { width: nw, height: nh, data: out }
}

// ------------------------------------------------------------------------------------------------- one multiview being watched
struct Tile {
    rgb: picture::Rgb,
    at: Instant,
}

pub struct Compositor {
    feed: Arc<Feed>,
    mv: Value,
    width: usize,
    height: usize,
    rects: Vec<(i64, i64, i64, i64)>,
    tiles: Arc<Mutex<HashMap<usize, Tile>>>,
    leases: Mutex<Vec<Lease>>,
    stop: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
}

pub struct Env {
    pub pool: Arc<Pool>,
    pub analysis: Arc<Analyzer>,
    pub limits: Arc<dyn Fn() -> Limits + Send + Sync>,
    pub settings: Arc<dyn Fn() -> Value + Send + Sync>,
    pub clock: Arc<dyn Fn() -> Clock + Send + Sync>,
}

fn rects(mv: &Value, width: usize, height: usize) -> Vec<(i64, i64, i64, i64)> {
    let name = mv["layout"].as_str().unwrap_or("2x2");
    let (cols, rows, _) = layout(name).unwrap_or((2, 2, None));
    let (cw, ch) = (width as f64 / cols as f64, height as f64 / rows as f64);
    cells(name)
        .into_iter()
        .map(|(c, r, w, h)| {
            let (x0, y0) = ((c as f64 * cw).round() as i64 + GAP / 2, (r as f64 * ch).round() as i64 + GAP / 2);
            let (x1, y1) = (((c + w) as f64 * cw).round() as i64 - GAP / 2, ((r + h) as f64 * ch).round() as i64 - GAP / 2);
            (x0, y0, x1 - x0, y1 - y0)
        })
        .collect()
}

impl crate::viewer::Frames for Compositor {
    async fn next(&self, after: u64, timeout: Duration) -> Option<(u64, bytes::Bytes)> {
        self.feed.next(after, timeout).await
    }
}

impl Compositor {
    /// The multiview's id.
    pub fn id(&self) -> &str {
        &self.feed.source
    }

    /// Another browser is watching.
    pub(crate) fn add_viewer(&self) {
        self.feed.add_viewer();
    }

    /// One browser fewer; the number left.
    pub(crate) fn remove_viewer(&self) -> i64 {
        self.feed.remove_viewer()
    }

    /// How many browsers are watching.
    pub fn viewers(&self) -> i64 {
        self.feed.viewers()
    }

    fn new(mv: Value, limits: Limits) -> Compositor {
        let width = (limits.width as usize).max(320) / 2 * 2;
        let height = width * 9 / 16 / 2 * 2;
        let rects = rects(&mv, width, height);
        Compositor { feed: Feed::new(mv["id"].as_str().unwrap_or("")), mv, width, height, rects, tiles: Arc::default(), leases: Mutex::default(), stop: Arc::new(AtomicBool::new(false)), thread: Mutex::default() }
    }

    fn start(self: &Arc<Self>, env: &Arc<Env>) {
        let boxes = self.mv["boxes"].as_array().cloned().unwrap_or_default();
        for (i, b) in boxes.iter().enumerate() {
            let source = b["source"].as_str().unwrap_or("");
            if b["kind"] != "source" || source.is_empty() || i >= self.rects.len() {
                continue;
            }
            let (tiles, limits, w, h) = (Arc::clone(&self.tiles), Arc::clone(&env.limits), self.rects[i].2 as usize, self.rects[i].3 as usize);
            let last = Mutex::new(None::<Instant>);
            let cb = Arc::new(move |v: &relay_ndi::recv::Video| {
                let now = Instant::now();
                if let Ok(mut l) = last.lock() {
                    if l.is_some_and(|t| now.duration_since(t) < Duration::from_secs_f64(1.0 / f64::from(limits().fps))) {
                        return;
                    }
                    *l = Some(now);
                }
                if let Some(rgb) = picture::to_rgb(&v.data, v.width as usize, v.height as usize, v.fourcc, w) {
                    if let Ok(mut t) = tiles.lock() {
                        t.insert(i, Tile { rgb: fit(rgb, w, h), at: now });
                    }
                }
            });
            if let Some(lease) = env.pool.acquire(source, false, cb, None) {
                if let Ok(mut l) = self.leases.lock() {
                    l.push(lease);
                }
            }
        }
        let (me, env) = (Arc::clone(self), Arc::clone(env));
        let handle = std::thread::Builder::new().name("relay-ndi-multiview".into()).spawn(move || {
            while !me.stop.load(Ordering::SeqCst) {
                let t0 = Instant::now();
                let limits = (env.limits)();
                if let Some(jpeg) = me.compose(&env).jpeg(limits.quality) {
                    me.feed.publish(jpeg);
                }
                let every = Duration::from_secs_f64(1.0 / f64::from(limits.fps));
                std::thread::sleep(every.saturating_sub(t0.elapsed()).max(Duration::from_millis(10)));
            }
        });
        if let Ok(h) = handle {
            if let Ok(mut t) = self.thread.lock() {
                *t = Some(h);
            }
        }
    }

    pub fn stop(&self) {
        self.stop.store(true, Ordering::SeqCst);
        let leases = self.leases.lock().map(|mut l| std::mem::take(&mut *l)).unwrap_or_default();
        drop(leases);
        let t = self.thread.lock().ok().and_then(|mut t| t.take());
        if let Some(t) = t {
            if t.thread().id() != std::thread::current().id() {
                let _ = t.join();
            }
        }
    }

    /// One picture of the whole multiview.
    pub fn compose(&self, env: &Env) -> Canvas {
        let mut c = Canvas::new(self.width, self.height);
        let (big, small) = ((self.height / 14).max(14) / font::GLYPH_H, (self.height / 40).max(11) / font::GLYPH_H);
        let (big, small) = (big.max(1), small.max(1));
        let clock = (env.clock)();
        for (i, b) in self.mv["boxes"].as_array().into_iter().flatten().enumerate() {
            let Some(&rect) = self.rects.get(i) else { break };
            let (x, y, w, h) = rect;
            c.fill(x, y, w, h, [0, 0, 0]);
            c.outline(x, y, w, h, OUTLINE);
            let label = b["label"].as_str().unwrap_or("");
            match b["kind"].as_str().unwrap_or("") {
                "source" => self.source(&mut c, b, i, rect, small, env),
                "clock" => {
                    c.centre(&clock.text(), rect, big);
                    c.label(if label.is_empty() { "Clock" } else { label }, rect, small);
                }
                "timer" => {
                    let (text, default) = if b["mode"] == "service" {
                        ("No service time".to_owned(), "Next service".to_owned())
                    } else {
                        let at = b["at"].as_str().unwrap_or("10:30");
                        (countdown_text(seconds_until(at, clock.secs_of_day) as f64), format!("Starts {at}"))
                    };
                    c.centre(&text, rect, big);
                    c.label(if label.is_empty() { &default } else { label }, rect, small);
                }
                _ => {}
            }
        }
        c
    }

    fn source(&self, c: &mut Canvas, b: &Value, i: usize, rect: (i64, i64, i64, i64), small: usize, env: &Env) {
        let (x, y, w, h) = rect;
        let source = b["source"].as_str().unwrap_or("");
        let shown = self.tiles.lock().ok().and_then(|t| {
            t.get(&i).filter(|t| t.at.elapsed() < SIGNAL_LOST).map(|t| (t.rgb.data.clone(), t.rgb.width, t.rgb.height))
        });
        match shown {
            Some((data, tw, th)) => c.paste(&data, tw, th, x + (w - tw as i64) / 2, y + (h - th as i64) / 2),
            None => c.centre(if source.is_empty() { "No source" } else { "No signal" }, rect, small),
        }
        let label = b["label"].as_str().filter(|l| !l.is_empty()).unwrap_or(source);
        c.label(label, rect, small);
        if b["meter"] == true {
            let mut peaks: Vec<Option<f64>> = env.analysis.audio_levels(source).map(|l| l.into_iter().map(Some).collect()).unwrap_or_else(|| env.analysis.audio_level(source).map(|d| vec![Some(d)]).unwrap_or_default());
            peaks.truncate(MAX_METER_BARS);
            if peaks.is_empty() {
                peaks.push(None);
            }
            let (bw, bh) = ((w / 4).max(40), (h / 60).max(3));
            let (mx, my) = (x + w - bw - 8, y + h - (bh + 2) * peaks.len() as i64 - 6);
            for (k, db) in peaks.iter().enumerate() {
                let frac = db.map_or(0.0, |d| ((d + 60.0) / 60.0).clamp(0.0, 1.0));
                let by = my + k as i64 * (bh + 2);
                c.fill(mx, by, bw + 1, bh + 1, [50, 50, 54]);
                if frac > 0.0 {
                    c.fill(mx, by, (bw as f64 * frac) as i64 + 1, bh + 1, if frac < 0.92 { [64, 192, 87] } else { [224, 49, 49] });
                }
            }
        }
    }
}

pub struct Multiviews {
    env: Arc<Env>,
    open: Mutex<HashMap<String, Arc<Compositor>>>,
}

impl Multiviews {
    pub fn new(env: Env) -> Multiviews {
        Multiviews { env: Arc::new(env), open: Mutex::default() }
    }

    pub fn get(&self, id: &str) -> Option<Value> {
        (self.env.settings)()["multiviews"].as_array()?.iter().find(|m| m["id"] == id).cloned()
    }

    pub fn open(&self, id: &str) -> Option<Arc<Compositor>> {
        let mut open = self.open.lock().ok()?;
        if let Some(c) = open.get(id) {
            c.add_viewer();
            return Some(Arc::clone(c));
        }
        let c = Arc::new(Compositor::new(self.get(id)?, (self.env.limits)()));
        c.start(&self.env);
        open.insert(id.to_owned(), Arc::clone(&c));
        Some(c)
    }

    pub fn close(&self, c: &Arc<Compositor>) {
        if c.remove_viewer() > 0 {
            return;
        }
        let id = c.id().to_owned();
        if let Ok(mut open) = self.open.lock() {
            if open.get(&id).is_some_and(|o| Arc::ptr_eq(o, c)) {
                open.remove(&id);
            }
        }
        c.stop();
    }

    pub fn count(&self) -> HashMap<String, i64> {
        self.open.lock().map(|o| o.iter().map(|(k, c)| (k.clone(), c.viewers())).collect()).unwrap_or_default()
    }

    pub fn close_all(&self) {
        let all: Vec<Arc<Compositor>> = self.open.lock().map(|mut o| o.drain().map(|(_, c)| c).collect()).unwrap_or_default();
        for c in all {
            c.stop();
        }
    }
}
