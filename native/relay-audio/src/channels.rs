//! Channels (`app/channels.py`, #542): every audio, video and talk channel in one list, made of the ends each module reports.
//! A ref names one: `channel:relay:<id>`. The tables are the Python migration 0014's, read and written in place.
//!
//! A module reports what it sees with [`Channels::sync`]; a new end gets a channel of its own, one no longer reported is marked
//! missing, not dropped, so its name stays. Relay never joins two ends by itself: [`Channels::suggestions`] lists the likely
//! pairs and an operator confirms each with [`Channels::merge`]. After a merge the old ref still works.

use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::pin::Pin;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, RwLock};

use relay_auth::now;
use serde_json::{json, Value};
use sqlx::{Row, SqlitePool};

use crate::error::{Error, Result};

pub const KINDS: [&str; 3] = ["audio", "video", "talk"];
pub const NAME_MAX: usize = 60;
pub const NOTE_MAX: usize = 300;
pub const MAX_CHANNELS: i64 = 20000;
pub const MAX_PER_PROVIDER: usize = 4000;

pub type Fut<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// What a module that owns gear offers for naming it (`modules.provide("gear.<id>", {...})`). `None` while that module is off.
pub trait Gear: Send + Sync {
    fn can_rename(&self, provider: &str, key: &str) -> bool;
    /// Send the new name to the gear. `Err` when the gear refused it.
    fn rename<'a>(&'a self, provider: &'a str, key: &'a str, name: &'a str) -> Fut<'a, std::result::Result<(), ()>>;
}

#[derive(Clone, Debug, PartialEq)]
pub struct End {
    pub id: i64,
    pub provider: String,
    pub key: String,
    pub gear: Option<i64>,
    pub direction: String,
    pub number: Option<i64>,
    pub gear_name: String,
    pub missing: bool,
}

impl End {
    pub fn json(&self) -> Value {
        json!({"id": self.id, "provider": self.provider, "key": self.key, "gear": self.gear,
               "gear_ref": self.gear.map(|g| format!("gear:relay:{g}")).unwrap_or_default(), "direction": self.direction,
               "number": self.number, "gear_name": self.gear_name, "missing": self.missing})
    }
    fn name(&self) -> String {
        if !self.gear_name.is_empty() {
            self.gear_name.clone()
        } else if let Some(n) = self.number {
            format!("Channel {n}")
        } else {
            self.key.clone()
        }
    }
}

#[derive(Clone, Debug)]
pub struct Channel {
    pub id: i64,
    pub kind: String,
    pub name: String,
    pub note: String,
    pub shown: String,
    pub gear_name: String,
    pub can_device: bool,
    pub missing: bool,
    pub ends: Vec<End>,
    pub providers: Vec<String>,
    pub direction: String,
    pub number: Option<i64>,
    pub gear_ref: String,
}

impl Channel {
    pub fn reference(&self) -> String {
        reference(self.id)
    }
    pub fn json(&self) -> Value {
        json!({"id": self.id, "ref": self.reference(), "kind": self.kind, "name": self.name, "note": self.note, "shown": self.shown,
               "gear_name": self.gear_name, "can_device": self.can_device, "missing": self.missing,
               "ends": self.ends.iter().map(End::json).collect::<Vec<_>>(), "providers": self.providers,
               "direction": self.direction, "number": self.number, "gear_ref": self.gear_ref})
    }
}

pub fn reference(id: i64) -> String {
    format!("channel:relay:{id}")
}

/// The id in "channel:relay:5", or `None` for anything else.
pub fn channel_id(text: &Value) -> Option<i64> {
    let s = text.as_str()?;
    let mut p = s.split(':');
    let (a, b, c) = (p.next()?, p.next()?, p.next()?);
    (p.next().is_none() && a == "channel" && b == "relay" && !c.is_empty() && c.bytes().all(|x| x.is_ascii_digit())).then(|| c.parse().ok()).flatten()
}

fn squeeze(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

fn text(raw: &str, limit: usize) -> String {
    squeeze(raw).chars().take(limit).collect::<String>().trim().to_owned()
}

/// Two names are the same when their case-folded, space-squeezed forms are equal; two empty names are not (`text.same_name`).
pub fn same_name(a: &str, b: &str) -> bool {
    let fa = squeeze(a).to_lowercase();
    !fa.is_empty() && fa == squeeze(b).to_lowercase()
}

/// "Input 3", "Out 12", "Channel 5": names a device gives everything, so they say nothing about which channel it is.
fn generic(name: &str) -> bool {
    let n = name.trim().to_lowercase();
    for word in ["camera input", "channel", "source", "input", "output", "out", "src", "in", "ch"] {
        if let Some(rest) = n.strip_prefix(word) {
            let rest = rest.trim_start();
            if rest.bytes().all(|b| b.is_ascii_digit()) {
                return true;
            }
        }
    }
    false
}

fn valid_provider(p: &str) -> bool {
    let mut c = p.chars();
    matches!(c.next(), Some(f) if f.is_ascii_lowercase())
        && p.len() <= 32
        && c.all(|x| x.is_ascii_lowercase() || x.is_ascii_digit() || x == '_')
}

struct Clean {
    key: String,
    kind: String,
    direction: String,
    number: Option<i64>,
    gear_name: String,
}

fn clean(raw: &Value) -> Option<Clean> {
    let o = raw.as_object()?;
    let key = o.get("key")?.as_str()?;
    let kind = o.get("kind")?.as_str()?;
    if key.is_empty() || key.chars().count() > 200 || key.chars().any(|c| (c as u32) < 0x20) || !KINDS.contains(&kind) {
        return None;
    }
    let direction = o.get("direction").and_then(Value::as_str).filter(|d| matches!(*d, "source" | "destination")).unwrap_or("source");
    let number = o.get("number").and_then(Value::as_i64).filter(|n| (0..1_000_000_000).contains(n));
    let gear_name = text(o.get("gear_name").and_then(Value::as_str).unwrap_or(""), NAME_MAX);
    Some(Clean { key: key.to_owned(), kind: kind.to_owned(), direction: direction.to_owned(), number, gear_name })
}

/// Every channel, one list; the database is `relay.db`.
pub struct Channels {
    pool: SqlitePool,
    generation: AtomicU64,
    gear: RwLock<Option<Arc<dyn Gear>>>,
}

fn end_of(r: &sqlx::sqlite::SqliteRow) -> End {
    End {
        id: r.get("id"),
        provider: r.get("provider"),
        key: r.get("key"),
        gear: r.get("gear_id"),
        direction: r.get("direction"),
        number: r.get("number"),
        gear_name: r.get("gear_name"),
        missing: r.get::<i64, _>("missing") != 0,
    }
}

impl Channels {
    pub fn new(pool: SqlitePool) -> Arc<Self> {
        Arc::new(Self { pool, generation: AtomicU64::new(0), gear: RwLock::new(None) })
    }

    pub fn pool(&self) -> &SqlitePool {
        &self.pool
    }

    /// Where a rename on the gear goes (a module's `gear.<id>`): the Python app until the module is ported.
    pub fn set_gear(&self, gear: Option<Arc<dyn Gear>>) {
        *self.gear.write().unwrap_or_else(|e| e.into_inner()) = gear;
    }

    fn gear(&self) -> Option<Arc<dyn Gear>> {
        self.gear.read().unwrap_or_else(|e| e.into_inner()).clone()
    }

    /// A number that goes up whenever any channel is written, so a reader of names can tell its copy is stale.
    pub fn generation(&self) -> u64 {
        self.generation.load(Ordering::Acquire)
    }

    fn touch(&self) {
        self.generation.fetch_add(1, Ordering::AcqRel);
    }

    fn build(&self, r: &sqlx::sqlite::SqliteRow, ends: Vec<End>) -> Channel {
        let id: i64 = r.get("id");
        let name: String = r.get("name");
        let live: Vec<&End> = ends.iter().filter(|e| !e.missing).collect();
        let lead = live.first().copied().or_else(|| ends.first());
        let gear = self.gear();
        let can_device = gear.as_ref().is_some_and(|g| ends.iter().any(|e| !e.missing && g.can_rename(&e.provider, &e.key)));
        let mut providers: Vec<String> = ends.iter().map(|e| e.provider.clone()).collect::<HashSet<_>>().into_iter().collect();
        providers.sort();
        Channel {
            id,
            kind: r.get("kind"),
            shown: if !name.is_empty() { name.clone() } else { lead.map(End::name).unwrap_or_else(|| format!("Channel {id}")) },
            name,
            note: r.get("note"),
            gear_name: lead.map(End::name).unwrap_or_default(),
            can_device,
            missing: live.is_empty(),
            providers,
            direction: lead.map(|e| e.direction.clone()).unwrap_or_else(|| "source".into()),
            number: lead.and_then(|e| e.number),
            gear_ref: lead.and_then(|e| e.gear).map(|g| format!("gear:relay:{g}")).unwrap_or_default(),
            ends,
        }
    }

    async fn roots(&self, kind: &str, only: i64) -> Result<Vec<Channel>> {
        let rows = sqlx::query(
            "select * from channels where merged_into is null and (? = '' or kind = ?) and (? = 0 or id = ?) order by id",
        )
        .bind(kind)
        .bind(kind)
        .bind(only)
        .bind(only)
        .fetch_all(&self.pool)
        .await?;
        if rows.is_empty() {
            return Ok(Vec::new());
        }
        let mut ends: HashMap<i64, Vec<End>> = HashMap::new();
        for e in sqlx::query("select * from channel_ends order by position, id").fetch_all(&self.pool).await? {
            ends.entry(e.get("channel_id")).or_default().push(end_of(&e));
        }
        Ok(rows.iter().map(|r| self.build(r, ends.remove(&r.get::<i64, _>("id")).unwrap_or_default())).collect())
    }

    /// Every channel (one per merged group), oldest first, with its ends. `kind` narrows it.
    pub async fn listing(&self, kind: &str) -> Result<Vec<Channel>> {
        self.roots(if KINDS.contains(&kind) { kind } else { "" }, 0).await
    }

    /// The id of the channel `cid` is now part of (following a merge), or `None` when there is no such channel.
    pub async fn root_id(&self, cid: i64) -> Result<Option<i64>> {
        let mut seen = HashSet::new();
        let mut cid = cid;
        while seen.insert(cid) {
            let Some(row) = sqlx::query("select id, merged_into from channels where id = ?").bind(cid).fetch_optional(&self.pool).await? else {
                return Ok(None);
            };
            match row.get::<Option<i64>, _>("merged_into") {
                None => return Ok(Some(row.get("id"))),
                Some(next) => cid = next,
            }
        }
        Ok(None)
    }

    /// The channel `cid` is now part of (following a merge), or `None`.
    pub async fn get(&self, cid: i64) -> Result<Option<Channel>> {
        match self.root_id(cid).await? {
            Some(root) => Ok(self.roots("", root).await?.into_iter().next()),
            None => Ok(None),
        }
    }

    pub async fn by_ref(&self, text: &Value) -> Result<Option<Channel>> {
        match channel_id(text) {
            Some(id) => self.get(id).await,
            None => Ok(None),
        }
    }

    /// `{channel ref: name shown}` for every channel, a merged-away ref included (it shows its new channel's name).
    pub async fn names(&self) -> Result<HashMap<String, String>> {
        let roots: HashMap<i64, String> = self.listing("").await?.into_iter().map(|c| (c.id, c.shown)).collect();
        let mut out = HashMap::new();
        for r in sqlx::query("select id from channels").fetch_all(&self.pool).await? {
            let id: i64 = r.get("id");
            if let Some(root) = self.root_id(id).await? {
                if let Some(name) = roots.get(&root) {
                    out.insert(reference(id), name.clone());
                }
            }
        }
        Ok(out)
    }

    async fn follow(tx: &mut sqlx::SqliteConnection, mut cid: i64) -> Result<i64> {
        for _ in 0..64 {
            let row = sqlx::query("select merged_into from channels where id = ?").bind(cid).fetch_optional(&mut *tx).await?;
            match row.and_then(|r| r.get::<Option<i64>, _>("merged_into")) {
                Some(next) => cid = next,
                None => return Ok(cid),
            }
        }
        Ok(cid)
    }

    /// Every channel `provider` reports now (on `gear_id`, or with no gear), each `{key, kind, direction, number, gear_name}`
    /// (key and kind required). A new one gets a channel of its own; one already there is updated and comes back when it was
    /// missing; one this provider had on that gear and no longer reports is marked missing. `within` limits "no longer reported"
    /// to keys starting with it. Returns `{key: channel ref}`, following any merge.
    pub async fn sync(&self, provider: &str, reported: &[Value], gear_id: Option<i64>, within: &str) -> Result<HashMap<String, String>> {
        let mut out = HashMap::new();
        if !valid_provider(provider) {
            return Ok(out);
        }
        let stamp = now();
        let mut tx = self.pool.begin().await?;
        if let Some(g) = gear_id {
            if sqlx::query("select 1 x from gear where id = ?").bind(g).fetch_optional(&mut *tx).await?.is_none() {
                return Ok(out);
            }
        }
        let have: HashMap<String, End> = sqlx::query("select * from channel_ends where provider = ?")
            .bind(provider)
            .fetch_all(&mut *tx)
            .await?
            .iter()
            .map(|r| (r.get::<String, _>("key"), end_of(r)))
            .collect();
        let mut seen: HashSet<String> = HashSet::new();
        for (pos, item) in reported.iter().enumerate() {
            let Some(c) = clean(item) else { continue };
            if seen.contains(&c.key) || seen.len() >= MAX_PER_PROVIDER {
                continue;
            }
            seen.insert(c.key.clone());
            match have.get(&c.key) {
                None => {
                    let n: i64 = sqlx::query("select count(*) n from channels").fetch_one(&mut *tx).await?.get("n");
                    if n >= MAX_CHANNELS {
                        tracing::warn!("channels: too many to keep another from {provider}");
                        continue;
                    }
                    let cid = sqlx::query("insert into channels (kind, created_at) values (?, ?)")
                        .bind(&c.kind)
                        .bind(&stamp)
                        .execute(&mut *tx)
                        .await?
                        .last_insert_rowid();
                    sqlx::query(
                        "insert into channel_ends (channel_id, provider, key, gear_id, direction, number, gear_name, position, last_seen, created_at) \
                         values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    )
                    .bind(cid)
                    .bind(provider)
                    .bind(&c.key)
                    .bind(gear_id)
                    .bind(&c.direction)
                    .bind(c.number)
                    .bind(&c.gear_name)
                    .bind(pos as i64)
                    .bind(&stamp)
                    .bind(&stamp)
                    .execute(&mut *tx)
                    .await?;
                }
                Some(old) => {
                    sqlx::query(
                        "update channel_ends set gear_id = ?, direction = ?, number = ?, gear_name = ?, position = ?, missing = 0, last_seen = ? where id = ?",
                    )
                    .bind(gear_id)
                    .bind(&c.direction)
                    .bind(c.number)
                    .bind(&c.gear_name)
                    .bind(pos as i64)
                    .bind(&stamp)
                    .bind(old.id)
                    .execute(&mut *tx)
                    .await?;
                }
            }
        }
        for (key, old) in &have {
            if !seen.contains(key) && !old.missing && old.gear == gear_id && key.starts_with(within) {
                sqlx::query("update channel_ends set missing = 1 where id = ?").bind(old.id).execute(&mut *tx).await?;
            }
        }
        let rows = sqlx::query("select e.key, e.channel_id from channel_ends e where e.provider = ?").bind(provider).fetch_all(&mut *tx).await?;
        for r in rows {
            let key: String = r.get("key");
            if seen.contains(&key) {
                let root = Self::follow(&mut tx, r.get("channel_id")).await?;
                out.insert(key, reference(root));
            }
        }
        tx.commit().await?;
        self.touch();
        Ok(out)
    }

    /// The channel ref of the end (`provider`, `key`), made as a missing end when nothing has reported it yet: for saved data that
    /// names a source which is offline now. `None` when it cannot be made.
    pub async fn ensure(&self, provider: &str, key: &str, kind: &str, direction: &str, gear_name: &str) -> Result<Option<String>> {
        let Some(c) = clean(&json!({"key": key, "kind": kind, "direction": direction, "gear_name": gear_name})) else { return Ok(None) };
        if !valid_provider(provider) {
            return Ok(None);
        }
        let mut tx = self.pool.begin().await?;
        if let Some(old) = sqlx::query("select channel_id from channel_ends where provider = ? and key = ?").bind(provider).bind(key).fetch_optional(&mut *tx).await? {
            let root = Self::follow(&mut tx, old.get("channel_id")).await?;
            return Ok(Some(reference(root)));
        }
        let stamp = now();
        let cid = sqlx::query("insert into channels (kind, created_at) values (?, ?)").bind(&c.kind).bind(&stamp).execute(&mut *tx).await?.last_insert_rowid();
        sqlx::query("insert into channel_ends (channel_id, provider, key, direction, gear_name, missing, created_at) values (?, ?, ?, ?, ?, 1, ?)")
            .bind(cid)
            .bind(provider)
            .bind(key)
            .bind(&c.direction)
            .bind(&c.gear_name)
            .bind(&stamp)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        self.touch();
        Ok(Some(reference(cid)))
    }

    /// `sync` for a caller that reports from a thread or a refresh loop: the database being down is logged and answered with `None`.
    pub async fn report(&self, provider: &str, reported: &[Value]) -> Option<HashMap<String, String>> {
        match self.sync(provider, reported, None, "").await {
            Ok(m) => Some(m),
            Err(e) => {
                tracing::info!("channels: the database is not ready, so {provider}'s channels were not recorded ({e:?})");
                None
            }
        }
    }

    /// A Relay-only name for the channel. Empty text shows the reporting end's own name again.
    pub async fn rename(&self, cid: i64, name: &str) -> Result<()> {
        let root = self.root_id(cid).await?.ok_or(Error::Refused("no_channel"))?;
        let done = sqlx::query("update channels set name = ? where id = ?").bind(text(name, NAME_MAX)).bind(root).execute(&self.pool).await?;
        if done.rows_affected() == 0 {
            return Err(Error::Refused("no_channel"));
        }
        self.touch();
        Ok(())
    }

    /// Send a new name to the gear for every end of this channel whose module can rename it, then show the gear's name.
    /// Nothing changes in Relay when the gear refuses.
    pub async fn rename_on_device(&self, cid: i64, name: &str) -> Result<()> {
        let name = text(name, NAME_MAX);
        let got = self.get(cid).await?.ok_or(Error::Refused("no_channel"))?;
        if name.is_empty() {
            return Err(Error::Refused("name"));
        }
        let gear = self.gear();
        let ends: Vec<&End> = got.ends.iter().filter(|e| !e.missing && gear.as_ref().is_some_and(|g| g.can_rename(&e.provider, &e.key))).collect();
        let Some(gear) = gear.filter(|_| !ends.is_empty()) else { return Err(Error::Refused("no_device")) };
        for e in &ends {
            if gear.rename(&e.provider, &e.key, &name).await.is_err() {
                tracing::info!("channels: {} refused the name for {}", e.provider, e.key);
                return Err(Error::Refused("device"));
            }
        }
        let mut tx = self.pool.begin().await?;
        for e in &ends {
            sqlx::query("update channel_ends set gear_name = ? where id = ?").bind(&name).bind(e.id).execute(&mut *tx).await?;
        }
        sqlx::query("update channels set name = '' where id = ?").bind(got.id).execute(&mut *tx).await?;
        tx.commit().await?;
        self.touch();
        Ok(())
    }

    pub async fn set_note(&self, cid: i64, note: &str) -> Result<()> {
        let root = self.root_id(cid).await?.ok_or(Error::Refused("no_channel"))?;
        let done = sqlx::query("update channels set note = ? where id = ?").bind(text(note, NOTE_MAX)).bind(root).execute(&self.pool).await?;
        if done.rows_affected() == 0 {
            return Err(Error::Refused("no_channel"));
        }
        Ok(())
    }

    /// Make `other` part of `keep`: its ends move over, and its ref follows to `keep`. The name an operator typed on `keep` stays;
    /// `other`'s is used only when `keep` has none.
    pub async fn merge(&self, keep: i64, other: i64) -> Result<Channel> {
        let mut tx = self.pool.begin().await?;
        let (a, b) = (Self::follow(&mut tx, keep).await?, Self::follow(&mut tx, other).await?);
        let ra = sqlx::query("select * from channels where id = ?").bind(a).fetch_optional(&mut *tx).await?;
        let rb = sqlx::query("select * from channels where id = ?").bind(b).fetch_optional(&mut *tx).await?;
        let (Some(ra), Some(rb)) = (ra, rb) else { return Err(Error::Refused("no_channel")) };
        if a == b {
            return Err(Error::Refused("same"));
        }
        if ra.get::<String, _>("kind") != rb.get::<String, _>("kind") {
            return Err(Error::Refused("kind"));
        }
        sqlx::query("update channel_ends set channel_id = ? where channel_id = ?").bind(a).bind(b).execute(&mut *tx).await?;
        sqlx::query("update channels set merged_into = ? where id = ?").bind(a).bind(b).execute(&mut *tx).await?;
        sqlx::query("update channels set merged_into = ? where merged_into = ?").bind(a).bind(b).execute(&mut *tx).await?;
        for field in ["name", "note"] {
            let (mine, theirs): (String, String) = (ra.get(field), rb.get(field));
            if mine.is_empty() && !theirs.is_empty() {
                // `field` is one of the two constants above, never a request's text.
                let sql = if field == "name" { "update channels set name = ? where id = ?" } else { "update channels set note = ? where id = ?" };
                sqlx::query(sql).bind(theirs).bind(a).execute(&mut *tx).await?;
            }
        }
        sqlx::query("delete from channel_apart where a in (?, ?) and b in (?, ?)").bind(a).bind(b).bind(a).bind(b).execute(&mut *tx).await?;
        tx.commit().await?;
        self.touch();
        self.get(a).await?.ok_or(Error::Refused("no_channel"))
    }

    /// Take one end out of its channel into a channel of its own. Returns the new channel.
    pub async fn split(&self, end_id: i64) -> Result<Channel> {
        let mut tx = self.pool.begin().await?;
        let e = sqlx::query("select * from channel_ends where id = ?").bind(end_id).fetch_optional(&mut *tx).await?.ok_or(Error::Refused("no_end"))?;
        let from: i64 = e.get("channel_id");
        let n: i64 = sqlx::query("select count(*) n from channel_ends where channel_id = ?").bind(from).fetch_one(&mut *tx).await?.get("n");
        if n < 2 {
            return Err(Error::Refused("only_end"));
        }
        let kind: String = sqlx::query("select kind from channels where id = ?").bind(from).fetch_one(&mut *tx).await?.get("kind");
        let cid = sqlx::query("insert into channels (kind, created_at) values (?, ?)").bind(kind).bind(now()).execute(&mut *tx).await?.last_insert_rowid();
        sqlx::query("update channel_ends set channel_id = ? where id = ?").bind(cid).bind(end_id).execute(&mut *tx).await?;
        tx.commit().await?;
        self.touch();
        self.get(cid).await?.ok_or(Error::Refused("no_channel"))
    }

    /// Remember that these two channels are not the same, so they are not suggested again.
    pub async fn decline(&self, a: i64, b: i64) -> Result<()> {
        let (Some(ra), Some(rb)) = (self.root_id(a).await?, self.root_id(b).await?) else { return Err(Error::Refused("no_channel")) };
        if ra == rb {
            return Err(Error::Refused("same"));
        }
        let (lo, hi) = (ra.min(rb), ra.max(rb));
        sqlx::query("insert or ignore into channel_apart (a, b) values (?, ?)").bind(lo).bind(hi).execute(&self.pool).await?;
        Ok(())
    }

    /// Remove a channel none of whose ends is reported any more (its name goes with it).
    pub async fn forget(&self, cid: i64) -> Result<()> {
        let got = self.get(cid).await?.ok_or(Error::Refused("no_channel"))?;
        if !got.missing {
            return Err(Error::Refused("present"));
        }
        sqlx::query("delete from channels where id = ?").bind(got.id).execute(&self.pool).await?;
        self.touch();
        Ok(())
    }

    /// Pairs of present channels that are probably one: `{a, b, why}` with "name" (the same name) or "number" (the same number on
    /// the same gear). A pair an operator declined is left out.
    pub async fn suggestions(&self) -> Result<Vec<(Channel, Channel, &'static str)>> {
        let chans: Vec<Channel> = self.listing("").await?.into_iter().filter(|c| !c.missing).collect();
        let apart: HashSet<(i64, i64)> =
            sqlx::query("select a, b from channel_apart").fetch_all(&self.pool).await?.iter().map(|r| (r.get("a"), r.get("b"))).collect();
        let mut out = Vec::new();
        for (i, a) in chans.iter().enumerate() {
            for b in &chans[i + 1..] {
                if a.kind != b.kind || apart.contains(&(a.id, b.id)) {
                    continue;
                }
                let why = why(a, b);
                if !why.is_empty() {
                    out.push((a.clone(), b.clone(), why));
                }
            }
        }
        Ok(out)
    }
}

fn labels(c: &Channel) -> Vec<&str> {
    let mut out: Vec<&str> = Vec::new();
    if !c.name.is_empty() {
        out.push(&c.name);
    }
    out.extend(c.ends.iter().filter(|e| !e.gear_name.is_empty() && !e.missing).map(|e| e.gear_name.as_str()));
    out.into_iter().filter(|n| !generic(n)).collect()
}

fn why(a: &Channel, b: &Channel) -> &'static str {
    for x in labels(a) {
        for y in labels(b) {
            if same_name(x, y) {
                return "name";
            }
        }
    }
    for e in &a.ends {
        for f in &b.ends {
            if e.gear.is_some() && e.gear == f.gear && e.number.is_some() && e.number == f.number && !e.missing && !f.missing && e.direction == f.direction {
                return "number";
            }
        }
    }
    ""
}
