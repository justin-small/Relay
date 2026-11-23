//! Screens and places (#527, part of the shared-concepts epic #521): `app/screens.py`. One record per physical display: a browser
//! (a TV, a tablet) or a hardware panel, with one name, one place and an address. Boards, signage, alerts and panels attach to it
//! instead of keeping ids of their own. The tables are the Python migration 0008's, on the same `relay.db`.
//!
//! A ref names one: `screen:relay:<id>` and `place:relay:<id>`. The older ids a feature knew a display by are *keys*
//! (`board:<id>`, `signage:<slug>`, `panel:<id>`, `alert:<name>`): `adopt` turns one into the screen, creating it the first time.

use serde_json::{json, Map, Value};
use sqlx::sqlite::SqliteRow;
use sqlx::{Row, SqlitePool};

use crate::text::same_name;

pub const NAME_MAX: usize = 40;
pub const MAX_SCREENS: i64 = 500;
pub const MAX_PLACES: i64 = 100;
pub const SHOWS: [&str; 2] = ["board", "signage"];
pub const ONLINE_AFTER_S: f64 = 60.0;

/// A refusal; `key` picks the fixed sentence.
#[derive(Debug, PartialEq, Eq)]
pub enum Error {
    Refused(&'static str),
    Unavailable(String),
}

impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Error::Unavailable(e.to_string())
    }
}

pub type Result<T> = std::result::Result<T, Error>;

pub fn sentence(key: &str) -> &'static str {
    match key {
        "name" => "Give it a name.",
        "name_taken" => "Another place already has that name.",
        "no_screen" => "That screen does not exist.",
        "no_place" => "That place does not exist.",
        "too_many" => "That is as many as Relay keeps.",
        "shows" => "Choose a board or a signage playlist.",
        "panel_name" => "A panel is renamed on the Panels page.",
        _ => "That cannot be done.",
    }
}

pub fn screen_ref(id: i64) -> String {
    format!("screen:relay:{id}")
}

pub fn place_ref(id: i64) -> String {
    format!("place:relay:{id}")
}

fn parse(text: &str, head: &str) -> Option<i64> {
    let parts: Vec<&str> = text.split(':').collect();
    if parts.len() != 3 || parts[0] != head || parts[1] != "relay" || parts[2].is_empty() || !parts[2].bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    parts[2].parse().ok()
}

/// The id in `screen:relay:5`, or `None` for anything else.
pub fn screen_id(text: &str) -> Option<i64> {
    parse(text, "screen")
}

/// The id in `place:relay:5`, or `None` for anything else.
pub fn place_id(text: &str) -> Option<i64> {
    parse(text, "place")
}

fn name_of(raw: &str, limit: usize) -> String {
    raw.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(limit).collect::<String>().trim().to_owned()
}

fn now() -> String {
    relay_auth::now()
}

pub struct Screens {
    pool: SqlitePool,
}

fn screen_row(r: &SqliteRow) -> Value {
    let place: Option<i64> = r.get("place_id");
    json!({"id": r.get::<i64, _>("id"), "name": r.get::<String, _>("name"), "place_id": place, "kind": r.get::<String, _>("kind"),
        "address": r.get::<String, _>("address"), "last_seen": r.get::<String, _>("last_seen"), "created_at": r.get::<String, _>("created_at"),
        "normal_kind": r.get::<String, _>("normal_kind"), "normal_ref": r.get::<String, _>("normal_ref"),
        "during_schedule": r.get::<String, _>("during_schedule"), "during_kind": r.get::<String, _>("during_kind"),
        "during_ref": r.get::<String, _>("during_ref"), "last_board": r.get::<String, _>("last_board"),
        "hold_kind": r.get::<String, _>("hold_kind"), "hold_ref": r.get::<String, _>("hold_ref"),
        "ref": screen_ref(r.get::<i64, _>("id")), "place_ref": place.map(place_ref).unwrap_or_default()})
}

/// Whether `relay.db` has the screens tables (migration 0008). A file the Rust server made itself does not; Python brings it up to date.
pub async fn check_tables(pool: &SqlitePool) -> std::result::Result<(), String> {
    for (table, probe) in [("places", "select 1 from places limit 1"), ("screens", "select 1 from screens limit 1"), ("screen_keys", "select 1 from screen_keys limit 1"), ("alert_history", "select 1 from alert_history limit 1")] {
        if let Err(e) = sqlx::query(probe).fetch_optional(pool).await {
            return Err(format!("relay.db has no {table} table yet ({e}); start the Python Relay once to update it"));
        }
    }
    Ok(())
}

impl Screens {
    pub fn new(pool: SqlitePool) -> Self {
        Self { pool }
    }

    // -- places ---------------------------------------------------------------------------------------------
    /// Every place with the number of screens in it: `{"id", "ref", "name", "screens"}`.
    pub async fn places(&self) -> Result<Vec<Value>> {
        let rows = sqlx::query("select p.id, p.name, (select count(*) from screens s where s.place_id = p.id) n from places p order by p.name collate nocase").fetch_all(&self.pool).await?;
        Ok(rows.iter().map(|r| json!({"id": r.get::<i64, _>("id"), "ref": place_ref(r.get("id")), "name": r.get::<String, _>("name"), "screens": r.get::<i64, _>("n")})).collect())
    }

    pub async fn place_by_name(&self, name: &str) -> Result<Option<Value>> {
        let row = sqlx::query("select id, name from places where name = ? collate nocase").bind(name_of(name, NAME_MAX)).fetch_optional(&self.pool).await?;
        Ok(row.map(|r| json!({"id": r.get::<i64, _>("id"), "ref": place_ref(r.get("id")), "name": r.get::<String, _>("name")})))
    }

    /// A new place. A name that is already one returns that place.
    pub async fn place_add(&self, name: &str) -> Result<Value> {
        let text = name_of(name, NAME_MAX);
        if text.is_empty() {
            return Err(Error::Refused("name"));
        }
        if let Some(found) = self.place_by_name(&text).await? {
            return Ok(found);
        }
        let n: i64 = sqlx::query("select count(*) n from places").fetch_one(&self.pool).await?.get("n");
        if n >= MAX_PLACES {
            return Err(Error::Refused("too_many"));
        }
        sqlx::query("insert into places (name, created_at) values (?, ?)").bind(&text).bind(now()).execute(&self.pool).await?;
        self.place_by_name(&text).await?.ok_or(Error::Unavailable("the new place was not found".into()))
    }

    pub async fn place_rename(&self, pid: i64, name: &str) -> Result<()> {
        let text = name_of(name, NAME_MAX);
        if text.is_empty() {
            return Err(Error::Refused("name"));
        }
        let other = sqlx::query("select id from places where name = ? collate nocase").bind(&text).fetch_optional(&self.pool).await?;
        if other.is_some_and(|o| o.get::<i64, _>("id") != pid) {
            return Err(Error::Refused("name_taken"));
        }
        if sqlx::query("update places set name = ? where id = ?").bind(&text).bind(pid).execute(&self.pool).await?.rows_affected() == 0 {
            return Err(Error::Refused("no_place"));
        }
        Ok(())
    }

    /// Remove a place; its screens stay, with no place.
    pub async fn place_delete(&self, pid: i64) -> Result<()> {
        if sqlx::query("delete from places where id = ?").bind(pid).execute(&self.pool).await?.rows_affected() == 0 {
            return Err(Error::Refused("no_place"));
        }
        Ok(())
    }

    // -- screens --------------------------------------------------------------------------------------------
    pub async fn get(&self, sid: i64) -> Result<Option<Value>> {
        Ok(sqlx::query("select * from screens where id = ?").bind(sid).fetch_optional(&self.pool).await?.map(|r| screen_row(&r)))
    }

    pub async fn by_name(&self, name: &str) -> Result<Option<Value>> {
        let text = name_of(name, NAME_MAX);
        if text.is_empty() {
            return Ok(None);
        }
        Ok(sqlx::query("select * from screens where name = ? collate nocase").bind(text).fetch_optional(&self.pool).await?.map(|r| screen_row(&r)))
    }

    pub async fn by_key(&self, key: &str) -> Result<Option<Value>> {
        Ok(sqlx::query("select s.* from screens s join screen_keys k on k.screen_id = s.id where k.key = ?").bind(key).fetch_optional(&self.pool).await?.map(|r| screen_row(&r)))
    }

    /// The screens in a place: `(name, panel id)`, the panel id "" for a browser.
    pub async fn in_place(&self, pid: i64) -> Result<Vec<(String, String)>> {
        let rows = sqlx::query("select s.name, (select substr(k.key, 7) from screen_keys k where k.screen_id = s.id and k.key like 'panel:%' limit 1) panel_id from screens s where s.place_id = ? order by s.name collate nocase")
            .bind(pid)
            .fetch_all(&self.pool)
            .await?;
        Ok(rows.iter().map(|r| (r.get::<String, _>("name"), r.get::<Option<String>, _>("panel_id").unwrap_or_default())).collect())
    }

    /// The same, with ids and kinds, for the Screens page.
    pub async fn in_place_rows(&self, pid: i64) -> Result<Vec<Value>> {
        let rows = sqlx::query("select s.id, s.name, s.kind, (select substr(k.key, 7) from screen_keys k where k.screen_id = s.id and k.key like 'panel:%' limit 1) panel_id from screens s where s.place_id = ? order by s.name collate nocase")
            .bind(pid)
            .fetch_all(&self.pool)
            .await?;
        Ok(rows.iter().map(|r| json!({"id": r.get::<i64, _>("id"), "name": r.get::<String, _>("name"), "kind": r.get::<String, _>("kind"), "panel_id": r.get::<Option<String>, _>("panel_id").unwrap_or_default()})).collect())
    }

    pub async fn keys(&self, sid: i64) -> Result<Vec<String>> {
        Ok(sqlx::query("select key from screen_keys where screen_id = ? order by key").bind(sid).fetch_all(&self.pool).await?.iter().map(|r| r.get::<String, _>("key")).collect())
    }

    /// `{id after the prefix: the screen's name}` for every key that starts with `prefix` (`signage:`, `board:`).
    pub async fn names_by_key(&self, prefix: &str) -> Result<Map<String, Value>> {
        let like = format!("{}%", prefix.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_"));
        let rows = sqlx::query("select k.key, s.name from screen_keys k join screens s on s.id = k.screen_id where k.key like ? escape '\\'").bind(like).fetch_all(&self.pool).await?;
        Ok(rows.iter().map(|r| (r.get::<String, _>("key")[prefix.len()..].to_owned(), json!(r.get::<String, _>("name")))).collect())
    }

    /// Every screen, with its place name and the keys it is known by, by place then name.
    pub async fn listing(&self) -> Result<Vec<Value>> {
        let mut keyed: std::collections::HashMap<i64, Vec<String>> = Default::default();
        for r in sqlx::query("select key, screen_id from screen_keys order by key").fetch_all(&self.pool).await? {
            keyed.entry(r.get("screen_id")).or_default().push(r.get("key"));
        }
        let rows = sqlx::query("select s.*, p.name place from screens s left join places p on p.id = s.place_id order by (p.name is null), p.name collate nocase, s.name collate nocase").fetch_all(&self.pool).await?;
        Ok(rows
            .iter()
            .map(|r| {
                let mut v = screen_row(r);
                v["place"] = json!(r.get::<Option<String>, _>("place").unwrap_or_default());
                v["keys"] = json!(keyed.get(&r.get::<i64, _>("id")).cloned().unwrap_or_default());
                v
            })
            .collect())
    }

    async fn free_name(tx: &mut sqlx::SqliteConnection, base: &str, own: Option<i64>) -> Result<String> {
        let mut text: String = base.chars().take(NAME_MAX).collect();
        let mut n = 2;
        loop {
            let row = sqlx::query("select id from screens where name = ? collate nocase").bind(&text).fetch_optional(&mut *tx).await?;
            if row.is_none_or(|r| Some(r.get::<i64, _>("id")) == own) {
                return Ok(text);
            }
            let suffix = format!(" {n}");
            text = format!("{}{suffix}", base.chars().take(NAME_MAX - suffix.len()).collect::<String>());
            n += 1;
        }
    }

    /// The screen this older id stands for, made the first time it is seen and marked as heard from now. `name` is the name the
    /// feature knew it by: a new key whose name another screen already has joins that screen. `heard = false` only records the key.
    pub async fn adopt(&self, key: &str, name: &str, kind: &str, address: &str, board: &str, heard: bool) -> Result<Value> {
        if !["browser", "panel"].contains(&kind) {
            return Err(Error::Unavailable(format!("not a screen kind: {kind}")));
        }
        let text = name_of(name, NAME_MAX);
        let stamp = now();
        let mut tx = self.pool.begin().await?;
        let mut row = sqlx::query("select s.* from screens s join screen_keys k on k.screen_id = s.id where k.key = ?").bind(key).fetch_optional(&mut *tx).await?;
        if row.is_none() {
            row = if text.is_empty() { None } else { sqlx::query("select * from screens where name = ? collate nocase").bind(&text).fetch_optional(&mut *tx).await? };
            if row.is_none() {
                let n: i64 = sqlx::query("select count(*) n from screens").fetch_one(&mut *tx).await?.get("n");
                if n >= MAX_SCREENS {
                    return Err(Error::Refused("too_many"));
                }
                let tail: String = key.rsplit(':').next().unwrap_or("").chars().take(6).collect();
                let shown = if text.is_empty() { format!("screen {tail}") } else { text.clone() };
                let free = Self::free_name(&mut tx, &shown, None).await?;
                sqlx::query("insert into screens (name, kind, created_at) values (?, ?, ?)").bind(free).bind(kind).bind(&stamp).execute(&mut *tx).await?;
                row = sqlx::query("select * from screens where id = last_insert_rowid()").fetch_optional(&mut *tx).await?;
            }
            let id: i64 = row.as_ref().map(|r| r.get("id")).unwrap_or_default();
            sqlx::query("insert or ignore into screen_keys (key, screen_id) values (?, ?)").bind(key).bind(id).execute(&mut *tx).await?;
        }
        let id: i64 = row.map(|r| r.get("id")).unwrap_or_default();
        let address: String = address.chars().take(64).collect();
        sqlx::query("update screens set last_seen = case when ? then ? else last_seen end, address = coalesce(nullif(?, ''), address), last_board = coalesce(nullif(?, ''), last_board), kind = case when ? = 'panel' then 'panel' else kind end where id = ?")
            .bind(heard)
            .bind(&stamp)
            .bind(address)
            .bind(board)
            .bind(kind)
            .bind(id)
            .execute(&mut *tx)
            .await?;
        let out = sqlx::query("select * from screens where id = ?").bind(id).fetch_one(&mut *tx).await?;
        tx.commit().await?;
        Ok(screen_row(&out))
    }

    /// The screen was heard from just now, at this address, showing this board.
    pub async fn touch(&self, sid: i64, address: &str, board: &str) -> Result<()> {
        let address: String = address.chars().take(64).collect();
        sqlx::query("update screens set last_seen = ?, address = coalesce(nullif(?, ''), address), last_board = coalesce(nullif(?, ''), last_board) where id = ?").bind(now()).bind(address).bind(board).bind(sid).execute(&self.pool).await?;
        Ok(())
    }

    /// Name a screen. A name another screen has merges this one into it: that screen keeps the name and takes this one's keys, and
    /// its id is returned (the same id when nothing merged).
    pub async fn rename(&self, sid: i64, name: &str, panel_ok: bool) -> Result<i64> {
        let text = name_of(name, NAME_MAX);
        if text.is_empty() {
            return Err(Error::Refused("name"));
        }
        let mut tx = self.pool.begin().await?;
        let Some(row) = sqlx::query("select * from screens where id = ?").bind(sid).fetch_optional(&mut *tx).await? else { return Err(Error::Refused("no_screen")) };
        if row.get::<String, _>("kind") == "panel" && !panel_ok {
            return Err(Error::Refused("panel_name"));
        }
        let other = sqlx::query("select * from screens where name = ? collate nocase and id != ?").bind(&text).bind(sid).fetch_optional(&mut *tx).await?;
        let Some(other) = other else {
            sqlx::query("update screens set name = ? where id = ?").bind(&text).bind(sid).execute(&mut *tx).await?;
            tx.commit().await?;
            return Ok(sid);
        };
        let oid: i64 = other.get("id");
        sqlx::query("update screen_keys set screen_id = ? where screen_id = ?").bind(oid).bind(sid).execute(&mut *tx).await?;
        sqlx::query("update screens set place_id = coalesce(place_id, ?), last_board = case when last_board = '' then ? else last_board end where id = ?")
            .bind(row.get::<Option<i64>, _>("place_id"))
            .bind(row.get::<String, _>("last_board"))
            .bind(oid)
            .execute(&mut *tx)
            .await?;
        sqlx::query("delete from screens where id = ?").bind(sid).execute(&mut *tx).await?;
        tx.commit().await?;
        Ok(oid)
    }

    /// A panel's own name (Panels page) is the screen's name, kept even if that makes it differ from what an older screen was called.
    pub async fn sync_panel_name(&self, sid: i64, name: &str) -> Result<()> {
        let text = name_of(name, NAME_MAX);
        let Some(row) = self.get(sid).await? else { return Ok(()) };
        if text.is_empty() || same_name(row["name"].as_str().unwrap_or(""), &text) {
            return Ok(());
        }
        let mut tx = self.pool.begin().await?;
        let free = Self::free_name(&mut tx, &text, Some(sid)).await?;
        sqlx::query("update screens set name = ? where id = ?").bind(free).bind(sid).execute(&mut *tx).await?;
        tx.commit().await?;
        Ok(())
    }

    /// Put the screen in a place (a place id), or in none with `None`.
    pub async fn set_place(&self, sid: i64, place: Option<i64>) -> Result<()> {
        if let Some(p) = place {
            if sqlx::query("select id from places where id = ?").bind(p).fetch_optional(&self.pool).await?.is_none() {
                return Err(Error::Refused("no_place"));
            }
        }
        if sqlx::query("update screens set place_id = ? where id = ?").bind(place).bind(sid).execute(&self.pool).await?.rows_affected() == 0 {
            return Err(Error::Refused("no_screen"));
        }
        Ok(())
    }

    /// What the screen shows: `normal` and `during` are `(kind, ref)` (`""`, `""` for nothing), `schedule` is the ref of the named
    /// schedule that switches to `during`. A part left `None` is kept.
    pub async fn set_shows(&self, sid: i64, normal: Option<(&str, &str)>, during: Option<(&str, &str)>, schedule: Option<&str>) -> Result<()> {
        let row = self.get(sid).await?.ok_or(Error::Refused("no_screen"))?;
        let s = |k: &str| row[k].as_str().unwrap_or("").to_owned();
        let (n_kind, n_ref) = match normal {
            Some((k, r)) => clean_shows(k, r)?,
            None => (s("normal_kind"), s("normal_ref")),
        };
        let (d_kind, d_ref) = match during {
            Some((k, r)) => clean_shows(k, r)?,
            None => (s("during_kind"), s("during_ref")),
        };
        let mut sched = schedule.map_or_else(|| s("during_schedule"), |x| x.chars().take(80).collect());
        if d_kind.is_empty() {
            sched.clear();
        }
        sqlx::query("update screens set normal_kind = ?, normal_ref = ?, during_kind = ?, during_ref = ?, during_schedule = ? where id = ?")
            .bind(n_kind)
            .bind(n_ref)
            .bind(d_kind)
            .bind(d_ref)
            .bind(sched)
            .bind(sid)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    /// Put a board or playlist on the screen by hand, over its schedule, until `set_hold(sid, "", "")` clears it.
    pub async fn set_hold(&self, sid: i64, kind: &str, ref_: &str) -> Result<()> {
        let (kind, ref_) = clean_shows(kind, ref_)?;
        if sqlx::query("update screens set hold_kind = ?, hold_ref = ? where id = ?").bind(kind).bind(ref_).bind(sid).execute(&self.pool).await?.rows_affected() == 0 {
            return Err(Error::Refused("no_screen"));
        }
        Ok(())
    }

    pub async fn delete(&self, sid: i64) -> Result<()> {
        if sqlx::query("delete from screens where id = ?").bind(sid).execute(&self.pool).await?.rows_affected() == 0 {
            return Err(Error::Refused("no_screen"));
        }
        Ok(())
    }

    /// An older feature dropped this id (a signage screen was deleted); the screen itself stays.
    pub async fn forget_key(&self, key: &str) -> Result<()> {
        sqlx::query("delete from screen_keys where key = ?").bind(key).execute(&self.pool).await?;
        Ok(())
    }

    /// An older feature changed this id (a signage screen's address): the screen follows.
    pub async fn move_key(&self, old: &str, new: &str) -> Result<()> {
        if old != new {
            sqlx::query("update or ignore screen_keys set key = ? where key = ?").bind(new).bind(old).execute(&self.pool).await?;
        }
        Ok(())
    }

    /// A board was deleted: no screen shows it any more.
    pub async fn forget_board(&self, board_id: &str) -> Result<()> {
        for sql in [
            "update screens set normal_kind = '', normal_ref = '' where normal_kind = 'board' and normal_ref = ?",
            "update screens set during_kind = '', during_ref = '', during_schedule = '' where during_kind = 'board' and during_ref = ?",
            "update screens set hold_kind = '', hold_ref = '' where hold_kind = 'board' and hold_ref = ?",
            "update screens set last_board = '' where last_board = ?",
        ] {
            sqlx::query(sql).bind(board_id).execute(&self.pool).await?;
        }
        Ok(())
    }
}

fn clean_shows(kind: &str, ref_: &str) -> Result<(String, String)> {
    if kind.is_empty() {
        return Ok((String::new(), String::new()));
    }
    let text: String = ref_.trim().chars().take(80).collect();
    if !SHOWS.contains(&kind) || text.is_empty() {
        return Err(Error::Refused("shows"));
    }
    Ok((kind.to_owned(), text))
}

/// What the screen shows now: `{"kind": "board"|"signage"|"", "ref", "why"}`. `schedule_open(ref)` says whether a named schedule
/// is open. While Signage is off a signage choice falls back to the board the screen last showed.
pub fn showing(screen: &Value, schedule_open: impl Fn(&str) -> bool, signage_on: bool) -> Value {
    let s = |k: &str| screen[k].as_str().unwrap_or("").to_owned();
    let (mut kind, mut ref_, mut why) = (s("normal_kind"), s("normal_ref"), "all the time");
    if !s("during_kind").is_empty() && !s("during_schedule").is_empty() && schedule_open(&s("during_schedule")) {
        (kind, ref_, why) = (s("during_kind"), s("during_ref"), "while its schedule is open");
    }
    if !s("hold_kind").is_empty() {
        (kind, ref_, why) = (s("hold_kind"), s("hold_ref"), "held by hand");
    }
    if kind == "signage" && !signage_on {
        let mut board = s("last_board");
        if board.is_empty() && s("during_kind") == "board" {
            board = s("during_ref");
        }
        if s("normal_kind") == "board" && board.is_empty() {
            board = s("normal_ref");
        }
        return if board.is_empty() { json!({"kind": "", "ref": "", "why": ""}) } else { json!({"kind": "board", "ref": board, "why": "Signage is off"}) };
    }
    json!({"kind": kind, "ref": ref_, "why": if kind.is_empty() { "" } else { why }})
}

/// Whether the screen was heard from lately (`now` is epoch seconds).
pub fn online(screen: &Value, now: f64) -> bool {
    let Some(seen) = screen["last_seen"].as_str().and_then(epoch_of) else { return false };
    (0.0..=ONLINE_AFTER_S).contains(&(now - seen))
}

/// Epoch seconds of `YYYY-MM-DDTHH:MM:SS[.ffffff]Z`.
pub fn epoch_of(stamp: &str) -> Option<f64> {
    let s = stamp.trim_end_matches('Z');
    let (date, time) = s.split_once('T')?;
    let d: Vec<i64> = date.split('-').map(|p| p.parse().ok()).collect::<Option<_>>()?;
    let (hms, frac) = time.split_once('.').unwrap_or((time, ""));
    let t: Vec<i64> = hms.split(':').map(|p| p.parse().ok()).collect::<Option<_>>()?;
    if d.len() != 3 || t.len() != 3 {
        return None;
    }
    let (y, m, day) = (d[0] - i64::from(d[1] <= 2), d[1], d[2]);
    let era = y.div_euclid(400);
    let yoe = y.rem_euclid(400);
    let doy = (153 * (if m > 2 { m - 3 } else { m + 9 }) + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146_097 + doe - 719_468;
    let fraction = if frac.is_empty() { 0.0 } else { format!("0.{frac}").parse().unwrap_or(0.0) };
    Some((days * 86_400 + t[0] * 3600 + t[1] * 60 + t[2]) as f64 + fraction)
}
