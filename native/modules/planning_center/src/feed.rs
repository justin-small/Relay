//! The Service feed: today's plan and its LIVE state, for caption panels (`servicefeed.py`).
//!
//! `GET /stream?stream=service&type=<service_type_id>` on the viewer port with no login, like the caption feeds. Each `service` message carries
//! the whole current state, so a panel that reconnects needs nothing else. A new one is sent only when something a panel shows changes;
//! between them a small `service_tick` every heartbeat carries Relay's clock and when Planning Center last answered, so the panel can count
//! timers down on its own and say how fresh they are.
//!
//! Timers are sent as they stood at `server_time`. The panel works them out for later times itself:
//! `item_left_s(t) = item_left_s - (t - server_time)`, and `service_over_s(t) = service_over_s + max(0, (t - server_time) - max(0, item_left_s))`.
//!
//! Item notes can hold private things (names, pastoral notes), so they are sent only while the operator has "Show item notes on panels" on.
//! The Planning Center token never reaches this feed.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use bytes::Bytes;
use relay_core::caps::{FeedExtra, FEED_EXTRA};
use relay_core::Host;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::follower::{now, Follower, Listener};
use crate::plan::s;

pub const QUEUE_MAX: usize = 32;
const ITEM_TYPES: [&str; 4] = ["song", "header", "media", "item"];
/// Fields that move with the clock alone: left out of the "has it changed" test.
const CLOCK_FIELDS: [&str; 4] = ["server_time", "updated_at", "item_left_s", "service_over_s"];

/// `data: <json>\n\n`.
pub fn frame(msg: &Value) -> Bytes {
    Bytes::from(format!("data: {msg}\n\n"))
}

struct Sub {
    id: u64,
    tx: mpsc::Sender<Bytes>,
}

#[derive(Default)]
struct Inner {
    subs: BTreeMap<String, Vec<Sub>>,
    sigs: BTreeMap<String, String>,
    offered: Vec<Value>,
    next: u64,
}

pub struct ServiceFeed {
    host: Host,
    follower: Arc<Follower>,
    inner: Mutex<Inner>,
    /// Called when the list of service feeds a panel can pick changes (the server re-sends the `targets` message).
    on_offered_change: Mutex<Option<Listener>>,
}

impl ServiceFeed {
    pub fn new(host: Host, follower: Arc<Follower>) -> Arc<Self> {
        let feed = Arc::new(Self { host, follower: Arc::clone(&follower), inner: Mutex::new(Inner::default()), on_offered_change: Mutex::new(None) });
        let weak = Arc::downgrade(&feed);
        follower.on_change(Arc::new(move || {
            if let Some(f) = weak.upgrade() {
                f.refresh();
            }
        }));
        feed
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn set_on_offered_change(&self, listener: Option<Listener>) {
        *self.on_offered_change.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = listener;
    }

    fn connected(&self) -> bool {
        let st = self.follower.settings();
        !s(&st, "app_id").is_empty() && !s(&st, "secret").is_empty()
    }

    pub fn show_notes(&self) -> bool {
        self.follower.settings()["show_notes"] == Value::Bool(true)
    }

    /// One entry per followed service type, for `/api/targets` and `targets`.
    pub fn offered(&self) -> Vec<Value> {
        if !self.connected() {
            return Vec::new();
        }
        self.follower.followed().iter().map(|st| json!({"service_type": st, "label": self.follower.label(st)})).collect()
    }

    /// The message for one service type.
    pub fn payload(&self, st: &str, at: Option<f64>) -> Value {
        let at = at.unwrap_or_else(now);
        let state = if self.connected() { self.follower.state(st, Some(at)) } else { None };
        let Some(state) = state else {
            // Not (or no longer) followed: the panel says so and keeps listening.
            return json!({"type": "service", "service_type": st, "followed": false, "server_time": at});
        };
        let notes = self.show_notes();
        let plan = &state["plan"];
        let mut items = Vec::new();
        for i in state["items"].as_array().into_iter().flatten() {
            let kind = s(i, "type");
            let mut out = json!({"id": i["id"], "title": i["title"], "type": if ITEM_TYPES.contains(&kind.as_str()) { kind.as_str() } else { "item" },
                                 "length": i["length"], "position": i["position"]});
            if kind == "song" {
                out["key"] = i["key"].clone();
                out["arrangement"] = i["arrangement"].clone();
                out["sequence"] = i["arrangement_sequence"].clone();
            }
            if notes && i["notes"].as_array().is_some_and(|n| !n.is_empty()) {
                out["notes"] = Value::Array(i["notes"].as_array().into_iter().flatten().map(|n| json!({"category": n["category"], "content": n["content"]})).collect());
            }
            items.push(out);
        }
        let t = &state["timers"];
        // Extra fields for a service type's message (which plan item ProPresenter has up), while that module is on.
        let extra = self.host.using::<FeedExtra>(FEED_EXTRA).and_then(|f| (f.0)(st)).unwrap_or_else(|| json!({}));
        let mut msg = extra.as_object().cloned().unwrap_or_default();
        let controller = &state["controller"];
        let me = self.follower.me_id();
        let fields = json!({
            "type": "service",
            "service_type": st,
            "followed": true,
            "name": state["name"],
            "plan": if plan.is_object() { json!({"id": plan["id"], "title": plan["title"], "series_title": plan["series_title"], "dates": plan["dates"]}) } else { Value::Null },
            "items": items,
            "notes": notes,
            "current_item": t["current_item"],
            "next_item": t["next_item"],
            "item_started_at": t["item_started_at"],
            "item_length_s": t["item_length_s"],
            "item_left_s": t["item_left_s"],
            "service_started_at": t["service_started_at"],
            "service_over_s": t["service_over_s"],
            "live": state["live"],
            "controller": controller["name"],
            // Whether the controller is the token's owner, so the panel offers Release rather than Take control.
            "relay_controls": controller.is_object() && me.as_deref().is_some_and(|m| !m.is_empty() && controller["id"] == m),
            // The reason stays in admin; a panel only says Planning Center is unreachable.
            "reachable": state["reachable"] == Value::Bool(true) && state["error"].is_null(),
            "updated_at": state["updated_at"],
            "server_time": at,
        });
        if let Some(o) = fields.as_object() {
            for (k, v) in o {
                msg.insert(k.clone(), v.clone());
            }
        }
        Value::Object(msg)
    }

    pub fn tick_payload(&self, st: &str, at: Option<f64>) -> Value {
        let at = at.unwrap_or_else(now);
        json!({"type": "service_tick", "service_type": st, "server_time": at, "updated_at": self.follower.track(st).and_then(|t| t.updated_at)})
    }

    fn signature(msg: &Value) -> String {
        let mut m = msg.clone();
        if let Some(o) = m.as_object_mut() {
            o.retain(|k, _| !CLOCK_FIELDS.contains(&k.as_str()));
        }
        m.to_string()
    }

    /// A new listener for one service type, already holding the current state. `(id to unsubscribe with, frames)`.
    pub fn subscribe(&self, st: &str) -> (u64, mpsc::Receiver<Bytes>) {
        let (tx, rx) = mpsc::channel(QUEUE_MAX);
        let msg = self.payload(st, None);
        let mut inner = self.inner();
        inner.sigs.entry(st.to_owned()).or_insert_with(|| Self::signature(&msg));
        let _ = tx.try_send(frame(&msg));
        inner.next += 1;
        let id = inner.next;
        inner.subs.entry(st.to_owned()).or_default().push(Sub { id, tx });
        (id, rx)
    }

    pub fn unsubscribe(&self, st: &str, id: u64) {
        let mut inner = self.inner();
        if let Some(subs) = inner.subs.get_mut(st) {
            subs.retain(|s| s.id != id);
            if subs.is_empty() {
                inner.subs.remove(st);
                inner.sigs.remove(st);
            }
        }
    }

    /// Re-send each watched service type whose state changed. Called by the follower when it notices a change, and when the notes switch moves.
    pub fn refresh(&self) {
        let offered = self.offered();
        let changed = {
            let mut inner = self.inner();
            if inner.offered == offered {
                false
            } else {
                inner.offered = offered;
                true
            }
        };
        if changed {
            let listener = self.on_offered_change.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone();
            if let Some(l) = listener {
                l();
            }
        }
        let watched: Vec<String> = self.inner().subs.keys().cloned().collect();
        for st in watched {
            let msg = self.payload(&st, None);
            let sig = Self::signature(&msg);
            let mut inner = self.inner();
            if inner.sigs.get(&st) == Some(&sig) {
                continue;
            }
            inner.sigs.insert(st.clone(), sig);
            let data = frame(&msg);
            if let Some(subs) = inner.subs.get_mut(&st) {
                // A panel that stopped reading is dropped: it reconnects for a fresh state.
                subs.retain(|sub| sub.tx.try_send(data.clone()).is_ok());
            }
        }
    }

    pub fn viewer_count(&self) -> usize {
        self.inner().subs.values().map(Vec::len).sum()
    }
}
