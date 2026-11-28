//! Dante routing (#259) and routing presets (#260): change a receive channel's subscription from Relay, lock channels Relay must never change, undo the
//! last change, and save, compare and apply whole routings (`routing.py`).
//!
//! Every write goes through [`Routing::apply`], one at a time: locked channels are skipped and listed, each subscription is written, read back and
//! reported by itself, every result goes to the module's history with who made it, and what was there before is kept so the whole change can be undone.
//! The write packet itself is `arc::set_subscription`, byte for byte what Dante Controller sends (`tests/dante.rs`); a result Relay cannot confirm is
//! reported as such.
//!
//! A route is `{"device": Dante name, "channel": receive channel number, "source": "<transmit channel>@<device>" or "" for no subscription}`.

use std::collections::{BTreeSet, HashMap};
use std::sync::{Mutex, PoisonError};

use relay_core::text::same_name;
use serde_json::{json, Value};
use sqlx::Row;

use crate::arc::{self, Wrote};
use crate::clock;
use crate::inventory::DEFAULT_ARC;
use crate::service::Service;

pub const MAX_PRESETS: usize = 200;
pub const MAX_NAME: usize = 80;

/// A request Relay will not carry out; the text says why, in words for the operator.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Refused(pub String);

impl Refused {
    fn new(text: &str) -> Self {
        Self(text.to_owned())
    }
}

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}

/// The source a receive channel is subscribed to: `""` when it has none.
fn source_of(sub: &Value) -> String {
    if text(&sub["status"]) == "none" { String::new() } else { text(&sub["source"]).to_owned() }
}

/// Every receive channel of every device whose subscriptions Relay could read (all of `scope` when given).
pub fn routes_of(devices: &[Value], scope: &[String]) -> Vec<Value> {
    let wanted: Option<BTreeSet<String>> = if scope.is_empty() { None } else { Some(scope.iter().map(|s| s.to_lowercase()).collect()) };
    let mut out = Vec::new();
    for d in devices {
        if wanted.as_ref().is_some_and(|w| !w.contains(&text(&d["name"]).to_lowercase())) {
            continue;
        }
        for s in d["subscriptions"].as_array().into_iter().flatten() {
            out.push(json!({"device": d["name"], "channel": s["channel"], "source": source_of(s)}));
        }
    }
    out
}

/// Two subscription sources are the same: both empty (no subscription), or the same name ignoring case.
pub fn same_source(a: &str, b: &str) -> bool {
    (a.is_empty() && b.is_empty()) || same_name(a, b)
}

pub fn sources(devices: &[Value]) -> BTreeSet<String> {
    devices.iter().flat_map(|d| d["transmit"].as_array().into_iter().flatten().map(|c| format!("{}@{}", text(c), text(&d["name"])))).collect()
}

/// What applying `routes` would do now, one entry per route that differs or cannot be applied:
/// `{"device", "channel", "from", "to", "action": "change"|"locked"|"missing", "why"}`.
pub fn diff(routes: &[Value], devices: &[Value], locked: &BTreeSet<(String, i64)>) -> Vec<Value> {
    let by_name: HashMap<String, &Value> = devices.iter().map(|d| (text(&d["name"]).to_lowercase(), d)).collect();
    let known: BTreeSet<String> = sources(devices).into_iter().map(|s| s.to_lowercase()).collect();
    let mut out = Vec::new();
    for r in routes {
        let channel = r["channel"].as_i64().unwrap_or(0);
        let want = text(&r["source"]);
        let mut entry = json!({"device": r["device"], "channel": r["channel"], "from": "", "to": want, "action": "change", "why": ""});
        fn set(entry: &mut Value, action: &str, why: &str) {
            entry["action"] = json!(action);
            entry["why"] = json!(why);
        }
        match by_name.get(&text(&r["device"]).to_lowercase()) {
            None => set(&mut entry, "missing", "that device is not on the network"),
            Some(d) if d["subscriptions"].is_null() => set(&mut entry, "missing", "Relay cannot read that device's subscriptions"),
            Some(d) => match d["subscriptions"].as_array().into_iter().flatten().find(|s| s["channel"].as_i64() == Some(channel)) {
                None => set(&mut entry, "missing", &format!("the device has no receive channel {channel}")),
                Some(now) => {
                    let from = source_of(now);
                    entry["from"] = json!(from);
                    if same_source(&from, want) {
                        continue;
                    }
                    if locked.contains(&(text(&d["name"]).to_owned(), channel)) {
                        set(&mut entry, "locked", "the channel is locked");
                    } else if !want.is_empty() && !known.contains(&want.to_lowercase()) {
                        set(&mut entry, "missing", "the source is not on the network");
                    }
                }
            },
        }
        out.push(entry);
    }
    out
}

struct Change {
    device: String,
    channel: i64,
    to: String,
}

struct Last {
    label: String,
    by: String,
    at: String,
    changes: Vec<Change>,
}

pub struct Routing {
    write: tokio::sync::Mutex<()>,
    /// What the last change replaced, for Undo (in memory: gone on a restart).
    last: Mutex<Option<Last>>,
}

impl Default for Routing {
    fn default() -> Self {
        Self::new()
    }
}

impl Routing {
    pub fn new() -> Self {
        Self { write: tokio::sync::Mutex::new(()), last: Mutex::new(None) }
    }

    // ------------------------------------------------------------------------------------------------ locks
    pub async fn locks(&self, s: &Service) -> BTreeSet<(String, i64)> {
        let Some(pool) = s.monitor.pool() else { return BTreeSet::new() };
        match sqlx::query("select device, channel from dante_locks").fetch_all(&pool).await {
            Ok(rows) => rows.iter().map(|r| (r.get::<String, _>("device"), r.get::<i64, _>("channel"))).collect(),
            Err(_) => BTreeSet::new(),
        }
    }

    pub async fn set_lock(&self, s: &Service, device: &str, channel: i64, on: bool) -> Result<(), sqlx::Error> {
        let Some(pool) = s.monitor.pool() else { return Err(sqlx::Error::PoolClosed) };
        if on {
            sqlx::query("insert or ignore into dante_locks (device, channel) values (?, ?)").bind(device).bind(channel).execute(&pool).await?;
        } else {
            sqlx::query("delete from dante_locks where device = ? and channel = ?").bind(device).bind(channel).execute(&pool).await?;
        }
        Ok(())
    }

    // ------------------------------------------------------------------------------------------------ writing
    /// One subscription written and read back: `("ok"|"refused"|"silent"|"unconfirmed"|"error", message)`.
    async fn write_one(&self, s: &Service, d: &mut Value, channel: i64, source: &str) -> (String, String) {
        let ip = d["ips"][0].as_str().unwrap_or("").to_owned();
        let port = d["ports"]["arc"].as_u64().and_then(|p| u16::try_from(p).ok()).unwrap_or(DEFAULT_ARC);
        let modern = arc::is_modern(text(&d["arc_version"]));
        let channel_no = u32::try_from(channel).unwrap_or(0);
        match arc::set_subscription(s.host.net(), (&ip, port), channel_no, source, 5, s.tuning.write_wait, modern).await {
            Err(e) => return ("error".into(), format!("Relay could not reach the Dante network: {}", e.message)),
            Ok(Wrote::Refused(why)) => return ("refused".into(), why),
            Ok(Wrote::Silent(why)) => return ("silent".into(), why),
            Ok(Wrote::Ok) => {}
        }
        s.read_state(d, &ip, port).await;
        let now = d["subscriptions"].as_array().into_iter().flatten().find(|x| x["channel"].as_i64() == Some(channel)).map(source_of);
        if now.is_none_or(|n| n.to_lowercase() != source.to_lowercase()) {
            return ("unconfirmed".into(), "The device accepted the change but did not show it afterwards.".into());
        }
        s.keep_subscriptions(text(&d["key"]), &d["subscriptions"]);
        ("ok".into(), String::new())
    }

    /// Write each change `{"device", "channel", "to"}`. Returns one result per change: `{"device", "channel", "from", "to", "result", "message"}`;
    /// `result` is "ok" or what went wrong.
    async fn apply(&self, s: &Service, changes: Vec<Change>, who: &str, label: &str, undoing: bool) -> Result<Vec<Value>, Refused> {
        let Ok(_guard) = tokio::time::timeout(s.tuning.write_lock, self.write.lock()).await else {
            return Err(Refused::new("Another routing change is still being written. Try again in a moment."));
        };
        let mut devices: HashMap<String, Value> = s.devices_copy().into_iter().map(|d| (text(&d["name"]).to_lowercase(), d)).collect();
        let locked = self.locks(s).await;
        let mut results: Vec<Value> = Vec::new();
        let mut done: Vec<Change> = Vec::new();
        for c in changes {
            let mut row = json!({"device": c.device, "channel": c.channel, "from": "", "to": c.to, "result": "", "message": ""});
            let fail = |row: &mut Value, result: &str, message: &str| {
                row["result"] = json!(result);
                row["message"] = json!(message);
            };
            let Some(d) = devices.get_mut(&c.device.to_lowercase()) else {
                fail(&mut row, "missing", "Relay cannot read that device right now.");
                results.push(row);
                continue;
            };
            if d["ips"].as_array().is_none_or(Vec::is_empty) || d["subscriptions"].is_null() {
                fail(&mut row, "missing", "Relay cannot read that device right now.");
                results.push(row);
                continue;
            }
            let Some(now) = d["subscriptions"].as_array().into_iter().flatten().find(|x| x["channel"].as_i64() == Some(c.channel)).map(source_of) else {
                fail(&mut row, "missing", &format!("The device has no receive channel {}.", c.channel));
                results.push(row);
                continue;
            };
            row["from"] = json!(now);
            let name = text(&d["name"]).to_owned();
            if locked.contains(&(name.clone(), c.channel)) {
                fail(&mut row, "locked", "The channel is locked, so Relay left it alone.");
                results.push(row);
                continue;
            }
            if same_source(&now, &c.to) {
                fail(&mut row, "ok", "Already set.");
                results.push(row);
                continue;
            }
            s.monitor.relay_wrote(text(&d["key"]));
            let (result, message) = self.write_one(s, d, c.channel, &c.to).await;
            let shown = |t: &str| if t.is_empty() { "unsubscribed".to_owned() } else { t.to_owned() };
            let detail = if result == "ok" {
                format!("by {who}: receive channel {} {} (was {}), {label}", c.channel, shown(&c.to), shown(&now))
            } else {
                format!("by {who}: receive channel {} -> {} failed, {label}: {message}", c.channel, shown(&c.to))
            };
            s.monitor.event(&name, "routing", &detail).await;
            if result == "ok" {
                done.push(Change { device: name, channel: c.channel, to: now });
            }
            fail(&mut row, &result, &message);
            results.push(row);
        }
        let mut last = self.last.lock().unwrap_or_else(PoisonError::into_inner);
        if !done.is_empty() && !undoing {
            *last = Some(Last { label: label.to_owned(), by: who.to_owned(), at: clock::now(), changes: done });
        } else if undoing {
            *last = None;
        }
        Ok(results)
    }

    /// One subscription changed. `Refused` when it cannot be tried at all; otherwise the result row.
    pub async fn subscribe(&self, s: &Service, device: &str, channel: i64, source: &str, who: &str) -> Result<Value, Refused> {
        let devices = s.devices_copy();
        let Some(d) = devices.iter().find(|x| same_name(text(&x["name"]), device)) else { return Err(Refused::new("Relay has not seen that device.")) };
        if !source.is_empty() && !sources(&devices).iter().any(|x| x.to_lowercase() == source.to_lowercase()) {
            return Err(Refused::new("Relay has not seen that transmit channel."));
        }
        let name = text(&d["name"]).to_owned();
        if self.locks(s).await.contains(&(name.clone(), channel)) {
            return Err(Refused::new("That receive channel is locked, so Relay will not change it."));
        }
        let label = format!("{} to {name} channel {channel}", if source.is_empty() { "no source" } else { source });
        let mut rows = self.apply(s, vec![Change { device: name, channel, to: source.to_owned() }], who, &label, false).await?;
        Ok(rows.remove(0))
    }

    pub async fn undo(&self, s: &Service, who: &str) -> Result<Vec<Value>, Refused> {
        let (label, changes) = {
            let last = self.last.lock().unwrap_or_else(PoisonError::into_inner);
            let Some(l) = last.as_ref() else { return Err(Refused::new("There is nothing to undo.")) };
            (l.label.clone(), l.changes.iter().map(|c| Change { device: c.device.clone(), channel: c.channel, to: c.to.clone() }).collect::<Vec<_>>())
        };
        self.apply(s, changes, who, &format!("undo of {label}"), true).await
    }

    pub fn undo_info(&self) -> Value {
        match self.last.lock().unwrap_or_else(PoisonError::into_inner).as_ref() {
            Some(l) => json!({"label": l.label, "by": l.by, "at": l.at, "count": l.changes.len()}),
            None => Value::Null,
        }
    }

    // ------------------------------------------------------------------------------------------------ presets
    pub async fn presets(&self, s: &Service) -> Vec<Value> {
        let Some(pool) = s.monitor.pool() else { return Vec::new() };
        let Ok(rows) = sqlx::query("select id, name, saved_at, saved_by, devices, routes from dante_presets order by name collate nocase").fetch_all(&pool).await else { return Vec::new() };
        rows.iter()
            .map(|r| {
                let parse = |c: &str| serde_json::from_str::<Value>(&r.get::<String, _>(c)).unwrap_or_else(|_| json!([]));
                json!({"id": r.get::<i64, _>("id"), "name": r.get::<String, _>("name"), "saved_at": r.get::<String, _>("saved_at"), "saved_by": r.get::<String, _>("saved_by"),
                       "devices": parse("devices"), "count": parse("routes").as_array().map_or(0, Vec::len)})
            })
            .collect()
    }

    pub async fn preset(&self, s: &Service, id: i64) -> Result<Value, Refused> {
        let none = || Refused::new("There is no such preset.");
        let pool = s.monitor.pool().ok_or_else(none)?;
        let row = sqlx::query("select id, name, saved_at, saved_by, devices, routes from dante_presets where id = ?").bind(id).fetch_optional(&pool).await.ok().flatten().ok_or_else(none)?;
        let parse = |c: &str| serde_json::from_str::<Value>(&row.get::<String, _>(c)).unwrap_or_else(|_| json!([]));
        Ok(json!({"id": row.get::<i64, _>("id"), "name": row.get::<String, _>("name"), "saved_at": row.get::<String, _>("saved_at"), "saved_by": row.get::<String, _>("saved_by"),
                  "devices": parse("devices"), "routes": parse("routes")}))
    }

    pub async fn save_preset(&self, s: &Service, name: &str, scope: &[String], who: &str) -> Result<Value, Refused> {
        let name = name.trim();
        if name.is_empty() || name.chars().count() > MAX_NAME {
            return Err(Refused(format!("Give the preset a name of up to {MAX_NAME} characters.")));
        }
        let devices = s.devices_copy();
        let known: HashMap<String, String> = devices.iter().map(|d| (text(&d["name"]).to_lowercase(), text(&d["name"]).to_owned())).collect();
        if let Some(unknown) = scope.iter().find(|x| !known.contains_key(&x.to_lowercase())) {
            return Err(Refused(format!("Relay has not seen {unknown}.")));
        }
        let routes = routes_of(&devices, scope);
        if routes.is_empty() {
            return Err(Refused::new("Relay can read no subscriptions to save yet."));
        }
        let Some(pool) = s.monitor.pool() else { return Err(Refused::new("The database is not available.")) };
        let exists = sqlx::query("select 1 as x from dante_presets where name = ?").bind(name).fetch_optional(&pool).await.ok().flatten().is_some();
        if self.presets(s).await.len() >= MAX_PRESETS && !exists {
            return Err(Refused::new("There are too many presets: delete one first."));
        }
        let names: Vec<String> = scope.iter().filter_map(|x| known.get(&x.to_lowercase()).cloned()).collect();
        let saved = sqlx::query(
            "insert into dante_presets (name, saved_at, saved_by, devices, routes) values (?, ?, ?, ?, ?) on conflict(name) do update set saved_at = excluded.saved_at, saved_by = excluded.saved_by, devices = excluded.devices, routes = excluded.routes",
        )
        .bind(name)
        .bind(clock::now())
        .bind(who)
        .bind(json!(names).to_string())
        .bind(Value::Array(routes.clone()).to_string())
        .execute(&pool)
        .await;
        if saved.is_err() {
            return Err(Refused::new("The preset could not be saved."));
        }
        let id: i64 = sqlx::query("select id from dante_presets where name = ?").bind(name).fetch_one(&pool).await.map_or(0, |r| r.get("id"));
        Ok(json!({"id": id, "name": name, "count": routes.len()}))
    }

    pub async fn delete_preset(&self, s: &Service, id: i64) -> Result<String, Refused> {
        let p = self.preset(s, id).await?;
        if let Some(pool) = s.monitor.pool() {
            let _ = sqlx::query("delete from dante_presets where id = ?").bind(id).execute(&pool).await;
        }
        Ok(text(&p["name"]).to_owned())
    }

    /// The preset against the live network: what would change, what is locked, what is missing, how many match.
    pub async fn compare(&self, s: &Service, id: i64) -> Result<Value, Refused> {
        let p = self.preset(s, id).await?;
        let routes = p["routes"].as_array().cloned().unwrap_or_default();
        let entries = diff(&routes, &s.devices_copy(), &self.locks(s).await);
        let of = |action: &str| -> Vec<Value> { entries.iter().filter(|e| text(&e["action"]) == action).cloned().collect() };
        Ok(json!({
            "preset": {"id": p["id"], "name": p["name"], "saved_at": p["saved_at"], "saved_by": p["saved_by"]},
            "changes": of("change"), "locked": of("locked"), "missing": of("missing"),
            "same": routes.len() - entries.len(),
        }))
    }

    /// Apply a preset: shows what it did per subscription. Locked channels and what is missing are listed and skipped. Undo restores the routing from
    /// before the apply.
    pub async fn apply_preset(&self, s: &Service, id: i64, who: &str) -> Result<Value, Refused> {
        let cmp = self.compare(s, id).await?;
        let name = text(&cmp["preset"]["name"]).to_owned();
        let changes: Vec<Change> = cmp["changes"].as_array().into_iter().flatten().map(|e| Change { device: text(&e["device"]).to_owned(), channel: e["channel"].as_i64().unwrap_or(0), to: text(&e["to"]).to_owned() }).collect();
        let results = self.apply(s, changes, who, &format!("preset {name}"), false).await?;
        let failed = results.iter().filter(|r| text(&r["result"]) != "ok").count();
        Ok(json!({"preset": cmp["preset"], "results": results, "locked": cmp["locked"], "missing": cmp["missing"], "failed": failed}))
    }
}
