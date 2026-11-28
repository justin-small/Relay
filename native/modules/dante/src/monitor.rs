//! Dante monitoring (#258): what is wrong on the Dante network, what changed, and the history that survives a restart (`monitor.py`).
//!
//! [`Monitor::observe`] takes what a scan read (each device record carries `reachable`, `clock`, `subscriptions`) and works out, with no network of its
//! own:
//!
//! * the problems now (a device gone, a subscription that does not resolve, a clock that is unlocked or counting errors), which become status cards and,
//!   through the stage alert named "Dante" when the operator has made one, a flash;
//! * the changes since last time (device appeared or vanished, subscription changed, clock leader changed), which go to the history in the module's own
//!   tables.
//!
//! A problem raises once when it starts and clears when it ends. Two scans in a row must miss a device before it counts as gone, so one dropped
//! announcement is not an outage.

use std::collections::{BTreeMap, BTreeSet, HashMap, HashSet, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};

use relay_core::caps::{AlertButtonsCap, ALERT_BUTTONS};
use relay_core::text::same_name;
use relay_core::Host;
use serde_json::{json, Value};
use sqlx::{Row, SqlitePool};

use crate::clock;

pub const MISSING_SCANS: u32 = 2;
pub const KEEP_HISTORY: i64 = 1000;
pub const STAGE_BUTTON: &str = "Dante";

/// What a monitor does when problems start (`true`) and when they have all ended (`false`).
pub type Stage = Arc<dyn Fn(&str, bool) + Send + Sync>;

type Subs = BTreeMap<i64, (String, String)>;

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}

/// A device's receive channels as `{channel: (status, source)}`.
pub fn sub_map(device: &Value) -> Subs {
    device["subscriptions"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|s| (s["channel"].as_i64().unwrap_or(0), (text(&s["status"]).to_owned(), text(&s["source"]).to_owned())))
        .collect()
}

pub fn leader_of(devices: &[Value]) -> String {
    devices.iter().find(|d| d["clock"]["leader"] == json!(true)).map(|d| text(&d["name"]).to_owned()).unwrap_or_default()
}

/// Problems keyed in the order they were found (a map that keeps insertion order, as Python's dict does).
#[derive(Default, Clone, Debug, PartialEq, Eq)]
pub struct Problems(pub Vec<(String, String)>);

impl Problems {
    fn set(&mut self, key: String, why: String) {
        match self.0.iter_mut().find(|(k, _)| *k == key) {
            Some(slot) => slot.1 = why,
            None => self.0.push((key, why)),
        }
    }

    fn has(&self, key: &str) -> bool {
        self.0.iter().any(|(k, _)| k == key)
    }
}

/// One entry per thing that is wrong right now. `gone` is the devices missing long enough to count (`(key, name)`); `before` is each device's last clock
/// reading, to see an error count still climbing.
pub fn problems(devices: &[Value], gone: &[(String, String)], before: &HashMap<String, Value>) -> Problems {
    let mut out = Problems::default();
    for (key, name) in gone {
        out.set(format!("offline:{key}"), format!("{name} is not on the network"));
    }
    for d in devices {
        let (key, name) = (text(&d["key"]), text(&d["name"]));
        if d["reachable"] == json!(false) {
            out.set(format!("offline:{key}"), format!("{name} does not answer on its control port"));
        }
        for s in d["subscriptions"].as_array().into_iter().flatten() {
            if s["status"] == json!("unresolved") {
                let source = if text(&s["source"]).is_empty() { "its source" } else { text(&s["source"]) };
                out.set(format!("unresolved:{key}:{}", s["channel"].as_i64().unwrap_or(0)), format!("{name} receive channel {} cannot find {source}", s["channel"].as_i64().unwrap_or(0)));
            }
        }
        let c = &d["clock"];
        if c.is_object() {
            let was = before.get(key).cloned().unwrap_or(Value::Null);
            let n = |v: &Value, k: &str, default: i64| v[k].as_i64().unwrap_or(default);
            let (fe, ie) = (n(c, "follower_errors", 0), n(c, "interface_errors", 0));
            if c["locked"] != json!(true) {
                out.set(format!("clock:{key}"), format!("{name}'s clock is not locked"));
            } else if fe > n(&was, "follower_errors", fe) {
                out.set(format!("clock:{key}"), format!("{name} is counting clock follower errors"));
            } else if ie > n(&was, "interface_errors", ie) {
                out.set(format!("clock:{key}"), format!("{name} is counting network interface errors"));
            }
        }
    }
    out
}

struct Seen {
    name: String,
    subs: Option<Subs>,
    clock: Option<Value>,
    missing: u32,
}

struct Inner {
    active: Vec<(String, String)>,
    /// In memory while the database is down.
    recent: VecDeque<Value>,
    seen: HashMap<String, Seen>,
    leader: String,
    first: bool,
    relay_wrote: HashSet<String>,
}

/// A history line not yet written.
type Event = (String, String, String);

pub struct Monitor {
    inner: Mutex<Inner>,
    pool: Mutex<Option<SqlitePool>>,
    stage: Mutex<Option<Stage>>,
}

impl Default for Monitor {
    fn default() -> Self {
        Self::new()
    }
}

impl Monitor {
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(Inner { active: Vec::new(), recent: VecDeque::new(), seen: HashMap::new(), leader: String::new(), first: true, relay_wrote: HashSet::new() }),
            pool: Mutex::new(None),
            stage: Mutex::new(None),
        }
    }

    fn inner(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// The database the history, stars, locks and presets are in, once the module has opened it.
    pub fn pool(&self) -> Option<SqlitePool> {
        self.pool.lock().unwrap_or_else(PoisonError::into_inner).clone()
    }

    /// Where history and stars are kept; without one they stay in memory (history) or are not kept (stars).
    pub fn use_database(&self, pool: Option<SqlitePool>) {
        *self.pool.lock().unwrap_or_else(PoisonError::into_inner) = pool;
    }

    pub fn set_stage(&self, stage: Option<Stage>) {
        *self.stage.lock().unwrap_or_else(PoisonError::into_inner) = stage;
    }

    // ------------------------------------------------------------------------------------------ history
    pub async fn event(&self, device: &str, kind: &str, detail: &str) {
        let at = clock::now();
        self.inner().recent.push_front(json!({"at": at, "device": device, "kind": kind, "detail": detail}));
        self.inner().recent.truncate(usize::try_from(KEEP_HISTORY).unwrap_or(1000));
        let Some(pool) = self.pool() else {
            tracing::warn!("dante: history not saved, the database is down");
            return;
        };
        let wrote = sqlx::query("insert into dante_history (at, device, kind, detail) values (?, ?, ?, ?)").bind(&at).bind(device).bind(kind).bind(detail).execute(&pool).await;
        let pruned = sqlx::query("delete from dante_history where id <= (select max(id) from dante_history) - ?").bind(KEEP_HISTORY).execute(&pool).await;
        if wrote.is_err() || pruned.is_err() {
            tracing::warn!("dante: history not saved");
        }
    }

    async fn events(&self, events: Vec<Event>) {
        for (device, kind, detail) in events {
            self.event(&device, &kind, &detail).await;
        }
    }

    pub async fn history(&self, limit: i64) -> Vec<Value> {
        let limit = limit.clamp(1, KEEP_HISTORY);
        if let Some(pool) = self.pool() {
            if let Ok(rows) = sqlx::query("select at, device, kind, detail from dante_history order by id desc limit ?").bind(limit).fetch_all(&pool).await {
                return rows.iter().map(|r| json!({"at": r.get::<String, _>("at"), "device": r.get::<String, _>("device"), "kind": r.get::<String, _>("kind"), "detail": r.get::<String, _>("detail")})).collect();
            }
        }
        self.inner().recent.iter().take(usize::try_from(limit).unwrap_or(100)).cloned().collect()
    }

    /// Routing says it is about to change this device's subscriptions, so the change is logged as Relay's.
    pub fn relay_wrote(&self, device_key: &str) {
        self.inner().relay_wrote.insert(device_key.to_owned());
    }

    // ------------------------------------------------------------------------------------------ stars
    pub async fn starred(&self) -> BTreeSet<(String, String)> {
        let Some(pool) = self.pool() else { return BTreeSet::new() };
        match sqlx::query("select device, channel from dante_starred").fetch_all(&pool).await {
            Ok(rows) => rows.iter().map(|r| (r.get::<String, _>("device"), r.get::<String, _>("channel"))).collect(),
            Err(_) => BTreeSet::new(),
        }
    }

    pub async fn star(&self, device: &str, channel: &str, on: bool) -> Result<(), sqlx::Error> {
        let Some(pool) = self.pool() else { return Err(sqlx::Error::PoolClosed) };
        if on {
            sqlx::query("insert or ignore into dante_starred (device, channel) values (?, ?)").bind(device).bind(channel).execute(&pool).await?;
        } else {
            sqlx::query("delete from dante_starred where device = ? and channel = ?").bind(device).bind(channel).execute(&pool).await?;
        }
        Ok(())
    }

    // ------------------------------------------------------------------------------------------ the diff
    pub async fn observe(&self, devices: &[Value]) {
        let (events, stage_call) = {
            let mut events: Vec<Event> = Vec::new();
            let mut inner = self.inner();
            let live: HashMap<&str, &Value> = devices.iter().map(|d| (text(&d["key"]), d)).collect();
            let first = std::mem::replace(&mut inner.first, false);
            let mut gone: Vec<(String, String)> = Vec::new();
            let mut before: HashMap<String, Value> = HashMap::new();
            let mut keys: Vec<String> = inner.seen.keys().cloned().collect();
            keys.sort();
            for key in keys {
                let old = inner.seen.get_mut(&key).expect("key was listed");
                before.insert(key.clone(), old.clock.clone().unwrap_or(Value::Null));
                if live.contains_key(key.as_str()) {
                    continue;
                }
                old.missing += 1;
                if old.missing == MISSING_SCANS {
                    events.push((old.name.clone(), "vanished".into(), "stopped announcing on the network".into()));
                }
                if old.missing >= MISSING_SCANS {
                    gone.push((key.clone(), old.name.clone()));
                }
            }
            for d in devices {
                let key = text(&d["key"]).to_owned();
                let subs = sub_map(d);
                let have_subs = !d["subscriptions"].is_null();
                let name = text(&d["name"]).to_owned();
                let old = inner.seen.get(&key);
                let (old_subs, old_clock, old_missing) = old.map_or((None, None, 0), |o| (o.subs.clone(), o.clock.clone(), o.missing));
                if old.is_none() {
                    if !first {
                        let ips: Vec<&str> = d["ips"].as_array().into_iter().flatten().filter_map(Value::as_str).collect();
                        events.push((name.clone(), "appeared".into(), ips.join(", ")));
                    }
                } else {
                    if old_missing >= MISSING_SCANS {
                        events.push((name.clone(), "appeared".into(), "back on the network".into()));
                    }
                    if have_subs {
                        if let Some(was) = &old_subs {
                            let by = if inner.relay_wrote.remove(&key) { "Relay" } else { "someone in Dante Controller or another controller" };
                            events.extend(subscription_changes(&name, was, &subs, by));
                        }
                    }
                }
                let clock = if d["clock"].is_object() { Some(d["clock"].clone()) } else { old_clock };
                inner.seen.insert(key, Seen { name, subs: if have_subs { Some(subs) } else { old_subs }, clock, missing: 0 });
            }
            let leader = leader_of(devices);
            if !leader.is_empty() && !inner.leader.is_empty() && leader != inner.leader {
                events.push((leader.clone(), "clock_leader".into(), format!("clock leader changed from {}", inner.leader)));
            }
            if !leader.is_empty() {
                inner.leader = leader;
            }
            let now = problems(devices, &gone, &before);
            let (more, stage_call) = settle(&mut inner, &now);
            events.extend(more);
            inner.seen.retain(|_, v| v.missing <= MISSING_SCANS * 20);
            (events, stage_call)
        };
        self.events(events).await;
        if let Some((why, on)) = stage_call {
            let stage = self.stage.lock().unwrap_or_else(PoisonError::into_inner).clone();
            if let Some(stage) = stage {
                stage(&why, on);
            }
        }
    }

    pub fn current(&self) -> Vec<Value> {
        let mut now: Vec<(String, String)> = self.inner().active.clone();
        now.sort();
        now.into_iter().map(|(k, v)| json!({"key": k, "text": v})).collect()
    }
}

fn subscription_changes(name: &str, old: &Subs, new: &Subs, by: &str) -> Vec<Event> {
    let mut out = Vec::new();
    let channels: BTreeSet<i64> = old.keys().chain(new.keys()).copied().collect();
    let none = ("none".to_owned(), String::new());
    for ch in channels {
        if old.get(&ch) == new.get(&ch) {
            continue;
        }
        let (was, now) = (old.get(&ch).unwrap_or(&none), new.get(&ch).unwrap_or(&none));
        if was.1 == now.1 && was.0 != now.0 {
            continue; // the same source resolving or not is a problem and a recovery, not a change
        }
        let show = |s: &str| if s.is_empty() { "unsubscribed".to_owned() } else { s.to_owned() };
        out.push((name.to_owned(), "subscription".into(), format!("receive channel {ch}: {} (was {}), by {by}", show(&now.1), show(&was.1))));
    }
    out
}

/// Which problems started and which ended, and what the stage alert should do about it.
fn settle(inner: &mut Inner, now: &Problems) -> (Vec<Event>, Option<(String, bool)>) {
    let started: Vec<String> = now.0.iter().filter(|(k, _)| !inner.active.iter().any(|(a, _)| a == k)).map(|(_, v)| v.clone()).collect();
    let ended: Vec<String> = inner.active.iter().filter(|(k, _)| !now.has(k)).map(|(_, v)| v.clone()).collect();
    let was_quiet = inner.active.is_empty();
    inner.active = now.0.clone();
    let mut events: Vec<Event> = started.iter().map(|why| (String::new(), "problem".to_owned(), why.clone())).collect();
    events.extend(ended.iter().map(|why| (String::new(), "recovered".to_owned(), why.clone())));
    let call = if !started.is_empty() && was_quiet {
        Some((started[0].clone(), true))
    } else if !ended.is_empty() && now.0.is_empty() {
        Some((String::new(), false))
    } else {
        None
    };
    (events, call)
}

/// Flash or stop the stage alert button named "Dante", if the operator made one; nothing happens without it.
pub fn stage_alert(host: &Host, on: bool) {
    let Some(buttons) = host.using::<AlertButtonsCap>(ALERT_BUTTONS) else { return };
    let Some((id, _)) = buttons.0.buttons().into_iter().find(|(_, name)| same_name(name, STAGE_BUTTON)) else {
        if on {
            tracing::info!("dante: no stage alert button named {STAGE_BUTTON}, so only the status card shows this");
        }
        return;
    };
    if on {
        if !buttons.0.is_on(&id) {
            buttons.0.press(&id, "Dante");
        }
    } else if buttons.0.is_on(&id) {
        buttons.0.cancel(&id);
    }
}
