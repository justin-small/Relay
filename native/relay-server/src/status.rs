//! The status feed (#224) and the shared stream status modules publish into (#249): `app/streamstatus.py`, ported by #656.
//!
//! ```text
//! GET /stream?stream=status            (add &sound=1 for the room's SPL and RTA, #149)
//! ```
//!
//! For the sound desk and the booth: is Relay capturing, how hot is the input, which languages are live, how many are watching and,
//! from the modules that watch a livestream, is the stream on air and healthy. On the viewer port with no login, so it carries no
//! error text, no addresses and no keys: a stream entry says `health` only, and the reason stays on the module's admin page.
//!
//! Each message is the whole state. One is sent when something shown changes, and the whole state again every [`TICK`] so the input
//! level and the clock move and a panel's silence timer never fires.

use std::collections::BTreeMap;
use std::sync::Mutex;
use std::time::Duration;

use relay_core::hub::Hub;
use serde_json::{json, Value};

pub const STREAM: &str = "status";
pub const POLL: Duration = Duration::from_secs(1);
pub const TICK: Duration = Duration::from_secs(5);
/// More than any church streams to at once.
pub const MAX_SOURCES: usize = 8;
pub const MAX_LANGUAGES: usize = 8;
pub const HEALTH: [&str; 4] = ["ok", "warn", "error", "idle"];
pub const LABEL_MAX: usize = 24;
pub const DETAIL_MAX: usize = 160;
/// Below this the input counts as silent: a room with nobody at the microphone, or an input that delivers nothing.
pub const SILENT_DBFS: f64 = -50.0;

/// What the show is doing now. The engine, the capture and the panels are Python's until #657, #659 and #660 move them; until then
/// the Rust server has nothing to say, and [`Idle`] says so. Each of those issues implements the part it moves.
pub trait Show: Send + Sync {
    fn running(&self) -> bool {
        false
    }
    fn started_at(&self) -> Option<f64> {
        None
    }
    /// `capture.status()`: `running`, `reconnecting`, `error`, `clipping`, `rms_dbfs`.
    fn audio(&self) -> Value {
        json!({})
    }
    /// The labels of the languages being translated into now.
    fn live_languages(&self) -> Vec<String> {
        Vec::new()
    }
    /// The room's meters, when asked for with `sound=1`.
    fn sound(&self) -> Option<Value> {
        None
    }
    /// `engine.status()`: the first message of the admin status stream.
    fn engine_status(&self) -> Value {
        json!({"running": self.running()})
    }
    /// `engine.meter()`: the small frame sent every second between changes.
    fn meter(&self) -> Value {
        json!({"type": "meter"})
    }
    fn panels(&self) -> Value {
        json!({"type": "panels", "panels": []})
    }
    fn alerts(&self) -> Value {
        json!({"type": "alerts"})
    }
    /// Changes when a panel checks in or goes quiet.
    fn panels_online(&self) -> String {
        String::new()
    }
}

/// A show that is not running anything.
pub struct Idle;

impl Show for Idle {}

fn text(value: &Value, limit: usize) -> String {
    let Some(s) = value.as_str() else { return String::new() };
    let printable: String = s.chars().filter(|c| *c == ' ' || (!c.is_control() && !c.is_whitespace())).collect();
    printable.trim().chars().take(limit).collect()
}

fn valid_source(s: &str) -> bool {
    let mut chars = s.chars();
    s.len() <= 32 && chars.next().is_some_and(|c| c.is_ascii_lowercase()) && chars.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
}

/// What a stream-watching module said last, one entry per source (a module id), at most [`MAX_SOURCES`].
#[derive(Default)]
pub struct StreamStatus {
    entries: Mutex<BTreeMap<String, Value>>,
}

pub struct Publish<'a> {
    pub label: &'a str,
    /// `None`: not known (not connected yet).
    pub on_air: Option<bool>,
    pub health: &'a str,
    /// When the stream went live (epoch seconds), sent so a screen counts the elapsed time itself.
    pub live_since: Option<f64>,
    /// `None`: the provider gives no count.
    pub viewers: Option<i64>,
    /// For the admin page only.
    pub detail: &'a str,
}

impl StreamStatus {
    pub fn new() -> Self {
        Self::default()
    }

    /// Replace `source`'s entry. A module publishes from its own work and calls [`StreamStatus::clear`] in `detach`, so a module
    /// switched off leaves nothing behind.
    pub fn publish(&self, source: &str, p: Publish<'_>) -> Result<(), &'static str> {
        if !valid_source(source) {
            return Err("source must be a module id");
        }
        if !HEALTH.contains(&p.health) {
            return Err("unknown health");
        }
        let mut entries = self.entries.lock().unwrap_or_else(|e| e.into_inner());
        if !entries.contains_key(source) && entries.len() >= MAX_SOURCES {
            return Err("too many stream sources");
        }
        let label = text(&json!(p.label), LABEL_MAX);
        entries.insert(
            source.to_owned(),
            json!({
                "source": source,
                "label": if label.is_empty() { source.to_owned() } else { label },
                "on_air": p.on_air,
                "health": p.health,
                "live_since": if p.on_air == Some(true) { p.live_since.map_or(Value::Null, |f| json!(f)) } else { Value::Null },
                "viewers": p.viewers.map_or(Value::Null, |v| json!(v.max(0))),
                "detail": text(&json!(p.detail), DETAIL_MAX),
            }),
        );
        Ok(())
    }

    pub fn clear(&self, source: &str) {
        self.entries.lock().unwrap_or_else(|e| e.into_inner()).remove(source);
    }

    /// Sorted by label then source. Without `admin` the `detail` is left out.
    pub fn entries(&self, admin: bool) -> Vec<Value> {
        let mut out: Vec<Value> = self.entries.lock().unwrap_or_else(|e| e.into_inner()).values().cloned().collect();
        out.sort_by_key(|e| (e["label"].as_str().unwrap_or("").to_lowercase(), e["source"].as_str().unwrap_or("").to_owned()));
        if !admin {
            for e in &mut out {
                if let Some(o) = e.as_object_mut() {
                    o.remove("detail");
                }
            }
        }
        out
    }
}

/// The input as a screen shows it: `state` is off, lost, clipping, silent or ok; `dbfs` the level, rounded.
pub fn input_state(audio: &Value, running: bool) -> Value {
    let dbfs = audio.get("rms_dbfs").and_then(Value::as_f64).map(|f| f.round() as i64).unwrap_or(-60);
    let truthy = |k: &str| audio.get(k).is_some_and(|v| v.as_bool().unwrap_or(!v.is_null() && v != &json!("") && v != &json!(0)));
    let state = if !running || !truthy("running") {
        "off"
    } else if truthy("reconnecting") || truthy("error") {
        "lost"
    } else if truthy("clipping") {
        "clipping"
    } else if (dbfs as f64) <= SILENT_DBFS {
        "silent"
    } else {
        "ok"
    };
    json!({"state": state, "dbfs": dbfs.clamp(-60, 0)})
}

pub fn payload(show: &dyn Show, hub: &Hub, streams: &StreamStatus, now: f64, sound: bool) -> Value {
    let running = show.running();
    let entries = streams.entries(false);
    let any_known = entries.iter().any(|e| !e["on_air"].is_null());
    let on_air = if any_known { json!(entries.iter().any(|e| e["on_air"] == json!(true))) } else { Value::Null };
    let languages: Vec<String> = show.live_languages().iter().take(MAX_LANGUAGES).map(|l| text(&json!(l), LABEL_MAX)).collect();
    let mut out = json!({
        "type": "status",
        "running": running,
        "started_at": if running { show.started_at().map_or(Value::Null, |f| json!(f)) } else { Value::Null },
        "input": input_state(&show.audio(), running),
        "languages": languages,
        "viewers": hub.viewer_count(),
        "on_air": on_air,
        "streams": entries,
    });
    if sound {
        if let Some(snd) = show.sound().filter(|s| !s.is_null() && s != &json!({}) && s != &json!([])) {
            out["sound"] = snd;
        }
    }
    out["server_time"] = json!(now);
    out
}

fn sorted(v: &Value) -> Value {
    match v {
        Value::Object(o) => Value::Object(o.iter().map(|(k, v)| (k.clone(), sorted(v))).collect::<std::collections::BTreeMap<_, _>>().into_iter().collect()),
        Value::Array(a) => Value::Array(a.iter().map(sorted).collect()),
        other => other.clone(),
    }
}

/// What counts as a change: everything but the clock and the level's exact dB (only its coarse `state`).
pub fn signature(msg: &Value) -> String {
    let mut shown = msg.clone();
    if let Some(o) = shown.as_object_mut() {
        o.remove("server_time");
        let state = msg.get("input").and_then(|i| i.get("state")).cloned().unwrap_or(Value::Null);
        o.insert("input".into(), state);
    }
    sorted(&shown).to_string()
}
