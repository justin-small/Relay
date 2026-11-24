//! Routes (`app/routing.py`, #544): which source channel feeds which destination channel. A route says "Lobby TV takes Camera 2",
//! with a gain in dB where the gear has one. The tables are the Python migration 0015's, read and written in place.
//!
//! **Wanted and actual.** A route here is what was asked for. The module that owns the destination applies it and reports what the
//! gear really does, through a [`Provider`]. The gear is the truth: a view shows what it reports and flags a difference from what
//! was asked, and Relay never quietly overwrites the gear. The audio bus is a provider of its own ([`crate::bus`]); a module that is
//! still Python is reached through the provider the server builds from the app's answer.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};

use relay_auth::now;
use serde_json::{json, Value};
use sqlx::Row;

use crate::channels::{channel_id, reference, Channel, Channels, End, Fut};
use crate::error::{Error, Result};

pub const MAX_SALVOS: i64 = 200;
pub const NAME_MAX: usize = 60;
pub const SCOPE_MAX: usize = 80;
pub const GAIN_MIN_DB: f64 = -144.0;
pub const GAIN_MAX_DB: f64 = 24.0;
pub const KINDS: [&str; 2] = ["audio", "video"];
pub const MANAGE: &str = "network.manage";
pub const NO_PROVIDER: &str = "No module that is on can apply this, so Relay will when it is.";

/// What a source feeds, as a provider sees it: the source channel's ends and the gain asked for.
#[derive(Clone, Debug)]
pub struct Wanted {
    pub ends: Vec<End>,
    pub gain_db: Option<f64>,
}

/// One source a provider says a destination takes now.
#[derive(Clone, Debug)]
pub struct ReadItem {
    /// The provider of the source end; `None` means the destination's own provider.
    pub provider: Option<String>,
    pub key: String,
    pub gain_db: Option<f64>,
}

/// What the module that owns a destination offers (`modules.provide("routes.<id>", {...})`).
pub trait Provider: Send + Sync {
    /// True when a destination takes one source (a video output), false when it mixes several (an audio output).
    fn exclusive(&self) -> bool;
    /// True when the gear has a gain per route.
    fn gain(&self) -> bool;
    /// A permission that may route its destinations, besides `network.manage`.
    fn permission(&self) -> Option<String> {
        None
    }
    /// Point the destination end at `wanted`. Returns "" or a sentence saying why not.
    fn apply<'a>(&'a self, end: &'a End, wanted: &'a [Wanted]) -> Fut<'a, String>;
    /// What the gear does now, or `None` when it cannot say.
    fn read<'a>(&'a self, end: &'a End) -> Fut<'a, Option<Vec<ReadItem>>>;
}

type World = BTreeMap<i64, Channel>;

/// A salvo's route as stored.
struct SalvoRow {
    destination: i64,
    source: Option<i64>,
    gain_db: Option<f64>,
}

pub struct Routing {
    pub channels: Arc<Channels>,
    core: RwLock<HashMap<String, Arc<dyn Provider>>>,
    external: RwLock<HashMap<String, Arc<dyn Provider>>>,
    native: RwLock<HashMap<String, Arc<dyn Provider>>>,
    generation: AtomicU64,
}

/// `_gain`: a number is clamped to the range and rounded to two places; anything else (a bool, text, NaN) is no gain.
pub fn gain_of(raw: &Value) -> Option<f64> {
    match raw {
        Value::Number(n) => n.as_f64().filter(|f| !f.is_nan()).map(|f| ((f.clamp(GAIN_MIN_DB, GAIN_MAX_DB)) * 100.0).round() / 100.0),
        _ => None,
    }
}

fn gain_key(g: Option<f64>) -> Option<i64> {
    g.map(|g| (g * 100.0).round() as i64)
}

fn squeeze(s: &str, limit: usize) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(limit).collect()
}

fn salvo_ref(id: i64) -> String {
    format!("salvo:relay:{id}")
}

impl Routing {
    pub fn new(channels: Arc<Channels>) -> Arc<Self> {
        Arc::new(Self { channels, core: RwLock::default(), external: RwLock::default(), native: RwLock::default(), generation: AtomicU64::new(0) })
    }

    /// A number that goes up whenever a route is written through this process, so a cache of what routes say can tell it is stale.
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::Acquire)
    }

    /// Core code that owns destinations (the audio bus) offers the same interface a module does, under its provider id.
    pub fn provide_core(&self, provider: &str, api: Arc<dyn Provider>) {
        self.core.write().unwrap_or_else(|e| e.into_inner()).insert(provider.to_owned(), api);
    }

    /// The modules that are on and can apply routes (the Python app's answer, until each is ported); replaces the last set.
    pub fn set_external(&self, providers: HashMap<String, Arc<dyn Provider>>) {
        *self.external.write().unwrap_or_else(|e| e.into_inner()) = providers;
    }

    /// A module ported to Rust offers its interface under its provider id; unlike `set_external` it is not replaced by the app's answer.
    pub fn provide_native(&self, provider: &str, api: Arc<dyn Provider>) {
        self.native.write().unwrap_or_else(|e| e.into_inner()).insert(provider.to_owned(), api);
    }

    pub fn withdraw_native(&self, provider: &str) {
        self.native.write().unwrap_or_else(|e| e.into_inner()).remove(provider);
    }

    fn api(&self, provider: &str) -> Option<Arc<dyn Provider>> {
        self.core
            .read()
            .unwrap_or_else(|e| e.into_inner())
            .get(provider)
            .cloned()
            .or_else(|| self.native.read().unwrap_or_else(|e| e.into_inner()).get(provider).cloned())
            .or_else(|| self.external.read().unwrap_or_else(|e| e.into_inner()).get(provider).cloned())
    }

    fn appliers(&self, dest: &Channel) -> Vec<(End, Arc<dyn Provider>)> {
        dest.ends
            .iter()
            .filter(|e| e.direction == "destination" && !e.missing)
            .filter_map(|e| self.api(&e.provider).map(|a| (e.clone(), a)))
            .collect()
    }

    /// A channel id from an id or a ref, followed through any merge.
    pub async fn cid(&self, v: &Value) -> Result<Option<i64>> {
        let id = match v {
            Value::Number(n) if n.is_i64() => n.as_i64(),
            Value::Number(_) | Value::Bool(_) => None,
            other => channel_id(other),
        };
        match id {
            Some(id) => self.channels.root_id(id).await,
            None => Ok(None),
        }
    }

    // -- permissions --------------------------------------------------------------------------------------------
    /// The permissions that may route `destination`: network.manage, plus the one each module that applies it names.
    pub async fn permissions(&self, destination: &Value) -> Result<Vec<String>> {
        let mut out = vec![MANAGE.to_owned()];
        if let Some(id) = self.cid(destination).await? {
            if let Some(dest) = self.channels.get(id).await? {
                for (_, api) in self.appliers(&dest) {
                    if let Some(p) = api.permission().filter(|p| !p.is_empty() && !out.contains(p)) {
                        out.push(p);
                    }
                }
            }
        }
        Ok(out)
    }

    pub async fn may_route(&self, destination: &Value, can: &(dyn Fn(&str) -> bool + Sync)) -> Result<bool> {
        Ok(self.permissions(destination).await?.iter().any(|p| can(p)))
    }

    fn exclusive(&self, dest: &Channel) -> bool {
        let a = self.appliers(dest);
        if a.is_empty() {
            return dest.kind == "video";
        }
        a.iter().any(|(_, api)| api.exclusive())
    }

    fn has_gain(&self, dest: &Channel) -> bool {
        self.appliers(dest).iter().any(|(_, api)| api.gain())
    }

    // -- reading ------------------------------------------------------------------------------------------------
    /// `[(route id, source root, gain)]` for what was asked of a destination, one per source.
    async fn wanted_rows(&self, dest_id: i64) -> Result<Vec<(i64, i64, Option<f64>)>> {
        let rows = sqlx::query("select id, source, gain_db from routes where destination = ? order by id").bind(dest_id).fetch_all(self.channels.pool()).await?;
        let mut seen = HashSet::new();
        let mut out = Vec::new();
        for r in rows {
            if let Some(src) = self.channels.root_id(r.get("source")).await? {
                if src != dest_id && seen.insert(src) {
                    out.push((r.get("id"), src, r.get("gain_db")));
                }
            }
        }
        Ok(out)
    }

    async fn world(&self) -> Result<World> {
        Ok(self.channels.listing("").await?.into_iter().map(|c| (c.id, c)).collect())
    }

    /// What was asked for `destination`: the source channels' ends and the gain. For a module that rebuilds its own state from the
    /// routes (the audio bus).
    pub async fn wanted(&self, destination: &Value) -> Result<Vec<Wanted>> {
        let world = self.world().await?;
        let Some(cid) = self.cid(destination).await?.filter(|c| world.contains_key(c)) else { return Ok(Vec::new()) };
        Ok(self.wanted_rows(cid).await?.into_iter().filter_map(|(_, s, g)| world.get(&s).map(|c| Wanted { ends: c.ends.clone(), gain_db: g })).collect())
    }

    async fn dest_ids(&self) -> Result<Vec<i64>> {
        let mut ids = Vec::new();
        for r in sqlx::query("select distinct destination d from routes order by d").fetch_all(self.channels.pool()).await? {
            if let Some(root) = self.channels.root_id(r.get("d")).await? {
                if !ids.contains(&root) {
                    ids.push(root);
                }
            }
        }
        Ok(ids)
    }

    fn sources_of(sources: &[(i64, Option<f64>)], world: &World) -> Vec<Value> {
        let mut out: Vec<(String, Value)> = sources
            .iter()
            .filter_map(|(s, g)| world.get(s).map(|c| (c.reference(), json!({"ref": c.reference(), "name": c.shown, "missing": c.missing, "gain_db": g}))))
            .collect();
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out.into_iter().map(|x| x.1).collect()
    }

    /// What the gear does for `dest`, or `None` when no module that is on can say.
    async fn actual(&self, dest: &Channel, world: &World) -> Option<Vec<(i64, Option<f64>)>> {
        let ends: HashMap<(String, String), i64> = world.values().flat_map(|c| c.ends.iter().map(move |e| ((e.provider.clone(), e.key.clone()), c.id))).collect();
        let mut found: BTreeMap<i64, Option<f64>> = BTreeMap::new();
        let mut said = false;
        for (end, api) in self.appliers(dest) {
            let Some(got) = api.read(&end).await else { continue };
            said = true;
            for item in got {
                let provider = item.provider.clone().unwrap_or_else(|| end.provider.clone());
                if let Some(&cid) = ends.get(&(provider, item.key.clone())) {
                    found.insert(cid, item.gain_db.and_then(|g| gain_of(&json!(g))));
                }
            }
        }
        said.then(|| found.into_iter().collect())
    }

    async fn view(&self, dest: &Channel, world: &World) -> Result<Value> {
        let wanted: Vec<(i64, Option<f64>)> = self.wanted_rows(dest.id).await?.into_iter().map(|(_, s, g)| (s, g)).collect();
        let actual = self.actual(dest, world).await;
        let gain = self.has_gain(dest);
        let key = |v: &[(i64, Option<f64>)]| {
            let mut k: Vec<(i64, Option<i64>)> = v.iter().map(|(s, g)| (*s, if gain { gain_key(*g) } else { None })).collect();
            k.sort();
            k
        };
        let state = match &actual {
            None => if wanted.is_empty() { "none" } else { "wanted" },
            Some(a) if wanted.is_empty() => if a.is_empty() { "none" } else { "actual" },
            Some(a) => if key(&wanted) == key(a) { "same" } else { "differs" },
        };
        let shown = match (&actual, state) {
            (Some(a), "actual" | "differs" | "same") => a.clone(),
            _ => wanted.clone(),
        };
        Ok(json!({"destination": dest.reference(), "name": dest.shown, "kind": dest.kind, "state": state, "gain": gain,
                  "exclusive": self.exclusive(dest), "providers": dest.providers,
                  "wanted": Self::sources_of(&wanted, world),
                  "actual": actual.as_ref().map(|a| Self::sources_of(a, world)),
                  "sources": Self::sources_of(&shown, world)}))
    }

    /// One destination's route: what was asked for, what the gear does, and whether they agree.
    pub async fn status(&self, destination: &Value) -> Result<Option<Value>> {
        let world = self.world().await?;
        let Some(dest) = self.cid(destination).await?.and_then(|c| world.get(&c)) else { return Ok(None) };
        Ok(Some(self.view(dest, &world).await?))
    }

    /// Every destination that has a route, wanted or on the gear, shown with the sources it takes. `provider` and `gear_id` keep the
    /// destinations with an end there.
    pub async fn listing(&self, provider: &str, gear_id: Option<i64>) -> Result<Vec<Value>> {
        let world = self.world().await?;
        let mut out = Vec::new();
        for dest in world.values() {
            if !dest.ends.iter().any(|e| e.direction == "destination") {
                continue;
            }
            if !provider.is_empty() && !dest.providers.iter().any(|p| p == provider) {
                continue;
            }
            if gear_id.is_some() && !dest.ends.iter().any(|e| e.gear == gear_id) {
                continue;
            }
            let v = self.view(dest, &world).await?;
            if v["state"] != "none" {
                out.push(v);
            }
        }
        Ok(out)
    }

    /// What feeds this channel, from the gear when it can say, else from what was asked for.
    pub async fn feeds(&self, destination: &Value) -> Result<Vec<Value>> {
        Ok(self.status(destination).await?.and_then(|v| v["sources"].as_array().cloned()).unwrap_or_default())
    }

    /// Where this channel goes: the destinations that take it.
    pub async fn feeding(&self, source: &Value) -> Result<Vec<Value>> {
        let Some(cid) = self.cid(source).await? else { return Ok(Vec::new()) };
        let me = reference(cid);
        Ok(self
            .listing("", None)
            .await?
            .into_iter()
            .filter(|v| v["sources"].as_array().is_some_and(|s| s.iter().any(|x| x["ref"] == me.as_str())))
            .map(|v| json!({"ref": v["destination"], "name": v["name"]}))
            .collect())
    }

    /// Every channel that takes a signal on `provider` (and `gear_id`), routed or not.
    pub async fn destinations(&self, provider: &str, gear_id: Option<i64>) -> Result<Vec<Value>> {
        Ok(self
            .channels
            .listing("")
            .await?
            .into_iter()
            .filter(|c| c.ends.iter().any(|e| e.provider == provider && e.direction == "destination" && !e.missing && (gear_id.is_none() || e.gear == gear_id)))
            .map(|c| json!({"ref": c.reference(), "name": c.shown, "kind": c.kind}))
            .collect())
    }

    // -- writing ------------------------------------------------------------------------------------------------
    fn check(dest: Option<&Channel>, src: Option<&Channel>) -> Result<()> {
        let (Some(dest), Some(src)) = (dest, src) else { return Err(Error::Refused("no_channel")) };
        if dest.id == src.id {
            return Err(Error::Refused("route_same"));
        }
        if !KINDS.contains(&dest.kind.as_str()) || dest.kind != src.kind {
            return Err(Error::Refused("route_kind"));
        }
        if !dest.ends.iter().any(|e| e.direction == "destination") {
            return Err(Error::Refused("no_destination"));
        }
        if !src.ends.iter().any(|e| e.direction == "source") {
            return Err(Error::Refused("no_source"));
        }
        Ok(())
    }

    /// Tell every module that owns an end of `dest` what it should take now. Sentences for what would not apply.
    async fn push(&self, dest: &Channel) -> Result<Vec<String>> {
        let world = self.world().await?;
        let wanted: Vec<Wanted> = self.wanted_rows(dest.id).await?.into_iter().filter_map(|(_, s, g)| world.get(&s).map(|c| Wanted { ends: c.ends.clone(), gain_db: g })).collect();
        let appliers = self.appliers(dest);
        if appliers.is_empty() {
            return Ok(vec![NO_PROVIDER.to_owned()]);
        }
        let mut problems = Vec::new();
        for (end, api) in appliers {
            let said = api.apply(&end, &wanted).await;
            if !said.is_empty() {
                problems.push(said);
            }
        }
        Ok(problems)
    }

    async fn channel_of(&self, v: &Value) -> Result<Option<Channel>> {
        match self.cid(v).await? {
            Some(id) => self.channels.get(id).await,
            None => Ok(None),
        }
    }

    /// Make `wanted` (`[(source channel, gain_db or null)]`) the sources `destination` takes, and apply it. An empty list takes the
    /// destination off everything. Returns `{status, problems}`.
    pub async fn replace(&self, destination: &Value, wanted: &[(Value, Value)], apply: bool) -> Result<Value> {
        let dest = self.channel_of(destination).await?.ok_or(Error::Refused("no_channel"))?;
        let mut pairs: Vec<(i64, Option<f64>)> = Vec::new();
        for (src_ref, gain) in wanted {
            let src = self.channel_of(src_ref).await?;
            Self::check(Some(&dest), src.as_ref())?;
            let id = src.map(|s| s.id).unwrap_or_default();
            pairs.retain(|(s, _)| *s != id);
            pairs.push((id, gain_of(gain)));
        }
        if pairs.len() > 1 && self.exclusive(&dest) {
            return Err(Error::Refused("route_kind"));
        }
        let gains = self.has_gain(&dest) || self.appliers(&dest).is_empty();
        let stamp = now();
        let mut tx = self.channels.pool().begin().await?;
        sqlx::query("delete from routes where destination = ?").bind(dest.id).execute(&mut *tx).await?;
        for (src, gain) in &pairs {
            sqlx::query("insert into routes (destination, source, gain_db, created_at, updated_at) values (?, ?, ?, ?, ?)")
                .bind(dest.id)
                .bind(src)
                .bind(if gains { *gain } else { None })
                .bind(&stamp)
                .bind(&stamp)
                .execute(&mut *tx)
                .await?;
        }
        tx.commit().await?;
        self.generation.fetch_add(1, Ordering::AcqRel);
        let problems = if apply { self.push(&dest).await? } else { Vec::new() };
        Ok(json!({"status": self.status(&json!(dest.id)).await?, "problems": problems}))
    }

    /// `destination` takes `source`. A destination that takes one source (video) drops the others; one that mixes (audio) keeps them
    /// and sets this one's gain.
    pub async fn set(&self, destination: &Value, source: &Value, gain_db: &Value, apply: bool) -> Result<Value> {
        let (dest, src) = (self.channel_of(destination).await?, self.channel_of(source).await?);
        Self::check(dest.as_ref(), src.as_ref())?;
        let (dest, src) = (dest.unwrap_or_else(|| unreachable!()), src.unwrap_or_else(|| unreachable!()));
        let mut keep: Vec<(Value, Value)> = Vec::new();
        if !self.exclusive(&dest) {
            for (_, s, g) in self.wanted_rows(dest.id).await? {
                if s != src.id {
                    keep.push((json!(s), g.map_or(Value::Null, |g| json!(g))));
                }
            }
        }
        keep.push((json!(src.id), gain_db.clone()));
        self.replace(&json!(dest.id), &keep, apply).await
    }

    /// Take `destination` off `source`, or off everything when `source` is null.
    pub async fn clear(&self, destination: &Value, source: &Value, apply: bool) -> Result<Value> {
        let dest = self.channel_of(destination).await?.ok_or(Error::Refused("no_channel"))?;
        let mut keep: Vec<(Value, Value)> = Vec::new();
        if !source.is_null() {
            let drop = self.cid(source).await?;
            for (_, s, g) in self.wanted_rows(dest.id).await? {
                if Some(s) != drop {
                    keep.push((json!(s), g.map_or(Value::Null, |g| json!(g))));
                }
            }
        }
        self.replace(&json!(dest.id), &keep, apply).await
    }

    /// Apply every route asked for on a destination of `provider` again: for a module whose gear forgets its routes when it restarts.
    pub async fn reapply(&self, provider: &str) -> Result<Vec<String>> {
        let world = self.world().await?;
        let mut problems = Vec::new();
        for did in self.dest_ids().await? {
            let Some(dest) = world.get(&did).filter(|d| d.providers.iter().any(|p| p == provider)) else { continue };
            if self.view(dest, &world).await?["state"] == "same" {
                continue;
            }
            problems.extend(self.push(dest).await?);
        }
        Ok(problems)
    }

    // -- salvos -------------------------------------------------------------------------------------------------
    fn scope(raw: &str) -> String {
        squeeze(raw, SCOPE_MAX)
    }

    fn salvo_json(id: i64, name: &str, scope: &str, rows: &[SalvoRow], names: &HashMap<i64, String>) -> Value {
        json!({"id": id, "ref": salvo_ref(id), "name": name, "scope": scope,
               "routes": rows.iter().map(|x| json!({
                   "destination": reference(x.destination),
                   "destination_name": names.get(&x.destination).cloned().unwrap_or_default(),
                   "source": x.source.map(reference).unwrap_or_default(),
                   "source_name": x.source.and_then(|s| names.get(&s).cloned()).unwrap_or_default(),
                   "gain_db": x.gain_db})).collect::<Vec<_>>()})
    }

    /// Saved presets, all or one scope's.
    pub async fn salvos(&self, scope: Option<&str>) -> Result<Vec<Value>> {
        let names: HashMap<i64, String> = self.world().await?.into_iter().map(|(k, c)| (k, c.shown)).collect();
        let mut rows: HashMap<i64, Vec<SalvoRow>> = HashMap::new();
        for x in sqlx::query("select * from salvo_routes order by rowid").fetch_all(self.channels.pool()).await? {
            rows.entry(x.get("salvo_id")).or_default().push(SalvoRow { destination: x.get("destination"), source: x.get("source"), gain_db: x.get("gain_db") });
        }
        let want = scope.map(Self::scope);
        Ok(sqlx::query("select * from salvos order by id")
            .fetch_all(self.channels.pool())
            .await?
            .iter()
            .filter(|r| want.as_ref().is_none_or(|w| r.get::<String, _>("scope") == *w))
            .map(|r| {
                let id: i64 = r.get("id");
                Self::salvo_json(id, &r.get::<String, _>("name"), &r.get::<String, _>("scope"), rows.get(&id).map(Vec::as_slice).unwrap_or(&[]), &names)
            })
            .collect())
    }

    async fn salvo(&self, sid: i64) -> Result<Option<Value>> {
        Ok(self.salvos(None).await?.into_iter().find(|s| s["id"] == sid))
    }

    /// Save what each of `destinations` takes now (the gear's word, else what was asked) as a preset; with `sid` an existing preset is
    /// replaced. A destination that takes nothing is saved as taking nothing.
    pub async fn save_salvo(&self, name: &str, destinations: &[Value], scope: &str, sid: Option<i64>) -> Result<Value> {
        let name = squeeze(name, NAME_MAX);
        if name.is_empty() {
            return Err(Error::Refused("salvo_name"));
        }
        let scope = Self::scope(scope);
        let world = self.world().await?;
        let mut cells: Vec<(i64, Option<i64>, Option<f64>)> = Vec::new();
        for d in destinations {
            let did = self.cid(d).await?;
            let Some((did, dest)) = did.and_then(|i| world.get(&i).map(|c| (i, c))) else { return Err(Error::Refused("no_channel")) };
            let got = self.view(dest, &world).await?["sources"].as_array().cloned().unwrap_or_default();
            if got.is_empty() {
                cells.push((did, None, None));
            }
            for s in got {
                cells.push((did, channel_id(&s["ref"]), s["gain_db"].as_f64()));
            }
        }
        let mut tx = self.channels.pool().begin().await?;
        let old = match sid {
            Some(id) => sqlx::query("select id from salvos where id = ?").bind(id).fetch_optional(&mut *tx).await?,
            None => None,
        };
        if sid.is_some() && old.is_none() {
            return Err(Error::Refused("no_salvo"));
        }
        let all = sqlx::query("select id, name, scope from salvos").fetch_all(&mut *tx).await?;
        if all.iter().any(|r| Some(r.get::<i64, _>("id")) != sid && r.get::<String, _>("scope") == scope && r.get::<String, _>("name").to_lowercase() == name.to_lowercase()) {
            return Err(Error::Refused("name_taken"));
        }
        let id = if old.is_none() {
            let n: i64 = sqlx::query("select count(*) n from salvos").fetch_one(&mut *tx).await?.get("n");
            if n >= MAX_SALVOS {
                return Err(Error::Refused("too_many_salvos"));
            }
            sqlx::query("insert into salvos (name, scope, created_at) values (?, ?, ?)").bind(&name).bind(&scope).bind(now()).execute(&mut *tx).await?.last_insert_rowid()
        } else {
            let id = sid.unwrap_or_default();
            sqlx::query("update salvos set name = ?, scope = ? where id = ?").bind(&name).bind(&scope).bind(id).execute(&mut *tx).await?;
            sqlx::query("delete from salvo_routes where salvo_id = ?").bind(id).execute(&mut *tx).await?;
            id
        };
        for (d, s, g) in cells {
            sqlx::query("insert or ignore into salvo_routes (salvo_id, destination, source, gain_db) values (?, ?, ?, ?)").bind(id).bind(d).bind(s).bind(g).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        self.salvo(id).await?.ok_or(Error::Refused("no_salvo"))
    }

    pub async fn delete_salvo(&self, sid: i64) -> Result<()> {
        let mut tx = self.channels.pool().begin().await?;
        if sqlx::query("select 1 x from salvos where id = ?").bind(sid).fetch_optional(&mut *tx).await?.is_none() {
            return Err(Error::Refused("no_salvo"));
        }
        sqlx::query("delete from salvos where id = ?").bind(sid).execute(&mut *tx).await?;
        tx.commit().await?;
        self.generation.fetch_add(1, Ordering::AcqRel);
        Ok(())
    }

    /// Apply every route in a preset. Returns the sentences for what would not apply (empty when all did).
    pub async fn take_salvo(&self, sid: i64) -> Result<Vec<String>> {
        if sqlx::query("select 1 x from salvos where id = ?").bind(sid).fetch_optional(self.channels.pool()).await?.is_none() {
            return Err(Error::Refused("no_salvo"));
        }
        let mut per: Vec<(i64, Vec<(Value, Value)>)> = Vec::new();
        for x in sqlx::query("select * from salvo_routes where salvo_id = ? order by rowid").bind(sid).fetch_all(self.channels.pool()).await? {
            let dest: i64 = x.get("destination");
            if !per.iter().any(|(d, _)| *d == dest) {
                per.push((dest, Vec::new()));
            }
            if let Some(src) = x.get::<Option<i64>, _>("source") {
                let gain: Option<f64> = x.get("gain_db");
                if let Some((_, list)) = per.iter_mut().find(|(d, _)| *d == dest) {
                    list.push((json!(src), gain.map_or(Value::Null, |g| json!(g))));
                }
            }
        }
        let mut problems = Vec::new();
        for (dest, wanted) in per {
            match self.replace(&json!(dest), &wanted, true).await {
                Ok(v) => problems.extend(v["problems"].as_array().into_iter().flatten().filter_map(|p| p.as_str().map(str::to_owned))),
                Err(Error::Refused(_)) => problems.push("A channel in that preset is gone or no longer fits.".to_owned()),
                Err(e) => return Err(e),
            }
        }
        Ok(problems)
    }
}
