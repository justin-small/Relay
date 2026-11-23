//! The saved audio bus setting (`app/audiobus.py` `normalize`, `first_setup`, `needs_setup`, `migrate_legacy_input`): out-of-range
//! or duplicate entries are dropped rather than refused, so a hand-edited config never stops Relay starting. The setting stays
//! a JSON value with Python's keys, so config.json is read and written unchanged by either server.

use relay_bus::{Cell, ChannelSel, Config, LaneSpec, OutputSpec, DEVICE_SOURCE};
use serde_json::{json, Map, Value};

pub const LANES: i64 = 32;
pub const DEFAULT_LATENCY_MS: i64 = 60;
pub const MIN_LATENCY_MS: i64 = 20;
pub const MAX_LATENCY_MS: i64 = 500;
pub const GAIN_MIN_DB: f64 = -90.0;
pub const GAIN_MAX_DB: f64 = 24.0;
/// A reserved output id: it was the AudioOutput that played Relay's own sounds, before destinations.
pub const BUS_OUTPUT: &str = "bus";
/// The audio source id that lets the capture take a lane as its input.
pub const LANE_SOURCE: &str = "lane";

// -- Python's coercions: `int(v)`, `float(v)` and `str(v or "")`, so a posted "3" or 3.9 means what it meant ---------------------

/// `int(v)`: a number truncates, a numeric string parses, a bool is 0 or 1; anything else is not a number.
pub fn py_int(v: &Value) -> Option<i64> {
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        Value::Bool(b) => Some(*b as i64),
        _ => None,
    }
}

/// `float(v)`.
pub fn py_float(v: &Value) -> Option<f64> {
    match v {
        Value::Number(n) => n.as_f64(),
        Value::String(s) => s.trim().parse::<f64>().ok(),
        Value::Bool(b) => Some(*b as i64 as f64),
        _ => None,
    }
}

/// `str(v or "")`: text, a number as written, `True` for true; null, false and containers are empty.
pub fn py_str(v: &Value) -> String {
    match v {
        Value::String(s) => s.clone(),
        Value::Number(n) => n.to_string(),
        Value::Bool(true) => "True".into(),
        _ => String::new(),
    }
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// `v` as an int inside `lo..=hi`: clamped, or `default` when `drop` (a lane number out of range is not the nearest lane, it is nothing).
pub fn int_in(v: Option<&Value>, lo: i64, hi: i64, default: i64, drop: bool) -> i64 {
    let Some(n) = v.and_then(py_int) else { return default };
    if drop && !(lo..=hi).contains(&n) {
        return default;
    }
    n.clamp(lo, hi)
}

fn device_of(item: &Value) -> Value {
    match item.get("device") {
        None | Some(Value::Null) => Value::Null,
        Some(Value::String(s)) if s.is_empty() => Value::Null,
        Some(v) => Value::String(clip(&match v { Value::String(s) => s.clone(), other => other.to_string() }, 200)),
    }
}

fn round2(x: f64) -> f64 {
    (x * 100.0).round() / 100.0
}

/// A clean `audio_bus` setting from whatever was saved or posted.
pub fn normalize(raw: &Value) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let list = |key: &str| raw.get(key).and_then(Value::as_array).cloned().unwrap_or_default();

    let mut lanes: Vec<Value> = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for item in list("lanes") {
        if !item.is_object() {
            continue;
        }
        let n = int_in(item.get("lane"), 1, LANES, 0, true);
        if n == 0 || !seen.insert(n) {
            continue;
        }
        let channel = match item.get("channel") {
            Some(Value::String(s)) if s.trim().eq_ignore_ascii_case("mix") => json!("mix"),
            other => json!(int_in(other, 0, 255, 0, false)),
        };
        let source = py_str(item.get("source").unwrap_or(&Value::Null)).trim().to_lowercase();
        lanes.push(json!({
            "lane": n,
            "name": clip(py_str(item.get("name").unwrap_or(&Value::Null)).trim(), 40),
            "source": clip(if source.is_empty() { DEVICE_SOURCE } else { &source }, 40),
            "device": device_of(&item),
            "channel": channel,
        }));
    }
    lanes.sort_by_key(|l| l["lane"].as_i64());

    let mut outputs: Vec<Value> = Vec::new();
    let mut ids = std::collections::HashSet::new();
    for item in list("outputs") {
        if !item.is_object() {
            continue;
        }
        let oid: String = py_str(item.get("id").unwrap_or(&Value::Null))
            .trim()
            .to_lowercase()
            .replace(' ', "_")
            .chars()
            .filter(|c| c.is_alphanumeric() || *c == '_')
            .take(24)
            .collect();
        if oid.is_empty() || oid == BUS_OUTPUT || !ids.insert(oid.clone()) {
            continue;
        }
        let kind = py_str(item.get("output").unwrap_or(&Value::Null)).trim().to_lowercase();
        outputs.push(json!({
            "id": oid,
            "name": clip(py_str(item.get("name").unwrap_or(&Value::Null)).trim(), 40),
            "output": clip(if kind.is_empty() { DEVICE_SOURCE } else { &kind }, 40),
            "device": device_of(&item),
            "channels": int_in(item.get("channels"), 1, LANES, 2, false),
            "latency_ms": int_in(item.get("latency_ms"), MIN_LATENCY_MS, MAX_LATENCY_MS, DEFAULT_LATENCY_MS, false),
        }));
    }
    let channels: std::collections::HashMap<String, i64> =
        outputs.iter().map(|o| (o["id"].as_str().unwrap_or("").to_owned(), o["channels"].as_i64().unwrap_or(0))).collect();

    let mut matrix: Vec<Value> = Vec::new();
    let mut cells = std::collections::HashSet::new();
    for item in list("matrix") {
        if !item.is_object() {
            continue;
        }
        let lane = int_in(item.get("in"), 1, LANES, 0, true);
        let oid = py_str(item.get("out").unwrap_or(&Value::Null));
        let ch = int_in(item.get("ch"), 1, LANES, 0, true);
        let Some(&width) = channels.get(&oid) else { continue };
        if lane == 0 || ch == 0 || ch > width || !cells.insert((lane, oid.clone(), ch)) {
            continue;
        }
        let gain = item.get("gain_db").and_then(py_float).filter(|g| !g.is_nan()).unwrap_or(0.0);
        matrix.push(json!({"in": lane, "out": oid, "ch": ch, "gain_db": round2(gain.clamp(GAIN_MIN_DB, GAIN_MAX_DB))}));
    }
    matrix.sort_by(|a, b| {
        (a["out"].as_str(), a["ch"].as_i64(), a["in"].as_i64()).cmp(&(b["out"].as_str(), b["ch"].as_i64(), b["in"].as_i64()))
    });
    json!({"lanes": lanes, "outputs": outputs, "matrix": matrix})
}

/// What first setup still has to ask for: `{"input": no lane yet, "output": no output yet}`.
pub fn needs_setup(cfg: &Value) -> Value {
    let empty = |k: &str| cfg.get(k).and_then(Value::as_array).is_none_or(Vec::is_empty);
    json!({"input": empty("lanes"), "output": empty("outputs")})
}

/// What the operator picked at first setup, for one part: leave it for later, use the system default, or a named device.
#[derive(Clone, Debug, PartialEq)]
pub enum Pick {
    Later,
    Default,
    Device(String),
}

impl Pick {
    /// The posted value: null leaves it for later, "" is the system default.
    pub fn of(v: Option<&Value>) -> Pick {
        match v {
            None | Some(Value::Null) => Pick::Later,
            Some(v) => match py_str(v) {
                s if s.is_empty() => Pick::Default,
                s => Pick::Device(s),
            },
        }
    }
    fn device(&self) -> Value {
        match self {
            Pick::Device(d) => json!(d),
            _ => Value::Null,
        }
    }
}

/// Add what the operator picked at first setup to `current`: the input becomes lane 1 when there is no lane, the output
/// becomes output "main" when there is none. A part already set up is kept. Returns (setting, made_output).
pub fn first_setup(current: &Value, input: &Pick, output: &Pick, out_channels: i64) -> (Value, bool) {
    let mut cfg = normalize(current);
    let none = |cfg: &Value, k: &str| cfg[k].as_array().is_none_or(Vec::is_empty);
    if none(&cfg, "lanes") && *input != Pick::Later {
        cfg["lanes"] = json!([{"lane": 1, "name": "Main input", "source": DEVICE_SOURCE, "device": input.device(), "channel": "mix"}]);
    }
    let made = none(&cfg, "outputs") && *output != Pick::Later;
    if made {
        cfg["outputs"] = json!([{"id": "main", "name": "Main output", "output": DEVICE_SOURCE, "device": output.device(),
                                  "channels": out_channels.clamp(1, LANES), "latency_ms": DEFAULT_LATENCY_MS}]);
    }
    (normalize(&cfg), made)
}

/// An install from before the capture read a lane keeps its input (#599): `audio_source`, `audio_device` and `input_channel`
/// become a lane on the bus, and `audio_lane` points at it. A config with none of those keys is a fresh install, left alone.
pub fn migrate_legacy_input(cfg: &mut Value) {
    if !["audio_source", "audio_device", "input_channel"].iter().any(|k| cfg.get(*k).is_some()) {
        return;
    }
    let source = {
        let s = py_str(cfg.get("audio_source").unwrap_or(&Value::Null)).trim().to_lowercase();
        if s.is_empty() { DEVICE_SOURCE.to_owned() } else { s }
    };
    let bus = cfg.get("audio_bus").filter(|v| v.is_object()).cloned().unwrap_or_else(|| json!({}));
    let mut lanes: Vec<Value> = bus.get("lanes").and_then(Value::as_array).map(|a| a.iter().filter(|x| x.is_object()).cloned().collect()).unwrap_or_default();
    if source == LANE_SOURCE {
        let device = py_str(cfg.get("audio_device").unwrap_or(&Value::Null)).to_lowercase().replace("lane", "");
        cfg["audio_lane"] = json!(int_in(Some(&json!(device.trim())), 1, LANES, 1, false));
    } else {
        let taken: std::collections::HashSet<i64> = lanes.iter().filter_map(|l| l["lane"].as_i64()).collect();
        let want = int_in(cfg.get("audio_lane"), 1, LANES, 1, false);
        let n = if !taken.contains(&want) { want } else { (1..=LANES).find(|k| !taken.contains(k)).unwrap_or(0) };
        if n != 0 {
            let channel = match cfg.get("input_channel") {
                Some(Value::String(s)) if s.trim().eq_ignore_ascii_case("mix") => json!("mix"),
                Some(v) => v.clone(),
                None => json!(0),
            };
            lanes.push(json!({"lane": n, "name": "Main input", "source": source, "device": cfg.get("audio_device").cloned().unwrap_or(Value::Null), "channel": channel}));
            cfg["audio_lane"] = json!(n);
        }
    }
    let mut merged = bus.as_object().cloned().unwrap_or_default();
    merged.insert("lanes".into(), Value::Array(lanes));
    cfg["audio_bus"] = Value::Object(merged);
}

/// The engine's configuration from a clean setting (the output of [`normalize`]).
pub fn engine_config(cfg: &Value) -> Config {
    let arr = |k: &str| cfg.get(k).and_then(Value::as_array).cloned().unwrap_or_default();
    let device = |v: &Value| v.get("device").and_then(Value::as_str).map(str::to_owned);
    Config {
        enabled: true,
        lanes: arr("lanes")
            .iter()
            .map(|l| LaneSpec {
                lane: l["lane"].as_u64().unwrap_or(1) as usize,
                name: l["name"].as_str().unwrap_or("").to_owned(),
                source: l["source"].as_str().unwrap_or(DEVICE_SOURCE).to_owned(),
                device: device(l),
                channel: match &l["channel"] {
                    Value::String(_) => ChannelSel::Mix,
                    n => ChannelSel::Index(n.as_u64().unwrap_or(0) as usize),
                },
            })
            .collect(),
        outputs: arr("outputs")
            .iter()
            .map(|o| OutputSpec {
                id: o["id"].as_str().unwrap_or("").to_owned(),
                name: o["name"].as_str().unwrap_or("").to_owned(),
                output: o["output"].as_str().unwrap_or(DEVICE_SOURCE).to_owned(),
                device: device(o),
                channels: o["channels"].as_u64().unwrap_or(2) as usize,
                latency_ms: o["latency_ms"].as_u64().unwrap_or(DEFAULT_LATENCY_MS as u64) as u32,
            })
            .collect(),
        matrix: arr("matrix")
            .iter()
            .map(|c| Cell {
                lane: c["in"].as_u64().unwrap_or(1) as usize,
                out: c["out"].as_str().unwrap_or("").to_owned(),
                ch: c["ch"].as_u64().unwrap_or(1) as usize,
                gain_db: c["gain_db"].as_f64().unwrap_or(0.0) as f32,
            })
            .collect(),
    }
}
