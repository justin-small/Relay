//! The audit log (app/audit.py, #172): who did what and when. Sign-ins, user, role, device and token changes, and every change
//! made through /api/admin, each tied to the person (or API token) that made it. `action` is a dotted id ("signin.failed",
//! "user.update"); `summary` is the sentence people read; `detail` is a small object of plain values and never a secret.
//! Writing never fails the caller: a database that is down loses the entry and logs a warning. Entries are kept
//! [`KEEP_DAYS`] days and at most [`MAX_ROWS`].

use std::collections::BTreeSet;

use serde_json::{Map, Value};
use sqlx::sqlite::SqliteRow;
use sqlx::Row;

use crate::db::{later, now, Auth};
use crate::error::Result;
use crate::perms::User;

pub const KEEP_DAYS: i64 = 90;
pub const MAX_ROWS: i64 = 200_000;
pub const PAGE: i64 = 100;
const MAX_SUMMARY: usize = 300;
const MAX_TARGET: usize = 200;
const MAX_DETAIL: usize = 4000;

/// How long an entry is kept; kept under this name too for callers that read it as a setting.
pub const ACTION_KEEP_DAYS: i64 = KEEP_DAYS;

const COLS: &str = "id, at, actor_id, actor_name, actor_username, token_name, action, summary, target, detail, ip";

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct Entry {
    pub id: i64,
    pub at: String,
    pub actor_id: Option<i64>,
    pub actor_name: String,
    pub actor_username: String,
    pub token_name: String,
    pub action: String,
    pub summary: String,
    pub target: String,
    pub detail: Value,
    pub ip: String,
}

#[derive(Default)]
pub struct Search<'a> {
    /// A user id, or `system` for entries with no person.
    pub actor: Option<Actor>,
    /// An action's area ("signin") or a whole action.
    pub kind: Option<&'a str>,
    /// Text in the summary, target, person or address.
    pub q: Option<&'a str>,
    pub days: Option<i64>,
    /// An id to page from.
    pub before: Option<i64>,
    pub limit: Option<i64>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Actor {
    System,
    Id(i64),
}

#[derive(Debug, serde::Serialize)]
pub struct SearchResult {
    pub entries: Vec<Entry>,
    pub more: bool,
}

fn clip(v: &str, n: usize) -> String {
    v.chars().map(|c| if c.is_control() { ' ' } else { c }).take(n).collect()
}

fn valid_action(a: &str) -> bool {
    let parts: Vec<&str> = a.split('.').collect();
    (2..=4).contains(&parts.len())
        && parts.iter().all(|p| {
            let mut c = p.chars();
            matches!(c.next(), Some('a'..='z')) && c.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
        })
}

/// A JSON value with its keys sorted, as Python's `json.dumps(sort_keys=True)` writes it.
fn sorted(v: &Value) -> Value {
    match v {
        Value::Object(m) => {
            let keys: BTreeSet<&String> = m.keys().collect();
            let mut out = Map::new();
            for k in keys {
                out.insert(k.clone(), sorted(&m[k]));
            }
            Value::Object(out)
        }
        Value::Array(a) => Value::Array(a.iter().map(sorted).collect()),
        other => other.clone(),
    }
}

fn detail_text(detail: Option<&Value>) -> String {
    let Some(d) = detail.filter(|d| !d.is_null() && d.as_object().is_none_or(|m| !m.is_empty())) else { return String::new() };
    let text = serde_json::to_string(&sorted(d)).unwrap_or_default();
    if text.chars().count() <= MAX_DETAIL { text } else { "{\"truncated\": true}".into() }
}

fn like(s: &str) -> String {
    s.replace('\\', "\\\\").replace('%', "\\%").replace('_', "\\_")
}

fn entry(r: &SqliteRow) -> Entry {
    let text: String = r.get("detail");
    Entry {
        id: r.get("id"),
        at: r.get("at"),
        actor_id: r.get("actor_id"),
        actor_name: r.get("actor_name"),
        actor_username: r.get("actor_username"),
        token_name: r.get("token_name"),
        action: r.get("action"),
        summary: r.get("summary"),
        target: r.get("target"),
        detail: if text.is_empty() { Value::Object(Map::new()) } else { serde_json::from_str(&text).unwrap_or(Value::Object(Map::new())) },
        ip: r.get("ip"),
    }
}

/// What a caller says about one entry besides its action and summary.
#[derive(Default)]
pub struct Record<'a> {
    pub actor: Option<&'a User>,
    pub target: &'a str,
    pub detail: Option<&'a Value>,
    pub ip: &'a str,
}

impl Auth {
    /// Write one entry. Returns false (and logs why) instead of failing; true once written.
    pub async fn record(&self, action: &str, summary: &str, rec: Record<'_>) -> bool {
        if !valid_action(action) {
            tracing::error!("audit: bad action id {action:?}, entry dropped");
            return false;
        }
        let (mut id, mut name, mut username, mut token) = (None, String::new(), String::new(), String::new());
        if let Some(a) = rec.actor {
            id = Some(a.id).filter(|i| *i != 0); // id 0 is the setup token standing in for an Owner
            name = clip(&a.name, 80);
            username = clip(&a.username, 32);
            if a.is_token() {
                let n: std::result::Result<Option<String>, _> =
                    sqlx::query_scalar("select name from api_tokens where id = ?").bind(a.token_id).fetch_optional(&self.pool).await;
                token = clip(&n.ok().flatten().unwrap_or_else(|| "(revoked token)".into()), 80);
            }
        }
        let summary = clip(summary, MAX_SUMMARY);
        let written = sqlx::query(
            "insert into audit_log (at, actor_id, actor_name, actor_username, token_name, action, summary, target, detail, ip) \
             values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        )
        .bind(now())
        .bind(id)
        .bind(name)
        .bind(username)
        .bind(token)
        .bind(action)
        .bind(if summary.is_empty() { action.to_string() } else { summary })
        .bind(clip(rec.target, MAX_TARGET))
        .bind(detail_text(rec.detail))
        .bind(clip(rec.ip, 64))
        .execute(&self.pool)
        .await;
        match written {
            Ok(_) => true,
            Err(e) => {
                tracing::warn!("audit: entry {action} not written: {e}");
                false
            }
        }
    }

    /// Newest first.
    pub async fn search_audit(&self, s: Search<'_>) -> Result<SearchResult> {
        let mut where_: Vec<&str> = Vec::new();
        enum Arg {
            Int(i64),
            Text(String),
        }
        let mut args: Vec<Arg> = Vec::new();
        match s.actor {
            Some(Actor::System) => where_.push("actor_id is null"),
            Some(Actor::Id(i)) => {
                where_.push("actor_id = ?");
                args.push(Arg::Int(i));
            }
            None => {}
        }
        if let Some(k) = s.kind.filter(|k| !k.is_empty()) {
            where_.push("(action = ? or action like ? escape '\\')");
            args.push(Arg::Text(k.into()));
            args.push(Arg::Text(format!("{}.%", like(k))));
        }
        if let Some(q) = s.q.filter(|q| !q.is_empty()) {
            where_.push(
                "(summary like ? escape '\\' or target like ? escape '\\' or actor_name like ? escape '\\' \
                 or actor_username like ? escape '\\' or token_name like ? escape '\\' or ip like ? escape '\\')",
            );
            for _ in 0..6 {
                args.push(Arg::Text(format!("%{}%", like(q))));
            }
        }
        if let Some(d) = s.days.filter(|d| *d != 0) {
            where_.push("at >= ?");
            args.push(Arg::Text(later(-d * 86400)));
        }
        if let Some(b) = s.before.filter(|b| *b != 0) {
            where_.push("id < ?");
            args.push(Arg::Int(b));
        }
        let limit = s.limit.unwrap_or(PAGE).clamp(1, 500);
        let mut sql = format!("select {COLS} from audit_log");
        if !where_.is_empty() {
            sql.push_str(&format!(" where {}", where_.join(" and ")));
        }
        sql.push_str(" order by id desc limit ?");
        let mut q = sqlx::query(sqlx::AssertSqlSafe(sql.clone()));
        for a in args {
            q = match a {
                Arg::Int(i) => q.bind(i),
                Arg::Text(t) => q.bind(t),
            };
        }
        let rows = q.bind(limit + 1).fetch_all(&self.pool).await?;
        let more = rows.len() as i64 > limit;
        Ok(SearchResult { entries: rows.iter().take(limit as usize).map(entry).collect(), more })
    }

    /// Everyone the log names, by their latest name in it: (id, name, username).
    async fn audit_people(&self) -> Result<Vec<(i64, String, String)>> {
        let rows = sqlx::query(
            "select a.actor_id as id, a.actor_name as name, a.actor_username as username from audit_log a \
             join (select actor_id, max(id) as last from audit_log where actor_id is not null group by actor_id) m \
             on a.id = m.last order by lower(a.actor_name), a.actor_id",
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows.iter().map(|r| (r.get("id"), r.get("name"), r.get("username"))).collect())
    }

    /// The areas of the actions in the log ("signin", "user", a module's id, …).
    async fn audit_kinds(&self) -> Result<Vec<String>> {
        let actions: Vec<String> = sqlx::query_scalar("select distinct action from audit_log").fetch_all(&self.pool).await?;
        let kinds: BTreeSet<String> = actions.iter().map(|a| a.split('.').next().unwrap_or("").to_string()).collect();
        Ok(kinds.into_iter().collect())
    }

    /// What the audit page's two filters offer: the people who appear in the log (id, name, username) and the areas of its actions.
    pub async fn audit_filters(&self) -> Result<(Vec<(i64, String, String)>, Vec<String>)> {
        Ok((self.audit_people().await?, self.audit_kinds().await?))
    }

    /// Delete entries older than [`KEEP_DAYS`], then any beyond the newest [`MAX_ROWS`]. Returns how many went.
    pub async fn prune_audit(&self) -> Result<u64> {
        let mut gone = sqlx::query("delete from audit_log where at < ?").bind(later(-KEEP_DAYS * 86400)).execute(&self.pool).await?.rows_affected();
        gone += sqlx::query("delete from audit_log where id <= (select id from audit_log order by id desc limit 1 offset ?)")
            .bind(MAX_ROWS)
            .execute(&self.pool)
            .await?
            .rows_affected();
        if gone > 0 {
            tracing::info!("audit: {gone} old entries deleted");
        }
        Ok(gone)
    }
}
