//! Follows today's plan for each chosen Planning Center service type and keeps its LIVE state current (`pclive.py`).
//!
//! A background task does two things per followed service type:
//!
//! * Finds today's plan. Planning Center has no "today" filter, so Relay lists the next plans with their times and picks the one with a
//!   `service` time today in the organisation's time zone ([`plan::pick_plan`]). Re-checked every few minutes, when the local date turns over,
//!   and when the operator picks a different plan for today in admin. A service still running at midnight keeps its plan until it ends.
//! * Polls the plan's LIVE state. Fast (about every 2 s) only from an hour before a service time until shortly after it, or while someone is
//!   running LIVE; every few minutes otherwise. The fast rate is stretched when several service types are hot at once so that all of them
//!   together stay under half of the rate limit Planning Center last reported.
//!
//! Planning Center has no confirmed push for LIVE, so this polls. Nothing is sent until a token is saved and a service type is followed.
//! The state is read by the admin page and by the panels' Service feed ([`crate::feed`]), which registers as a listener.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use jiff::civil::Date;
use relay_core::Host;
use serde_json::{json, Value};

use crate::client::{Kind, Op, PcError, Rate, Services};
use crate::plan::{self, f, s, Zone};
use crate::settings;
use crate::KEY;

pub const TICK_S: f64 = 0.5;
pub const FAST_S: f64 = 2.0;
pub const SLOW_S: f64 = 180.0;
pub const PLAN_CHECK_S: f64 = 300.0;
pub const PLAN_CHECK_FAST_S: f64 = 30.0;
pub const CATALOG_CHECK_S: f64 = 1800.0;
pub const TEAM_CHECK_S: f64 = 120.0;
pub const ERROR_RETRY_S: f64 = 20.0;
pub const AUTH_RETRY_S: f64 = 300.0;
pub const ZONE_RETRY_S: f64 = 300.0;
pub const RATE_SHARE: f64 = 0.5;
/// Plans are listed from this long before local midnight.
pub const LOOKBACK_S: f64 = 12.0 * 3600.0;

/// Seconds between LIVE polls of one service type while `hot` of them are polling fast, keeping their total under [`RATE_SHARE`] of the
/// reported limit.
pub fn fast_interval(hot: usize, rate: &Rate) -> f64 {
    let limit = rate.limit.filter(|l| *l != 0).unwrap_or(100) as f64;
    let period = rate.period.filter(|p| *p != 0).unwrap_or(20) as f64;
    FAST_S.max(hot.max(1) as f64 * period / (limit * RATE_SHARE))
}

/// One followed service type.
#[derive(Clone, Debug, Default)]
pub struct Track {
    pub st: String,
    /// The last listing, for the admin picker.
    pub plans: Vec<Value>,
    pub plan: Option<Value>,
    /// The plan id the operator picked for today.
    pub override_plan: Option<String>,
    pub items: Vec<Value>,
    /// Who is scheduled on the plan (#148).
    pub team: Vec<Value>,
    /// Positions still to fill.
    pub needed: Vec<Value>,
    pub team_due: f64,
    /// Every team with its positions and ids (#525).
    pub catalog: Vec<Value>,
    pub catalog_due: f64,
    pub live: Option<Value>,
    pub day: Option<Date>,
    pub plan_due: f64,
    pub live_due: f64,
    pub fast: bool,
    pub error: Option<String>,
    pub reachable: bool,
    /// Last good LIVE read.
    pub updated_at: Option<f64>,
    /// Earliest item start seen, per plan time.
    pub service_start: BTreeMap<String, f64>,
    /// The one-off refresh already made for this item.
    pub asked: Option<(String, String)>,
}

impl Track {
    pub fn new(st: &str) -> Self {
        Self { st: st.to_owned(), reachable: true, ..Self::default() }
    }

    fn plan_id(&self) -> Option<String> {
        self.plan.as_ref().map(|p| s(p, "id"))
    }
}

#[derive(Default)]
struct State {
    tracks: Vec<Track>,
    rev: u64,
    zone: Option<Zone>,
    names: BTreeMap<String, String>,
    client: Option<Arc<dyn Services>>,
    token: Option<(String, String)>,
    zone_due: f64,
    /// The token owner's Planning Center person id: LIVE's controller is compared with it, so a panel can tell whether Relay itself has
    /// control (Take control or Release). Read once per token.
    me_id: Option<String>,
    me_due: f64,
    names_for: Option<Vec<String>>,
    sig: String,
}

/// Builds the client for the saved token (`planning_center.client_from`); a test hands one that answers from a table.
pub type MakeClient = Arc<dyn Fn(&Value) -> Option<Arc<dyn Services>> + Send + Sync>;

pub type Listener = Arc<dyn Fn() + Send + Sync>;

pub struct Follower {
    host: Host,
    make_client: MakeClient,
    state: Mutex<State>,
    listeners: Mutex<Vec<Listener>>,
    gate: tokio::sync::Mutex<()>,
}

impl Follower {
    pub fn new(host: Host, make_client: MakeClient) -> Arc<Self> {
        Arc::new(Self { host, make_client, state: Mutex::new(State::default()), listeners: Mutex::new(Vec::new()), gate: tokio::sync::Mutex::new(()) })
    }

    fn st(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    pub fn on_change(&self, listener: Listener) {
        self.listeners.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(listener);
    }

    /// The saved settings, cleaned.
    pub fn settings(&self) -> Value {
        settings::clean_stored(&self.host.settings(KEY).unwrap_or_else(|| json!({})))
    }

    /// Relay's own zone, used while Planning Center's is unknown.
    pub fn fallback_zone(&self) -> Zone {
        let name = self.host.settings("timezone").and_then(|v| v.as_str().map(str::to_owned)).unwrap_or_default();
        Zone::named(relay_core::schedules::clean_zone(&name)).unwrap_or_else(Zone::utc)
    }

    /// The zone plan days are read in: Planning Center's, else Relay's.
    pub fn zone(&self) -> Zone {
        let zone = self.st().zone.clone();
        zone.unwrap_or_else(|| self.fallback_zone())
    }

    pub fn zone_known(&self) -> bool {
        self.st().zone.is_some()
    }

    pub fn me_id(&self) -> Option<String> {
        self.st().me_id.clone()
    }

    pub fn rev(&self) -> u64 {
        self.st().rev
    }

    pub fn names(&self) -> BTreeMap<String, String> {
        self.st().names.clone()
    }

    /// The followed service types' names, in the order they are followed: the name Planning Center gave, else `Service type <id>`.
    pub fn label(&self, st: &str) -> String {
        self.st().names.get(st).filter(|n| !n.is_empty()).cloned().unwrap_or_else(|| format!("Service type {st}"))
    }

    pub fn followed(&self) -> Vec<String> {
        self.st().tracks.iter().map(|t| t.st.clone()).collect()
    }

    pub fn track(&self, st: &str) -> Option<Track> {
        self.st().tracks.iter().find(|t| t.st == st).cloned()
    }

    pub fn tracks(&self) -> Vec<Track> {
        self.st().tracks.clone()
    }

    fn upd<R>(&self, st: &str, change: impl FnOnce(&mut Track) -> R) -> Option<R> {
        self.st().tracks.iter_mut().find(|t| t.st == st).map(change)
    }

    /// The client for the saved token, shared with the poll so both count against the same rate-limit reading. `None` until the first tick.
    pub fn client(&self) -> Option<Arc<dyn Services>> {
        self.st().client.clone()
    }

    /// Seed a track a test or the sync wants to hold (not how the loop makes one).
    pub fn put_track(&self, track: Track) {
        let mut st = self.st();
        match st.tracks.iter_mut().find(|t| t.st == track.st) {
            Some(slot) => *slot = track,
            None => st.tracks.push(track),
        }
    }

    pub fn put_name(&self, st: &str, name: &str) {
        self.st().names.insert(st.to_owned(), name.to_owned());
    }

    /// Epoch of the next call this follower wants to make (for tests and pacing).
    pub fn next_due(&self) -> f64 {
        self.st().tracks.iter().map(|t| if t.plan.is_some() { t.plan_due.min(t.live_due) } else { t.plan_due }).fold(f64::INFINITY, f64::min)
    }

    // -- the loop ------------------------------------------------------------------------------------------------------------------
    /// Run [`Follower::tick`] twice a second until shutdown.
    pub async fn run(self: Arc<Self>) {
        let shutdown = self.host.shutdown.clone();
        loop {
            self.tick(now()).await;
            tokio::select! {
                () = shutdown.wait() => return,
                () = tokio::time::sleep(std::time::Duration::from_secs_f64(TICK_S)) => {}
            }
        }
    }

    /// Re-evaluate straight away, after the token, the followed service types or the operator's plan pick changes.
    pub async fn poke(&self) {
        for t in &mut self.st().tracks {
            t.plan_due = 0.0;
        }
        self.tick(now()).await;
    }

    pub async fn tick(&self, now: f64) {
        let _turn = self.gate.lock().await;
        self.tick_locked(now).await;
    }

    async fn tick_locked(&self, now: f64) {
        let stored = self.settings();
        let ids: Vec<String> = stored["service_types"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_owned)).collect();
        let token = (s(&stored, "app_id"), s(&stored, "secret"));
        let has_token = !token.0.is_empty() && !token.1.is_empty();
        if !(has_token && !ids.is_empty()) {
            {
                let mut st = self.st();
                st.tracks.clear();
                if !has_token {
                    st.client = None;
                    st.token = None;
                    st.zone = None;
                    st.names_for = None;
                    st.me_id = None;
                    st.names.clear();
                    st.zone_due = 0.0;
                    st.me_due = 0.0;
                }
            }
            self.note_change();
            return;
        }
        {
            let mut st = self.st();
            if st.token.as_ref() != Some(&token) {
                st.client = (self.make_client)(&stored);
                st.token = Some(token);
                st.tracks.clear();
                st.zone = None;
                st.zone_due = 0.0;
                st.names_for = None;
                st.me_id = None;
                st.me_due = 0.0;
            }
            st.tracks.retain(|t| ids.contains(&t.st));
            for id in &ids {
                if !st.tracks.iter().any(|t| &t.st == id) {
                    st.tracks.push(Track::new(id));
                }
            }
        }
        let Some(client) = self.client() else { return };

        let (zone_unset, zone_due, me_unset, me_due, names_stale) = {
            let st = self.st();
            (st.zone.is_none(), st.zone_due, st.me_id.is_none(), st.me_due, st.names_for.as_ref() != Some(&ids))
        };
        if zone_unset && now >= zone_due {
            self.read_zone(client.as_ref(), now).await;
        }
        if me_unset && now >= me_due {
            match client.call(Op::Me).await {
                Ok(v) => self.st().me_id = v.as_str().filter(|t| !t.is_empty()).map(str::to_owned),
                Err(_) => self.st().me_due = now + ERROR_RETRY_S,
            }
        }
        if names_stale {
            self.read_names(client.as_ref(), &ids).await;
        }
        let zone = self.zone();
        let day = zone.day_of(now);

        for id in &ids {
            let ov = &stored["plan_overrides"][id.as_str()];
            let override_plan = (ov.is_object() && s(ov, "day") == plan::iso_day(day)).then(|| s(ov, "plan"));
            let Some(tr) = self.track(id) else { continue };
            if now >= tr.plan_due || tr.day != Some(day) || tr.override_plan != override_plan {
                let r = self.check_plan(client.as_ref(), id, now, &zone, day, override_plan.clone()).await;
                self.guard(id, now, true, r);
            }
            let due = self.track(id).is_some_and(|t| t.plan.is_some() && now >= t.live_due);
            if due {
                let r = self.poll_live(client.as_ref(), id, now).await;
                self.guard(id, now, false, r);
            }
        }
        self.note_change();
    }

    fn guard(&self, st: &str, ts: f64, plan_due: bool, result: Result<(), PcError>) {
        match result {
            Ok(()) => {
                self.upd(st, |t| {
                    t.error = None;
                    t.reachable = true;
                });
            }
            Err(exc) => {
                let wait = exc.retry_after.unwrap_or(if exc.kind == Kind::Auth { AUTH_RETRY_S } else { ERROR_RETRY_S });
                let text = exc.operator_message().to_owned();
                let first = self.upd(st, |t| t.error.as_deref() != Some(text.as_str())).unwrap_or(false);
                if first {
                    tracing::warn!("Planning Center, service type {st}: {}", exc.detail());
                }
                self.upd(st, |t| {
                    t.error = Some(text);
                    t.reachable = exc.kind != Kind::Network;
                    if plan_due { t.plan_due = ts + wait } else { t.live_due = ts + wait }
                });
            }
        }
    }

    async fn read_zone(&self, client: &dyn Services, ts: f64) {
        match client.call(Op::TimeZone).await {
            Err(exc) => {
                tracing::warn!("Planning Center time zone not read ({:?}); using {} for now", exc.kind, self.fallback_zone().name);
                self.st().zone_due = ts + ERROR_RETRY_S;
            }
            Ok(name) => {
                let name = name.as_str().unwrap_or("").to_owned();
                let zone = Zone::for_name(&name);
                let mut st = self.st();
                st.zone = zone.clone();
                if zone.is_none() {
                    // Asked and answered, but not a zone Relay can place: say so once and carry on in Relay's own zone rather than guess silently.
                    tracing::warn!("Planning Center time zone {name:?} is not one Relay knows; using {} for now", self.fallback_zone().name);
                    st.zone_due = ts + ZONE_RETRY_S;
                }
            }
        }
    }

    async fn read_names(&self, client: &dyn Services, ids: &[String]) {
        // Names are a nicety; the ids still work.
        if let Ok(types) = client.call(Op::ServiceTypes).await {
            let names = types.as_array().into_iter().flatten().map(|t| (s(t, "id"), s(t, "name"))).collect();
            self.st().names = names;
        }
        self.st().names_for = Some(ids.to_vec());
    }

    async fn check_plan(&self, client: &dyn Services, st: &str, now: f64, zone: &Zone, day: Date, override_plan: Option<String>) -> Result<(), PcError> {
        // Set first: a failure must not retry every tick.
        let fast = self.track(st).is_some_and(|t| t.fast);
        self.upd(st, |t| t.plan_due = now + if fast { PLAN_CHECK_FAST_S } else { PLAN_CHECK_S });
        let midnight = zone.midnight(day);
        let after = (midnight - LOOKBACK_S) as i64;
        let plans = client.call(Op::PlansFrom { st: st.to_owned(), after }).await?;
        let plans: Vec<Value> = plans.as_array().cloned().unwrap_or_default();
        self.upd(st, |t| {
            t.plans = plans.clone();
            t.day = Some(day);
            t.override_plan = override_plan.clone();
        });
        let by_id = |id: &str| plans.iter().find(|p| p["id"] == id).cloned();
        let current = self.track(st).ok_or_else(|| PcError::new(Kind::Http, "gone"))?;
        let chosen: Option<Value> = if let Some(ov) = &override_plan {
            match by_id(ov) {
                Some(p) => Some(p),
                None => Some(client.call(Op::Plan { st: st.to_owned(), plan: ov.clone() }).await?),
            }
        } else {
            let mut chosen = plan::pick_plan(&plans, now, zone);
            if let Some(cur) = &current.plan {
                if plan::running(current.live.as_ref(), now) && chosen.as_ref().is_none_or(|c| c["id"] != cur["id"]) {
                    // Still being run (past midnight, or over its planned end): do not pull the plan out from under the service.
                    chosen = by_id(&s(cur, "id")).or_else(|| Some(cur.clone()));
                }
            }
            chosen
        };
        let chosen_id = chosen.as_ref().map(|c| s(c, "id"));
        if chosen_id != current.plan_id() {
            match &chosen {
                Some(c) => tracing::info!("Planning Center, service type {st}: following plan {} ({})", s(c, "id"), if s(c, "dates").is_empty() { "no date".to_owned() } else { s(c, "dates") }),
                None => tracing::info!("Planning Center, service type {st}: following no plan today"),
            }
            self.upd(st, |t| {
                t.items.clear();
                t.live = None;
                t.live_due = 0.0;
                t.service_start.clear();
                t.asked = None;
                t.team.clear();
                t.needed.clear();
                t.team_due = 0.0;
                t.fast = false;
            });
        }
        self.upd(st, |t| t.plan = chosen.clone());
        let catalog_due = self.track(st).map_or(0.0, |t| t.catalog_due);
        if now >= catalog_due {
            self.upd(st, |t| t.catalog_due = now + CATALOG_CHECK_S);
            match client.call(Op::Teams { st: st.to_owned() }).await {
                Ok(v) => {
                    let teams = v.as_array().cloned().unwrap_or_default();
                    self.upd(st, |t| t.catalog = teams);
                }
                Err(exc) if exc.kind == Kind::Network => return Err(exc),
                Err(exc) => {
                    self.upd(st, |t| t.catalog_due = now + ERROR_RETRY_S * 6.0);
                    tracing::info!("Planning Center, service type {st}: teams not read ({:?})", exc.kind);
                }
            }
        }
        let Some(chosen) = chosen else { return Ok(()) };
        let pid = s(&chosen, "id");
        let items = client.call(Op::Items { st: st.to_owned(), plan: pid.clone() }).await?;
        self.upd(st, |t| t.items = items.as_array().cloned().unwrap_or_default());
        let team_due = self.track(st).map_or(0.0, |t| t.team_due);
        if now >= team_due {
            self.upd(st, |t| t.team_due = now + TEAM_CHECK_S);
            let read = async {
                let team = client.call(Op::TeamMembers { st: st.to_owned(), plan: pid.clone() }).await?;
                let needed = client.call(Op::NeededPositions { st: st.to_owned(), plan: pid.clone() }).await?;
                Ok::<_, PcError>((team, needed))
            };
            match read.await {
                Ok((team, needed)) => {
                    self.upd(st, |t| {
                        t.team = team.as_array().cloned().unwrap_or_default();
                        t.needed = needed.as_array().cloned().unwrap_or_default();
                        tag_positions(&mut t.team, &t.catalog);
                        tag_positions(&mut t.needed, &t.catalog);
                    });
                }
                // The rundown's people are extra: a token that may not see them leaves the plan working.
                Err(exc) if exc.kind == Kind::Network => return Err(exc),
                Err(exc) => tracing::info!("Planning Center, service type {st}: team members not read ({:?})", exc.kind),
            }
        }
        Ok(())
    }

    async fn poll_live(&self, client: &dyn Services, st: &str, ts: f64) -> Result<(), PcError> {
        let Some(track) = self.track(st) else { return Ok(()) };
        let Some(mut plan) = track.plan.clone() else { return Ok(()) };
        // Set first, as above.
        self.upd(st, |t| t.live_due = ts + if t.fast { FAST_S } else { SLOW_S });
        let pid = s(&plan, "id");
        let live = client.call(Op::Live { st: st.to_owned(), plan: pid.clone() }).await?;
        self.upd(st, |t| {
            t.live = Some(live.clone());
            t.updated_at = Some(ts);
        });
        let cur = live["current"].clone();
        let mut asked_items: Option<Vec<Value>> = None;
        if cur.is_object() {
            if let (Some(started), Some(pt)) = (f(&cur, "live_start_at").filter(|v| *v != 0.0), Some(s(&cur, "plan_time")).filter(|t| !t.is_empty())) {
                self.upd(st, |t| {
                    let seen = t.service_start.get(&pt).copied();
                    t.service_start.insert(pt, seen.filter(|v| *v != 0.0).unwrap_or(started).min(started));
                });
            }
            let key = (s(&cur, "id"), s(&cur, "item"));
            if track.asked.as_ref() != Some(&key) {
                // A new current item: once, fill in what the last listing lacks -- the service's live_starts_at, or an item added since.
                self.upd(st, |t| t.asked = Some(key));
                let pt = plan["times"].as_array().and_then(|ts| ts.iter().find(|t| t["id"] == cur["plan_time"]));
                if pt.is_none_or(|p| f(p, "live_starts_at").is_none_or(|v| v == 0.0)) {
                    plan = client.call(Op::Plan { st: st.to_owned(), plan: pid.clone() }).await?;
                    self.upd(st, |t| t.plan = Some(plan.clone()));
                }
                if !track.items.iter().any(|i| i["id"] == cur["item"]) {
                    let items = client.call(Op::Items { st: st.to_owned(), plan: pid.clone() }).await?;
                    asked_items = Some(items.as_array().cloned().unwrap_or_default());
                }
            }
        }
        if let Some(items) = asked_items {
            self.upd(st, |t| t.items = items);
        }
        let fast = plan::near_service(Some(&plan), ts) || plan::running(Some(&live), ts);
        let hot = {
            self.upd(st, |t| {
                t.fast = fast;
                if fast {
                    t.plan_due = t.plan_due.min(ts + PLAN_CHECK_FAST_S);
                }
            });
            self.st().tracks.iter().filter(|t| t.fast).count()
        };
        self.upd(st, |t| t.live_due = ts + if fast { fast_interval(hot, &client.rate()) } else { SLOW_S });
        Ok(())
    }

    // -- LIVE buttons (control.rs) -------------------------------------------------------------------------------------------------
    /// A LIVE state Planning Center just answered an action with: shown to every panel at once, and the next poll comes soon to fill in the rest.
    pub fn apply_live(&self, st: &str, live: Value, ts: Option<f64>) {
        let ts = ts.unwrap_or_else(now);
        let found = self.upd(st, |t| {
            t.live = Some(live.clone());
            t.updated_at = Some(ts);
            t.error = None;
            t.reachable = true;
            let cur = &live["current"];
            if let (true, Some(started), pt) = (cur.is_object(), f(cur, "live_start_at").filter(|v| *v != 0.0), s(cur, "plan_time")) {
                if !pt.is_empty() {
                    let seen = t.service_start.get(&pt).copied();
                    t.service_start.insert(pt, seen.filter(|v| *v != 0.0).unwrap_or(started).min(started));
                }
            }
            t.fast = true;
            t.live_due = t.live_due.min(ts + FAST_S);
        });
        if found.is_some() {
            self.note_change();
        }
    }

    // -- state ----------------------------------------------------------------------------------------------------------------------
    fn note_change(&self) {
        let sig = {
            let st = self.st();
            let rows: Vec<Value> = st
                .tracks
                .iter()
                .map(|t| {
                    json!([t.st, t.plan, t.items, t.team, t.needed, t.catalog, t.live, t.error, t.override_plan, t.plans.iter().map(|p| p["id"].clone()).collect::<Vec<_>>()])
                })
                .collect();
            json!([st.me_id, rows]).to_string()
        };
        let changed = {
            let mut st = self.st();
            if st.sig == sig {
                false
            } else {
                st.sig = sig;
                st.rev += 1;
                true
            }
        };
        if changed {
            let listeners: Vec<Listener> = self.listeners.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone();
            for l in listeners {
                l();
            }
        }
    }

    /// Everything known about one followed service type, timers worked out for `now`.
    pub fn state(&self, st: &str, now: Option<f64>) -> Option<Value> {
        let tr = self.track(st)?;
        let now = now.unwrap_or_else(self::now);
        let live = tr.live.clone().unwrap_or_else(|| json!({}));
        let cur = live["current"].clone();
        let start = cur.as_object().and_then(|_| tr.service_start.get(&s(&cur, "plan_time")).copied());
        let controller = live["controller"].clone();
        Some(json!({
            "service_type": st,
            "name": self.label(st),
            "day": tr.day.map(plan::iso_day),
            "plan": tr.plan,
            "override": tr.override_plan.is_some(),
            "items": tr.items,
            // Nobody controlling is "not live", not an error.
            "live": !controller.is_null(),
            "live_id": live["id"],
            "controller": controller,
            "timers": plan::timers(tr.plan.as_ref(), &tr.items, tr.live.as_ref(), now, start),
            "polling": if tr.fast { "fast" } else { "slow" },
            "reachable": tr.reachable,
            "error": tr.error,
            "updated_at": tr.updated_at,
        }))
    }

    /// For the admin page: every followed service type, plus the plans the operator can pick from for today. Item notes are left out.
    pub fn snapshot(&self, now: Option<f64>) -> Value {
        let mut types = Vec::new();
        for tr in self.tracks() {
            let Some(mut st) = self.state(&tr.st, now) else { continue };
            st["items"] = Value::Array(
                st["items"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .map(|i| Value::Object(i.as_object().into_iter().flatten().filter(|(k, _)| k.as_str() != "notes").map(|(k, v)| (k.clone(), v.clone())).collect()))
                    .collect(),
            );
            st["plans"] = Value::Array(tr.plans.iter().map(|p| json!({"id": p["id"], "title": p["title"], "dates": p["dates"], "sort_date": p["sort_date"]})).collect());
            types.push(st);
        }
        let (rev, zone_guessed, rate) = {
            let st = self.st();
            (st.rev, st.zone.is_none(), st.client.as_ref().map(|c| c.rate()).unwrap_or_default())
        };
        json!({"rev": rev, "time_zone": self.zone().name, "time_zone_guessed": zone_guessed, "rate": rate.json(), "service_types": types})
    }
}

/// Put the position's id on each member or needed row, found by its team and name in the catalog (`""` when the catalog does not list it).
pub fn tag_positions(rows: &mut [Value], catalog: &[Value]) {
    let mut ids: BTreeMap<(String, String), String> = BTreeMap::new();
    for t in catalog {
        for p in t["positions"].as_array().into_iter().flatten() {
            ids.insert((s(t, "id"), relay_core::text::fold(&s(p, "name"))), s(p, "id"));
        }
    }
    for r in rows {
        let id = ids.get(&(s(r, "team_id"), relay_core::text::fold(&s(r, "position")))).cloned().unwrap_or_default();
        r["position_id"] = json!(id);
    }
}

/// Now, epoch seconds.
pub fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}
