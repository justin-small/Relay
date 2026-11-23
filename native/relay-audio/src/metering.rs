//! Sound-level metering (`app/metering.py`, #149, #604): calibrated SPL meters and 1/3-octave analysers read from lanes of the bus.
//! The maths is in `relay-bus` (a [`relay_bus::ProbeHub`] probe taps a lane and a Rust thread does the weighting, the time constants,
//! the Leq and the 31 bands), so a meter never loses audio while the server is busy. This file keeps the settings, opens and closes
//! probes to match them, and turns a probe's finished numbers into the reading the pages and the status feed carry.
//!
//! Other tools' readings. A module that reads a measurement from another product registers a source with
//! [`Metering::register_source`] and calls [`Metering::publish`] as readings arrive. A reading is levels in dB SPL, optionally the
//! 31 bands and its own `warn_db`/`limit_db`. Boards and the status feed carry them next to Relay's own.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use relay_bus::analysis::spl::CalibrateError;
use relay_bus::{ProbeReading, ProbeSpec};
use serde_json::{json, Map, Value};

use crate::bus::AudioBus;
use crate::normalize::{py_float, py_int, py_str, LANES};

pub const SILENT_S: f64 = 2.0;
pub const CAL_MIN: f64 = 60.0;
pub const CAL_MAX: f64 = 160.0;
pub const MAX_METERS: usize = 32;
pub const NAME_MAX: usize = 40;
pub const BAND_N: usize = 31;
pub const EXTERNAL_STALE: Duration = Duration::from_secs(5);
pub const MAX_SOURCES: usize = 8;
pub const BAND_LABELS: [&str; BAND_N] = [
    "20", "25", "31.5", "40", "50", "63", "80", "100", "125", "160", "200", "250", "315", "400", "500", "630", "800", "1k", "1.25k", "1.6k", "2k", "2.5k",
    "3.15k", "4k", "5k", "6.3k", "8k", "10k", "12.5k", "16k", "20k",
];
pub const LEVELS: [&str; 5] = ["a_fast", "a_slow", "c_fast", "c_slow", "leq_a"];

/// What the calibrate route may say, by reason. The route answers from this table, never from an error's text.
pub fn calibrate_sentence(reason: &str) -> &'static str {
    match reason {
        "reference" => "Enter the level the calibrator says, in dB, between 30 and 140.",
        "off" => "Switch the meter on first.",
        "no_signal" => "No sound is arriving on that lane. Patch the measurement mic and play the calibrator.",
        _ => "The calibrator is too quiet to read. Raise the mic gain or move it closer.",
    }
}

/// Where the saved `metering` block lives.
pub trait MeterStore: Send + Sync {
    fn metering(&self) -> Value;
    /// Replace the `metering` block with `cfg` and say what was saved.
    fn save_metering(&self, cfg: &Value) -> Value;
}

fn num(v: Option<&Value>, lo: f64, hi: f64, default: f64) -> f64 {
    match v.and_then(py_float) {
        Some(n) if !n.is_nan() => ((n.clamp(lo, hi)) * 10.0).round() / 10.0,
        _ => default,
    }
}

fn valid_id(id: &str) -> bool {
    let mut c = id.chars();
    matches!(c.next(), Some(f) if f.is_ascii_lowercase() || f.is_ascii_digit()) && id.len() <= 20 && c.all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '-')
}

pub fn default_meter(n: usize) -> Value {
    json!({"id": format!("m{n}"), "name": if n == 1 { "Room".to_owned() } else { format!("Meter {n}") }, "enabled": true, "lane": 1, "kind": "room",
           "calibration_db": 110.0, "calibrated": false, "warn_db": 85.0, "limit_db": 95.0})
}

fn clean_meter(raw: &Value, taken: &[String], n: usize) -> Value {
    let mut out = default_meter(n);
    let mut id = py_str(raw.get("id").unwrap_or(&Value::Null)).to_lowercase();
    if !valid_id(&id) || taken.contains(&id) {
        id = (n..n + MAX_METERS + 2).map(|i| format!("m{i}")).find(|c| !taken.contains(c)).unwrap_or_else(|| format!("m{n}"));
    }
    out["id"] = json!(id);
    let name: String = py_str(raw.get("name").unwrap_or(&Value::Null)).split_whitespace().collect::<Vec<_>>().join(" ").chars().take(NAME_MAX).collect();
    if !name.is_empty() {
        out["name"] = json!(name);
    }
    out["enabled"] = json!(match raw.get("enabled") {
        None => true,
        Some(Value::Bool(b)) => *b,
        Some(Value::Null) => false,
        Some(Value::Number(n)) => n.as_f64().is_some_and(|f| f != 0.0),
        Some(Value::String(s)) => !s.is_empty(),
        Some(_) => true,
    });
    if let Some(l) = raw.get("lane").and_then(py_int) {
        out["lane"] = json!(l.clamp(1, LANES));
    }
    out["kind"] = json!(raw.get("kind").and_then(Value::as_str).filter(|k| matches!(*k, "room" | "mix")).unwrap_or("room"));
    out["calibration_db"] = json!(num(raw.get("calibration_db"), CAL_MIN, CAL_MAX, 110.0));
    out["calibrated"] = json!(raw.get("calibrated").is_some_and(|v| match v {
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        _ => false,
    }));
    let warn = num(raw.get("warn_db"), 40.0, 140.0, 85.0);
    out["warn_db"] = json!(warn);
    out["limit_db"] = json!(warn.max(num(raw.get("limit_db"), 40.0, 140.0, 95.0)));
    out
}

/// A clean `metering` setting from whatever was saved or posted: out-of-range values are clamped, unknown keys dropped, ids made
/// unique. The warning level never sits above the limit.
pub fn normalize(raw: &Value) -> Value {
    let mut meters: Vec<Value> = Vec::new();
    let mut taken: Vec<String> = Vec::new();
    for (i, m) in raw.get("meters").and_then(Value::as_array).into_iter().flatten().take(MAX_METERS).enumerate() {
        let meter = clean_meter(m, &taken, i + 1);
        taken.push(meter["id"].as_str().unwrap_or("").to_owned());
        meters.push(meter);
    }
    json!({"meters": meters})
}

fn db(v: Option<&Value>) -> Option<f64> {
    let n = v.and_then(py_float).filter(|n| n.is_finite())?;
    Some((n.clamp(-20.0, 200.0) * 10.0).round() / 10.0)
}

struct External {
    label: String,
    reading: Option<Map<String, Value>>,
    at: Option<Instant>,
}

struct Running {
    config: Value,
    /// meter id -> (probe id, lane it was opened on)
    probes: HashMap<String, (u64, i64)>,
}

pub struct Metering {
    bus: Arc<AudioBus>,
    store: Arc<dyn MeterStore>,
    running: Mutex<Running>,
    sources: Mutex<Vec<(String, External)>>,
}

fn r1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

impl Metering {
    pub fn new(bus: Arc<AudioBus>, store: Arc<dyn MeterStore>) -> Arc<Self> {
        Arc::new(Self { bus, store, running: Mutex::new(Running { config: json!({"meters": []}), probes: HashMap::new() }), sources: Mutex::new(Vec::new()) })
    }

    fn run(&self) -> std::sync::MutexGuard<'_, Running> {
        self.running.lock().unwrap_or_else(|e| e.into_inner())
    }

    pub fn config(&self) -> Value {
        self.store.metering()
    }

    /// Open, retarget or close probes to match the saved setting. A meter whose lane did not change keeps its probe, and so its averages.
    pub async fn apply(&self, cfg: &Value) {
        let cfg = normalize(cfg);
        {
            let mut run = self.run();
            run.config = cfg.clone();
            let want: HashMap<String, &Value> = cfg["meters"].as_array().into_iter().flatten().filter(|m| m["enabled"] == true).map(|m| (m["id"].as_str().unwrap_or("").to_owned(), m)).collect();
            let stale: Vec<String> = run.probes.iter().filter(|(k, (_, lane))| want.get(*k).is_none_or(|m| m["lane"].as_i64() != Some(*lane))).map(|(k, _)| k.clone()).collect();
            for mid in stale {
                if let Some((pid, _)) = run.probes.remove(&mid) {
                    let _ = self.bus.probes().close(pid);
                }
            }
            for (mid, m) in want {
                let cal = m["calibration_db"].as_f64().unwrap_or(110.0);
                if let Some((pid, _)) = run.probes.get(&mid) {
                    let _ = self.bus.probes().set_calibration(*pid, cal);
                    continue;
                }
                let lane = m["lane"].as_i64().unwrap_or(1);
                match self.bus.probes().open(ProbeSpec::Spl { lane: lane as usize, calibration_db: cal }) {
                    Ok(pid) => {
                        run.probes.insert(mid, (pid, lane));
                    }
                    Err(e) => tracing::warn!("Could not start meter {mid}: {e}"),
                }
            }
        }
        self.report_sources().await;
    }

    pub fn stop(&self) {
        let mut run = self.run();
        for (_, (pid, _)) in run.probes.drain() {
            let _ = self.bus.probes().close(pid);
        }
    }

    /// Start the meters the way Relay starts them: the saved setting, when any meter is on.
    pub async fn start(&self) {
        let cfg = self.store.metering();
        if cfg["meters"].as_array().is_some_and(|m| m.iter().any(|x| x["enabled"] == true)) {
            self.apply(&cfg).await;
        }
    }

    fn meter_cfg(run: &Running, mid: &str) -> Option<Value> {
        run.config["meters"].as_array().into_iter().flatten().find(|m| m["id"] == mid).cloned()
    }

    /// Set the offset so the meter's current Slow reading is `reference_db`. Needs a signal; `Err` carries a key of
    /// [`calibrate_sentence`].
    pub fn calibrate(&self, mid: &str, reference_db: &Value) -> Result<f64, &'static str> {
        let reference = reference_db.as_f64().or_else(|| py_float(reference_db)).filter(|r| !r.is_nan()).map(|r| r1(r.clamp(30.0, 140.0))).ok_or("reference")?;
        let mut run = self.run();
        let (Some((pid, _)), Some(_)) = (run.probes.get(mid).copied(), Self::meter_cfg(&run, mid)) else { return Err("off") };
        let st = self.bus.probes().status(pid).map_err(|_| "off")?;
        if st.idle_s.is_none_or(|s| s > SILENT_S) {
            return Err("no_signal");
        }
        let cal = match self.bus.probes().calibrate(pid, reference) {
            Ok(Ok(c)) => c,
            Ok(Err(CalibrateError::Reference)) => return Err("reference"),
            Ok(Err(CalibrateError::TooQuiet)) | Err(_) => return Err("too_quiet"),
        };
        let cal = r1(cal.clamp(CAL_MIN, CAL_MAX));
        if let Some(m) = run.config["meters"].as_array_mut().and_then(|a| a.iter_mut().find(|m| m["id"] == mid)) {
            m["calibration_db"] = json!(cal);
            m["calibrated"] = json!(true);
        }
        Ok(cal)
    }

    /// The reading of one meter, with its setting; `None` for a meter that is not in the setting.
    pub fn snapshot(&self, mid: &str) -> Option<Value> {
        let run = self.run();
        Self::snapshot_of(&self.bus, &run, mid)
    }

    fn snapshot_of(bus: &AudioBus, run: &Running, mid: &str) -> Option<Value> {
        let cfg = Self::meter_cfg(run, mid)?;
        let mut out = json!({"id": cfg["id"], "name": cfg["name"], "enabled": cfg["enabled"], "lane": cfg["lane"], "kind": cfg["kind"],
                             "calibrated": cfg["calibrated"], "calibration_db": cfg["calibration_db"], "warn_db": cfg["warn_db"], "limit_db": cfg["limit_db"],
                             "labels": BAND_LABELS});
        let off = |mut o: Value| {
            o["state"] = json!("off");
            o["dropped"] = json!(0);
            o
        };
        let Some((pid, _)) = run.probes.get(mid).filter(|_| cfg["enabled"] == true) else { return Some(off(out)) };
        let Ok(st) = bus.probes().status(*pid) else { return Some(off(out)) };
        let ProbeReading::Spl(r) = st.reading else { return Some(off(out)) };
        out["state"] = json!(if st.idle_s.is_some_and(|s| s <= SILENT_S) { "ok" } else { "no_input" });
        out["dropped"] = json!(st.dropped);
        for (k, v) in [("a_fast", r.a_fast), ("a_slow", r.a_slow), ("c_fast", r.c_fast), ("c_slow", r.c_slow), ("leq_a", r.leq_a)] {
            out[k] = json!(r1(v));
        }
        out["bands"] = json!(r.bands.iter().map(|b| r1(*b)).collect::<Vec<_>>());
        Some(out)
    }

    pub fn snapshots(&self) -> Vec<Value> {
        let run = self.run();
        run.config["meters"].as_array().into_iter().flatten().filter_map(|m| Self::snapshot_of(&self.bus, &run, m["id"].as_str().unwrap_or(""))).collect()
    }

    /// What the status feed carries (the viewer port, no login): the levels and the labels that keep the number honest, nothing about
    /// devices. The first meter that is on is the default reading; every meter, and every other tool's source, is also under `sources`
    /// by id. `None` when nothing is measuring.
    pub fn public(&self) -> Option<Value> {
        const KEEP: [&str; 11] = ["state", "kind", "calibrated", "warn_db", "limit_db", "a_fast", "a_slow", "c_fast", "c_slow", "leq_a", "bands"];
        let mine: Vec<Value> = self.snapshots().into_iter().filter(|s| s["state"] != "off").collect();
        let ext = self.external_public();
        if mine.is_empty() && ext.is_empty() {
            return None;
        }
        let pick = |s: &Value| -> Map<String, Value> { KEEP.iter().filter_map(|k| s.get(*k).map(|v| ((*k).to_owned(), v.clone()))).collect() };
        let mut sources = Map::new();
        for s in &mine {
            let mut o = Map::new();
            o.insert("label".into(), s["name"].clone());
            o.extend(pick(s));
            sources.insert(s["id"].as_str().unwrap_or("").to_owned(), Value::Object(o));
        }
        sources.extend(ext);
        let mut head = mine.first().map(pick).unwrap_or_else(|| Map::from_iter([("state".to_owned(), json!("off"))]));
        if !sources.is_empty() {
            head.insert("sources".into(), Value::Object(sources));
        }
        Some(Value::Object(head))
    }

    // -- other tools' readings --------------------------------------------------------------------------------------
    /// The sound-level sources are audio channels in core (#542): a source is a channel you can meter. Relay's own meters and the
    /// registered tools' sources both.
    pub async fn report_sources(&self) {
        let mut found: Vec<Value> = self
            .run()
            .config["meters"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|m| m["enabled"] == true)
            .map(|m| json!({"key": m["id"], "kind": "audio", "direction": "source", "gear_name": m["name"]}))
            .collect();
        found.extend(self.sources().into_iter().map(|(k, label)| json!({"key": k, "kind": "audio", "direction": "source", "gear_name": label})));
        self.bus.channels.report("metering", &found).await;
    }

    pub async fn register_source(&self, id: &str, label: &str) {
        {
            let mut s = self.sources.lock().unwrap_or_else(|e| e.into_inner());
            let label: String = label.chars().take(40).collect();
            if let Some((_, e)) = s.iter_mut().find(|(k, _)| k == id) {
                e.label = label;
            } else if s.len() < MAX_SOURCES {
                s.push((id.to_owned(), External { label, reading: None, at: None }));
            }
        }
        self.report_sources().await;
    }

    pub async fn unregister_source(&self, id: &str) {
        self.sources.lock().unwrap_or_else(|e| e.into_inner()).retain(|(k, _)| k != id);
        self.report_sources().await;
    }

    /// `(id, label)` of every registered source from other tools.
    pub fn sources(&self) -> Vec<(String, String)> {
        self.sources.lock().unwrap_or_else(|e| e.into_inner()).iter().map(|(k, v)| (k.clone(), v.label.clone())).collect()
    }

    /// `(id, label)` of everything a sound widget can read: Relay's own meters, then each tool's source.
    pub fn choices(&self) -> Vec<(String, String)> {
        let mut out: Vec<(String, String)> = self.run().config["meters"].as_array().into_iter().flatten().filter(|m| m["enabled"] == true).map(|m| (m["id"].as_str().unwrap_or("").to_owned(), m["name"].as_str().unwrap_or("").to_owned())).collect();
        out.extend(self.sources());
        out
    }

    /// A registered source's newest reading. Unknown ids, junk numbers and a `bands` list that is not 31 numbers are dropped.
    pub fn publish(&self, id: &str, reading: &Value) {
        let Some(o) = reading.as_object() else { return };
        let mut clean = Map::new();
        for k in LEVELS {
            if let Some(v) = db(o.get(k)) {
                clean.insert(k.into(), json!(v));
            }
        }
        if let Some(bands) = o.get("bands").and_then(Value::as_array).filter(|b| b.len() == BAND_N) {
            let vals: Vec<Option<f64>> = bands.iter().map(|b| db(Some(b))).collect();
            if vals.iter().all(Option::is_some) {
                clean.insert("bands".into(), json!(vals.into_iter().flatten().collect::<Vec<_>>()));
            }
        }
        for k in ["warn_db", "limit_db"] {
            if let Some(v) = db(o.get(k)) {
                clean.insert(k.into(), json!(v));
            }
        }
        if clean.is_empty() {
            return;
        }
        if let Some((_, e)) = self.sources.lock().unwrap_or_else(|e| e.into_inner()).iter_mut().find(|(k, _)| k == id) {
            e.reading = Some(clean);
            e.at = Some(Instant::now());
        }
    }

    fn external_public(&self) -> Map<String, Value> {
        let mut out = Map::new();
        for (k, v) in self.sources.lock().unwrap_or_else(|e| e.into_inner()).iter() {
            let fresh = v.reading.is_some() && v.at.is_some_and(|t| t.elapsed() <= EXTERNAL_STALE);
            let mut o = Map::new();
            o.insert("label".into(), json!(v.label));
            o.insert("state".into(), json!(if fresh { "ok" } else { "no_input" }));
            o.insert("calibrated".into(), json!(true));
            o.insert("kind".into(), json!("room"));
            if let Some(r) = &v.reading {
                o.extend(r.clone());
            }
            out.insert(k.clone(), Value::Object(o));
        }
        out
    }

    // -- the routes' own logic --------------------------------------------------------------------------------------
    /// Save the list of meters and apply it. A meter whose calibration was set by hand (against a reference meter) counts as
    /// calibrated.
    pub async fn save(&self, payload: &Value) -> Value {
        let mut payload = payload.clone();
        let cur: HashMap<String, Value> = self.store.metering()["meters"].as_array().into_iter().flatten().map(|m| (m["id"].as_str().unwrap_or("").to_owned(), m.clone())).collect();
        if let Some(meters) = payload.get_mut("meters").and_then(Value::as_array_mut) {
            for m in meters.iter_mut().filter(|m| m.is_object()) {
                let Some(old) = m.get("id").and_then(Value::as_str).and_then(|id| cur.get(id)) else { continue };
                let changed = m.get("calibration_db").is_some_and(|c| !c.is_null() && c != &old["calibration_db"]);
                if m.get("calibrated").is_none() && changed {
                    m["calibrated"] = json!(true);
                } else if m.get("calibrated").is_none() {
                    m["calibrated"] = old["calibrated"].clone();
                }
            }
        }
        let cfg = self.store.save_metering(&normalize(&payload));
        self.apply(&cfg).await;
        json!({"config": cfg, "status": self.snapshots()})
    }

    /// After a calibration, the new offset is saved on that meter.
    pub fn save_calibration(&self, mid: &str, cal: f64) -> Value {
        let cur = self.store.metering();
        let meters: Vec<Value> = cur["meters"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|m| {
                let mut m = m.clone();
                if m["id"] == mid {
                    m["calibration_db"] = json!(cal);
                    m["calibrated"] = json!(true);
                }
                m
            })
            .collect();
        let cfg = self.store.save_metering(&json!({"meters": meters}));
        json!({"config": cfg, "status": self.snapshots()})
    }
}
