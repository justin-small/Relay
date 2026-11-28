//! Next / Previous / Take control / Release from a caption panel (#80) or the admin page (`pccontrol.py`).
//!
//! `POST /api/panels/{id}/service/{next|previous|take_control|release}` with `{"key": "<the panel's control key>", "type": "<service type id>"}`
//! on the viewer port, like everything a panel sends: no login and plain HTTP. What bounds a stranger on the LAN:
//!
//! * a panel may press only while the operator has set its service mode to "control" or "both" in admin. Every panel starts "off";
//! * it must send the random key Relay gave it when control was turned on;
//! * a quarter second between presses from one panel (the panel itself sends one at a time, so this only stops a flood);
//! * the press goes to a service type Relay follows, and to today's plan for it.
//!
//! The panel never holds the Planning Center token. Relay presses the button as the token's owner, and the answer (the new LIVE state) goes
//! straight to every panel on the Service feed.
//!
//! What Planning Center does, checked against a real account: the action paths are `.../plans/{plan}/live/{action}`, with no LIVE id; Next and
//! Previous are refused (403) unless the token's owner has control; `toggle_control` takes control, and releases it when the owner already has
//! it. So Take control does nothing when Relay's person already has control, and Release does nothing when they don't.

use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex};

use relay_core::caps::{PanelsCap, PANELS};
use relay_core::Host;
use serde_json::{json, Value};
use subtle::ConstantTimeEq;

use crate::client::{Kind, Op, Services};
use crate::feed::ServiceFeed;
use crate::follower::{now, Follower, Listener};
use crate::plan::s;

pub const ACTIONS: [&str; 4] = ["next", "previous", "take_control", "release"];
pub const MIN_GAP_S: f64 = 0.25;
pub const LOG_MAX: usize = 100;
pub const BODY_MAX_BYTES: usize = 512;

/// What a panel is told when a press does not go through: a short code it words itself, and an HTTP status. The reason stays in Relay's log.
pub fn status_of(code: &str) -> u16 {
    match code {
        "not_allowed" | "bad_key" => 403,
        "too_fast" => 429,
        "not_followed" | "no_plan" | "not_controlling" => 409,
        "not_connected" => 503,
        _ => 502,
    }
}

/// How the admin page words a refusal (a panel words the code itself).
pub fn admin_words(code: &str) -> &'static str {
    match code {
        "not_followed" => "Relay is not following that service type.",
        "no_plan" => "There is no plan today for that service type.",
        "not_controlling" => "Take control first: Planning Center lets only the person running LIVE move it.",
        "not_connected" => "Connect to Planning Center first.",
        "unreachable" => "Planning Center did not answer. Try again.",
        "refused" => "Planning Center refused that. The log has the reason.",
        _ => "",
    }
}

/// A press that did not go through, as one of the fixed codes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Refused(pub &'static str);

impl Refused {
    pub fn status(self) -> u16 {
        status_of(self.0)
    }
}

pub struct Control {
    host: Host,
    follower: Arc<Follower>,
    feed: Arc<ServiceFeed>,
    actions: Mutex<VecDeque<Value>>,
    last: Mutex<BTreeMap<String, f64>>,
    me: Mutex<Option<String>>,
    lock: tokio::sync::Mutex<()>,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
    /// Called after each press, for the admin status stream.
    on_action: Mutex<Option<Listener>>,
}

impl Control {
    pub fn new(host: Host, follower: Arc<Follower>, feed: Arc<ServiceFeed>) -> Arc<Self> {
        let started = std::time::Instant::now();
        Self::with_clock(host, follower, feed, Box::new(move || started.elapsed().as_secs_f64()))
    }

    /// With a clock of the caller's: a test moves it instead of sleeping.
    pub fn with_clock(host: Host, follower: Arc<Follower>, feed: Arc<ServiceFeed>, clock: Box<dyn Fn() -> f64 + Send + Sync>) -> Arc<Self> {
        Arc::new(Self {
            host,
            follower,
            feed,
            actions: Mutex::new(VecDeque::new()),
            last: Mutex::new(BTreeMap::new()),
            me: Mutex::new(None),
            lock: tokio::sync::Mutex::new(()),
            clock,
            on_action: Mutex::new(None),
        })
    }

    pub fn set_on_action(&self, listener: Option<Listener>) {
        *self.on_action.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = listener;
    }

    /// The panels' registry, while the server owns it; `None` otherwise.
    fn panels(&self) -> Option<Arc<PanelsCap>> {
        self.host.using::<PanelsCap>(PANELS)
    }

    /// Whether `id` names a panel the registry knows.
    pub fn knows_panel(&self, id: &str) -> bool {
        self.panels().is_some_and(|p| p.0.known(id))
    }

    // -- checks ---------------------------------------------------------------------------------------------------------------------
    /// Refused unless this panel may control and sent its key. Not logged: anyone on the LAN can get this far, and must not be able to flood
    /// the operator's list.
    fn authorize(&self, pid: &str, body: &Value) -> Result<(), Refused> {
        let want = self.panels().and_then(|p| p.0.control_key(pid)).ok_or(Refused("not_allowed"))?;
        match body.get("key").and_then(Value::as_str) {
            Some(key) if bool::from(key.as_bytes().ct_eq(want.as_bytes())) => Ok(()),
            _ => Err(Refused("bad_key")),
        }
    }

    /// The service type to act on, or a refusal.
    fn target(&self, pid: &str, body: &Value) -> Result<String, Refused> {
        let at = (self.clock)();
        {
            let mut last = self.last.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            if at - last.get(pid).copied().unwrap_or(-1e9) < MIN_GAP_S {
                return Err(Refused("too_fast"));
            }
            last.insert(pid.to_owned(), at);
        }
        let offered: Vec<String> = self.feed.offered().iter().map(|o| s(o, "service_type")).collect();
        if offered.is_empty() && self.follower.client().is_none() {
            return Err(Refused("not_connected"));
        }
        let configured = self.panels().map(|p| s(&p.0.service(pid), "service_type")).unwrap_or_default();
        let mut st = if configured.is_empty() { s(body, "type") } else { configured };
        if st.is_empty() && offered.len() == 1 {
            st = offered[0].clone();
        }
        if !offered.contains(&st) {
            return Err(Refused("not_followed"));
        }
        match self.follower.track(&st) {
            Some(t) if t.plan.is_some() => Ok(st),
            _ => Err(Refused("no_plan")),
        }
    }

    async fn my_id(&self, client: &dyn Services) -> Result<String, crate::client::PcError> {
        if let Some(id) = self.follower.me_id().filter(|i| !i.is_empty()) {
            return Ok(id);
        }
        let cached = self.me.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone();
        if let Some(c) = cached {
            return Ok(c);
        }
        let id = s(&json!({"id": client.call(Op::Me).await?}), "id");
        *self.me.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(id.clone());
        Ok(id)
    }

    // -- the press ------------------------------------------------------------------------------------------------------------------
    /// Carry out one press. Returns the panel's Service feed message with the new state; a refusal otherwise. Every attempt that gets past the
    /// key is logged.
    pub async fn press(&self, pid: &str, action: &str, body: &Value) -> Result<Value, Refused> {
        self.authorize(pid, body)?;
        let mut st: String = s(body, "type").chars().take(20).collect();
        let result = async {
            st = self.target(pid, body)?;
            let _turn = self.lock.lock().await; // one press at a time across panels: no interleaved toggles
            self.press_once(&st, action).await
        }
        .await;
        match result {
            Ok(did) => {
                self.record(pid, &st, action, if did { "ok" } else { "unchanged" }, None);
                Ok(self.feed.payload(&st, None))
            }
            Err(r) => {
                self.record(pid, &st, action, r.0, None);
                Err(r)
            }
        }
    }

    /// The same press from the admin page or /screen's operator (#148): the signed-in user is already checked by the route, so no panel key or
    /// gap. Logged with the panels' presses under the user's name.
    pub async fn press_admin(&self, st: &str, action: &str, who: &str) -> Result<bool, Refused> {
        let offered: Vec<String> = self.feed.offered().iter().map(|o| s(o, "service_type")).collect();
        let mut st = st.to_owned();
        if offered.is_empty() && self.follower.client().is_none() {
            self.record("admin", &st, action, "not_connected", Some(who));
            return Err(Refused("not_connected"));
        }
        if st.is_empty() && offered.len() == 1 {
            st = offered[0].clone();
        }
        let result = async {
            if !offered.contains(&st) {
                return Err(Refused("not_followed"));
            }
            if !self.follower.track(&st).is_some_and(|t| t.plan.is_some()) {
                return Err(Refused("no_plan"));
            }
            let _turn = self.lock.lock().await;
            self.press_once(&st, action).await
        }
        .await;
        match result {
            Ok(did) => {
                self.record("admin", &st, action, if did { "ok" } else { "unchanged" }, Some(who));
                Ok(did)
            }
            Err(r) => {
                self.record("admin", &st, action, r.0, Some(who));
                Err(r)
            }
        }
    }

    async fn press_once(&self, st: &str, action: &str) -> Result<bool, Refused> {
        let client = self.follower.client().ok_or(Refused("not_connected"))?;
        let plan = self.follower.track(st).and_then(|t| t.plan).map(|p| s(&p, "id")).ok_or(Refused("no_plan"))?;
        let st_owned = st.to_owned();
        let result: Result<Option<Value>, crate::client::PcError> = async {
            if matches!(action, "take_control" | "release") {
                let live = client.call(Op::Live { st: st_owned.clone(), plan: plan.clone() }).await?;
                let me = self.my_id(client.as_ref()).await?;
                let mine = live["controller"].is_object() && s(&live["controller"], "id") == me;
                if mine == (action == "take_control") {
                    self.follower.apply_live(st, live, None);
                    return Ok(None); // already as asked: toggling would undo it
                }
                Ok(Some(client.call(Op::LiveAction { st: st_owned.clone(), plan: plan.clone(), action: "toggle_control" }).await?))
            } else {
                let name = if action == "next" { "go_to_next_item" } else { "go_to_previous_item" };
                Ok(Some(client.call(Op::LiveAction { st: st_owned.clone(), plan: plan.clone(), action: name }).await?))
            }
        }
        .await;
        match result {
            Ok(Some(live)) => {
                self.follower.apply_live(st, live, None);
                Ok(true)
            }
            Ok(None) => Ok(false),
            Err(exc) if exc.kind == Kind::Network => Err(Refused("unreachable")),
            Err(exc) if exc.status == Some(403) && matches!(action, "next" | "previous") => Err(Refused("not_controlling")),
            Err(exc) => {
                tracing::warn!("Planning Center refused {action} for service type {st}: {}", exc.detail());
                Err(Refused("refused"))
            }
        }
    }

    fn record(&self, pid: &str, st: &str, action: &str, result: &str, name: Option<&str>) {
        let name = name.map(str::to_owned).or_else(|| self.panels().map(|p| p.0.display_name(pid))).unwrap_or_default();
        let entry = json!({"at": now(), "panel": pid, "panel_name": name, "service_type": st, "action": action, "result": result});
        {
            let mut log = self.actions.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            log.push_front(entry);
            log.truncate(LOG_MAX);
        }
        tracing::info!("Panel {name} ({}): {action} on service type {st} -> {result}", pid.chars().take(8).collect::<String>());
        if let Some(l) = self.on_action.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone() {
            l();
        }
    }

    pub fn recent(&self) -> Vec<Value> {
        self.actions.lock().unwrap_or_else(std::sync::PoisonError::into_inner).iter().cloned().collect()
    }
}
