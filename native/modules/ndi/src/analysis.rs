//! NDI analysis (`analysis.py`, #300): stream health, frame timing, picture scopes, audio loudness, sync and SCTE-104 cues for one source at
//! a time, plus the per-source audio levels the viewer's meters and the sources list read.
//!
//! The measuring is `relay-ndi`'s (`health`, `scopes`, `audio`, `scte`); this keeps the tracks, the clock, the recording and the JSON the page reads.
//! Timing is always on for a source with a receiver, so the sources list can show a health dot; scopes and the audio meter run only while an
//! analysis is open (`watch`). A still picture sends no frames, so a gap there is "quiet", not broken.

use std::collections::{HashMap, VecDeque};
use std::fs::File;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Instant, SystemTime, UNIX_EPOCH};

use relay_ndi::audio::AudioMeter;
use relay_ndi::health::{self, Stats, SyncMeter, Timing};
use relay_ndi::recv::{Audio, Video};
use relay_ndi::scopes::{self, Frame, Grid, Pack};
use serde_json::{json, Value};

pub const MAX_CUES: usize = 100;
/// An audio level older than this is "no audio".
pub const AUDIO_FRESH_S: f64 = 2.0;
/// The loudness meter runs this long after a viewer last asked for it.
pub const LOUD_HOLD_S: f64 = 10.0;
/// Sound but no picture for this long: an audio-only source.
pub const AUDIO_ONLY_AFTER_S: f64 = 3.0;
pub const MAX_RECORD_S: i64 = 3600;
/// Copy a frame for the scopes at most this often: they are drawn a few times a second.
const SCOPE_COPY_EVERY_S: f64 = 0.1;

/// Seconds on one monotonic clock for the whole process (the same clock `Timing` expects). An instant before the clock's first reading
/// (a frame that arrived while it waited in the queue) is a smaller number, not the same one.
pub fn mono(at: Instant) -> f64 {
    static START: OnceLock<Instant> = OnceLock::new();
    let start = *START.get_or_init(Instant::now);
    let secs = if at >= start { at.duration_since(start).as_secs_f64() } else { -start.duration_since(at).as_secs_f64() };
    secs + 1.0e6
}

pub fn now() -> f64 {
    mono(Instant::now())
}

fn epoch() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

// ------------------------------------------------------------------------------------------------- recording
/// Per-frame timing for one source to a CSV for `seconds`. The file stays until the next recording replaces it.
pub struct Recording {
    pub path: PathBuf,
    pub source: String,
    pub seconds: i64,
    began: f64,
    inner: Mutex<RecInner>,
}

struct RecInner {
    file: Option<File>,
    rows: u64,
    last: Option<f64>,
}

fn safe(source: &str) -> String {
    let mut out = String::new();
    let mut pending = false;
    for c in source.chars() {
        if c.is_ascii_alphanumeric() || "._-".contains(c) {
            if pending && !out.is_empty() {
                out.push('_');
            }
            pending = false;
            out.push(c);
        } else {
            pending = true;
        }
    }
    out.chars().take(60).collect::<String>().trim_matches('_').to_owned()
}

impl Recording {
    pub fn start(folder: &Path, source: &str, seconds: i64) -> std::io::Result<Recording> {
        std::fs::create_dir_all(folder)?;
        let iso = relay_core::timewin::iso(epoch() as i64).replace(['-', ':'], "").replace('T', "-").replace('Z', "");
        let path = folder.join(format!("{}-{iso}.csv", safe(source)));
        let mut file = File::create(&path)?;
        writeln!(file, "seconds,interval_ms,frame_bytes,width,height,flag")?;
        Ok(Recording { path, source: source.to_owned(), seconds: seconds.clamp(1, MAX_RECORD_S), began: now(), inner: Mutex::new(RecInner { file: Some(file), rows: 0, last: None }) })
    }

    pub fn frame(&self, at: f64, nbytes: usize, size: (u32, u32), flag: &str) {
        let Ok(mut g) = self.inner.lock() else { return };
        if g.file.is_none() {
            return;
        }
        if at - self.began >= self.seconds as f64 {
            g.file = None;
            return;
        }
        let interval = g.last.map_or(String::new(), |l| format!("{}", ((at - l) * 1000.0 * 1000.0).round() / 1000.0));
        let line = format!("{},{interval},{nbytes},{},{},{flag}", (((at - self.began) * 10000.0).round()) / 10000.0, size.0, size.1);
        if let Some(f) = g.file.as_mut() {
            let _ = writeln!(f, "{line}");
        }
        g.last = Some(at);
        g.rows += 1;
    }

    pub fn stop(&self) {
        if let Ok(mut g) = self.inner.lock() {
            g.file = None;
        }
    }

    pub fn active(&self) -> bool {
        self.inner.lock().is_ok_and(|g| g.file.is_some())
    }

    pub fn status(&self) -> Value {
        let elapsed = (now() - self.began).min(self.seconds as f64);
        let rows = self.inner.lock().map_or(0, |g| g.rows);
        json!({"source": self.source, "seconds": self.seconds, "elapsed": (elapsed * 10.0).round() / 10.0, "rows": rows, "active": self.active()})
    }
}

// ------------------------------------------------------------------------------------------------- per source
struct Latest {
    data: Vec<u8>,
    width: usize,
    height: usize,
    fourcc: &'static str,
}

/// Everything measured for one source. Timing always; scopes and audio only while an analysis is open.
pub struct Track {
    timing: Mutex<Timing>,
    meter: Mutex<AudioMeter>,
    sync: Mutex<SyncMeter>,
    cues: Mutex<VecDeque<Value>>,
    cue_total: AtomicU64,
    latest: Mutex<Option<(Latest, f64)>>,
    pub watch: AtomicBool,
    prev_grid: Mutex<Option<Grid>>,
    meta: Mutex<serde_json::Map<String, Value>>,
    dropped: AtomicU64,
    declared_fps: Mutex<f64>,
    size: Mutex<(u32, u32)>,
    recording: Mutex<Option<Arc<Recording>>>,
}

impl Track {
    fn new() -> Track {
        Track {
            timing: Mutex::new(Timing::new()),
            meter: Mutex::new(AudioMeter::new()),
            sync: Mutex::new(SyncMeter::new()),
            cues: Mutex::default(),
            cue_total: AtomicU64::new(0),
            latest: Mutex::new(None),
            watch: AtomicBool::new(false),
            prev_grid: Mutex::new(None),
            meta: Mutex::default(),
            dropped: AtomicU64::new(0),
            declared_fps: Mutex::new(0.0),
            size: Mutex::new((0, 0)),
            recording: Mutex::new(None),
        }
    }

    fn stats(&self) -> Stats {
        self.timing.lock().map(|t| t.stats(now())).unwrap_or_default()
    }
}

/// The sound a source sent lately, for the viewer's meters and the sources list.
struct Level {
    channels: Vec<(f64, f64)>,
    peak: f64,
    at: f64,
}

pub struct Analyzer {
    folder: PathBuf,
    tracks: Mutex<HashMap<String, Arc<Track>>>,
    last_recording: Mutex<HashMap<String, Arc<Recording>>>,
    levels: Mutex<HashMap<String, Level>>,
    loud: Mutex<HashMap<String, (AudioMeter, f64)>>,
    first_audio: Mutex<HashMap<String, f64>>,
    video_at: Mutex<HashMap<String, f64>>,
}

fn attrs(tag: &str) -> HashMap<String, String> {
    let mut out = HashMap::new();
    let mut rest = tag;
    while let Some(eq) = rest.find("=\"") {
        let key: String = rest[..eq].chars().rev().take_while(|c| c.is_alphanumeric() || *c == '_').collect::<Vec<_>>().into_iter().rev().collect();
        let after = &rest[eq + 2..];
        let Some(end) = after.find('"') else { break };
        if !key.is_empty() {
            out.entry(key).or_insert_with(|| after[..end].to_owned());
        }
        rest = &after[end + 1..];
    }
    out
}

fn tag<'a>(xml: &'a str, name: &str) -> Option<&'a str> {
    let at = xml.find(&format!("<{name}"))?;
    let after = &xml[at + name.len() + 1..];
    if !after.starts_with(|c: char| c.is_whitespace() || c == '/' || c == '>') {
        return None;
    }
    Some(&after[..after.find('>')?])
}

/// What a sender says about itself in NDI's connection metadata: `<ndi_product .../>` and `<ndi_capabilities .../>`.
pub fn parse_meta(xml: &str) -> serde_json::Map<String, Value> {
    let mut out = serde_json::Map::new();
    if let Some(t) = tag(xml, "ndi_product") {
        let a = attrs(t);
        for (key, name) in [("long_name", "name"), ("short_name", "short"), ("model_name", "model"), ("version", "version"), ("manufacturer", "maker"), ("serial", "serial")] {
            if let Some(v) = a.get(key).filter(|v| !v.is_empty()) {
                out.insert(name.into(), json!(v.chars().take(80).collect::<String>()));
            }
        }
    }
    if let Some(t) = tag(xml, "ndi_capabilities") {
        let a = attrs(t);
        let is = |k: &str| a.get(k).is_some_and(|v| v == "true");
        out.insert("ptz".into(), json!(is("ntk_ptz")));
        out.insert("recordable".into(), json!(is("ntk_record") || is("ntk_rec")));
        out.insert("kvm".into(), json!(is("ntk_kvm")));
    }
    out
}

/// NDI or NDI|HX, from the instance name the sender advertises ("PTZ-1 (NDI HX2)").
pub fn source_type(source: &str, machine: &str) -> &'static str {
    let text = format!("{source} {machine}");
    let hx = text.split(|c: char| !c.is_alphanumeric()).any(|w| {
        let w = w.to_ascii_lowercase();
        w == "hx" || (w.len() == 3 && w.starts_with("hx") && w.as_bytes()[2].is_ascii_digit())
    });
    if hx {
        "NDI|HX"
    } else {
        "NDI"
    }
}

fn pack(p: &Pack) -> Value {
    json!({"w": p.w, "h": p.h, "b64": p.b64})
}

fn alarms(a: &[(&'static str, &'static str)]) -> Value {
    json!(a.iter().map(|(x, y)| vec![*x, *y]).collect::<Vec<_>>())
}

pub fn stats_json(s: &Stats) -> Value {
    let mut d = json!({"frames": s.frames, "late": s.late, "long_gaps": s.long_gaps, "declared_fps": s.declared_fps, "recent_late": s.recent_late, "since_last_s": s.since_last_s});
    match &s.measured {
        None => {
            d["interval_ms"] = Value::Null;
            d["measured_fps"] = json!(0.0);
            d["jitter_ms"] = Value::Null;
            d["rate_mbps"] = json!(0.0);
            d["frame_bytes"] = Value::Null;
        }
        Some(m) => {
            d["interval_ms"] = json!({"min": m.iv_min, "avg": m.iv_avg, "max": m.iv_max, "sd": m.iv_sd});
            d["measured_fps"] = json!(m.measured_fps);
            d["jitter_ms"] = json!({"mean": m.jitter_mean, "peak": m.jitter_peak});
            d["rate_mbps"] = json!(m.rate_mbps);
            d["frame_bytes"] = json!({"min": m.bytes_min, "avg": m.bytes_avg, "max": m.bytes_max});
        }
    }
    d
}

fn audio_json(m: &AudioMeter, at: f64) -> Value {
    let Some(s) = m.snapshot(at) else { return Value::Null };
    let chans: Vec<Value> = s.channels.iter().map(|c| json!({"rms": c.rms, "peak": c.peak, "hold": c.hold, "true_peak": c.true_peak, "dc": c.dc, "mute": c.mute})).collect();
    json!({"rate": s.rate, "channels": chans, "clips": s.clips, "clip_now": s.clip_now, "momentary": s.momentary, "short": s.short, "integrated": s.integrated,
           "lra": s.lra, "correlation": s.correlation, "spectrum": s.spectrum, "alarms": alarms(&s.alarms)})
}

fn scopes_json(s: &scopes::Scopes) -> Value {
    let r = &s.read;
    json!({"waveform": pack(&s.waveform), "parade": s.parade.iter().map(pack).collect::<Vec<_>>(), "vectorscope": pack(&s.vectorscope), "histogram": s.histogram,
           "read": {"luma_min": r.luma_min, "luma_max": r.luma_max, "luma_mean": r.luma_mean, "sub_black": r.sub_black, "super_white": r.super_white,
                    "illegal_rgb": r.illegal_rgb, "frame_diff": r.frame_diff, "sharpness": r.sharpness, "letterbox": r.letterbox, "pillarbox": r.pillarbox,
                    "aspect": r.aspect, "matrix": r.matrix, "width": r.width, "height": r.height},
           "alarms": alarms(&s.alarms)})
}

impl Analyzer {
    pub fn new(folder: PathBuf) -> Analyzer {
        Analyzer { folder, tracks: Mutex::default(), last_recording: Mutex::default(), levels: Mutex::default(), loud: Mutex::default(), first_audio: Mutex::default(), video_at: Mutex::default() }
    }

    fn track(&self, key: &str) -> Arc<Track> {
        let Ok(mut t) = self.tracks.lock() else { return Arc::new(Track::new()) };
        Arc::clone(t.entry(key.to_owned()).or_insert_with(|| Arc::new(Track::new())))
    }

    pub fn get(&self, key: &str) -> Option<Arc<Track>> {
        self.tracks.lock().ok()?.get(key).cloned()
    }

    pub fn has(&self, key: &str) -> bool {
        self.get(key).is_some()
    }

    /// The source's receiver is gone: its numbers are no longer live.
    pub fn drop_key(&self, key: &str) {
        let t = self.tracks.lock().ok().and_then(|mut m| m.remove(key));
        if let Some(rec) = t.and_then(|t| t.recording.lock().ok().and_then(|mut r| r.take())) {
            rec.stop();
        }
    }

    pub fn clear(&self) {
        let keys: Vec<String> = self.tracks.lock().map(|m| m.keys().cloned().collect()).unwrap_or_default();
        for k in keys {
            self.drop_key(&k);
        }
        for m in [&self.levels as &dyn Clearable, &self.loud, &self.first_audio, &self.video_at] {
            m.clear_all();
        }
    }

    // ---- the receiver's thread
    pub fn on_video(&self, key: &str, source: &str, low: bool, v: &Video, arrived: Instant) {
        let t = self.track(key);
        let at = mono(arrived);
        let (nbytes, size) = (v.data.len(), (v.width, v.height));
        let flag = t.timing.lock().map_or("", |mut tm| tm.record(at, nbytes as u64, v.fps).name());
        if v.fps > 0.0 {
            if let Ok(mut f) = t.declared_fps.lock() {
                *f = v.fps;
            }
        }
        if v.width > 0 {
            if let Ok(mut s) = t.size.lock() {
                *s = size;
            }
        }
        if v.timecode != i64::MAX || v.timestamp != 0 {
            if let Ok(mut s) = t.sync.lock() {
                s.video(v.timecode, v.timestamp);
            }
        }
        if v.metadata.contains("vancPacket") {
            for cue in relay_ndi::scte::cues(&v.metadata) {
                let mut c = json!({"line": cue.line, "action": cue.action, "immediate": cue.immediate, "event_id": cue.event_id, "unique_program_id": cue.unique_program_id,
                                   "pre_roll_ms": cue.pre_roll_ms, "break_tenths": cue.break_tenths, "auto_return": cue.auto_return, "time_type": cue.time_type,
                                   "time": cue.time, "warnings": cue.warnings});
                c["at"] = json!(epoch());
                if let Ok(mut q) = t.cues.lock() {
                    if q.len() == MAX_CUES {
                        q.pop_front();
                    }
                    q.push_back(c);
                }
                t.cue_total.fetch_add(1, Ordering::Relaxed);
            }
        }
        if t.watch.load(Ordering::Relaxed) {
            if let Ok(mut l) = t.latest.lock() {
                if l.as_ref().is_none_or(|(_, when)| at - when >= SCOPE_COPY_EVERY_S) {
                    *l = Some((Latest { data: v.data.clone(), width: v.width as usize, height: v.height as usize, fourcc: v.fourcc }, at));
                }
            }
        }
        if let Some(rec) = t.recording.lock().ok().and_then(|r| r.clone()) {
            rec.frame(at, nbytes, size, flag);
        }
        if !low {
            if let Ok(mut m) = self.video_at.lock() {
                m.insert(source.to_owned(), at);
            }
        }
    }

    pub fn on_audio(&self, key: &str, source: &str, low: bool, a: &Audio) {
        let at = now();
        let t = self.track(key);
        if a.timecode != i64::MAX || a.timestamp != 0 {
            if let Ok(mut s) = t.sync.lock() {
                s.audio(a.timecode, a.timestamp);
            }
        }
        if t.watch.load(Ordering::Relaxed) {
            if let Ok(mut m) = t.meter.lock() {
                feed(&mut m, a, at);
            }
        }
        if low {
            return;
        }
        if let Ok(mut l) = self.levels.lock() {
            l.insert(source.to_owned(), Level { channels: a.levels.clone(), peak: a.peak_db, at });
        }
        if let Ok(mut m) = self.loud.lock() {
            if let Some((meter, asked)) = m.get_mut(source) {
                if at - *asked > LOUD_HOLD_S {
                    m.remove(source);
                } else {
                    feed(meter, a, at);
                }
            }
        }
        if let Ok(mut f) = self.first_audio.lock() {
            f.entry(source.to_owned()).or_insert(at);
        }
    }

    pub fn on_meta(&self, key: &str, xml: &str) {
        let parsed = parse_meta(xml);
        if let Ok(mut m) = self.track(key).meta.lock() {
            m.extend(parsed);
        }
    }

    pub fn on_perf(&self, key: &str, dropped: i64) {
        self.track(key).dropped.store(dropped.max(0) as u64, Ordering::Relaxed);
    }

    // ---- any thread
    pub fn watch(&self, key: &str, on: bool) {
        let t = self.track(key);
        t.watch.store(on, Ordering::Relaxed);
        if !on {
            if let Ok(mut l) = t.latest.lock() {
                *l = None;
            }
            if let Ok(mut g) = t.prev_grid.lock() {
                *g = None;
            }
        }
    }

    /// The names of the audio alarms lit now ("SILENCE", "CLIP", "PHASE"); none when no meter runs.
    pub fn audio_alarms(&self, key: &str) -> Vec<&'static str> {
        let Some(t) = self.get(key) else { return Vec::new() };
        let snap = t.meter.lock().ok().and_then(|m| m.snapshot(now()));
        snap.map(|s| s.alarms.iter().map(|a| a.0).collect()).unwrap_or_default()
    }

    pub fn health(&self, key: &str) -> (&'static str, String) {
        let Some(t) = self.get(key) else { return ("unknown", "Not being received".into()) };
        let (s, why) = health::health(&t.stats(), t.dropped.load(Ordering::Relaxed) as i64);
        (s.name(), why)
    }

    pub fn reset_audio(&self, key: &str) -> bool {
        let Some(t) = self.get(key) else { return false };
        if let Ok(mut m) = t.meter.lock() {
            m.reset();
        }
        true
    }

    /// `rec` is the source's saved record (address, port, groups, machine, source) from the sources list.
    pub fn snapshot(&self, key: &str, source: &str, rec: &Value, want_scopes: bool) -> Value {
        let Some(t) = self.get(key) else { return json!({"source": source, "live": false, "health": "unknown", "why": "Not being received"}) };
        let st = t.stats();
        let dropped = t.dropped.load(Ordering::Relaxed);
        let (hs, why) = health::health(&st, dropped as i64);
        let (w, h) = t.size.lock().map_or((0, 0), |s| *s);
        let fps = t.declared_fps.lock().map_or(0.0, |f| *f);
        let (rate, ch) = t.meter.lock().map_or((0, 0), |m| (m.rate(), m.channels()));
        let pick = |k: &str, d: Value| rec.get(k).filter(|v| !v.is_null()).cloned().unwrap_or(d);
        let nz = |a: u32, k: &str| if a > 0 { json!(a) } else { pick(k, json!(0)) };
        let mut info = json!({"type": source_type(rec["source"].as_str().unwrap_or(""), rec["machine"].as_str().unwrap_or("")), "host": pick("address", json!("")), "port": pick("port", json!(0)),
                              "groups": pick("groups", json!([])),
                              "format": {"width": nz(w, "width"), "height": nz(h, "height"), "fps": if fps > 0.0 { json!(fps) } else { pick("fps", json!(0)) }, "audio_rate": rate, "audio_channels": ch}});
        if let (Some(o), Ok(meta)) = (info.as_object_mut(), t.meta.lock()) {
            for (k, v) in meta.iter() {
                o.insert(k.clone(), v.clone());
            }
        }
        let sync = t.sync.lock().map(|s| s.snapshot()).ok().map(|s| json!({"valid": s.valid, "synthesized": s.synthesized, "offset_ms": s.offset_ms, "state": s.state}));
        let cues: Vec<Value> = t.cues.lock().map(|q| q.iter().rev().take(50).cloned().collect()).unwrap_or_default();
        let recording = t.recording.lock().ok().and_then(|r| r.clone()).map(|r| r.status());
        let mut out = json!({"source": source, "live": true, "health": hs.name(), "why": why, "info": info, "timing": stats_json(&st), "dropped": dropped,
                             "audio": t.meter.lock().map_or(Value::Null, |m| audio_json(&m, now())), "sync": sync,
                             "scte": {"total": t.cue_total.load(Ordering::Relaxed), "cues": cues}, "recording": recording});
        if want_scopes {
            let frame = t.latest.lock().ok().and_then(|l| l.as_ref().map(|(f, _)| Latest { data: f.data.clone(), width: f.width, height: f.height, fourcc: f.fourcc }));
            if let Some(f) = frame {
                let prev = t.prev_grid.lock().ok().and_then(|g| g.clone());
                if let Some((sc, grid)) = scopes::scopes(&Frame { data: &f.data, width: f.width, height: f.height, fourcc: f.fourcc }, prev.as_ref()) {
                    if let Ok(mut g) = t.prev_grid.lock() {
                        *g = Some(grid);
                    }
                    out["picture"] = scopes_json(&sc);
                }
            }
        }
        out
    }

    pub fn record(&self, key: &str, seconds: i64) -> Option<Arc<Recording>> {
        let t = self.get(key)?;
        if let Some(old) = t.recording.lock().ok().and_then(|r| r.clone()) {
            old.stop();
        }
        let rec = Arc::new(Recording::start(&self.folder, key, seconds).ok()?);
        *t.recording.lock().ok()? = Some(Arc::clone(&rec));
        self.last_recording.lock().ok()?.insert(key.to_owned(), Arc::clone(&rec));
        Some(rec)
    }

    pub fn stop_recording(&self, key: &str) {
        if let Some(rec) = self.last_recording.lock().ok().and_then(|m| m.get(key).cloned()) {
            rec.stop();
        }
        if let Some(t) = self.get(key) {
            if let Ok(mut r) = t.recording.lock() {
                *r = None;
            }
        }
    }

    pub fn last_recording(&self, key: &str) -> Option<Arc<Recording>> {
        self.last_recording.lock().ok()?.get(key).cloned()
    }

    // ---- the viewer's meters
    /// Each channel's latest peak in dBFS, or `None` when the source has sent none lately.
    pub fn audio_levels(&self, source: &str) -> Option<Vec<f64>> {
        let l = self.levels.lock().ok()?;
        let got = l.get(source)?;
        (now() - got.at < AUDIO_FRESH_S).then(|| got.channels.iter().map(|c| c.0).collect())
    }

    /// The source's latest audio peak in dBFS, or `None` when it has sent none lately.
    pub fn audio_level(&self, source: &str) -> Option<f64> {
        let l = self.levels.lock().ok()?;
        let got = l.get(source)?;
        (now() - got.at < AUDIO_FRESH_S).then_some(got.peak)
    }

    pub fn audio_channels(&self, source: &str) -> usize {
        self.audio_levels(source).map_or(0, |l| l.len())
    }

    /// What the viewer's meters show: each channel's peak and RMS, the loudness (measured only while someone keeps asking), and whether the
    /// source is audio only.
    pub fn audio_state(&self, source: &str) -> Value {
        let at = now();
        let snap = {
            let Ok(mut m) = self.loud.lock() else { return json!({"channels": [], "momentary": null, "short": null, "audio_only": false}) };
            let e = m.entry(source.to_owned()).or_insert_with(|| (AudioMeter::new(), at));
            e.1 = at;
            e.0.snapshot(at)
        };
        let chans: Vec<Value> = self
            .levels
            .lock()
            .ok()
            .and_then(|l| l.get(source).filter(|g| at - g.at < AUDIO_FRESH_S).map(|g| g.channels.iter().map(|c| json!({"peak": c.0, "rms": c.1})).collect()))
            .unwrap_or_default();
        json!({"channels": chans, "momentary": snap.as_ref().and_then(|s| s.momentary), "short": snap.as_ref().and_then(|s| s.short), "audio_only": self.audio_only(source)})
    }

    /// Sound for a while but never a picture: an audio-only source (#301).
    pub fn audio_only(&self, source: &str) -> bool {
        let first = self.first_audio.lock().ok().and_then(|f| f.get(source).copied());
        let video = self.video_at.lock().is_ok_and(|v| v.contains_key(source));
        first.is_some_and(|f| !video && now() - f > AUDIO_ONLY_AFTER_S)
    }
}

/// The meter takes at most this many channels, the first ones.
pub const MAX_METER_CHANNELS: usize = 8;

fn feed(m: &mut AudioMeter, a: &Audio, at: f64) {
    let ch = a.channels.min(MAX_METER_CHANNELS);
    if let Some(data) = a.data.get(..ch * a.samples) {
        m.feed(data, ch, a.samples, a.rate, at);
    }
}

trait Clearable {
    fn clear_all(&self);
}

impl<V> Clearable for Mutex<HashMap<String, V>> {
    fn clear_all(&self) {
        if let Ok(mut m) = self.lock() {
            m.clear();
        }
    }
}
