//! Signage data in relay.db (`store.py`, #205): media rows, playlists and items, screens and groups, slots and overrides, and what a
//! screen plays. Every write bumps [`Live::bump`], which the players' event streams watch, so a change reaches the screens with no reload.
//!
//! The tables are the Python migrations 0001 to 0004's: this module applies them itself the first time it starts, under the namespace
//! `signage`, as the Python module does, so either server reads what the other wrote.

use std::sync::Arc;
use std::time::{SystemTime, UNIX_EPOCH};

use relay_core::schedules;
use relay_core::screens::{ScreenDirectory, ScreenError, SignageSource, SIGNAGE_SOURCE};
use relay_core::timewin::{self, Date};
use relay_core::Host;
use serde_json::{json, Map, Value};
use sqlx::sqlite::SqliteRow;
use sqlx::{Column, Row, SqlitePool};

use crate::live::Live;
use crate::media::{self, Media};
use crate::schedule;

pub const NAME_MAX: usize = 80;
pub const ITEMS_MAX: usize = 500;
pub const SCREEN_KEY: &str = "signage:";

const MIGRATIONS: [(&str, &str); 4] = [
    ("0001_signage", include_str!("../../../../app/modules/signage/migrations/0001_signage.sql")),
    ("0002_schedule", include_str!("../../../../app/modules/signage/migrations/0002_schedule.sql")),
    ("0003_takeover", include_str!("../../../../app/modules/signage/migrations/0003_takeover.sql")),
    ("0004_slot_schedule_ref", include_str!("../../../../app/modules/signage/migrations/0004_slot_schedule_ref.sql")),
];

/// What a request signage refuses (the text is a fixed sentence for the admin page), or a database that is down.
#[derive(Debug, PartialEq, Eq)]
pub enum Error {
    Invalid(String, u16),
    Unavailable,
}

pub type Result<T> = std::result::Result<T, Error>;

pub fn invalid<T>(text: &str) -> Result<T> {
    Err(Error::Invalid(text.to_owned(), 400))
}

pub fn gone<T>(text: &str) -> Result<T> {
    Err(Error::Invalid(text.to_owned(), 404))
}

impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        tracing::warn!("signage: the database failed: {e}");
        Error::Unavailable
    }
}

impl From<ScreenError> for Error {
    fn from(e: ScreenError) -> Self {
        match e {
            ScreenError::Refused(s) => Error::Invalid(s.to_owned(), 400),
            ScreenError::Unavailable => Error::Unavailable,
        }
    }
}

impl From<media::Refused> for Error {
    fn from(e: media::Refused) -> Self {
        Error::Invalid(e.message.to_owned(), e.status)
    }
}

/// One row as a JSON object, each column as its value's own type.
pub fn row_json(r: &SqliteRow) -> Value {
    let mut m = Map::new();
    for (i, c) in r.columns().iter().enumerate() {
        let v = if let Ok(v) = r.try_get::<Option<i64>, _>(i) {
            v.map_or(Value::Null, |x| json!(x))
        } else if let Ok(v) = r.try_get::<Option<f64>, _>(i) {
            v.map_or(Value::Null, |x| json!(x))
        } else if let Ok(v) = r.try_get::<Option<String>, _>(i) {
            v.map_or(Value::Null, |x| json!(x))
        } else {
            Value::Null
        };
        m.insert(c.name().to_owned(), v);
    }
    Value::Object(m)
}

fn rows_json(rows: &[SqliteRow]) -> Vec<Value> {
    rows.iter().map(row_json).collect()
}

fn id_of(v: &Value) -> i64 {
    v["id"].as_i64().unwrap_or(0)
}

pub fn epoch_secs() -> i64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs() as i64).unwrap_or(0)
}

pub fn now_iso() -> String {
    timewin::iso(epoch_secs())
}

fn clean_name(v: &Value, what: &str) -> Result<String> {
    let text: String = match v {
        Value::Null | Value::Bool(false) => String::new(),
        Value::String(s) => s.split_whitespace().collect::<Vec<_>>().join(" "),
        other => other.to_string().split_whitespace().collect::<Vec<_>>().join(" "),
    };
    if text.is_empty() {
        return invalid(&format!("Give the {what} a name."));
    }
    Ok(text.chars().take(NAME_MAX).collect())
}

fn clean_date(v: &Value) -> Result<String> {
    let text = v.as_str().unwrap_or("").trim().to_owned();
    if text.is_empty() {
        return Ok(text);
    }
    let shaped = text.len() == 10 && text.bytes().enumerate().all(|(i, b)| if i == 4 || i == 7 { b == b'-' } else { b.is_ascii_digit() });
    if !shaped {
        return invalid("Dates are written YYYY-MM-DD.");
    }
    if Date::parse(&text).is_none() {
        return invalid("That date does not exist.");
    }
    Ok(text)
}

fn slug_ok(s: &str) -> bool {
    let b = s.as_bytes();
    let ok = |c: u8| c.is_ascii_lowercase() || c.is_ascii_digit();
    !b.is_empty() && b.len() <= 48 && ok(b[0]) && ok(b[b.len() - 1]) && b.iter().all(|c| ok(*c) || *c == b'-')
}

pub fn slug_for(name: &str, taken: &[String]) -> String {
    let mut base = String::new();
    let mut gap = true;
    for c in name.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            base.push(c);
            gap = false;
        } else if !gap {
            base.push('-');
            gap = true;
        }
    }
    let base: String = base.trim_matches('-').chars().take(32).collect::<String>().trim_matches('-').to_owned();
    let base = if base.is_empty() { "screen".to_owned() } else { base };
    let (mut out, mut n) = (base.clone(), 2);
    while taken.contains(&out) {
        out = format!("{base}-{n}");
        n += 1;
    }
    out
}

pub struct Store {
    pub media: Media,
    pub pool: SqlitePool,
    pub screens: Arc<dyn ScreenDirectory>,
    pub live: Arc<Live>,
    pub host: Host,
}

impl Store {
    /// Open signage's tables on `pool`: apply the migrations that have not run.
    pub async fn open(pool: SqlitePool, screens: Arc<dyn ScreenDirectory>, live: Arc<Live>, host: Host, media: Media) -> Result<Self> {
        let mut tx = pool.begin().await?;
        sqlx::query("create table if not exists schema_migrations (namespace text not null, version text not null, applied_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), primary key (namespace, version))")
            .execute(&mut *tx)
            .await?;
        let done: Vec<String> = sqlx::query_scalar("select version from schema_migrations where namespace = 'signage'").fetch_all(&mut *tx).await?;
        for (version, sql) in MIGRATIONS {
            if done.iter().any(|d| d == version) {
                continue;
            }
            sqlx::raw_sql(sql).execute(&mut *tx).await?;
            sqlx::query("insert into schema_migrations (namespace, version) values ('signage', ?)").bind(version).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        Ok(Self { media, pool, screens, live, host })
    }

    // ------------------------------------------------------------------ settings and time
    pub fn settings(&self) -> Value {
        self.host.settings("signage").unwrap_or_else(|| json!({}))
    }

    pub fn tz_name(&self) -> String {
        schedules::clean_zone(self.host.settings("timezone").as_ref().and_then(Value::as_str).unwrap_or("")).to_owned()
    }

    fn named_schedules(&self) -> Value {
        self.host.settings("schedules").filter(Value::is_array).unwrap_or_else(|| json!([]))
    }

    /// Today's date in the signage time zone, which is what an item's start and end dates are read in.
    pub fn today(&self, now_secs: Option<i64>) -> String {
        let (d, _) = schedules::local_of_utc(now_secs.unwrap_or_else(epoch_secs).div_euclid(60), &self.tz_name());
        format!("{:04}-{:02}-{:02}", d.y, d.m, d.d)
    }

    /// Whether named schedule `ref` is enabled and open at wall-clock `(date, minute)` in the Relay zone.
    pub fn open_ref(&self, named: &str, date: Date, minute: i64) -> bool {
        let all = self.named_schedules();
        schedules::find(&all, named).is_some_and(|s| s["enabled"] == true && schedules::is_open_local(s, date, minute, &self.tz_name()))
    }

    // ------------------------------------------------------------------ media
    pub async fn media_list(&self) -> Result<Vec<Value>> {
        let mut rows = rows_json(&sqlx::query("select * from signage_media order by created_at desc, name").fetch_all(&self.pool).await?);
        let used: std::collections::HashMap<String, i64> = sqlx::query("select ref, count(*) as n from signage_items where source = 'media' group by ref")
            .fetch_all(&self.pool)
            .await?
            .iter()
            .map(|r| (r.get::<String, _>("ref"), r.get::<i64, _>("n")))
            .collect();
        for r in &mut rows {
            r["has_thumb"] = json!(r["has_thumb"].as_i64().unwrap_or(0) != 0);
            r["file"] = json!(media::file_name(r["sha256"].as_str().unwrap_or(""), r["ext"].as_str().unwrap_or("")));
            r["uses"] = json!(used.get(r["id"].as_str().unwrap_or("")).copied().unwrap_or(0));
        }
        Ok(rows)
    }

    pub async fn media_get(&self, mid: &str) -> Result<Option<Value>> {
        Ok(sqlx::query("select * from signage_media where id = ?").bind(mid).fetch_optional(&self.pool).await?.map(|r| row_json(&r)))
    }

    pub async fn media_add(&self, name: &str, sha: &str, ext: &str, size: i64, width: i64, height: i64, duration_s: f64) -> Result<Value> {
        let Some((kind, mime)) = media::type_of(ext) else { return invalid("That file is not an image or video signage can play.") };
        let mid = crate::rand_hex(8);
        let thumb = self.media.thumb_path(sha).is_some();
        sqlx::query("insert into signage_media (id, name, kind, mime, ext, size, sha256, width, height, duration_s, has_thumb, created_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
            .bind(&mid)
            .bind(clean_name(&json!(name), "file")?)
            .bind(kind)
            .bind(mime)
            .bind(ext)
            .bind(size)
            .bind(sha)
            .bind(width.max(0))
            .bind(height.max(0))
            .bind(duration_s.max(0.0))
            .bind(i64::from(thumb))
            .bind(now_iso())
            .execute(&self.pool)
            .await?;
        self.live.bump();
        Ok(self.media_get(&mid).await?.unwrap_or_else(|| json!({})))
    }

    pub async fn media_rename(&self, mid: &str, name: &Value) -> Result<()> {
        let name = clean_name(name, "file")?;
        if sqlx::query("update signage_media set name = ? where id = ?").bind(name).bind(mid).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("That file is not in the library.");
        }
        self.live.bump();
        Ok(())
    }

    pub async fn media_thumb(&self, mid: &str, data: &[u8]) -> Result<()> {
        let Some(row) = self.media_get(mid).await? else { return gone("That file is not in the library.") };
        let sha = row["sha256"].as_str().unwrap_or("").to_owned();
        self.media.save_thumb(&sha, data)?;
        sqlx::query("update signage_media set has_thumb = 1 where sha256 = ?").bind(&sha).execute(&self.pool).await?;
        Ok(())
    }

    pub async fn media_delete(&self, mid: &str) -> Result<()> {
        let Some(row) = self.media_get(mid).await? else { return gone("That file is not in the library.") };
        let names: Vec<String> = sqlx::query("select distinct p.name from signage_items i join signage_playlists p on p.id = i.playlist_id where i.source = 'media' and i.ref = ? order by p.name")
            .bind(mid)
            .fetch_all(&self.pool)
            .await?
            .iter()
            .map(|r| r.get::<String, _>("name"))
            .collect();
        if !names.is_empty() {
            return Err(Error::Invalid(format!("That file is in use in {}. Take it out of those playlists first.", names.join(", ")), 409));
        }
        sqlx::query("delete from signage_media where id = ?").bind(mid).execute(&self.pool).await?;
        let sha = row["sha256"].as_str().unwrap_or("");
        let still: Vec<String> = sqlx::query_scalar("select sha256 from signage_media where sha256 = ?").bind(sha).fetch_all(&self.pool).await?;
        self.media.remove_if_unused(sha, row["ext"].as_str().unwrap_or(""), &still);
        self.live.bump();
        Ok(())
    }

    pub async fn media_hashes(&self) -> Result<Vec<String>> {
        Ok(sqlx::query_scalar("select sha256 from signage_media").fetch_all(&self.pool).await?)
    }

    /// What the player shows for an item of source `kind`, or `None` to skip it. Uploaded media is signage's own; another module's source
    /// is whatever it provides as `signage.source.<kind>` while it is on (a module not ported yet provides none, so its items are skipped).
    pub async fn source_resolve(&self, kind: &str, reference: &str) -> Result<Option<Value>> {
        if kind != "media" {
            let Some(src) = self.host.using::<SignageSource>(&format!("{SIGNAGE_SOURCE}{kind}")) else { return Ok(None) };
            return Ok((src.resolve)(reference).and_then(|v| clean_target(&v)));
        }
        Ok(sqlx::query("select kind, sha256, ext from signage_media where id = ?").bind(reference).fetch_optional(&self.pool).await?.map(|r| {
            json!({"type": r.get::<String, _>("kind"), "url": format!("{}{}", media::PUBLIC_PREFIX, media::file_name(&r.get::<String, _>("sha256"), &r.get::<String, _>("ext")))})
        }))
    }

    async fn source_describe(&self, kind: &str, reference: &str) -> Result<Value> {
        if kind != "media" {
            return Ok(match self.host.using::<SignageSource>(&format!("{SIGNAGE_SOURCE}{kind}")) {
                None => json!({"name": reference, "thumb": "", "missing": true}),
                Some(src) => {
                    let d = (src.describe)(reference);
                    json!({"name": d["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(reference), "thumb": d["thumb"].as_str().unwrap_or(""), "missing": d["missing"] == true})
                }
            });
        }
        Ok(match sqlx::query("select id, name, has_thumb from signage_media where id = ?").bind(reference).fetch_optional(&self.pool).await? {
            None => json!({"name": "Deleted file", "thumb": "", "missing": true}),
            Some(r) => json!({"name": r.get::<String, _>("name"), "missing": false,
                "thumb": if r.get::<i64, _>("has_thumb") != 0 { format!("/api/admin/signage/media/{}/thumb", r.get::<String, _>("id")) } else { String::new() }}),
        })
    }

    /// What a kind of item is called on the admin page.
    pub fn source_label(&self, kind: &str) -> String {
        if kind == "media" {
            return "Uploaded image or video".to_owned();
        }
        self.host.using::<SignageSource>(&format!("{SIGNAGE_SOURCE}{kind}")).map_or_else(|| kind.to_owned(), |s| s.label.clone())
    }

    /// Every kind of item that can be added now: uploads, then each module's source that is on.
    pub fn source_kinds(&self) -> Vec<Value> {
        let mut out = vec![json!({"kind": "media", "label": "Uploaded image or video"})];
        let mut more: Vec<(String, std::sync::Arc<SignageSource>)> = self.host.using_all::<SignageSource>(SIGNAGE_SOURCE);
        more.sort_by(|a, b| a.0.cmp(&b.0));
        out.extend(more.into_iter().map(|(k, s)| json!({"kind": k, "label": s.label})));
        out
    }

    // ------------------------------------------------------------------ playlists
    fn clean_item(&self, raw: &Value, position: usize, media_ids: &[String]) -> Result<(i64, String, String, f64, String, String, String, String)> {
        let Some(raw) = raw.as_object() else { return invalid("Each item must be an object.") };
        let source = match raw.get("source") {
            None | Some(Value::Null) => "media".to_owned(),
            Some(Value::String(s)) if s.is_empty() => "media".to_owned(),
            Some(v) => v.as_str().map_or_else(|| v.to_string(), str::to_owned),
        };
        let reference = raw.get("ref").map(|v| v.as_str().map_or_else(|| v.to_string(), str::to_owned)).unwrap_or_default();
        let source_ok = {
            let b = source.as_bytes();
            !b.is_empty() && b.len() <= 41 && b[0].is_ascii_lowercase() && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_')
        };
        if !source_ok || reference.is_empty() || reference.chars().count() > 500 {
            return invalid("An item has no file or source.");
        }
        if source == "media" && !media_ids.contains(&reference) {
            return invalid("An item names a file that is not in the library.");
        }
        let duration = match raw.get("duration_s") {
            None => 10.0,
            Some(Value::Null) | Some(Value::Bool(false)) => 0.0,
            Some(Value::Number(n)) => n.as_f64().unwrap_or(0.0),
            Some(Value::String(s)) => s.trim().parse::<f64>().or_else(|_| invalid("A duration must be a number of seconds.").map(|_: ()| 0.0))?,
            Some(_) => return invalid("A duration must be a number of seconds."),
        };
        if !(0.0..=86400.0).contains(&duration) || duration.is_nan() {
            return invalid("A duration must be between 0 seconds and a day.");
        }
        let (start, end) = (clean_date(raw.get("start_date").unwrap_or(&Value::Null))?, clean_date(raw.get("end_date").unwrap_or(&Value::Null))?);
        if !start.is_empty() && !end.is_empty() && end < start {
            return invalid("An item ends before it starts.");
        }
        let pick = |k: &str, default: &str| match raw.get(k) {
            None | Some(Value::Null) => default.to_owned(),
            Some(v) => v.as_str().unwrap_or("?").to_owned(),
        };
        let (transition, fit) = (pick("transition", "fade"), pick("fit", "fit"));
        let (transition, fit) = (if transition.is_empty() { "fade".to_owned() } else { transition }, if fit.is_empty() { "fit".to_owned() } else { fit });
        if !["cut", "fade"].contains(&transition.as_str()) || !["fit", "fill"].contains(&fit.as_str()) {
            return invalid("Transition is cut or fade, and fit is fit or fill.");
        }
        Ok((position as i64, source, reference, duration, start, end, transition, fit))
    }

    pub async fn playlists(&self) -> Result<Vec<Value>> {
        let mut lists = rows_json(&sqlx::query("select * from signage_playlists order by name collate nocase, id").fetch_all(&self.pool).await?);
        let items = rows_json(&sqlx::query("select * from signage_items order by playlist_id, position").fetch_all(&self.pool).await?);
        let mut by: std::collections::HashMap<i64, Vec<Value>> = Default::default();
        for mut it in items {
            let described = self.source_describe(it["source"].as_str().unwrap_or(""), it["ref"].as_str().unwrap_or("")).await?;
            if let (Some(o), Some(d)) = (it.as_object_mut(), described.as_object()) {
                o.extend(d.clone());
            }
            it["source_label"] = json!(self.source_label(it["source"].as_str().unwrap_or("")));
            by.entry(it["playlist_id"].as_i64().unwrap_or(0)).or_default().push(it);
        }
        for p in &mut lists {
            p["items"] = json!(by.remove(&id_of(p)).unwrap_or_default());
        }
        Ok(lists)
    }

    pub async fn playlist_get(&self, pid: i64) -> Result<Option<Value>> {
        Ok(sqlx::query("select * from signage_playlists where id = ?").bind(pid).fetch_optional(&self.pool).await?.map(|r| row_json(&r)))
    }

    pub async fn playlist_create(&self, name: &Value) -> Result<Value> {
        let name = clean_name(name, "playlist")?;
        let now = now_iso();
        let mut tx = self.pool.begin().await?;
        sqlx::query("insert into signage_playlists (name, created_at, updated_at) values (?, ?, ?)").bind(name).bind(&now).bind(&now).execute(&mut *tx).await?;
        let row = sqlx::query("select * from signage_playlists where id = last_insert_rowid()").fetch_one(&mut *tx).await?;
        tx.commit().await?;
        self.live.bump();
        Ok(row_json(&row))
    }

    pub async fn playlist_rename(&self, pid: i64, name: &Value) -> Result<()> {
        let name = clean_name(name, "playlist")?;
        if sqlx::query("update signage_playlists set name = ?, updated_at = ? where id = ?").bind(name).bind(now_iso()).bind(pid).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("That playlist does not exist.");
        }
        self.live.bump();
        Ok(())
    }

    pub async fn playlist_duplicate(&self, pid: i64, name: &str) -> Result<Value> {
        let Some(src) = self.playlist_get(pid).await? else { return gone("That playlist does not exist.") };
        let now = now_iso();
        let label = if name.is_empty() { format!("{} (copy)", src["name"].as_str().unwrap_or("")) } else { name.to_owned() };
        let mut tx = self.pool.begin().await?;
        sqlx::query("insert into signage_playlists (name, created_at, updated_at) values (?, ?, ?)").bind(clean_name(&json!(label), "playlist")?).bind(&now).bind(&now).execute(&mut *tx).await?;
        let new = row_json(&sqlx::query("select * from signage_playlists where id = last_insert_rowid()").fetch_one(&mut *tx).await?);
        sqlx::query("insert into signage_items (playlist_id, position, source, ref, duration_s, start_date, end_date, transition, fit) select ?, position, source, ref, duration_s, start_date, end_date, transition, fit from signage_items where playlist_id = ? order by position")
            .bind(id_of(&new))
            .bind(pid)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        self.live.bump();
        Ok(new)
    }

    pub async fn playlist_delete(&self, pid: i64) -> Result<()> {
        if sqlx::query("delete from signage_playlists where id = ?").bind(pid).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("That playlist does not exist.");
        }
        self.live.bump();
        Ok(())
    }

    /// Replace a playlist's items with `items`, in order (add, reorder and remove are all this one call).
    pub async fn playlist_set_items(&self, pid: i64, items: &Value) -> Result<()> {
        if self.playlist_get(pid).await?.is_none() {
            return gone("That playlist does not exist.");
        }
        let Some(items) = items.as_array().filter(|a| a.len() <= ITEMS_MAX) else { return invalid(&format!("A playlist holds up to {ITEMS_MAX} items.")) };
        let media_ids: Vec<String> = sqlx::query_scalar("select id from signage_media").fetch_all(&self.pool).await?;
        let rows: Vec<_> = items.iter().enumerate().map(|(i, raw)| self.clean_item(raw, i, &media_ids)).collect::<Result<_>>()?;
        let mut tx = self.pool.begin().await?;
        sqlx::query("delete from signage_items where playlist_id = ?").bind(pid).execute(&mut *tx).await?;
        for r in rows {
            sqlx::query("insert into signage_items (playlist_id, position, source, ref, duration_s, start_date, end_date, transition, fit) values (?, ?, ?, ?, ?, ?, ?, ?, ?)")
                .bind(pid)
                .bind(r.0)
                .bind(r.1)
                .bind(r.2)
                .bind(r.3)
                .bind(r.4)
                .bind(r.5)
                .bind(r.6)
                .bind(r.7)
                .execute(&mut *tx)
                .await?;
        }
        sqlx::query("update signage_playlists set updated_at = ? where id = ?").bind(now_iso()).bind(pid).execute(&mut *tx).await?;
        tx.commit().await?;
        self.live.bump();
        Ok(())
    }

    async fn playlist_ref(&self, v: &Value) -> Result<Option<i64>> {
        let pid = match v {
            Value::Null | Value::Bool(false) => return Ok(None),
            Value::String(s) if s.is_empty() => return Ok(None),
            Value::Number(n) if n.as_i64() == Some(0) => return Ok(None),
            Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
            Value::String(s) => s.trim().parse().ok(),
            _ => None,
        };
        let Some(pid) = pid else { return invalid("Choose a playlist.") };
        if self.playlist_get(pid).await?.is_none() {
            return gone("That playlist does not exist.");
        }
        Ok(Some(pid))
    }

    // ------------------------------------------------------------------ groups
    pub async fn groups(&self) -> Result<Vec<Value>> {
        Ok(rows_json(&sqlx::query("select * from signage_groups order by name collate nocase, id").fetch_all(&self.pool).await?))
    }

    pub async fn group_save(&self, gid: Option<i64>, raw: &Value) -> Result<Value> {
        let name = clean_name(raw.get("name").unwrap_or(&Value::Null), "group")?;
        let pid = self.playlist_ref(raw.get("playlist_id").unwrap_or(&Value::Null)).await?;
        let mut tx = self.pool.begin().await?;
        let row = match gid {
            None => {
                sqlx::query("insert into signage_groups (name, playlist_id) values (?, ?)").bind(&name).bind(pid).execute(&mut *tx).await?;
                sqlx::query("select * from signage_groups where id = last_insert_rowid()").fetch_one(&mut *tx).await?
            }
            Some(g) => {
                if sqlx::query("update signage_groups set name = ?, playlist_id = ? where id = ?").bind(&name).bind(pid).bind(g).execute(&mut *tx).await?.rows_affected() == 0 {
                    return gone("That group does not exist.");
                }
                sqlx::query("select * from signage_groups where id = ?").bind(g).fetch_one(&mut *tx).await?
            }
        };
        tx.commit().await?;
        self.live.bump();
        Ok(row_json(&row))
    }

    pub async fn group_delete(&self, gid: i64) -> Result<()> {
        if sqlx::query("delete from signage_groups where id = ?").bind(gid).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("That group does not exist.");
        }
        self.live.bump();
        Ok(())
    }

    // ------------------------------------------------------------------ screens
    /// A signage screen is a core screen (#527): the display's name is the core one, and a screen that has none yet is linked here,
    /// joining the core screen of the same name when there is one.
    async fn core_screen(&self, mut row: Value) -> Result<Value> {
        let key = format!("{SCREEN_KEY}{}", row["slug"].as_str().unwrap_or(""));
        let core = match self.screens.by_key(&key).await? {
            Some(c) => c,
            None => self.screens.adopt(&key, row["name"].as_str().unwrap_or(""), false).await?,
        };
        row["sound"] = json!(row["sound"].as_i64().unwrap_or(0) != 0);
        row["name"] = core["name"].clone();
        row["core_id"] = core["id"].clone();
        Ok(row)
    }

    pub async fn screens(&self) -> Result<Vec<Value>> {
        let mut out = Vec::new();
        for r in rows_json(&sqlx::query("select * from signage_screens order by name collate nocase, id").fetch_all(&self.pool).await?) {
            out.push(self.core_screen(r).await?);
        }
        out.sort_by_key(|r| (r["name"].as_str().unwrap_or("").to_lowercase(), id_of(r)));
        Ok(out)
    }

    pub async fn screen_by_slug(&self, slug: &str) -> Result<Option<Value>> {
        if !slug_ok(slug) {
            return Ok(None);
        }
        match sqlx::query("select * from signage_screens where slug = ?").bind(slug).fetch_optional(&self.pool).await? {
            Some(r) => Ok(Some(self.core_screen(row_json(&r)).await?)),
            None => Ok(None),
        }
    }

    pub fn player_path(slug: &str) -> String {
        format!("/signage/{slug}")
    }

    /// The signage address of a core screen, or `""` when none of its signage screens exists yet.
    pub async fn core_screen_slug(&self, core_id: i64) -> Result<String> {
        Ok(self.screens.keys(core_id).await?.into_iter().find_map(|k| k.strip_prefix(SCREEN_KEY).map(str::to_owned)).unwrap_or_default())
    }

    /// The signage screen for a core screen, made (with an address from its name) when it has none.
    pub async fn ensure_screen(&self, core: &Value) -> Result<Value> {
        let slug = self.core_screen_slug(id_of(core)).await?;
        if !slug.is_empty() {
            return Ok(self.screen_by_slug(&slug).await?.unwrap_or_else(|| json!({})));
        }
        let taken: Vec<String> = sqlx::query_scalar("select slug from signage_screens").fetch_all(&self.pool).await?;
        let name = core["name"].as_str().unwrap_or("");
        let new = slug_for(name, &taken);
        sqlx::query("insert into signage_screens (slug, name, sound, created_at) values (?, ?, 0, ?)").bind(&new).bind(name).bind(now_iso()).execute(&self.pool).await?;
        self.screens.adopt(&format!("{SCREEN_KEY}{new}"), name, false).await?;
        let row = sqlx::query("select * from signage_screens where slug = ?").bind(&new).fetch_one(&self.pool).await?;
        self.core_screen(row_json(&row)).await
    }

    /// The playlist a core screen plays when nothing above it in the schedule picks one.
    pub async fn set_default_playlist(&self, core_id: i64, playlist_id: Option<i64>) -> Result<()> {
        let slug = self.core_screen_slug(core_id).await?;
        if !slug.is_empty() && sqlx::query("update signage_screens set playlist_id = ? where slug = ?").bind(playlist_id).bind(&slug).execute(&self.pool).await?.rows_affected() > 0 {
            self.live.bump();
        }
        Ok(())
    }

    pub async fn screen_save(&self, sid: Option<i64>, raw: &Value) -> Result<Value> {
        let name = clean_name(raw.get("name").unwrap_or(&Value::Null), "screen")?;
        let slug = raw.get("slug").and_then(Value::as_str).unwrap_or("").trim().to_lowercase();
        if !slug_ok(&slug) {
            return invalid("The address name uses lowercase letters, numbers and dashes, like lobby-left.");
        }
        let pid = self.playlist_ref(raw.get("playlist_id").unwrap_or(&Value::Null)).await?;
        let gid = match raw.get("group_id") {
            None | Some(Value::Null) | Some(Value::Bool(false)) => None,
            Some(Value::Number(n)) if n.as_i64() == Some(0) => None,
            Some(v) => {
                let g = match v {
                    Value::Number(n) => n.as_i64(),
                    Value::String(s) => s.trim().parse().ok(),
                    _ => None,
                };
                let Some(g) = g else { return invalid("Choose a group.") };
                if sqlx::query("select id from signage_groups where id = ?").bind(g).fetch_optional(&self.pool).await?.is_none() {
                    return gone("That group does not exist.");
                }
                Some(g)
            }
        };
        let sound = i64::from(matches!(raw.get("sound"), Some(Value::Bool(true))) || raw.get("sound").and_then(Value::as_i64).is_some_and(|n| n != 0));
        if let Some(clash) = sqlx::query("select id from signage_screens where slug = ?").bind(&slug).fetch_optional(&self.pool).await? {
            if Some(clash.get::<i64, _>("id")) != sid {
                return Err(Error::Invalid("Another screen already uses that address.".into(), 409));
            }
        }
        let old: Option<String> = match sid {
            Some(s) => sqlx::query_scalar("select slug from signage_screens where id = ?").bind(s).fetch_optional(&self.pool).await?,
            None => None,
        };
        let mut tx = self.pool.begin().await?;
        let row = match sid {
            None => {
                sqlx::query("insert into signage_screens (slug, name, sound, playlist_id, group_id, created_at) values (?, ?, ?, ?, ?, ?)")
                    .bind(&slug)
                    .bind(&name)
                    .bind(sound)
                    .bind(pid)
                    .bind(gid)
                    .bind(now_iso())
                    .execute(&mut *tx)
                    .await?;
                sqlx::query("select * from signage_screens where id = last_insert_rowid()").fetch_one(&mut *tx).await?
            }
            Some(s) => {
                if sqlx::query("update signage_screens set slug = ?, name = ?, sound = ?, playlist_id = ?, group_id = ? where id = ?")
                    .bind(&slug)
                    .bind(&name)
                    .bind(sound)
                    .bind(pid)
                    .bind(gid)
                    .bind(s)
                    .execute(&mut *tx)
                    .await?
                    .rows_affected()
                    == 0
                {
                    return gone("That screen does not exist.");
                }
                sqlx::query("select * from signage_screens where id = ?").bind(s).fetch_one(&mut *tx).await?
            }
        };
        tx.commit().await?;
        if let Some(old) = old {
            self.screens.move_key(&format!("{SCREEN_KEY}{old}"), &format!("{SCREEN_KEY}{slug}")).await?;
        }
        let key = format!("{SCREEN_KEY}{slug}");
        let core = match self.screens.by_key(&key).await? {
            Some(c) => c,
            None => self.screens.adopt(&key, &name, false).await?,
        };
        if !relay_core_same_name(core["name"].as_str().unwrap_or(""), &name) {
            self.screens.rename(id_of(&core), &name, true).await?;
        }
        self.live.bump();
        self.core_screen(row_json(&row)).await
    }

    pub async fn screen_delete(&self, sid: i64) -> Result<()> {
        let slug: Option<String> = sqlx::query_scalar("select slug from signage_screens where id = ?").bind(sid).fetch_optional(&self.pool).await?;
        if sqlx::query("delete from signage_screens where id = ?").bind(sid).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("That screen does not exist.");
        }
        if let Some(slug) = slug {
            self.screens.forget_key(&format!("{SCREEN_KEY}{slug}")).await?;
        }
        self.live.bump();
        Ok(())
    }

    // ------------------------------------------------------------------ what a screen plays
    /// `(playlist id, why)` for a screen with nothing else claiming it: its group's playlist when the group has one, else its own default.
    pub async fn default_playlist(&self, screen: &Value) -> Result<(Option<i64>, &'static str)> {
        if let Some(g) = screen["group_id"].as_i64() {
            let pid: Option<Option<i64>> = sqlx::query_scalar("select playlist_id from signage_groups where id = ?").bind(g).fetch_optional(&self.pool).await?;
            if let Some(Some(pid)) = pid {
                return Ok((Some(pid), "group"));
            }
        }
        if let Some(p) = screen["playlist_id"].as_i64().filter(|p| *p != 0) {
            return Ok((Some(p), "screen"));
        }
        Ok((None, ""))
    }

    // ------------------------------------------------------------------ schedule slots and overrides (#206)
    pub async fn slots(&self) -> Result<Vec<Value>> {
        let mut rows = rows_json(&sqlx::query("select * from signage_slots order by id").fetch_all(&self.pool).await?);
        let names: Map<String, Value> = self.named_schedules().as_array().into_iter().flatten().map(|s| (format!("{}{}", schedules::REF_PREFIX, s["id"].as_str().unwrap_or("")), s["name"].clone())).collect();
        for r in &mut rows {
            r["summary"] = json!(schedule::describe_slot(r, &names));
        }
        Ok(rows)
    }

    /// The slots that pick named schedule `ref`, for the Schedules page's "used by".
    pub async fn schedule_users(&self, named: &str) -> Result<Vec<Value>> {
        let mut out = Vec::new();
        for r in self.slots().await? {
            if r["schedule_ref"] == named {
                out.push(json!({"where": "Signage", "what": format!("Slot on {}", self.target_label(&r).await?)}));
            }
        }
        Ok(out)
    }

    async fn target_label(&self, slot: &Value) -> Result<String> {
        let target = slot["target"].as_str().unwrap_or("");
        if target == "all" {
            return Ok("all screens".into());
        }
        let sql = if target == "screen" { "select name from signage_screens where id = ?" } else { "select name from signage_groups where id = ?" };
        let name: Option<String> = sqlx::query_scalar(sql).bind(slot["target_id"].as_i64().unwrap_or(0)).fetch_optional(&self.pool).await?;
        Ok(name.map_or_else(|| target.to_owned(), |n| format!("{target} {n}")))
    }

    async fn check_target(&self, target: &str, target_id: i64, playlist_id: i64) -> Result<()> {
        let missing = |sql: &'static str, id: i64| {
            let pool = self.pool.clone();
            async move { sqlx::query(sql).bind(id).fetch_optional(&pool).await.map(|r| r.is_none()) }
        };
        if target == "screen" && missing("select id from signage_screens where id = ?", target_id).await? {
            return gone("That screen does not exist.");
        }
        if target == "group" && missing("select id from signage_groups where id = ?", target_id).await? {
            return gone("That group does not exist.");
        }
        if missing("select id from signage_playlists where id = ?", playlist_id).await? {
            return gone("That playlist does not exist.");
        }
        Ok(())
    }

    pub async fn slot_save(&self, sid: Option<i64>, raw: &Value) -> Result<Value> {
        let row = schedule::clean_slot(raw).map_err(|e| Error::Invalid(e.to_owned(), 400))?;
        self.check_target(row["target"].as_str().unwrap_or(""), row["target_id"].as_i64().unwrap_or(0), row["playlist_id"].as_i64().unwrap_or(0)).await?;
        let named = row["schedule_ref"].as_str().unwrap_or("");
        if !named.is_empty() && schedules::find(&self.named_schedules(), named).is_none() {
            return gone("That schedule does not exist.");
        }
        const COLS: [&str; 9] = ["target", "target_id", "playlist_id", "days", "start_time", "end_time", "start_date", "end_date", "schedule_ref"];
        let mut tx = self.pool.begin().await?;
        let out = match sid {
            None => {
                let mut q = sqlx::query("insert into signage_slots (target, target_id, playlist_id, days, start_time, end_time, start_date, end_date, schedule_ref) values (?, ?, ?, ?, ?, ?, ?, ?, ?)");
                for c in COLS {
                    q = match &row[c] {
                        Value::Number(n) => q.bind(n.as_i64().unwrap_or(0)),
                        v => q.bind(v.as_str().unwrap_or("").to_owned()),
                    };
                }
                q.execute(&mut *tx).await?;
                sqlx::query("select * from signage_slots where id = last_insert_rowid()").fetch_one(&mut *tx).await?
            }
            Some(s) => {
                let mut q = sqlx::query("update signage_slots set target = ?, target_id = ?, playlist_id = ?, days = ?, start_time = ?, end_time = ?, start_date = ?, end_date = ?, schedule_ref = ? where id = ?");
                for c in COLS {
                    q = match &row[c] {
                        Value::Number(n) => q.bind(n.as_i64().unwrap_or(0)),
                        v => q.bind(v.as_str().unwrap_or("").to_owned()),
                    };
                }
                if q.bind(s).execute(&mut *tx).await?.rows_affected() == 0 {
                    return gone("That slot does not exist.");
                }
                sqlx::query("select * from signage_slots where id = ?").bind(s).fetch_one(&mut *tx).await?
            }
        };
        tx.commit().await?;
        self.live.bump();
        Ok(row_json(&out))
    }

    pub async fn slot_delete(&self, sid: i64) -> Result<()> {
        if sqlx::query("delete from signage_slots where id = ?").bind(sid).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("That slot does not exist.");
        }
        self.live.bump();
        Ok(())
    }

    pub async fn overrides(&self) -> Result<Vec<Value>> {
        Ok(rows_json(&sqlx::query("select * from signage_overrides where expires_at = '' or expires_at > ?").bind(now_iso()).fetch_all(&self.pool).await?))
    }

    async fn screens_for(&self, target: &str, target_id: i64) -> Result<Vec<Value>> {
        Ok(self.screens().await?.into_iter().filter(|s| target == "all" || (target == "screen" && id_of(s) == target_id) || (target == "group" && s["group_id"].as_i64() == Some(target_id))).collect())
    }

    /// Put `playlist_id` on a screen, a group or all screens. `until` is `next_change` (ends when the schedule next changes what any of
    /// those screens plays) or `cleared` (stays until an operator clears it).
    pub async fn override_set(&self, target: &str, target_id: i64, playlist_id: i64, until: &str) -> Result<Value> {
        if !schedule::TARGETS.contains(&target) {
            return invalid("Choose a screen, a group or all screens.");
        }
        let target_id = if target == "all" { 0 } else { target_id };
        self.check_target(target, target_id, playlist_id).await?;
        let mut expires = String::new();
        if until == "next_change" {
            let (rows, tz) = (self.slots().await?, self.tz_name());
            let targets = self.screens_for(target, target_id).await?;
            let open = |r: &str, d: Date, m: i64| self.open_ref(r, d, m);
            let key = |d: Date, m: i64| -> Vec<i64> { targets.iter().map(|s| schedule::matching(&rows, s, d, m, Some(&open)).first().map_or(0, |x| id_of(x))).collect() };
            if !targets.is_empty() {
                if let Some(at) = schedule::next_change(key, epoch_secs().div_euclid(60), &tz) {
                    expires = timewin::iso(at * 60);
                }
            }
        }
        let mut tx = self.pool.begin().await?;
        sqlx::query("delete from signage_overrides where target = ? and target_id = ?").bind(target).bind(target_id).execute(&mut *tx).await?;
        sqlx::query("insert into signage_overrides (target, target_id, playlist_id, set_at, expires_at) values (?, ?, ?, ?, ?)").bind(target).bind(target_id).bind(playlist_id).bind(now_iso()).bind(&expires).execute(&mut *tx).await?;
        tx.commit().await?;
        self.live.bump();
        Ok(json!({"target": target, "target_id": target_id, "playlist_id": playlist_id, "expires_at": expires}))
    }

    pub async fn override_clear(&self, target: &str, target_id: i64) -> Result<()> {
        let target_id = if target == "all" { 0 } else { target_id };
        if sqlx::query("delete from signage_overrides where target = ? and target_id = ?").bind(target).bind(target_id).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("There is no override to clear there.");
        }
        self.live.bump();
        Ok(())
    }

    /// Delete overrides that ran out (housekeeping; resolving already ignores them).
    pub async fn sweep_overrides(&self) -> Result<()> {
        if sqlx::query("delete from signage_overrides where expires_at != '' and expires_at <= ?").bind(now_iso()).execute(&self.pool).await?.rows_affected() > 0 {
            self.live.bump();
        }
        Ok(())
    }

    /// What a screen plays and why: `{"playlist_id", "layer", "why", "until"}` (`playlist_id` null when nothing is assigned). `layer` is
    /// override, schedule, group or screen; `until` is when an override ends.
    pub async fn resolve(&self, screen: &Value, now_secs: i64, slots: Option<&[Value]>, overrides: Option<&[Value]>) -> Result<Value> {
        let iso = timewin::iso(now_secs);
        let loaded;
        let overrides = match overrides {
            Some(o) => o,
            None => {
                loaded = self.overrides().await?;
                &loaded
            }
        };
        let mut best: Option<(i64, &Value)> = None;
        for o in overrides {
            if !o["expires_at"].as_str().unwrap_or("").is_empty() && o["expires_at"].as_str().unwrap_or("") <= iso.as_str() {
                continue;
            }
            let rank = match o["target"].as_str().unwrap_or("") {
                "screen" => 0,
                "group" => 1,
                _ => 2,
            };
            let applies = o["target"] == "all" || (o["target"] == "screen" && o["target_id"] == screen["id"]) || (o["target"] == "group" && !screen["group_id"].is_null() && screen["group_id"] == o["target_id"]);
            if applies && best.is_none_or(|b| rank < b.0) {
                best = Some((rank, o));
            }
        }
        if let Some((_, o)) = best {
            let what = match o["target"].as_str().unwrap_or("") {
                "screen" => "this screen",
                "group" => "its group",
                _ => "all screens",
            };
            return Ok(json!({"playlist_id": o["playlist_id"], "layer": "override", "until": o["expires_at"], "why": format!("Put on by hand for {what}")}));
        }
        let loaded_slots;
        let rows = match slots {
            Some(s) => s,
            None => {
                loaded_slots = self.slots().await?;
                &loaded_slots
            }
        };
        let (date, minute) = schedules::local_of_utc(now_secs.div_euclid(60), &self.tz_name());
        let open = |r: &str, d: Date, m: i64| self.open_ref(r, d, m);
        let hit = schedule::matching(rows, screen, date, minute, Some(&open));
        if let Some(first) = hit.first() {
            let more = hit.len() - 1;
            let extra = if more > 0 { format!(" ({more} other slot{} also match)", if more > 1 { "s" } else { "" }) } else { String::new() };
            return Ok(json!({"playlist_id": first["playlist_id"], "layer": "schedule", "until": "", "why": format!("Schedule: {}{extra}", first["summary"].as_str().unwrap_or(""))}));
        }
        let (pid, why) = self.default_playlist(screen).await?;
        let label = match why {
            "group" => "No schedule matches now; its group's playlist",
            "screen" => "No schedule matches now; the screen's default playlist",
            _ => "Nothing assigned",
        };
        Ok(json!({"playlist_id": pid, "layer": if why.is_empty() { "none" } else { why }, "why": label, "until": ""}))
    }

    /// The items of playlist `pid` that play on `day`, as the player wants them. Items whose source is off or whose file is gone are left out.
    pub async fn playable_items(&self, pid: i64, day: &str) -> Result<Vec<Value>> {
        let mut out = Vec::new();
        for it in rows_json(&sqlx::query("select * from signage_items where playlist_id = ? order by position").bind(pid).fetch_all(&self.pool).await?) {
            let (from, to) = (it["start_date"].as_str().unwrap_or(""), it["end_date"].as_str().unwrap_or(""));
            if !((from.is_empty() || from <= day) && (to.is_empty() || day <= to)) {
                continue;
            }
            let Some(target) = self.source_resolve(it["source"].as_str().unwrap_or(""), it["ref"].as_str().unwrap_or("")).await? else { continue };
            let mut item = json!({"id": it["id"]});
            if let (Some(o), Some(t)) = (item.as_object_mut(), target.as_object()) {
                o.extend(t.clone());
            }
            for k in ["duration_s", "transition", "fit", "start_date", "end_date"] {
                item[k] = it[k].clone();
            }
            out.push(item);
        }
        Ok(out)
    }

    /// What the player at `/signage/<slug>` needs: the screen, its playlist and each item it can play today.
    pub async fn player_state(&self, screen: &Value, now_secs: i64) -> Result<Value> {
        let day = self.today(Some(now_secs));
        let plan = self.resolve(screen, now_secs, None, None).await?;
        let playlist = match plan["playlist_id"].as_i64() {
            Some(p) => self.playlist_get(p).await?,
            None => None,
        };
        let items = match &playlist {
            Some(p) => self.playable_items(id_of(p), &day).await?,
            None => vec![],
        };
        Ok(json!({"screen": {"slug": screen["slug"], "name": screen["name"], "sound": screen["sound"] == true},
            "playlist": playlist.map(|p| json!({"id": p["id"], "name": p["name"], "from": plan["layer"]})),
            "items": items, "today": day}))
    }
}

fn relay_core_same_name(a: &str, b: &str) -> bool {
    let fold = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase();
    let fa = fold(a);
    !fa.is_empty() && fa == fold(b)
}

/// What a source may hand the player: one of three types and a URL, with a page's few extras bounded (`sources.resolve`).
fn clean_target(out: &Value) -> Option<Value> {
    let kind = out["type"].as_str().filter(|t| ["image", "video", "page"].contains(t))?;
    let url = out["url"].as_str()?;
    let mut res = json!({"type": kind, "url": url});
    if kind == "page" {
        for key in ["probe", "url_sound"] {
            if let Some(v) = out[key].as_str().filter(|v| v.chars().count() <= 600) {
                res[key] = json!(v);
            }
        }
        if out["post_message"].is_object() {
            res["post_message"] = out["post_message"].clone();
        }
        if let Some(ends) = out["end_when"].as_array().filter(|e| !e.is_empty() && e.iter().all(Value::is_object)) {
            res["end_when"] = json!(ends.iter().take(8).collect::<Vec<_>>());
        }
    }
    Some(res)
}
