//! What the follower knows, as `/stream` messages (`propresenter.py`'s `LyricsFeed` and `TimersFeed`).
//!
//! * `GET /stream?stream=lyrics&for=<viewer|screen|overlay|panel>`: Server-Sent Events, the whole state in each message, so a reader that
//!   reconnects needs nothing else. Whether an audience may read it is the operator's switch (`propresenter.lyrics`); the viewer port has no login,
//!   so the switch decides what Relay offers, not who may ask. `viewer` is off by default (song lyrics are licensed).
//! * `GET /stream?stream=pptimers&for=panel[&panel=<panel id>]`: the ProPresenter timers a panel shows, whole in each message, sent when something
//!   about a timer changes (never once a second) and by the route every heartbeat.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use bytes::Bytes;
use relay_core::caps::{PanelsCap, PANELS};
use relay_core::timers::MAX_TIMERS_SHOWN;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::follower::{now, Follower, AUDIENCES};
use crate::settings::{clean_timer_settings, configured};

pub const QUEUE_MAX: usize = 16;
pub const RUNNING_STATES: [&str; 2] = ["running", "overrunning"];
/// A running timer that is this far from where it should be was changed by someone.
pub const TIMER_JUMP_S: f64 = 2.0;

/// `data: <json>\n\n`.
pub fn frame(msg: &Value) -> Bytes {
    Bytes::from(format!("data: {msg}\n\n"))
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

struct Sub {
    id: u64,
    tx: mpsc::Sender<Bytes>,
}

// ---------------------------------------------------------------------------------------------------------------- the lyrics
#[derive(Default)]
struct LyricsInner {
    subs: BTreeMap<String, Vec<Sub>>,
    sigs: BTreeMap<String, String>,
    next: u64,
}

/// The lyrics: `{"type": "lyrics", "enabled": true, "live": true, "song": true, "title": "...", "part": "Verse 1", "index": 3, "text": "line one\nline two",
/// "next": "...", "rev": 12, "server_time": 1760000000.0}`. `live` is false (and text empty) when no presentation is up, when ProPresenter is
/// unreachable, or when the presentation is not a song and "songs only" is on. `enabled` is false when the operator has switched this audience off:
/// the message then carries nothing else.
pub struct LyricsFeed {
    source: Arc<Follower>,
    inner: Mutex<LyricsInner>,
}

fn signature(msg: &Value) -> String {
    let mut m = msg.clone();
    if let Some(o) = m.as_object_mut() {
        o.remove("server_time");
        o.remove("rev");
    }
    m.to_string()
}

impl LyricsFeed {
    pub fn new(source: Arc<Follower>) -> Arc<Self> {
        let feed = Arc::new(Self { source: Arc::clone(&source), inner: Mutex::new(LyricsInner::default()) });
        let weak = Arc::downgrade(&feed);
        source.on_change(Arc::new(move || {
            if let Some(f) = weak.upgrade() {
                f.refresh();
            }
        }));
        feed
    }

    pub fn allowed(&self, audience: &str) -> bool {
        let s = self.source.settings();
        if !configured(&s) {
            return false;
        }
        let key = if audience == "panel" { "panels" } else { audience };
        s["lyrics"][key] == Value::Bool(true)
    }

    pub fn payload(&self, audience: &str) -> Value {
        let at = now();
        if !self.allowed(audience) {
            return json!({"type": "lyrics", "enabled": false, "live": false, "server_time": at});
        }
        let mut out = json!({"type": "lyrics", "enabled": true});
        if let (Some(o), Some(l)) = (out.as_object_mut(), self.source.lyrics().as_object()) {
            for (k, v) in l {
                o.insert(k.clone(), v.clone());
            }
        }
        out["rev"] = json!(self.source.rev());
        out["server_time"] = json!(at);
        out
    }

    pub fn subscribe(&self, audience: &str) -> (u64, mpsc::Receiver<Bytes>) {
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let msg = self.payload(audience);
        let _ = tx.try_send(frame(&msg));
        let mut inner = lock(&self.inner);
        inner.sigs.insert(audience.to_owned(), signature(&msg));
        inner.next += 1;
        let id = inner.next;
        inner.subs.entry(audience.to_owned()).or_default().push(Sub { id, tx });
        (id, rx)
    }

    pub fn unsubscribe(&self, audience: &str, id: u64) {
        if let Some(v) = lock(&self.inner).subs.get_mut(audience) {
            v.retain(|s| s.id != id);
        }
    }

    /// Send each audience with readers whatever changed for it. Called when the follower's state changes and when the lyrics switches move.
    pub fn refresh(&self) {
        let audiences: Vec<String> = {
            let inner = lock(&self.inner);
            AUDIENCES.iter().filter(|a| inner.subs.get(**a).is_some_and(|v| !v.is_empty())).map(|a| (*a).to_owned()).collect()
        };
        for audience in audiences {
            let msg = self.payload(&audience);
            let sig = signature(&msg);
            let mut inner = lock(&self.inner);
            if inner.sigs.get(&audience) == Some(&sig) {
                continue;
            }
            inner.sigs.insert(audience.clone(), sig);
            let bytes = frame(&msg);
            if let Some(subs) = inner.subs.get_mut(&audience) {
                // A reader that stopped reading is let go: it reconnects and is told the state.
                subs.retain(|s| s.tx.try_send(bytes.clone()).is_ok());
            }
        }
    }

    pub fn viewer_count(&self) -> usize {
        lock(&self.inner).subs.values().map(Vec::len).sum()
    }
}

// ----------------------------------------------------------------------------------------------------------------- the timers
/// The colour a countdown is in: ok, amber (that many seconds left or fewer), red, or over (past zero, or overrunning). An elapsed timer counts up
/// and has no threshold, so it is always ok.
pub fn timer_level(value: f64, state: &str, direction: &str, amber_s: i64, red_s: i64) -> &'static str {
    if direction == "up" {
        return "ok";
    }
    if matches!(state, "overrunning" | "overran") || value < 0.0 {
        return "over";
    }
    if state == "complete" || value <= red_s as f64 {
        return "red";
    }
    if value <= amber_s as f64 {
        "amber"
    } else {
        "ok"
    }
}

#[derive(Default)]
struct TimersInner {
    subs: BTreeMap<String, Vec<Sub>>,
    sigs: BTreeMap<String, String>,
    /// timer -> (value, read_at, state)
    anchor: BTreeMap<String, (f64, f64, String)>,
    jumps: BTreeMap<String, u64>,
    next: u64,
}

/// `{"type": "pptimers", "enabled": true, "live": true, "amber_s": 120, "red_s": 30, "timers": [{"id", "name", "state", "dir", "value", "level"}], "rev": 12,
/// "server_time": 1760000000.0}`.
///
/// `value` is the seconds shown as of `server_time` (negative while a countdown overruns); the panel counts it itself, down for `dir` "down" and up
/// for "up", while the state is running or overrunning. A change made in ProPresenter to a running timer (time added, stopped, reset) is a new
/// message; the heartbeat re-sends the state, which is also what keeps a drifting clock honest. Which timers, and the thresholds, are the settings'
/// `timers` block, and a panel may override each of them: the panel names itself in the query.
pub struct TimersFeed {
    source: Arc<Follower>,
    inner: Mutex<TimersInner>,
}

impl TimersFeed {
    pub fn new(source: Arc<Follower>) -> Arc<Self> {
        let feed = Arc::new(Self { source: Arc::clone(&source), inner: Mutex::new(TimersInner::default()) });
        let weak = Arc::downgrade(&feed);
        source.on_change(Arc::new(move || {
            if let Some(f) = weak.upgrade() {
                f.refresh();
            }
        }));
        feed
    }

    pub fn allowed(&self) -> bool {
        self.source.configured()
    }

    fn panel_settings(&self, pid: &str) -> Value {
        if pid.is_empty() {
            return Value::Null;
        }
        self.source.host().using::<PanelsCap>(PANELS).and_then(|p| p.0.timers(pid)).unwrap_or(Value::Null)
    }

    /// The thresholds and timers to show for panel `pid` (`""` for the global settings).
    pub fn effective(&self, pid: &str) -> Value {
        clean_timer_settings(&self.panel_settings(pid), &self.source.settings()["timers"])
    }

    fn chosen(&self, show: &[String]) -> Vec<Value> {
        let timers = self.source.timers();
        if show.is_empty() {
            return timers.into_iter().take(MAX_TIMERS_SHOWN).collect();
        }
        show.iter().filter_map(|u| timers.iter().find(|t| t["id"].as_str() == Some(u.as_str())).cloned()).take(MAX_TIMERS_SHOWN).collect()
    }

    pub fn payload(&self, pid: &str) -> Value {
        let at = now();
        if !self.allowed() {
            return json!({"type": "pptimers", "enabled": false, "live": false, "timers": [], "server_time": at});
        }
        let eff = self.effective(pid);
        let up = self.source.is_up();
        let read_at = self.source.timers_read_at();
        // A reading older than this is not carried forward: Relay would be guessing.
        let dt = if read_at > 0.0 { (at - read_at).clamp(0.0, 5.0) } else { 0.0 };
        let (amber, red) = (eff["amber_s"].as_i64().unwrap_or(0), eff["red_s"].as_i64().unwrap_or(0));
        let show: Vec<String> = eff["show"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_owned)).collect();
        let mut rows = Vec::new();
        if up {
            for t in self.chosen(&show) {
                let state = t["state"].as_str().unwrap_or("stopped");
                let dir = t["dir"].as_str().unwrap_or("down");
                let mut value = t["value"].as_f64().unwrap_or(0.0);
                if RUNNING_STATES.contains(&state) {
                    value += if dir == "down" { -dt } else { dt };
                }
                let value = (value * 10.0).round() / 10.0;
                rows.push(json!({"id": t["id"], "name": t["name"], "state": state, "dir": dir, "value": value, "level": timer_level(value, state, dir, amber, red)}));
            }
        }
        json!({"type": "pptimers", "enabled": true, "live": up, "amber_s": amber, "red_s": red, "timers": rows, "rev": self.source.rev(), "server_time": at})
    }

    /// Note a running timer that is not where it was heading, so that is sent at once and not at the next heartbeat. ProPresenter reports whole
    /// seconds a second apart, so ordinary reads are within a second.
    fn watch(&self) {
        let read_at = self.source.timers_read_at();
        let mut inner = lock(&self.inner);
        for t in self.source.timers() {
            let id = t["id"].as_str().unwrap_or("").to_owned();
            let (value, state) = (t["value"].as_f64().unwrap_or(0.0), t["state"].as_str().unwrap_or("").to_owned());
            if let Some(old) = inner.anchor.get(&id).cloned() {
                if old.1 == read_at {
                    continue;
                }
                if RUNNING_STATES.contains(&old.2.as_str()) && RUNNING_STATES.contains(&state.as_str()) {
                    let elapsed = read_at - old.1;
                    let heading = old.0 + if t["dir"] == "down" { -elapsed } else { elapsed };
                    if (heading - value).abs() > TIMER_JUMP_S {
                        *inner.jumps.entry(id.clone()).or_insert(0) += 1;
                    }
                }
            }
            inner.anchor.insert(id, (value, read_at, state));
        }
    }

    /// What a panel must be told again: not the seconds of a running timer, which it counts itself.
    fn signature(&self, msg: &Value) -> String {
        let inner = lock(&self.inner);
        let rows: Vec<Value> = msg["timers"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|r| {
                let running = RUNNING_STATES.contains(&r["state"].as_str().unwrap_or(""));
                json!({"id": r["id"], "name": r["name"], "state": r["state"], "dir": r["dir"],
                       "jump": inner.jumps.get(r["id"].as_str().unwrap_or("")).copied().unwrap_or(0), "value": if running { Value::Null } else { r["value"].clone() }})
            })
            .collect();
        json!({"e": msg["enabled"], "l": msg["live"], "a": msg["amber_s"], "r": msg["red_s"], "t": rows}).to_string()
    }

    pub fn subscribe(&self, pid: &str) -> (u64, mpsc::Receiver<Bytes>) {
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let msg = self.payload(pid);
        let sig = self.signature(&msg);
        let _ = tx.try_send(frame(&msg));
        let mut inner = lock(&self.inner);
        inner.sigs.entry(pid.to_owned()).or_insert(sig);
        inner.next += 1;
        let id = inner.next;
        inner.subs.entry(pid.to_owned()).or_default().push(Sub { id, tx });
        (id, rx)
    }

    pub fn unsubscribe(&self, pid: &str, id: u64) {
        let mut inner = lock(&self.inner);
        if let Some(v) = inner.subs.get_mut(pid) {
            v.retain(|s| s.id != id);
            if v.is_empty() {
                inner.subs.remove(pid);
                inner.sigs.remove(pid);
            }
        }
    }

    /// Send each reader whatever changed for it. Called when the follower's state changes and when the settings (the global ones, or a panel's own)
    /// move.
    pub fn refresh(&self) {
        self.watch();
        let pids: Vec<String> = lock(&self.inner).subs.iter().filter(|(_, v)| !v.is_empty()).map(|(k, _)| k.clone()).collect();
        for pid in pids {
            let msg = self.payload(&pid);
            let sig = self.signature(&msg);
            let mut inner = lock(&self.inner);
            if inner.sigs.get(&pid) == Some(&sig) {
                continue;
            }
            inner.sigs.insert(pid.clone(), sig);
            let bytes = frame(&msg);
            if let Some(subs) = inner.subs.get_mut(&pid) {
                subs.retain(|s| s.tx.try_send(bytes.clone()).is_ok());
            }
        }
    }
}
