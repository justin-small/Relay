//! Alerts (#121) and stage messages (#214): `app/alerts.py`. A musician taps a button and the screens behind it flash until
//! someone there taps one of them. The crate holds the state and says what to send where; it knows no HTTP. What it needs from
//! outside is a few small traits ([`Env`], [`Store`], [`History`]) that `relay-server` fills in.
//!
//! The state is in memory only, as it always was: an alert is never stored, only its history row is.

pub mod rules;

use std::collections::HashMap;
use std::sync::{Arc, Mutex, MutexGuard};

use bytes::Bytes;
use serde_json::{json, Value};
use tokio::sync::mpsc;

pub use rules::*;

use crate::text::same_name;
use crate::Clock;

pub const PRESS_GAP_S: f64 = 0.8;
pub const OUTCOME_KEEP_S: f64 = 600.0;
pub const BODY_MAX_BYTES: usize = 512;
pub const MAX_SENDER_STREAMS: usize = 64;
pub const MAX_SCREEN_STREAMS: usize = 32;
pub const QUEUE_MAX: usize = 16;
pub const FLASH_HZ: i64 = 1;
pub const MAX_LIVE_MESSAGES: usize = 8;
pub const TEST_TEXT: &str = "Test from Relay";
const SCREEN_SUBS_PER_NAME: usize = 4;

/// A refusal: the HTTP status and the key of the fixed sentence (never an error's own text).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Refused {
    pub status: u16,
    pub key: &'static str,
    /// A fuller sentence for Relay's own history (a slide-note tag's), never sent in a response.
    pub detail: Option<String>,
}

impl Refused {
    pub fn new(status: u16, key: &'static str) -> Self {
        Self { status, key, detail: None }
    }
    pub fn sentence(&self) -> &'static str {
        sentence(self.key)
    }
}

pub fn sentence(key: &str) -> &'static str {
    match key {
        "no_button" => "no such alert button",
        "no_button_named" => "No alert button has that name. Add one under Alerts.",
        "bad_id" => "id must be an alert id",
        "no_alert" => "no such alert",
        "not_this_panel" => "this panel is not flashed by that alert",
        "not_this_screen" => "this screen is not flashed by that alert",
        "who_saw" => "say which panel or screen saw it",
        "too_many_screens" => "too many alert screens are open",
        "no_preset" => "no such message",
        "no_words" => "write the message first",
        "bad_level" => "level must be info, attention or urgent",
        "bad_reply" => "that is not one of the replies",
        "no_free_targets" => "the operator has not chosen where typed messages go",
        "no_test_target" => "say which panel or screen to test",
        _ => "The alert could not be sent.",
    }
}

/// The panels and places Alerts sends to.
pub trait Env: Send + Sync + 'static {
    /// Push `message` onto one panel's event stream; how many streams took it.
    fn push_panel(&self, pid: &str, message: &Value) -> usize;
    /// A panel's display name, "" when it has none.
    fn panel_name(&self, pid: &str) -> String;
    /// The screens in a place (`screens.in_place`): `(name, panel id or "")`. `None` while the database is unavailable.
    fn in_place(&self, place: i64) -> Option<Vec<(String, String)>>;
}

/// The `alerts` block of config.json.
pub trait Store: Send + Sync + 'static {
    fn read(&self) -> Value;
    fn save(&self, clean: &Value);
}

/// One row of the alert history (`app/alerthistory.py`).
#[derive(Debug, Clone, Default)]
pub struct Row {
    pub kind: &'static str,
    pub at: String,
    pub name: String,
    pub text: String,
    pub level: String,
    pub sender: String,
    pub targets: Vec<String>,
    pub outcome: String,
    pub seen_on: String,
    pub seen_after_s: Option<i64>,
    pub reply: String,
}

pub trait History: Send + Sync + 'static {
    /// Write one row. Never fails the caller: a database that is down loses the row.
    fn record(&self, row: Row);
}

/// ProPresenter's stage display, and any module that wants alerts as events (#138, #219): called with the event, on the
/// caller's thread, and must not block.
pub type Listener = Box<dyn Fn(&Value) + Send + Sync>;
pub type OnStage = Box<dyn Fn(&str, Option<&str>) + Send + Sync>;
/// Called when anything the admin page shows has changed.
pub type OnChange = Box<dyn Fn() + Send + Sync>;

#[derive(Debug, Clone)]
struct Alert {
    id: String,
    button: String,
    sender: String,
    started: f64,
    state: &'static str,
    seen_on: String,
    ended: f64,
    level: &'static str,
    reply: String,
    at: String,
    targets: Vec<String>,
}

#[derive(Debug, Clone)]
struct Message {
    id: String,
    key: String,
    text: String,
    banner: bool,
    sender: String,
    started: f64,
    seconds: i64,
    panels: Vec<String>,
    screens: Vec<String>,
    level: &'static str,
}

type Sub = mpsc::Sender<Bytes>;

#[derive(Default)]
struct State {
    latest: HashMap<String, Alert>,
    senders: Vec<Sub>,
    /// Showing now, oldest first (`live` is an ordered dict in Python: the oldest is the first to make room).
    live: Vec<Message>,
    msg_senders: Vec<Sub>,
    screens: HashMap<String, Vec<Sub>>,
    screen_names: HashMap<String, String>,
}

pub struct Alerts {
    state: Mutex<State>,
    env: Arc<dyn Env>,
    store: Arc<dyn Store>,
    history: Arc<dyn History>,
    clock: Arc<dyn Clock>,
    listeners: Mutex<Vec<(String, Listener)>>,
    on_stage: Mutex<Option<OnStage>>,
    on_change: Mutex<Option<OnChange>>,
}

fn frame(message: &Value) -> Bytes {
    Bytes::from(format!("data: {}\n\n", compact(message)))
}

/// `json.dumps(message, ensure_ascii=False)`: Python's default separators, which put a space after `,` and `:`.
pub fn compact(v: &Value) -> String {
    let mut out = String::new();
    write_py(v, &mut out);
    out
}

fn write_py(v: &Value, out: &mut String) {
    match v {
        Value::Object(m) => {
            out.push('{');
            for (i, (k, val)) in m.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                out.push_str(&Value::String(k.clone()).to_string());
                out.push_str(": ");
                write_py(val, out);
            }
            out.push('}');
        }
        Value::Array(a) => {
            out.push('[');
            for (i, val) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                write_py(val, out);
            }
            out.push(']');
        }
        other => out.push_str(&other.to_string()),
    }
}

/// Offer a frame to a subscriber: `false` when it has stopped reading (it is dropped, and reconnects to be told the state).
fn offer(sub: &Sub, frame: &Bytes) -> bool {
    sub.try_send(frame.clone()).is_ok()
}

impl Alerts {
    pub fn new(env: Arc<dyn Env>, store: Arc<dyn Store>, history: Arc<dyn History>, clock: Arc<dyn Clock>) -> Arc<Self> {
        Arc::new(Self {
            state: Mutex::default(),
            env,
            store,
            history,
            clock,
            listeners: Mutex::default(),
            on_stage: Mutex::default(),
            on_change: Mutex::default(),
        })
    }

    fn lock(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Events out (#219): a module adds one while it is on and removes it by `name` when it stops.
    pub fn add_listener(&self, name: &str, f: Listener) {
        let mut l = self.listeners.lock().unwrap_or_else(|e| e.into_inner());
        l.retain(|(n, _)| n != name);
        l.push((name.to_owned(), f));
    }

    pub fn remove_listener(&self, name: &str) {
        self.listeners.lock().unwrap_or_else(|e| e.into_inner()).retain(|(n, _)| n != name);
    }

    pub fn set_on_stage(&self, f: Option<OnStage>) {
        *self.on_stage.lock().unwrap_or_else(|e| e.into_inner()) = f;
    }

    pub fn set_on_change(&self, f: Option<OnChange>) {
        *self.on_change.lock().unwrap_or_else(|e| e.into_inner()) = f;
    }

    // ------------------------------------------------------------ settings
    pub fn settings(&self) -> Value {
        clean_stored(&self.store.read())
    }

    fn buttons(cfg: &Value) -> &[Value] {
        cfg["buttons"].as_array().map(Vec::as_slice).unwrap_or(&[])
    }

    fn button_in(cfg: &Value, bid: &str) -> Option<Value> {
        Self::buttons(cfg).iter().find(|b| b["id"] == bid).cloned()
    }

    pub fn button(&self, bid: &str) -> Option<Value> {
        Self::button_in(&self.settings(), bid)
    }

    /// Save the operator's settings, whole. A client that sends only some blocks leaves the others as they are.
    pub fn save(&self, raw: &Value) -> Result<Value, String> {
        let mut raw = raw.clone();
        if let Some(obj) = raw.as_object_mut() {
            let current = self.settings();
            for key in ["messages", "replies"] {
                if !obj.contains_key(key) {
                    obj.insert(key.to_owned(), current[key].clone());
                }
            }
        }
        let clean = validate(&raw)?;
        let kept: Vec<&str> = Self::buttons(&clean).iter().filter_map(|b| b["id"].as_str()).collect();
        {
            let mut st = self.lock();
            let gone: Vec<String> = st.latest.keys().filter(|k| !kept.contains(&k.as_str())).cloned().collect();
            for bid in gone {
                let alert = st.latest.get(&bid).cloned();
                if let Some(alert) = alert.filter(|a| a.state == "on") {
                    self.end_alert(&mut st, alert, "cancelled");
                }
                st.latest.remove(&bid);
            }
        }
        self.store.save(&clean);
        {
            let mut st = self.lock();
            let stale: Vec<Message> = st
                .live
                .iter()
                .filter(|m| m.key != "free" && !clean["messages"]["presets"].as_array().is_some_and(|p| p.iter().any(|x| x["id"] == m.key.as_str())))
                .cloned()
                .collect();
            for m in stale {
                self.end_message(&mut st, &m);
            }
            self.broadcast_state(&mut st);
            self.broadcast_messages(&mut st);
        }
        Ok(self.admin_view())
    }

    // --------------------------------------------------------------- state
    fn state_json(&self, st: &mut State) -> Value {
        self.expire_in(st);
        let cfg = self.settings();
        self.state_with(st, &cfg)
    }

    fn state_with(&self, st: &State, cfg: &Value) -> Value {
        let now = self.clock.now();
        let auto = cfg["auto_stop_s"].as_i64().unwrap_or(AUTO_STOP_DEFAULT_S) as f64;
        let rows: Vec<Value> = Self::buttons(cfg)
            .iter()
            .map(|b| {
                let bid = b["id"].as_str().unwrap_or("");
                let mut row = json!({"id": bid, "name": b["name"], "icon": b["icon"].as_str().filter(|s| !s.is_empty()).unwrap_or(DEFAULT_ICON), "state": "idle"});
                if let Some(a) = st.latest.get(bid) {
                    row["state"] = json!(a.state);
                    row["from"] = json!(a.sender);
                    row["seen_on"] = json!(a.seen_on);
                    row["reply"] = json!(a.reply);
                    let since = if a.ended != 0.0 { a.ended } else { a.started };
                    row["age_s"] = json!(round(now - since));
                    if a.state == "on" {
                        row["ends_in_s"] = json!(round(auto - (now - a.started)).max(0));
                    }
                }
                row
            })
            .collect();
        json!({"type": "alerts", "buttons": rows})
    }

    /// Every button and how its newest alert stands: what `/stream?stream=alert` carries, whole, each time.
    pub fn state(&self) -> Value {
        let mut st = self.lock();
        self.state_json(&mut st)
    }

    pub fn admin_view(&self) -> Value {
        let cfg = self.settings();
        let mut st = self.lock();
        let state = self.state_json(&mut st);
        let live = self.message_state_in(&mut st)["live"].clone();
        let screens = self.screen_names_in(&st);
        json!({"alerts": cfg, "screens": screens, "state": state["buttons"],
            "icons": ICONS, "levels": LEVELS,
            "limits": {"buttons": MAX_BUTTONS, "targets": MAX_TARGETS, "name": NAME_MAX, "auto_stop_min_s": AUTO_STOP_MIN_S,
                "auto_stop_max_s": AUTO_STOP_MAX_S, "presets": MAX_PRESETS, "text": TEXT_MAX, "message_min_s": MESSAGE_MIN_S,
                "message_max_s": MESSAGE_MAX_S, "banner_min_s": BANNER_MIN_S, "banner_max_s": BANNER_MAX_S,
                "replies": MAX_REPLIES, "reply": REPLY_MAX},
            "live_messages": live})
    }

    fn screen_names_in(&self, st: &State) -> Vec<String> {
        let mut names: Vec<String> = st.screens.keys().filter_map(|k| st.screen_names.get(k).cloned()).collect();
        names.sort_by_key(|n| n.to_lowercase());
        names
    }

    /// Browser screens open right now that can be flashed, as they were named.
    pub fn screen_names(&self) -> Vec<String> {
        self.screen_names_in(&self.lock())
    }

    /// The `alert` messages a panel's event stream opens with: alerts flashing on it now, and messages showing on it.
    pub fn active_for_panel(&self, pid: &str) -> Vec<Value> {
        let cfg = self.settings();
        let mut st = self.lock();
        self.expire_in(&mut st);
        let mut out = Vec::new();
        for b in Self::buttons(&cfg) {
            if let Some(alert) = st.latest.get(b["id"].as_str().unwrap_or("")) {
                if alert.state == "on" && self.targets_of(b).0.iter().any(|p| p == pid) {
                    out.push(self.alert_message(alert, b, &cfg));
                }
            }
        }
        out.extend(st.live.iter().filter(|m| m.panels.iter().any(|p| p == pid)).map(|m| self.message_wire(m)));
        out
    }

    fn alert_message(&self, alert: &Alert, button: &Value, cfg: &Value) -> Value {
        let remaining = cfg["auto_stop_s"].as_i64().unwrap_or(AUTO_STOP_DEFAULT_S) as f64 - (self.clock.now() - alert.started);
        let mut wire = json!({"type": "alert", "id": alert.id, "from": alert.sender, "to": button["name"],
            "ends_in_s": round(remaining).max(1), "level": alert.level, "hz": if level_flashes(alert.level) { FLASH_HZ } else { 0 }});
        let replies = cfg["replies"].as_array().cloned().unwrap_or_default();
        if !replies.is_empty() {
            wire["replies"] = Value::Array(replies);
        }
        wire
    }

    // ------------------------------------------------------------- presses
    /// Start an alert on a button, or cancel the one flashing. `level` overrides the button's for this alert (#215).
    pub fn press(&self, bid: &str, sender: &str, level: Option<&Value>) -> Result<Value, Refused> {
        let cfg = self.settings();
        let button = Self::button_in(&cfg, bid).ok_or(Refused::new(404, "no_button"))?;
        let level = match level {
            None | Some(Value::Null) => None,
            Some(v) => Some(v.as_str().and_then(|s| LEVELS.iter().find(|l| **l == s).copied()).ok_or(Refused::new(400, "bad_level"))?),
        };
        let mut st = self.lock();
        self.expire_in(&mut st);
        let now = self.clock.now();
        match st.latest.get(bid).cloned().filter(|a| a.state == "on") {
            Some(alert) => {
                if now - alert.started < PRESS_GAP_S {
                    return Ok(self.state_with(&st, &cfg));
                }
                self.end_alert(&mut st, alert, "cancelled");
            }
            None => {
                let alert = Alert {
                    id: token_hex(8),
                    button: bid.to_owned(),
                    sender: clean_name_str(sender),
                    started: now,
                    state: "on",
                    seen_on: String::new(),
                    ended: 0.0,
                    level: level.unwrap_or_else(|| clean_level(&button["level"], DEFAULT_ALERT_LEVEL)),
                    reply: String::new(),
                    at: self.clock.stamp(),
                    targets: self.target_names(&button),
                };
                st.latest.insert(bid.to_owned(), alert.clone());
                self.emit("alert.sent", Some(&alert), None, Some(&button));
                let message = self.alert_message(&alert, &button, &cfg);
                if button["stage"] == true {
                    let name = button["name"].as_str().unwrap_or("");
                    let text = if alert.sender.is_empty() { name.to_owned() } else { format!("{name} (from {})", alert.sender) };
                    self.stage(&alert.id, Some(&text));
                }
                let (panels, names) = self.targets_of(&button);
                for pid in &panels {
                    self.env.push_panel(pid, &message);
                }
                for name in &names {
                    self.push_screen(&st, name, &message);
                }
            }
        }
        self.broadcast_state(&mut st);
        Ok(self.state_json(&mut st))
    }

    /// Start the alert on the button NAMED `text` (any capitals), for something other than a person tapping it (a slide-note
    /// tag). Unlike `press` it never cancels: an alert already flashing stays as it is.
    pub fn raise_named(&self, text: &str, sender: &str) -> Result<String, Refused> {
        let want = clean_name_str(text).to_lowercase();
        let cfg = self.settings();
        let button = if want.is_empty() { None } else { Self::buttons(&cfg).iter().find(|b| b["name"].as_str().is_some_and(|n| n.to_lowercase() == want)).cloned() };
        let Some(button) = button else {
            return Err(Refused {
                status: 404,
                key: "no_button_named",
                detail: Some(format!("No alert button is named \"{}\". Add one under Alerts.", clean_name_str(text))),
            });
        };
        let name = button["name"].as_str().unwrap_or("").to_owned();
        {
            let mut st = self.lock();
            self.expire_in(&mut st);
            if st.latest.get(button["id"].as_str().unwrap_or("")).is_some_and(|a| a.state == "on") {
                return Ok(format!("The {name} alert is already on."));
            }
        }
        self.press(button["id"].as_str().unwrap_or(""), sender, None)?;
        Ok(format!("Alert {name} raised."))
    }

    pub fn cancel(&self, bid: &str) -> Result<Value, Refused> {
        let cfg = self.settings();
        Self::button_in(&cfg, bid).ok_or(Refused::new(404, "no_button"))?;
        let mut st = self.lock();
        if let Some(alert) = st.latest.get(bid).cloned().filter(|a| a.state == "on") {
            self.end_alert(&mut st, alert, "cancelled");
            self.broadcast_state(&mut st);
        }
        Ok(self.state_json(&mut st))
    }

    fn target_names(&self, item: &Value) -> Vec<String> {
        let (panels, names) = self.targets_of(item);
        let mut out: Vec<String> = panels.iter().map(|p| Some(self.env.panel_name(p)).filter(|n| !n.is_empty()).unwrap_or_else(|| "a panel".into())).collect();
        out.extend(names);
        out
    }

    /// `(panel ids, browser screen names)` a button flashes. An entry in `screens` that is a place ref (`place:relay:3`) stands
    /// for every screen in that place, panels and browsers alike.
    pub fn targets_of(&self, item: &Value) -> (Vec<String>, Vec<String>) {
        let mut panels: Vec<String> = item["panels"].as_array().map(|a| a.iter().filter_map(Value::as_str).map(str::to_owned).collect()).unwrap_or_default();
        let mut names: Vec<String> = Vec::new();
        for entry in item["screens"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
            let entry = entry.as_str().unwrap_or("");
            match crate::screens::place_id(entry) {
                None => names.push(entry.to_owned()),
                Some(pid) => {
                    let Some(found) = self.env.in_place(pid) else { continue };
                    for (name, panel) in found {
                        if panel.is_empty() {
                            names.push(name);
                        } else {
                            panels.push(panel);
                        }
                    }
                }
            }
        }
        (dedup(panels), dedup(names))
    }

    /// Someone tapped a flashing screen: the alert ends everywhere, and the sender is told where. `reply` is one of the
    /// operator's replies (#216), taken or refused before anything ends.
    pub fn ack(&self, alert_id: &Value, panel: Option<&str>, screen: Option<&str>, reply: Option<&Value>) -> Result<Value, Refused> {
        let alert_id = alert_id.as_str().filter(|a| is_alert_id(a)).ok_or(Refused::new(400, "bad_id"))?;
        let cfg = self.settings();
        let mut st = self.lock();
        self.expire_in(&mut st);
        let alert = st.latest.values().find(|a| a.id == alert_id).cloned().ok_or(Refused::new(404, "no_alert"))?;
        let button = Self::button_in(&cfg, &alert.button).ok_or(Refused::new(404, "no_alert"))?;
        let where_ = if let Some(panel) = panel {
            if !self.targets_of(&button).0.iter().any(|p| p == panel) {
                return Err(Refused::new(403, "not_this_panel"));
            }
            Some(self.env.panel_name(panel)).filter(|n| !n.is_empty()).unwrap_or_else(|| "a panel".into())
        } else if let Some(screen) = screen {
            let want = clean_name_str(screen).to_lowercase();
            let found = self.targets_of(&button).1.into_iter().find(|s| s.to_lowercase() == want).unwrap_or_default();
            if found.is_empty() {
                return Err(Refused::new(403, "not_this_screen"));
            }
            found
        } else {
            return Err(Refused::new(400, "who_saw"));
        };
        let mut chosen = String::new();
        if let Some(reply) = reply.filter(|r| !r.is_null() && r.as_str() != Some("")) {
            if let Some(text) = reply.as_str() {
                let want = clean_reply(&Value::String(text.to_owned())).to_lowercase();
                chosen = cfg["replies"].as_array().into_iter().flatten().filter_map(Value::as_str).find(|r| r.to_lowercase() == want).unwrap_or("").to_owned();
            }
            if chosen.is_empty() {
                return Err(Refused::new(400, "bad_reply"));
            }
        }
        let mut current = alert.clone();
        if alert.state == "on" {
            current.seen_on = where_;
            current.reply = chosen;
            st.latest.insert(alert.button.clone(), current.clone());
            self.end_alert(&mut st, current.clone(), "seen");
            self.broadcast_state(&mut st);
        }
        let now = st.latest.get(&alert.button).cloned().unwrap_or(current);
        Ok(json!({"ok": true, "state": now.state, "seen_on": now.seen_on, "reply": now.reply}))
    }

    /// End alerts nobody tapped in time, and forget old outcomes. True when something changed.
    pub fn expire(&self) -> bool {
        let mut st = self.lock();
        self.expire_in(&mut st)
    }

    fn expire_in(&self, st: &mut State) -> bool {
        let now = self.clock.now();
        let auto = self.settings()["auto_stop_s"].as_i64().unwrap_or(AUTO_STOP_DEFAULT_S) as f64;
        let mut changed = false;
        for (bid, alert) in st.latest.clone() {
            if alert.state == "on" && now - alert.started >= auto {
                self.end_alert(st, alert, "missed");
                changed = true;
            } else if alert.state != "on" && now - alert.ended >= OUTCOME_KEEP_S {
                st.latest.remove(&bid);
                changed = true;
            }
        }
        for msg in st.live.clone() {
            if now - msg.started >= msg.seconds as f64 {
                self.end_message(st, &msg);
                changed = true;
            }
        }
        changed
    }

    /// Once a second: end what is due and tell the readers.
    pub fn tick(&self) {
        let mut st = self.lock();
        if self.expire_in(&mut st) {
            self.broadcast_state(&mut st);
            self.broadcast_messages(&mut st);
        }
    }

    fn end_alert(&self, st: &mut State, mut alert: Alert, state: &'static str) {
        alert.state = state;
        alert.ended = self.clock.now();
        st.latest.insert(alert.button.clone(), alert.clone());
        let button = self.button(&alert.button);
        let message = json!({"type": "alert_end", "id": alert.id, "why": state});
        self.stage(&alert.id, None);
        let seen = state == "seen";
        self.history.record(Row {
            kind: "alert",
            at: alert.at.clone(),
            name: button.as_ref().and_then(|b| b["name"].as_str()).unwrap_or("").to_owned(),
            level: alert.level.to_owned(),
            sender: alert.sender.clone(),
            targets: alert.targets.clone(),
            outcome: alert.state.to_owned(),
            seen_on: alert.seen_on.clone(),
            seen_after_s: seen.then(|| round(alert.ended - alert.started)),
            reply: alert.reply.clone(),
            ..Row::default()
        });
        self.emit(if seen { "alert.acknowledged" } else { "alert.ended" }, Some(&alert), None, button.as_ref());
        if let Some(button) = button {
            let (panels, names) = self.targets_of(&button);
            for pid in &panels {
                self.env.push_panel(pid, &message);
            }
            for name in &names {
                self.push_screen(st, name, &message);
            }
        }
    }

    // ------------------------------------------------------- events out
    fn emit(&self, event: &str, alert: Option<&Alert>, msg: Option<&Message>, button: Option<&Value>) {
        let listeners = self.listeners.lock().unwrap_or_else(|e| e.into_inner());
        if listeners.is_empty() {
            return;
        }
        let data = if let Some(m) = msg {
            let mut targets: Vec<String> = m.panels.iter().map(|p| Some(self.env.panel_name(p)).filter(|n| !n.is_empty()).unwrap_or_else(|| "a panel".into())).collect();
            targets.extend(m.screens.iter().cloned());
            json!({"event": event, "id": m.id, "text": m.text, "level": m.level, "banner": m.banner, "from": m.sender, "targets": targets})
        } else if let Some(a) = alert {
            let mut d = json!({"event": event, "id": a.id, "button": button.and_then(|b| b["name"].as_str()).unwrap_or(""),
                "level": a.level, "from": a.sender, "targets": a.targets});
            if event != "alert.sent" {
                d["state"] = json!(a.state);
                d["seen_on"] = json!(a.seen_on);
                d["reply"] = json!(a.reply);
                if a.state == "seen" {
                    d["seen_after_s"] = json!(round(a.ended - a.started));
                }
            }
            d
        } else {
            return;
        };
        for (_, f) in listeners.iter() {
            f(&data);
        }
    }

    fn stage(&self, alert_id: &str, text: Option<&str>) {
        if let Some(f) = self.on_stage.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
            f(alert_id, text);
        }
    }

    fn admin_changed(&self) {
        if let Some(f) = self.on_change.lock().unwrap_or_else(|e| e.into_inner()).as_ref() {
            f();
        }
    }

    // ------------------------------------------------------ stage messages
    fn message_wire(&self, msg: &Message) -> Value {
        let remaining = msg.seconds as f64 - (self.clock.now() - msg.started);
        json!({"type": "message", "id": msg.id, "from": msg.sender, "text": msg.text, "banner": msg.banner, "level": msg.level,
            "hz": if level_flashes(msg.level) { FLASH_HZ } else { 0 }, "ends_in_s": round(remaining).max(1)})
    }

    /// What the /alert page needs for stage messages, whole: the presets to tap, whether typing is allowed, and what is showing.
    pub fn message_state(&self) -> Value {
        let mut st = self.lock();
        self.message_state_in(&mut st)
    }

    fn message_state_in(&self, st: &mut State) -> Value {
        self.expire_in(st);
        let cfg = self.settings();
        let m = &cfg["messages"];
        let now = self.clock.now();
        let free = &m["free"];
        let has_free = free["panels"].as_array().is_some_and(|a| !a.is_empty()) || free["screens"].as_array().is_some_and(|a| !a.is_empty());
        let presets: Vec<Value> = m["presets"].as_array().into_iter().flatten()
            .map(|p| json!({"id": p["id"], "text": p["text"], "banner": p["banner"], "level": p["level"].as_str().unwrap_or(DEFAULT_MESSAGE_LEVEL)}))
            .collect();
        let live: Vec<Value> = st.live.iter()
            .map(|x| json!({"id": x.id, "key": x.key, "text": x.text, "banner": x.banner, "level": x.level, "from": x.sender,
                "ends_in_s": round(x.seconds as f64 - (now - x.started)).max(0)}))
            .collect();
        json!({"type": "messages", "free": has_free, "text_max": TEXT_MAX, "free_level": free["level"].as_str().unwrap_or(DEFAULT_MESSAGE_LEVEL),
            "presets": presets, "live": live})
    }

    /// Show a preset (by id) or typed words on their screens. The same preset, or typed words again, replace what is showing
    /// from it. `level` overrides the preset's or the typed words' own (#215).
    pub fn send_message(&self, sender: &str, preset: Option<&Value>, text: Option<&Value>, banner: Option<&Value>, level: Option<&Value>) -> Result<Value, Refused> {
        let cfg = self.settings();
        let m = &cfg["messages"];
        let level = match level {
            None | Some(Value::Null) => None,
            Some(v) => Some(v.as_str().and_then(|s| LEVELS.iter().find(|l| **l == s).copied()).ok_or(Refused::new(400, "bad_level"))?),
        };
        let strs = |v: &Value| -> Vec<String> { v.as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_owned).collect() };
        let (key, words, is_banner, panels, screens, own);
        if let Some(preset) = preset.filter(|p| !p.is_null()) {
            let item = preset.as_str().and_then(|p| m["presets"].as_array().into_iter().flatten().find(|x| x["id"] == p)).ok_or(Refused::new(404, "no_preset"))?;
            key = item["id"].as_str().unwrap_or("").to_owned();
            words = item["text"].as_str().unwrap_or("").to_owned();
            is_banner = item["banner"] == true;
            panels = strs(&item["panels"]);
            screens = strs(&item["screens"]);
            own = clean_level(&item["level"], DEFAULT_MESSAGE_LEVEL);
        } else {
            words = clean_text(text.unwrap_or(&Value::Null));
            if words.is_empty() {
                return Err(Refused::new(400, "no_words"));
            }
            panels = strs(&m["free"]["panels"]);
            screens = strs(&m["free"]["screens"]);
            if panels.is_empty() && screens.is_empty() {
                return Err(Refused::new(403, "no_free_targets"));
            }
            key = "free".to_owned();
            is_banner = banner == Some(&Value::Bool(true));
            own = clean_level(&m["free"]["level"], DEFAULT_MESSAGE_LEVEL);
        }
        let mut st = self.lock();
        self.expire_in(&mut st);
        if let Some(old) = st.live.iter().find(|x| x.key == key).cloned() {
            self.end_message(&mut st, &old);
        }
        while st.live.len() >= MAX_LIVE_MESSAGES {
            let first = st.live[0].clone();
            self.end_message(&mut st, &first);
        }
        let msg = Message {
            id: token_hex(8),
            key,
            text: words,
            banner: is_banner,
            sender: clean_name_str(sender),
            started: self.clock.now(),
            seconds: if is_banner { m["banner_s"].as_i64().unwrap_or(BANNER_DEFAULT_S) } else { m["message_s"].as_i64().unwrap_or(MESSAGE_DEFAULT_S) },
            panels,
            screens,
            level: level.unwrap_or(own),
        };
        st.live.push(msg.clone());
        let mut targets: Vec<String> = msg.panels.iter().map(|p| Some(self.env.panel_name(p)).filter(|n| !n.is_empty()).unwrap_or_else(|| "a panel".into())).collect();
        targets.extend(msg.screens.iter().cloned());
        self.history.record(Row { kind: "message", at: self.clock.stamp(), text: msg.text.clone(), level: msg.level.to_owned(), sender: msg.sender.clone(), targets, outcome: "shown".into(), ..Row::default() });
        self.emit("message.sent", None, Some(&msg), None);
        let wire = self.message_wire(&msg);
        for pid in &msg.panels {
            self.env.push_panel(pid, &wire);
        }
        for name in &msg.screens {
            self.push_screen(&st, name, &wire);
        }
        self.broadcast_messages(&mut st);
        Ok(self.message_state_in(&mut st))
    }

    /// Show "Test from Relay" on one panel or browser screen for a message's time. Steady info blue: nothing flashes, nothing
    /// to tap, nothing in the history. How many streams took it (0 when the panel or screen is not connected).
    pub fn test(&self, panel: Option<&Value>, screen: Option<&Value>) -> Result<usize, Refused> {
        let (panels, screens, key): (Vec<String>, Vec<String>, String) = if let Some(p) = panel.and_then(Value::as_str).filter(|p| is_panel_id_prefix(p)) {
            (vec![p.to_owned()], vec![], format!("test:panel:{p}"))
        } else if let Some(name) = screen.map(clean_name).filter(|n| !n.is_empty()) {
            let key = format!("test:screen:{}", name.to_lowercase());
            (vec![], vec![name], key)
        } else {
            return Err(Refused::new(400, "no_test_target"));
        };
        let cfg = self.settings();
        let mut st = self.lock();
        self.expire_in(&mut st);
        if let Some(old) = st.live.iter().find(|x| x.key == key).cloned() {
            self.end_message(&mut st, &old);
        }
        while st.live.len() >= MAX_LIVE_MESSAGES {
            let first = st.live[0].clone();
            self.end_message(&mut st, &first);
        }
        let msg = Message {
            id: token_hex(8),
            key,
            text: TEST_TEXT.into(),
            banner: false,
            sender: "Relay".into(),
            started: self.clock.now(),
            seconds: cfg["messages"]["message_s"].as_i64().unwrap_or(MESSAGE_DEFAULT_S),
            panels,
            screens,
            level: "info",
        };
        st.live.push(msg.clone());
        let wire = self.message_wire(&msg);
        let mut took: usize = msg.panels.iter().map(|p| self.env.push_panel(p, &wire)).sum();
        for name in &msg.screens {
            took += st.screens.get(&name.to_lowercase()).map_or(0, Vec::len);
            self.push_screen(&st, name, &wire);
        }
        self.broadcast_messages(&mut st);
        Ok(took)
    }

    /// Take one showing message down (by its id), or every one when no id is given.
    pub fn clear_messages(&self, message_id: Option<&str>) -> Value {
        let mut st = self.lock();
        for msg in st.live.clone() {
            if message_id.is_none_or(|id| msg.id == id) {
                self.end_message(&mut st, &msg);
            }
        }
        self.broadcast_messages(&mut st);
        self.message_state_in(&mut st)
    }

    fn end_message(&self, st: &mut State, msg: &Message) {
        st.live.retain(|m| m.id != msg.id);
        let end = json!({"type": "message_end", "id": msg.id});
        for pid in &msg.panels {
            self.env.push_panel(pid, &end);
        }
        for name in &msg.screens {
            self.push_screen(st, name, &end);
        }
    }

    // ------------------------------------------------------------- streams
    /// A new reader of the stage messages, started with the state.
    pub fn subscribe_messages(&self) -> mpsc::Receiver<Bytes> {
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let mut st = self.lock();
        while st.msg_senders.len() >= MAX_SENDER_STREAMS {
            st.msg_senders.remove(0);
        }
        let first = frame(&self.message_state_in(&mut st));
        let _ = tx.try_send(first);
        st.msg_senders.push(tx);
        rx
    }

    fn broadcast_messages(&self, st: &mut State) {
        let f = frame(&self.message_state_in(st));
        st.msg_senders.retain(|q| offer(q, &f));
        self.admin_changed();
    }

    /// A new sender page (or panel) reading every button's state, started with it.
    pub fn subscribe_sender(&self) -> mpsc::Receiver<Bytes> {
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let mut st = self.lock();
        while st.senders.len() >= MAX_SENDER_STREAMS {
            st.senders.remove(0);
        }
        let first = frame(&self.state_json(&mut st));
        let _ = tx.try_send(first);
        st.senders.push(tx);
        rx
    }

    /// A browser screen's stream, started with what is flashing on it. Refused (429) past 32 distinct screen names.
    pub fn subscribe_screen(&self, name: &str) -> Result<mpsc::Receiver<Bytes>, Refused> {
        let key = name.to_lowercase();
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let cfg = self.settings();
        let mut st = self.lock();
        if !st.screens.contains_key(&key) && st.screens.len() >= MAX_SCREEN_STREAMS {
            return Err(Refused::new(429, "too_many_screens"));
        }
        let subs = st.screens.entry(key.clone()).or_default();
        while subs.len() >= SCREEN_SUBS_PER_NAME {
            subs.remove(0);
        }
        subs.push(tx.clone());
        st.screen_names.insert(key.clone(), name.to_owned());
        self.expire_in(&mut st);
        for b in Self::buttons(&cfg) {
            if let Some(alert) = st.latest.get(b["id"].as_str().unwrap_or("")) {
                if alert.state == "on" && self.targets_of(b).1.iter().any(|s| s.to_lowercase() == key) {
                    let _ = tx.try_send(frame(&self.alert_message(alert, b, &cfg)));
                }
            }
        }
        for msg in &st.live {
            if msg.screens.iter().any(|s| s.to_lowercase() == key) {
                let _ = tx.try_send(frame(&self.message_wire(msg)));
            }
        }
        self.admin_changed();
        Ok(rx)
    }

    /// A screen's reader went away: forget the name when it was the last one. Called when the stream ends.
    pub fn screen_closed(&self, name: &str) {
        let key = name.to_lowercase();
        let mut st = self.lock();
        if let Some(subs) = st.screens.get_mut(&key) {
            subs.retain(|s| !s.is_closed());
            if subs.is_empty() {
                st.screens.remove(&key);
                st.screen_names.remove(&key);
                self.admin_changed();
            }
        }
    }

    fn push_screen(&self, st: &State, name: &str, message: &Value) {
        let f = frame(message);
        // A reader that stopped reading is dropped when it is next closed; here it only misses the frame.
        for q in st.screens.get(&name.to_lowercase()).into_iter().flatten() {
            let _ = offer(q, &f);
        }
    }

    fn broadcast_state(&self, st: &mut State) {
        let f = frame(&self.state_json(st));
        st.senders.retain(|q| offer(q, &f));
        self.admin_changed();
    }

    /// Whether the newest alert on a button is on (for the callers that cancel their own, such as Dante and NDI's stage button).
    pub fn is_on(&self, bid: &str) -> bool {
        self.lock().latest.get(bid).is_some_and(|a| a.state == "on")
    }

    /// The button called `name` (any capitals), if there is one.
    pub fn button_named(&self, name: &str) -> Option<Value> {
        self.settings()["buttons"].as_array().and_then(|b| b.iter().find(|b| b["name"].as_str().is_some_and(|n| same_name(n, name))).cloned())
    }
}

fn is_panel_id_prefix(s: &str) -> bool {
    // Python's `re.match(r"^[0-9a-f]{32}$")` accepts a trailing newline; a panel id never has one.
    is_panel_id(s)
}

fn dedup(items: Vec<String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for i in items {
        if !out.contains(&i) {
            out.push(i);
        }
    }
    out
}

/// Python's `round()` (half to even) of a float, as an integer.
pub fn round(x: f64) -> i64 {
    let r = x.round();
    if (x - x.trunc()).abs() == 0.5 && (r as i64) % 2 != 0 {
        (r - x.signum()) as i64
    } else {
        r as i64
    }
}
