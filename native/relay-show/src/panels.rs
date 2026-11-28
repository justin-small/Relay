//! Caption panels: who is out there, and what each one should show (`app/panels.py`).
//!
//! A panel (firmware/relay-display) checks in over plain HTTP on the viewer port every 30 s and keeps `/api/panels/<id>/events`
//! open. Nothing here is authenticated (panels do not pair, by decision), so what a stray LAN client can do is bounded: the
//! registry holds at most [`MAX_PANELS`], a check-in body is at most [`CHECKIN_MAX_BYTES`], and nothing a panel sends is ever
//! written to config.json. Only what the operator sets from the admin side (a name, a feed, a rotation, a lock) is stored.

use std::collections::HashMap;
use std::net::IpAddr;
use std::sync::{Arc, Mutex, MutexGuard};

use bytes::Bytes;
use relay_core::languages;
use serde_json::{json, Map, Value};
use tokio::sync::mpsc;

use crate::alerts::{compact, token_hex};
use crate::firmware::{self, Info, Kind};
use crate::text::{bounded, strict_int};
use crate::Clock;

pub const OFFLINE_AFTER_S: f64 = 90.0;
pub const CHECKIN_INTERVAL_S: i64 = 30;
pub const CHECKIN_MAX_BYTES: usize = 2048;
pub const MAX_PANELS: usize = 64;
pub const MAX_EVENT_STREAMS_PER_PANEL: usize = 2;
pub const EVENT_QUEUE_MAX: usize = 16;
pub const UPDATE_REPORT_MAX_BYTES: usize = 512;
pub const UPDATE_STATES: [&str; 6] = ["downloading", "ready", "restarting", "failed", "installed", "rolled_back"];
pub const SERVICE_MODES: [&str; 4] = ["off", "show", "control", "both"];
pub const CONTROL_MODES: [&str; 2] = ["control", "both"];
pub const TRANSCRIPTION: &str = "transcription";
pub const TRANSLATION: &str = "translation";
pub const SERVICE: &str = "service";
pub const ALERT: &str = "alert";
pub const LYRICS: &str = "lyrics";
pub const TIMERS: &str = "timers";
pub const STATUS: &str = "status";
pub const MAX_FEEDS: usize = 2;
pub const ROTATIONS: [i64; 4] = [0, 90, 180, 270];
pub const POLICY_MIN_RANGE: (i64, i64) = (1, 60);
pub const DEFAULT_UNLOCK_MIN: i64 = 5;
pub use relay_core::timers::{timer_fields, timer_id_ok, MAX_THRESHOLD_S, MAX_TIMERS_SHOWN};
const I31: i64 = (1 << 31) - 1;

pub fn update_error(key: &str) -> &'static str {
    match key {
        "download" => "the image did not download completely",
        "signature" => "the image is not signed with the key this panel trusts",
        "version" => "the image is not newer than the one the panel runs",
        "compatible" => "the image is for other hardware",
        "install" => "the panel could not write the image",
        "rollback" => "the new image did not check in, so the panel went back to the old one",
        _ => "",
    }
}

fn all_digits(s: &str, max: usize) -> bool {
    (1..=max).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit())
}

fn is_hex32(s: &str) -> bool {
    s.len() == 32 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn valid_id(v: &Value) -> bool {
    v.as_str().is_some_and(is_hex32)
}

fn board_ok(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 32 && (b[0].is_ascii_lowercase() || b[0].is_ascii_digit()) && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, b'.' | b'-'))
}

fn script_ok(s: &str) -> bool {
    (2..=16).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_lowercase())
}

/// One or two feed choices, or `None` if `raw` is not a valid list of them.
pub fn clean_feeds(raw: &Value) -> Option<Vec<Value>> {
    let arr = raw.as_array().filter(|a| (1..=MAX_FEEDS).contains(&a.len()))?;
    let mut out = Vec::new();
    for item in arr {
        let item = item.as_object()?;
        match item.get("stream").and_then(Value::as_str) {
            Some(TRANSCRIPTION) => out.push(json!({"stream": TRANSCRIPTION})),
            Some(TRANSLATION) => {
                let lang = py_str(item.get("lang")).to_uppercase();
                languages::target(&lang)?;
                out.push(json!({"stream": TRANSLATION, "lang": lang}));
            }
            Some(SERVICE) => {
                let st = py_str(item.get("service_type"));
                if !all_digits(&st, 19) {
                    return None;
                }
                out.push(json!({"stream": SERVICE, "service_type": st}));
            }
            Some(ALERT) => out.push(json!({"stream": ALERT})),
            Some(s @ (LYRICS | TIMERS | STATUS)) => out.push(json!({"stream": s})),
            _ => return None,
        }
    }
    Some(out)
}

/// `str(x or "")`.
fn py_str(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) | Some(Value::Bool(false)) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(other) => other.to_string(),
    }
}

/// What a panel says it shows. Anything odd is dropped rather than refused: a check-in is how the operator sees the panel at all.
fn reported_feeds(raw: Option<&Value>) -> Vec<Value> {
    let Some(arr) = raw.and_then(Value::as_array) else { return vec![] };
    let mut out = Vec::new();
    for item in arr.iter().take(MAX_FEEDS) {
        let Some(item) = item.as_object() else { continue };
        match item.get("stream").and_then(Value::as_str) {
            Some(TRANSCRIPTION) => out.push(json!({"stream": TRANSCRIPTION})),
            Some(TRANSLATION) => {
                let lang = bounded(item.get("lang").unwrap_or(&Value::Null), 24).to_uppercase();
                if !lang.is_empty() {
                    out.push(json!({"stream": TRANSLATION, "lang": lang}));
                }
            }
            Some(SERVICE) => {
                let st = bounded(item.get("service_type").unwrap_or(&Value::Null), 24);
                if all_digits(&st, 19) {
                    out.push(json!({"stream": SERVICE, "service_type": st}));
                }
            }
            Some(ALERT) => out.push(json!({"stream": ALERT})),
            Some(s @ (LYRICS | TIMERS | STATUS)) => out.push(json!({"stream": s})),
            _ => {}
        }
    }
    out
}

fn clamp_int(v: Option<&Value>, low: i64, high: i64, default: i64) -> i64 {
    match v {
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).map_or(default, |n| n.clamp(low, high)),
        _ => default,
    }
}

fn minutes(v: Option<&Value>) -> Option<i64> {
    let n = strict_int(v?)?;
    (POLICY_MIN_RANGE.0..=POLICY_MIN_RANGE.1).contains(&n).then_some(n)
}

fn pin_ok(s: &str) -> bool {
    (4..=8).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit())
}

fn clean_policy(spec: &Map<String, Value>) -> Map<String, Value> {
    let mut out = Map::new();
    if spec.get("lock") == Some(&Value::Bool(true)) {
        out.insert("lock".into(), json!(true));
    }
    if let Some(pin) = spec.get("pin").and_then(Value::as_str).filter(|p| pin_ok(p)) {
        out.insert("pin".into(), json!(pin));
    }
    if let Some(n) = minutes(spec.get("unlock_min")) {
        out.insert("unlock_min".into(), json!(n));
    }
    if let Some(n) = minutes(spec.get("return_min")) {
        out.insert("return_min".into(), json!(n));
    }
    out
}

/// The `panels` block of config.json: what the operator set, validated. A hand-edited entry that does not validate is dropped.
pub fn clean_stored(raw: &Value) -> Map<String, Value> {
    let mut out = Map::new();
    let Some(raw) = raw.as_object() else { return out };
    for (pid, spec) in raw {
        if out.len() >= MAX_PANELS {
            break;
        }
        let Some(spec) = spec.as_object().filter(|_| is_hex32(pid)) else { continue };
        let mut entry = Map::new();
        let name = bounded(spec.get("name").unwrap_or(&Value::Null), 64);
        if !name.is_empty() {
            entry.insert("name".into(), json!(name));
        }
        if let Some(feeds) = clean_feeds(spec.get("feeds").unwrap_or(&Value::Null)).filter(|f| !f.is_empty()) {
            entry.insert("feeds".into(), json!(feeds));
        }
        entry.insert("feed_rev".into(), json!(clamp_int(spec.get("feed_rev"), 0, I31, 0)));
        if let Some(r) = spec.get("rotation").and_then(Value::as_i64).filter(|r| ROTATIONS.contains(r)) {
            entry.insert("rotation".into(), json!(r));
        }
        entry.insert("rotation_rev".into(), json!(clamp_int(spec.get("rotation_rev"), 0, I31, 0)));
        entry.extend(clean_policy(spec));
        if let Some(t) = spec.get("timers").filter(|t| t.is_object()) {
            let mine = timer_fields(t).0;
            if !mine.is_empty() {
                entry.insert("timers".into(), Value::Object(mine));
            }
        }
        if let Some(mode) = spec.get("service_mode").and_then(Value::as_str).filter(|m| SERVICE_MODES.contains(m) && *m != "off") {
            entry.insert("service_mode".into(), json!(mode));
            let st = py_str(spec.get("service_type"));
            if all_digits(&st, 20) {
                entry.insert("service_type".into(), json!(st));
            }
            let key = py_str(spec.get("control_key"));
            if CONTROL_MODES.contains(&mode) && is_hex32(&key) {
                entry.insert("control_key".into(), json!(key));
            }
        }
        out.insert(pid.clone(), Value::Object(entry));
    }
    out
}

#[derive(Clone, Debug, Default)]
pub struct Panel {
    pub id: String,
    pub name: String,
    pub version: String,
    pub board: String,
    pub rotation: Option<i64>,
    pub rotation_rev: i64,
    pub rotation_changed_by: String,
    pub scripts: Vec<String>,
    pub feeds: Vec<Value>,
    pub ip: String,
    pub uptime_s: i64,
    pub rssi: Option<i64>,
    pub last_seen: f64,
    pub acked_rev: i64,
    pub feeds_changed_at: f64,
    pub feeds_changed_by: String,
    pub screen_choice: bool,
    pub idle_s: Option<i64>,
    pub update_request: Option<(String, bool)>,
    pub update: Option<Value>,
}

/// A refusal of an operator action.
#[derive(Debug, PartialEq, Eq)]
pub enum PanelError {
    /// No such panel (404).
    Unknown,
    /// A bad value, with the sentence for the admin page (400).
    Invalid(String),
    /// The panel cannot do it now (409).
    Conflict(String),
    /// Every slot holds a panel that is online, or one the operator named (429).
    Full,
}

/// What the registry reads and writes outside itself.
pub trait Cfg: Send + Sync + 'static {
    /// The `panels` block as stored.
    fn panels(&self) -> Value;
    fn save_panels(&self, stored: &Value);
    /// The whole configuration (`source_language`, `targets`).
    fn config(&self) -> Value;
}

type Sub = mpsc::Sender<Bytes>;
/// What a panel stream opens with: the alerts flashing on it.
pub type AlertsFor = Box<dyn Fn(&str) -> Vec<Value> + Send + Sync>;

#[derive(Default)]
struct State {
    panels: HashMap<String, Panel>,
    subs: HashMap<String, Vec<Sub>>,
    /// Slide-note tags: panels switched to the lyrics, with what they showed (`restore`) and what Relay sent (`pushed`).
    swapped: HashMap<String, (Vec<Value>, Vec<Value>)>,
    rev_floor: HashMap<String, i64>,
}

pub struct Registry {
    st: Mutex<State>,
    cfg: Arc<dyn Cfg>,
    fw: Arc<firmware::Store>,
    clock: Arc<dyn Clock>,
    on_change: Mutex<Option<Box<dyn Fn() + Send + Sync>>>,
    alerts_for: Mutex<AlertsFor>,
    lyrics_offered: Mutex<Box<dyn Fn() -> bool + Send + Sync>>,
    on_timers_change: Mutex<Box<dyn Fn() + Send + Sync>>,
}

fn sse(message: &Value) -> Bytes {
    Bytes::from(format!("data: {}\n\n", compact(message)))
}

fn r1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

impl Registry {
    pub fn new(cfg: Arc<dyn Cfg>, fw: Arc<firmware::Store>, clock: Arc<dyn Clock>) -> Arc<Self> {
        Arc::new(Self {
            st: Mutex::default(),
            cfg,
            fw,
            clock,
            on_change: Mutex::default(),
            alerts_for: Mutex::new(Box::new(|_| vec![])),
            lyrics_offered: Mutex::new(Box::new(|| false)),
            on_timers_change: Mutex::new(Box::new(|| {})),
        })
    }

    pub fn set_on_change(&self, f: Option<Box<dyn Fn() + Send + Sync>>) {
        *lock(&self.on_change) = f;
    }
    pub fn set_alerts_for(&self, f: AlertsFor) {
        *lock(&self.alerts_for) = f;
    }
    pub fn set_lyrics_offered(&self, f: Box<dyn Fn() -> bool + Send + Sync>) {
        *lock(&self.lyrics_offered) = f;
    }
    pub fn set_on_timers_change(&self, f: Box<dyn Fn() + Send + Sync>) {
        *lock(&self.on_timers_change) = f;
    }
    pub fn timers_changed(&self) {
        (lock(&self.on_timers_change))();
    }

    fn changed(&self) {
        if let Some(f) = lock(&self.on_change).as_ref() {
            f();
        }
    }

    fn st(&self) -> MutexGuard<'_, State> {
        lock(&self.st)
    }

    fn now(&self) -> f64 {
        self.clock.now()
    }

    pub fn stored(&self) -> Map<String, Value> {
        clean_stored(&self.cfg.panels())
    }

    fn save_stored(&self, stored: &Map<String, Value>) {
        self.cfg.save_panels(&Value::Object(stored.clone()));
    }

    fn online_at(&self, p: &Panel) -> bool {
        p.last_seen > 0.0 && self.now() - p.last_seen <= OFFLINE_AFTER_S
    }

    pub fn online(&self, pid: &str) -> bool {
        self.st().panels.get(pid).is_some_and(|p| self.online_at(p))
    }

    pub fn display_name(&self, pid: &str) -> String {
        let stored = self.stored();
        let named = stored.get(pid).and_then(|s| s["name"].as_str()).filter(|n| !n.is_empty()).map(str::to_owned);
        named
            .or_else(|| self.st().panels.get(pid).map(|p| p.name.clone()).filter(|n| !n.is_empty()))
            .unwrap_or_else(|| format!("Panel {}", &pid[..4.min(pid.len())]))
    }

    /// Languages the operator may give this panel: every target whose script it can draw (all, for a panel that has not said).
    pub fn offered_languages(&self, pid: &str) -> Vec<Value> {
        let scripts = self.st().panels.get(pid).map(|p| p.scripts.clone()).unwrap_or_default();
        let mut out: Vec<(String, Value)> = languages::TARGETS
            .iter()
            .filter(|t| scripts.is_empty() || scripts.iter().any(|s| s == t.script))
            .map(|t| (t.label.to_lowercase(), json!({"id": t.name, "label": t.label})))
            .collect();
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out.into_iter().map(|(_, v)| v).collect()
    }

    fn update_available_for(&self, p: &Panel) -> bool {
        self.firmware_for(p).is_some_and(|info| !p.version.is_empty() && firmware::newer(&info.version, &p.version))
    }

    fn firmware_for(&self, p: &Panel) -> Option<Info> {
        let board = firmware::panel_board(&p.board);
        if board.is_empty() {
            None
        } else {
            self.fw.current(board, Kind::App)
        }
    }

    fn update_view(&self, p: &Panel) -> Value {
        let Some(u) = &p.update else { return Value::Null };
        let err = u["error"].as_str().unwrap_or("");
        json!({"state": u["state"], "version": u["version"], "progress": u["progress"], "error": err, "error_text": update_error(err),
            "ago_s": r1((self.clock.epoch() - u["at"].as_f64().unwrap_or(0.0)).max(0.0))})
    }

    fn view_in(&self, st: &State, stored_all: &Map<String, Value>, pid: &str) -> Value {
        let empty = Value::Object(Map::new());
        let stored = stored_all.get(pid).unwrap_or(&empty);
        let default = Panel { id: pid.to_owned(), ..Panel::default() };
        let panel = st.panels.get(pid).unwrap_or(&default);
        let board = if panel.last_seen > 0.0 { if panel.board.is_empty() { firmware::DEFAULT_BOARD.to_owned() } else { panel.board.clone() } } else { String::new() };
        let name = stored["name"].as_str().filter(|n| !n.is_empty()).map(str::to_owned).or_else(|| Some(panel.name.clone()).filter(|n| !n.is_empty())).unwrap_or_else(|| format!("Panel {}", &pid[..4]));
        json!({
            "id": pid, "name": name, "reported_name": panel.name, "named": stored["name"].as_str().is_some_and(|n| !n.is_empty()),
            "online": self.online_at(panel),
            "seen_ago_s": if panel.last_seen > 0.0 { json!(r1(self.now() - panel.last_seen)) } else { Value::Null },
            "version": panel.version, "board": board, "board_label": if board.is_empty() { String::new() } else { firmware::board_label(&board) },
            "rotation": panel.rotation, "set_rotation": stored.get("rotation").cloned().unwrap_or(Value::Null),
            "rotation_changed_by": if panel.rotation_changed_by.is_empty() { Value::Null } else { json!(panel.rotation_changed_by) },
            "can_reboot": st.subs.get(pid).is_some_and(|s| !s.is_empty()),
            "scripts": panel.scripts, "feeds": panel.feeds, "set_feeds": stored.get("feeds").cloned().unwrap_or(Value::Null),
            "ip": panel.ip, "uptime_s": panel.uptime_s, "rssi": panel.rssi, "languages": self.offered_languages_in(st, pid),
            "feeds_changed_ago_s": if panel.feeds_changed_at > 0.0 { json!(r1(self.clock.epoch() - panel.feeds_changed_at)) } else { Value::Null },
            "feeds_changed_by": if panel.feeds_changed_by.is_empty() { Value::Null } else { json!(panel.feeds_changed_by) },
            "update_available": self.update_available_for(panel), "update_requested": panel.update_request.is_some(),
            "update": self.update_view(panel),
            "service_mode": stored["service_mode"].as_str().unwrap_or("off"),
            "service_type": stored.get("service_type").filter(|v| v.as_str().is_some_and(|s| !s.is_empty())).cloned().unwrap_or(Value::Null),
            "set_timers": stored.get("timers").filter(|t| t.as_object().is_some_and(|o| !o.is_empty())).cloned().unwrap_or(Value::Null),
            "lock": stored["lock"] == true, "pin_set": stored["pin"].as_str().is_some_and(|p| !p.is_empty()),
            "unlock_min": stored["unlock_min"].as_i64().filter(|n| *n != 0).unwrap_or(DEFAULT_UNLOCK_MIN),
            "return_min": stored.get("return_min").cloned().unwrap_or(Value::Null),
            "idle_s": panel.idle_s,
        })
    }

    fn offered_languages_in(&self, st: &State, pid: &str) -> Vec<Value> {
        let scripts = st.panels.get(pid).map(|p| p.scripts.clone()).unwrap_or_default();
        let mut out: Vec<(String, Value)> = languages::TARGETS
            .iter()
            .filter(|t| scripts.is_empty() || scripts.iter().any(|s| s == t.script))
            .map(|t| (t.label.to_lowercase(), json!({"id": t.name, "label": t.label})))
            .collect();
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out.into_iter().map(|(_, v)| v).collect()
    }

    pub fn view(&self, pid: &str) -> Value {
        let stored = self.stored();
        let st = self.st();
        self.view_in(&st, &stored, pid)
    }

    /// What the panel is told about its lock (#126), as a `panel_policy` event. The PIN goes over plain HTTP, as the Planning
    /// Center control key does: it stops someone at the screen, not someone reading the network.
    pub fn policy(&self, pid: &str) -> Value {
        policy_of(self.stored().get(pid))
    }

    pub fn timer_settings(&self, pid: &str) -> Option<Value> {
        self.stored().get(pid).and_then(|s| s.get("timers")).filter(|t| t.as_object().is_some_and(|o| !o.is_empty())).cloned()
    }

    /// What the panel is told about the Planning Center service: its mode, the type it follows, and its control key while it may control.
    pub fn service(&self, pid: &str) -> Value {
        service_of(self.stored().get(pid))
    }

    pub fn control_key(&self, pid: &str) -> Option<String> {
        let stored = self.stored();
        let s = stored.get(pid)?;
        if s["service_mode"].as_str().is_some_and(|m| CONTROL_MODES.contains(&m)) {
            return s["control_key"].as_str().filter(|k| !k.is_empty()).map(str::to_owned);
        }
        None
    }

    /// Which panels are online right now: what changes with the clock alone, so the admin stream can notice a panel going quiet.
    pub fn online_signature(&self) -> Vec<String> {
        let st = self.st();
        let mut ids: Vec<String> = st.panels.iter().filter(|(_, p)| self.online_at(p)).map(|(i, _)| i.clone()).collect();
        ids.sort();
        ids
    }

    pub fn listing(&self) -> Vec<Value> {
        let stored = self.stored();
        let st = self.st();
        let mut ids: Vec<String> = st.panels.keys().cloned().collect();
        for k in stored.keys() {
            if !ids.contains(k) {
                ids.push(k.clone());
            }
        }
        let mut rows: Vec<Value> = ids.iter().map(|p| self.view_in(&st, &stored, p)).collect();
        rows.sort_by(|a, b| {
            let key = |r: &Value| (!r["online"].as_bool().unwrap_or(false), r["name"].as_str().unwrap_or("").to_lowercase(), r["id"].as_str().unwrap_or("").to_owned());
            key(a).cmp(&key(b))
        });
        rows
    }

    pub fn known(&self, pid: &str) -> bool {
        self.st().panels.contains_key(pid) || self.stored().contains_key(pid)
    }

    pub fn has_checked_in(&self, pid: &str) -> bool {
        self.st().panels.contains_key(pid)
    }

    pub fn panel(&self, pid: &str) -> Option<Panel> {
        self.st().panels.get(pid).cloned()
    }

    // -------------------------------------------------------------- check-in
    fn make_room(&self, st: &mut State, stored: &Map<String, Value>) -> Result<(), PanelError> {
        if st.panels.len() < MAX_PANELS {
            return Ok(());
        }
        let victim = st
            .panels
            .values()
            .filter(|p| !self.online_at(p) && !stored.contains_key(&p.id) && st.subs.get(&p.id).is_none_or(Vec::is_empty))
            .min_by(|a, b| a.last_seen.total_cmp(&b.last_seen))
            .map(|p| p.id.clone());
        match victim {
            Some(v) => {
                st.panels.remove(&v);
                Ok(())
            }
            None => Err(PanelError::Full),
        }
    }

    /// Record a check-in. Returns what the panel should be told: its name, the interval, and the operator's feed if it has not
    /// applied it yet. `Invalid` is the body's own fault (`id` must be 32 hex characters).
    pub fn checkin(&self, body: &Value, peer_ip: &str) -> Result<Value, PanelError> {
        let pid = body.get("id").and_then(Value::as_str).filter(|p| is_hex32(p)).ok_or_else(|| PanelError::Invalid("id must be 32 lower-case hex characters".into()))?.to_owned();
        let stored = self.stored();
        let mut reply;
        {
            let mut st = self.st();
            if !st.panels.contains_key(&pid) {
                self.make_room(&mut st, &stored)?;
                st.panels.insert(pid.clone(), Panel { id: pid.clone(), ..Panel::default() });
            }
            let mut panel = st.panels.get(&pid).cloned().unwrap_or_default();
            let old_feeds = (panel.last_seen > 0.0).then(|| panel.feeds.clone());
            let old_acked = panel.acked_rev;
            let old_rotation = if panel.last_seen > 0.0 { panel.rotation } else { None };
            let old_rotation_rev = panel.rotation_rev;
            panel.name = bounded(body.get("name").unwrap_or(&Value::Null), 64);
            panel.version = bounded(body.get("version").unwrap_or(&Value::Null), 32);
            panel.board = body.get("board").and_then(Value::as_str).filter(|b| board_ok(b)).unwrap_or("").to_owned();
            panel.rotation = body.get("rotation").and_then(Value::as_i64).filter(|r| ROTATIONS.contains(r));
            panel.rotation_rev = clamp_int(body.get("rotation_rev"), 0, I31, 0);
            if let (Some(o), Some(n)) = (old_rotation, panel.rotation) {
                if o != n {
                    panel.rotation_changed_by = if panel.rotation_rev != old_rotation_rev { "admin" } else { "screen" }.into();
                }
            }
            panel.scripts = body.get("scripts").and_then(Value::as_array).map(|a| a.iter().take(8).filter_map(Value::as_str).filter(|s| script_ok(s)).map(str::to_owned).collect()).unwrap_or_default();
            panel.feeds = reported_feeds(body.get("feeds"));
            panel.ip = reported_ip(body.get("ip")).unwrap_or_else(|| peer_ip.to_owned());
            panel.uptime_s = clamp_int(body.get("uptime_s"), 0, I31, 0);
            panel.rssi = match body.get("rssi") {
                Some(Value::Number(n)) => n.as_f64().map(|f| (f as i64).clamp(-127, 0)),
                _ => None,
            };
            panel.acked_rev = panel.acked_rev.max(clamp_int(body.get("feed_rev"), 0, I31, 0));
            if let Some(old) = old_feeds.filter(|o| *o != panel.feeds) {
                let _ = old;
                panel.feeds_changed_at = self.clock.epoch();
                panel.feeds_changed_by = if panel.acked_rev > old_acked { "admin" } else { "screen" }.into();
                panel.screen_choice = panel.feeds_changed_by == "screen";
            } else if panel.acked_rev > old_acked {
                panel.screen_choice = false;
            }
            panel.idle_s = body.get("idle_s").and_then(strict_int).map(|n| n.clamp(0, I31));
            panel.last_seen = self.now();
            // A check-in from the version that was asked for is the update's last word.
            if let Some((v, _)) = panel.update_request.clone() {
                if panel.version == v {
                    panel.update_request = None;
                    panel.update = Some(json!({"state": "installed", "version": panel.version, "progress": 100, "error": "", "at": self.clock.epoch()}));
                }
            }
            reply = json!({"ok": true, "name": self.display_name_in(&st, &stored, &pid, &panel), "interval_s": CHECKIN_INTERVAL_S});
            let pending = self.pending_feed(&stored, &panel).or_else(|| self.return_due(&mut st, &stored, &mut panel));
            if let Some(p) = pending {
                reply["set_feed"] = p;
            }
            if let Some(offer) = self.update_offer(&panel) {
                reply["update"] = offer;
            }
            if let Some(r) = pending_rotation(&stored, &panel) {
                reply["set_rotation"] = r;
            }
            reply["service"] = service_of(stored.get(&pid));
            reply["policy"] = policy_of(stored.get(&pid));
            st.panels.insert(pid.clone(), panel);
        }
        self.changed();
        Ok(reply)
    }

    fn display_name_in(&self, _st: &State, stored: &Map<String, Value>, pid: &str, panel: &Panel) -> String {
        stored.get(pid).and_then(|s| s["name"].as_str()).filter(|n| !n.is_empty()).map(str::to_owned)
            .or_else(|| Some(panel.name.clone()).filter(|n| !n.is_empty()))
            .unwrap_or_else(|| format!("Panel {}", &pid[..4]))
    }

    fn pending_feed(&self, stored: &Map<String, Value>, panel: &Panel) -> Option<Value> {
        let s = stored.get(&panel.id)?;
        let feeds = s.get("feeds").filter(|f| f.as_array().is_some_and(|a| !a.is_empty()))?;
        let rev = s["feed_rev"].as_i64().unwrap_or(0);
        if panel.acked_rev >= rev {
            return None;
        }
        Some(json!({"type": "set_feed", "rev": rev, "feeds": feeds}))
    }

    /// The operator's feed as a `set_feed`, when this panel is to go back to it now (#126): it has a return time, a change made at
    /// its screen is what differs, and its screen has gone that long with no touch. A panel on the lyrics because of a tag is the
    /// operator's doing, and is left alone.
    fn return_due(&self, st: &mut State, stored: &Map<String, Value>, panel: &mut Panel) -> Option<Value> {
        let s = stored.get(&panel.id)?;
        let minutes = s["return_min"].as_i64().filter(|m| *m != 0)?;
        let feeds = s.get("feeds").and_then(Value::as_array).filter(|f| !f.is_empty())?;
        let idle = panel.idle_s?;
        if !panel.screen_choice || idle < minutes * 60 || panel.feeds == *feeds || st.swapped.contains_key(&panel.id) {
            return None;
        }
        let rev = self.rev_base(st, stored, &panel.id, Some(panel)) + 1;
        st.rev_floor.insert(panel.id.clone(), rev);
        panel.screen_choice = false;
        let message = json!({"type": "set_feed", "rev": rev, "feeds": feeds});
        push_in(st, &panel.id, &message);
        Some(message)
    }

    fn rev_base(&self, st: &State, stored: &Map<String, Value>, pid: &str, panel: Option<&Panel>) -> i64 {
        let acked = panel.map(|p| p.acked_rev).or_else(|| st.panels.get(pid).map(|p| p.acked_rev)).unwrap_or(0);
        stored.get(pid).and_then(|s| s["feed_rev"].as_i64()).unwrap_or(0).max(acked).max(st.rev_floor.get(pid).copied().unwrap_or(0))
    }

    // ------------------------------------------------------- firmware updates
    fn update_offer(&self, panel: &Panel) -> Option<Value> {
        let (version, now) = panel.update_request.clone()?;
        let info = self.firmware_for(panel)?;
        if version != info.version {
            return None;
        }
        Some(update_message(&info, now))
    }

    /// Ask one panel to install the stored image: at a quiet moment, or at once with `now`. Pushed on its event stream if one is
    /// open, and carried in its next check-in reply either way.
    pub fn request_update(&self, pid: &str, now: bool) -> Result<Value, PanelError> {
        let stored = self.stored();
        {
            let mut st = self.st();
            let panel = st.panels.get(pid).cloned().filter(|_| is_hex32(pid)).ok_or(PanelError::Unknown)?;
            let Some(info) = self.firmware_for(&panel) else {
                let board = firmware::panel_board(&panel.board);
                return Err(PanelError::Conflict(if board.is_empty() {
                    format!("Relay keeps no firmware for this panel's board ({})", panel.board)
                } else {
                    format!("no firmware has been uploaded for the {}", firmware::board_label(board))
                }));
            };
            if !self.update_available_for(&panel) {
                let running = if panel.version.is_empty() { "a version Relay cannot read".to_owned() } else { panel.version.clone() };
                return Err(PanelError::Invalid(format!("{} already runs {running} or newer; the image held is {}", self.display_name_in(&st, &stored, pid, &panel), info.version)));
            }
            if let Some(p) = st.panels.get_mut(pid) {
                p.update_request = Some((info.version.clone(), now));
                p.update = Some(json!({"state": "queued", "version": info.version, "progress": 0, "error": "", "at": self.clock.epoch()}));
            }
            let mut m = update_message(&info, now);
            m["type"] = json!("update_available");
            push_in(&mut st, pid, &m);
        }
        self.changed();
        Ok(self.view(pid))
    }

    /// Ask every panel that is online and has an update available (of `board`, or of any board). Offline panels are left for the
    /// operator to ask one by one: an update should not be a surprise on a panel that comes back days later.
    pub fn request_update_all(&self, now: bool, board: Option<&str>) -> Value {
        let ids: Vec<(String, bool, bool)> = {
            let st = self.st();
            st.panels
                .values()
                .filter(|p| board.is_none_or(|b| firmware::panel_board(&p.board) == b))
                .filter(|p| self.update_available_for(p))
                .map(|p| (p.id.clone(), self.online_at(p), true))
                .collect()
        };
        let (mut asked, mut skipped) = (0, 0);
        for (id, online, _) in ids {
            if !online {
                skipped += 1;
            } else if self.request_update(&id, now).is_ok() {
                asked += 1;
            }
        }
        json!({"requested": asked, "offline": skipped})
    }

    /// A board's stored image (or any, with no `board`) was replaced or removed: no panel of it is still asked for the old one.
    pub fn firmware_changed(&self, board: Option<&str>) {
        {
            let mut st = self.st();
            for p in st.panels.values_mut() {
                if board.is_some_and(|b| firmware::panel_board(&p.board) != b) {
                    continue;
                }
                if p.update_request.take().is_some() && p.update.as_ref().and_then(|u| u["state"].as_str()).is_some_and(|s| ["queued", "downloading", "ready"].contains(&s)) {
                    p.update = None;
                }
            }
        }
        self.changed();
    }

    /// A panel's word on an update. `false` for a panel Relay does not know. Everything is bounded and anything unfamiliar is dropped.
    pub fn report_update(&self, pid: &str, body: &Value) -> bool {
        {
            let mut st = self.st();
            let Some(panel) = st.panels.get_mut(pid).filter(|_| is_hex32(pid)) else { return false };
            let Some(state) = body.get("state").and_then(Value::as_str).filter(|s| UPDATE_STATES.contains(s)) else { return true };
            let mut error = body.get("error").and_then(Value::as_str).filter(|e| !update_error(e).is_empty()).unwrap_or("").to_owned();
            if state == "rolled_back" && error.is_empty() {
                error = "rollback".into();
            }
            let mut version = bounded(body.get("version").unwrap_or(&Value::Null), 32);
            if firmware::parse_version(&version).is_none() {
                version = panel.update_request.as_ref().map(|r| r.0.clone()).unwrap_or_default();
            }
            let progress = if ["ready", "restarting", "installed"].contains(&state) { 100 } else { clamp_int(body.get("progress"), 0, 100, 0) };
            panel.update = Some(json!({"state": state, "version": version, "progress": progress,
                "error": if state == "failed" || state == "rolled_back" { error } else { String::new() }, "at": self.clock.epoch()}));
            if ["failed", "rolled_back", "installed"].contains(&state) {
                panel.update_request = None;
            }
        }
        self.changed();
        true
    }

    // ---------------------------------------------------------------- events
    /// A stream of finished SSE frames for one panel, primed with what it has not yet applied.
    pub fn subscribe(&self, pid: &str) -> mpsc::Receiver<Bytes> {
        let (tx, rx) = mpsc::channel(EVENT_QUEUE_MAX);
        let stored = self.stored();
        let alerts: Vec<Value> = (lock(&self.alerts_for))(pid);
        let mut st = self.st();
        let subs = st.subs.entry(pid.to_owned()).or_default();
        while subs.len() >= MAX_EVENT_STREAMS_PER_PANEL {
            subs.remove(0);
        }
        subs.push(tx.clone());
        let panel = st.panels.get(pid).cloned();
        let send = |v: &Value| {
            let _ = tx.try_send(sse(v));
        };
        if let Some(p) = &panel {
            if let Some(m) = self.pending_feed(&stored, p) {
                send(&m);
            }
            if let Some(offer) = self.update_offer(p) {
                let mut m = offer;
                m["type"] = json!("update_available");
                send(&m);
            }
        } else if let Some(s) = stored.get(pid) {
            let probe = Panel { id: pid.to_owned(), ..Panel::default() };
            if let Some(m) = self.pending_feed(&Map::from_iter([(pid.to_owned(), s.clone())]), &probe) {
                send(&m);
            }
        }
        let service = service_of(stored.get(pid));
        if service["mode"] != "off" {
            let mut m = service;
            m["type"] = json!("service_mode");
            send(&m);
        }
        if let Some(r) = pending_rotation(&stored, &panel.clone().unwrap_or(Panel { id: pid.to_owned(), ..Panel::default() })) {
            send(&r);
        }
        let policy = policy_of(stored.get(pid));
        if policy["lock"] == true {
            send(&policy);
        }
        for a in alerts.iter().take(8) {
            send(a);
        }
        rx
    }

    /// A panel's stream ended: forget readers that are gone.
    pub fn unsubscribe(&self, pid: &str) {
        let mut st = self.st();
        if let Some(subs) = st.subs.get_mut(pid) {
            subs.retain(|s| !s.is_closed());
            if subs.is_empty() {
                st.subs.remove(pid);
            }
        }
    }

    /// Push a message onto a panel's streams; how many took it.
    pub fn push(&self, pid: &str, message: &Value) -> usize {
        push_in(&mut self.st(), pid, message)
    }

    pub fn stream_count(&self) -> usize {
        self.st().subs.values().map(Vec::len).sum()
    }

    // ------------------------------------------------- feeds set by slide-note tags
    fn send_feeds(&self, st: &mut State, stored: &Map<String, Value>, pid: &str, feeds: &[Value]) -> usize {
        let rev = self.rev_base(st, stored, pid, None) + 1;
        st.rev_floor.insert(pid.to_owned(), rev);
        let sent = push_in(st, pid, &json!({"type": "set_feed", "rev": rev, "feeds": feeds}));
        if sent > 0 {
            if let Some(p) = st.panels.get_mut(pid) {
                p.feeds = feeds.to_vec();
                p.screen_choice = false;
            }
        }
        sent
    }

    fn reachable(&self, st: &State) -> Vec<Panel> {
        st.panels.values().filter(|p| self.online_at(p) && st.subs.get(&p.id).is_some_and(|s| !s.is_empty())).cloned().collect()
    }

    fn with_caption(feeds: &[Value], feed: &Value) -> Vec<Value> {
        let mut out: Vec<Value> = Vec::new();
        for f in feeds {
            let f = if matches!(f["stream"].as_str(), Some(TRANSCRIPTION | TRANSLATION)) { feed.clone() } else { f.clone() };
            if !out.contains(&f) {
                out.push(f);
            }
        }
        out
    }

    fn plural(n: usize) -> &'static str {
        if n == 1 { "" } else { "s" }
    }

    /// Every panel showing captions shows the ProPresenter lyrics instead, until `show_captions`.
    pub fn show_lyrics(&self) -> Result<String, String> {
        if !(lock(&self.lyrics_offered))() {
            return Err("Lyrics on caption panels are switched off (ProPresenter page, Lyrics), so no panel was changed.".into());
        }
        let stored = self.stored();
        let lyrics = json!({"stream": LYRICS});
        let mut done = 0;
        {
            let mut st = self.st();
            for panel in self.reachable(&st) {
                if let Some(held) = st.swapped.get(&panel.id) {
                    if panel.feeds == held.1 {
                        continue;
                    }
                }
                st.swapped.remove(&panel.id);
                let new = Self::with_caption(&panel.feeds, &lyrics);
                if new == panel.feeds || clean_feeds(&Value::Array(new.clone())).is_none() {
                    continue;
                }
                let before = panel.feeds.clone();
                if self.send_feeds(&mut st, &stored, &panel.id, &new) > 0 {
                    st.swapped.insert(panel.id.clone(), (before, new));
                    done += 1;
                }
            }
        }
        self.changed();
        Ok(format!("Lyrics shown on {done} panel{}.", Self::plural(done)))
    }

    /// Put back what each panel showed before `show_lyrics`. A panel someone has changed at its screen since is left as it is.
    pub fn show_captions(&self) -> String {
        let stored = self.stored();
        let mut done = 0;
        {
            let mut st = self.st();
            let ids: Vec<String> = st.swapped.keys().cloned().collect();
            for pid in ids {
                let Some((restore, pushed)) = st.swapped.get(&pid).cloned() else { continue };
                let gone = match st.panels.get(&pid) {
                    None => true,
                    Some(p) => !p.feeds.is_empty() && p.feeds != pushed,
                };
                if gone {
                    st.swapped.remove(&pid);
                    continue;
                }
                if self.send_feeds(&mut st, &stored, &pid, &restore) > 0 {
                    st.swapped.remove(&pid);
                    done += 1;
                }
            }
        }
        self.changed();
        if done > 0 { format!("Captions back on {done} panel{}.", Self::plural(done)) } else { "No panel was showing lyrics from a tag.".into() }
    }

    /// Panels showing captions show them in `target` (a name from languages). A panel on the lyrics because of a tag goes back to
    /// this language when `show_captions` puts it back.
    pub fn show_language(&self, target: &str) -> Result<String, String> {
        let Some(spec) = languages::target(target) else { return Err("That is not a language Relay translates into.".into()) };
        let cfg = self.cfg.config();
        let feed = if target == cfg["source_language"].as_str().unwrap_or("").to_uppercase() {
            json!({"stream": TRANSCRIPTION})
        } else if cfg["targets"][target]["enabled"].as_bool().unwrap_or(false) || cfg["targets"][target]["enabled"].as_i64().is_some_and(|n| n != 0) {
            json!({"stream": TRANSLATION, "lang": target})
        } else {
            return Err(format!("{} is not switched on under Languages, so no panel was changed.", spec.label));
        };
        let stored = self.stored();
        let (mut done, mut skipped) = (0, 0);
        {
            let mut st = self.st();
            for panel in self.reachable(&st) {
                if feed["stream"] == TRANSLATION && !panel.scripts.is_empty() && !panel.scripts.iter().any(|s| s == spec.script) {
                    skipped += 1;
                    continue;
                }
                if let Some(held) = st.swapped.get_mut(&panel.id) {
                    if panel.feeds == held.1 {
                        held.0 = Self::with_caption(&held.0, &feed);
                        done += 1;
                        continue;
                    }
                }
                st.swapped.remove(&panel.id);
                let new = Self::with_caption(&panel.feeds, &feed);
                if new != panel.feeds && self.send_feeds(&mut st, &stored, &panel.id, &new) > 0 {
                    done += 1;
                }
            }
        }
        self.changed();
        let out = format!("{} set on {done} panel{}.", spec.label, Self::plural(done));
        Ok(if skipped > 0 { format!("{out} {skipped} cannot draw it.") } else { out })
    }

    // ------------------------------------------------------- operator actions
    /// Rename a panel, set its feed, its rotation, its service mode, its timers or its lock (see [`Update`]). Pushes `set_feed`,
    /// `set_rotation`, `service_mode` and `panel_policy` at once.
    pub fn update(&self, pid: &str, up: &Update) -> Result<Value, PanelError> {
        if !is_hex32(pid) || !self.known(pid) {
            return Err(PanelError::Unknown);
        }
        let bad = |s: &str| PanelError::Invalid(s.to_owned());
        let mut stored = self.stored();
        if stored.len() >= MAX_PANELS && !stored.contains_key(pid) {
            return Err(bad("too many named panels; forget one first"));
        }
        let mut entry: Map<String, Value> = stored.get(pid).and_then(|v| v.as_object().cloned()).unwrap_or_else(|| Map::from_iter([("feed_rev".to_owned(), json!(0))]));
        if let Some(name) = &up.name {
            let name = bounded(&json!(name), 64);
            if name.is_empty() {
                entry.remove("name");
            } else {
                entry.insert("name".into(), json!(name));
            }
        }
        let mut push = None;
        let mut st_guard = self.st();
        if let Some(feeds) = &up.feeds {
            if feeds.is_null() {
                entry.remove("feeds");
            } else {
                let clean = clean_feeds(feeds).ok_or_else(|| bad("feeds must be one or two of transcription, a translation language, the service, the alert buttons, the lyrics, the timers or the status"))?;
                if let Some(panel) = st_guard.panels.get(pid).filter(|p| !p.scripts.is_empty()) {
                    for feed in &clean {
                        if feed["stream"] == TRANSLATION {
                            let script = languages::target(feed["lang"].as_str().unwrap_or("")).map_or("", |l| l.script);
                            if !panel.scripts.iter().any(|s| s == script) {
                                return Err(PanelError::Invalid(format!("this panel cannot draw {script} text")));
                            }
                        }
                    }
                }
                let rev = self.rev_base(&st_guard, &stored, pid, None).max(entry.get("feed_rev").and_then(Value::as_i64).unwrap_or(0)) + 1;
                entry.insert("feeds".into(), json!(clean));
                entry.insert("feed_rev".into(), json!(rev));
                st_guard.swapped.remove(pid);
                push = Some(json!({"type": "set_feed", "rev": rev, "feeds": clean}));
            }
        }
        let mut service_push = false;
        if let Some(mode) = &up.service_mode {
            if !SERVICE_MODES.contains(&mode.as_str()) {
                return Err(bad("service mode must be off, show, control or both"));
            }
            if mode == "off" {
                entry.remove("service_mode");
                entry.remove("control_key");
            } else {
                entry.insert("service_mode".into(), json!(mode));
                if CONTROL_MODES.contains(&mode.as_str()) {
                    if !entry.contains_key("control_key") {
                        entry.insert("control_key".into(), json!(token_hex(16)));
                    }
                } else {
                    entry.remove("control_key");
                }
            }
            service_push = true;
        }
        if let Some(t) = &up.service_type {
            let st = if t.is_null() { String::new() } else { py_str(Some(t)).trim().to_owned() };
            if !st.is_empty() && !all_digits(&st, 20) {
                return Err(bad("service type must be a Planning Center service type id"));
            }
            if st.is_empty() {
                entry.remove("service_type");
            } else {
                entry.insert("service_type".into(), json!(st));
            }
            service_push = true;
        }
        if entry.get("service_mode").is_none_or(|m| m.as_str().is_none_or(str::is_empty)) {
            entry.remove("service_type");
        }
        for feed in entry.get("feeds").and_then(Value::as_array).cloned().unwrap_or_default() {
            if feed["stream"] != SERVICE || (push.is_none() && !service_push) {
                continue;
            }
            if !matches!(entry.get("service_mode").and_then(Value::as_str), Some("show" | "both")) {
                return Err(bad("a Service feed needs the panel's service mode to be Show only or Show and control"));
            }
            if let Some(st) = entry.get("service_type").and_then(Value::as_str).filter(|s| !s.is_empty()) {
                if feed["service_type"] != st {
                    return Err(bad("a Service feed must be the service type this panel is set to"));
                }
            }
        }
        if let Some(t) = &up.timers {
            if t.is_null() {
                entry.remove("timers");
            } else if t.is_object() {
                let (mine, problem) = timer_fields(t);
                if !problem.is_empty() {
                    return Err(PanelError::Invalid(problem));
                }
                if mine.is_empty() {
                    entry.remove("timers");
                } else {
                    entry.insert("timers".into(), Value::Object(mine));
                }
            } else {
                return Err(bad("timers must be an object, or null to follow the global settings"));
            }
        }
        let policy_before = policy_key(&entry);
        if let Some(lock_) = &up.lock {
            match lock_ {
                Value::Bool(true) => {
                    entry.insert("lock".into(), json!(true));
                }
                Value::Bool(false) => {
                    entry.remove("lock");
                }
                _ => return Err(bad("lock must be true or false")),
            }
        }
        if let Some(pin) = &up.pin {
            match pin {
                Value::Null => {
                    entry.remove("pin");
                }
                Value::String(s) if s.is_empty() => {
                    entry.remove("pin");
                }
                Value::String(s) if pin_ok(s) => {
                    entry.insert("pin".into(), json!(s));
                }
                _ => return Err(bad("the PIN must be 4 to 8 digits")),
            }
        }
        for (key, value) in [("unlock_min", &up.unlock_min), ("return_min", &up.return_min)] {
            let Some(value) = value else { continue };
            if value.is_null() && key == "return_min" {
                entry.remove(key);
            } else if let Some(n) = minutes(Some(value)) {
                entry.insert(key.into(), json!(n));
            } else {
                return Err(bad("minutes must be a whole number from 1 to 60"));
            }
        }
        let policy_push = policy_key(&entry) != policy_before;
        let mut rotation_push = None;
        if let Some(r) = &up.rotation {
            if r.is_null() {
                entry.remove("rotation");
            } else if let Some(deg) = r.as_i64().filter(|d| ROTATIONS.contains(d) && !r.is_boolean()) {
                let rev = entry.get("rotation_rev").and_then(Value::as_i64).unwrap_or(0) + 1;
                entry.insert("rotation".into(), json!(deg));
                entry.insert("rotation_rev".into(), json!(rev));
                rotation_push = Some(json!({"type": "set_rotation", "rev": rev, "deg": deg}));
            } else {
                return Err(bad("rotation must be 0, 90, 180 or 270"));
            }
        }
        let empty = entry.get("name").is_none_or(|n| n.as_str().is_none_or(str::is_empty))
            && entry.get("feeds").is_none_or(|f| f.as_array().is_none_or(Vec::is_empty))
            && entry.get("service_mode").is_none_or(|m| m.as_str().is_none_or(str::is_empty))
            && entry.get("rotation").is_none_or(Value::is_null)
            && entry.get("timers").is_none_or(|t| t.as_object().is_none_or(Map::is_empty))
            && clean_policy(&entry).is_empty();
        if empty {
            stored.remove(pid);
        } else {
            stored.insert(pid.to_owned(), Value::Object(entry));
        }
        self.save_stored(&stored);
        let stored_now = self.stored();
        if let Some(p) = push {
            push_in(&mut st_guard, pid, &p);
        }
        if service_push {
            let mut m = service_of(stored_now.get(pid));
            m["type"] = json!("service_mode");
            push_in(&mut st_guard, pid, &m);
        }
        if let Some(r) = rotation_push {
            push_in(&mut st_guard, pid, &r);
        }
        if policy_push {
            push_in(&mut st_guard, pid, &policy_of(stored_now.get(pid)));
        }
        let view = self.view_in(&st_guard, &stored_now, pid);
        drop(st_guard);
        self.changed();
        Ok(view)
    }

    /// Restart a panel. Sent on its event stream only, never in a check-in reply, so a panel cannot be left restarting at every check-in.
    pub fn reboot(&self, pid: &str) -> Result<Value, PanelError> {
        if !is_hex32(pid) || !self.known(pid) {
            return Err(PanelError::Unknown);
        }
        if self.push(pid, &json!({"type": "reboot"})) == 0 {
            return Err(PanelError::Conflict(format!("{} is not connected; it can only be restarted while online", self.display_name(pid))));
        }
        Ok(self.view(pid))
    }

    /// Drop a panel from the list and from config.json. A panel that is still checking in comes straight back, unnamed.
    pub fn forget(&self, pid: &str) -> bool {
        if !is_hex32(pid) || !self.known(pid) {
            return false;
        }
        let mut stored = self.stored();
        stored.remove(pid);
        self.save_stored(&stored);
        {
            let mut st = self.st();
            st.panels.remove(pid);
            st.swapped.remove(pid);
            st.subs.remove(pid);
        }
        self.changed();
        true
    }

    /// An `alert` or `alert_end` onto a panel's event stream. Returns how many streams took it.
    pub fn push_alert(&self, pid: &str, message: &Value) -> usize {
        self.push(pid, message)
    }

    /// Tell one panel, or every panel with a stream open, that a firmware update is waiting. Only plain scalars are passed on.
    pub fn push_update_available(&self, message: &Value, pid: Option<&str>) -> usize {
        let mut body = json!({"type": "update_available"});
        for (k, v) in message.as_object().into_iter().flatten() {
            if k != "type" && (v.is_string() || v.is_number() || v.is_boolean()) {
                body[k] = v.clone();
            }
        }
        let mut st = self.st();
        let targets: Vec<String> = match pid {
            Some(p) => vec![p.to_owned()],
            None => st.subs.keys().cloned().collect(),
        };
        targets.iter().map(|t| push_in(&mut st, t, &body)).sum()
    }
}

/// What an operator change sets. A field left `None` changes nothing; `Some(Value::Null)` clears it where Python's `None` does.
#[derive(Default, Debug)]
pub struct Update {
    pub name: Option<String>,
    pub feeds: Option<Value>,
    pub service_mode: Option<String>,
    pub service_type: Option<Value>,
    pub rotation: Option<Value>,
    pub timers: Option<Value>,
    pub lock: Option<Value>,
    pub pin: Option<Value>,
    pub unlock_min: Option<Value>,
    pub return_min: Option<Value>,
}

fn push_in(st: &mut State, pid: &str, message: &Value) -> usize {
    let frame = sse(message);
    let mut sent = 0;
    if let Some(subs) = st.subs.get_mut(pid) {
        // A reader that stopped reading is cut: it reconnects and is told the current state.
        subs.retain(|q| match q.try_send(frame.clone()) {
            Ok(()) => {
                sent += 1;
                true
            }
            Err(_) => false,
        });
    }
    sent
}

fn policy_of(stored: Option<&Value>) -> Value {
    let s = stored.cloned().unwrap_or(Value::Null);
    let locked = s["lock"] == true;
    json!({"type": "panel_policy", "lock": locked, "pin": if locked { s["pin"].as_str().unwrap_or("") } else { "" },
        "unlock_s": s["unlock_min"].as_i64().filter(|n| *n != 0).unwrap_or(DEFAULT_UNLOCK_MIN) * 60})
}

fn policy_key(entry: &Map<String, Value>) -> (bool, String, i64) {
    (entry.get("lock") == Some(&Value::Bool(true)), entry.get("pin").and_then(Value::as_str).unwrap_or("").to_owned(), entry.get("unlock_min").and_then(Value::as_i64).filter(|n| *n != 0).unwrap_or(DEFAULT_UNLOCK_MIN))
}

fn service_of(stored: Option<&Value>) -> Value {
    let s = stored.cloned().unwrap_or(Value::Null);
    let mode = s["service_mode"].as_str().filter(|m| !m.is_empty()).unwrap_or("off");
    let mut out = json!({"mode": mode, "service_type": s.get("service_type").filter(|v| v.as_str().is_some_and(|x| !x.is_empty())).cloned().unwrap_or(Value::Null)});
    if CONTROL_MODES.contains(&mode) {
        if let Some(k) = s["control_key"].as_str().filter(|k| !k.is_empty()) {
            out["key"] = json!(k);
        }
    }
    out
}

fn pending_rotation(stored: &Map<String, Value>, panel: &Panel) -> Option<Value> {
    let s = stored.get(&panel.id)?;
    let deg = s.get("rotation").filter(|r| !r.is_null())?;
    let rev = s["rotation_rev"].as_i64().unwrap_or(0);
    if panel.last_seen > 0.0 && panel.rotation_rev == rev {
        return None;
    }
    Some(json!({"type": "set_rotation", "rev": rev, "deg": deg}))
}

fn update_message(info: &Info, now: bool) -> Value {
    json!({"version": info.version, "size": info.size, "sha256": info.sha256, "path": info.image_url(), "now": now})
}

fn reported_ip(v: Option<&Value>) -> Option<String> {
    let text = bounded(v.unwrap_or(&Value::Null), 45);
    text.parse::<IpAddr>().ok().map(|ip| ip.to_string())
}
