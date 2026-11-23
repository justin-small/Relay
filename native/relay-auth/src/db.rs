//! Opening relay.db. The file is SQLite in WAL mode, owner-only, shared with the Python server. A new file gets
//! `schema.sql` in one transaction and the auth versions recorded in `schema_migrations`; Python then adds the rest of its
//! tables when it starts, and skips these. An existing file is never altered: it must already hold every version in
//! [`AUTH_VERSIONS`], or [`Auth::open`] says which one is missing (run the Python build of Relay once to bring it up to date).

use std::path::Path;
use std::str::FromStr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use sqlx::sqlite::{SqliteConnectOptions, SqliteJournalMode, SqlitePoolOptions, SqliteSynchronous};
use sqlx::SqlitePool;
use time::format_description::FormatItem;
use time::macros::format_description;
use time::OffsetDateTime;

use crate::hash::Cost;

/// The core migrations that make the auth tables (app/migrations): users, API tokens, the audit log, preferences, devices and
/// the session user agent.
pub const AUTH_VERSIONS: [&str; 6] =
    ["0001_users", "0002_api_tokens", "0003_audit_log", "0004_user_prefs", "0009_devices", "0010_session_agent"];

const SCHEMA: &str = include_str!("../schema.sql");
const STAMP: &[FormatItem<'static>] = format_description!("[year]-[month]-[day]T[hour]:[minute]:[second]Z");

/// The timestamp text every time column holds (`db.now()` in Python): UTC ISO, which sorts correctly.
pub fn now() -> String {
    later(0)
}

/// The timestamp `seconds` from now (negative for the past).
pub fn later(seconds: i64) -> String {
    let at = OffsetDateTime::now_utc() + time::Duration::seconds(seconds);
    at.format(STAMP).unwrap_or_default()
}

type Hook = Arc<dyn Fn(i64) + Send + Sync>;

/// Accounts, sessions, tokens, devices and the audit log, on one database.
#[derive(Clone)]
pub struct Auth {
    pub(crate) pool: SqlitePool,
    pub(crate) cost: Cost,
    hooks: Arc<Mutex<Vec<Hook>>>,
}

impl Auth {
    /// Open (and, for a new file, create) the database. The error text is for the log and the console, never a response.
    pub async fn open(path: &Path, cost: Cost) -> Result<Auth, String> {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
        }
        make_private(path)?;
        let options = SqliteConnectOptions::from_str("sqlite://")
            .map_err(|e| e.to_string())?
            .filename(path)
            .create_if_missing(true)
            .foreign_keys(true)
            .journal_mode(SqliteJournalMode::Wal)
            .synchronous(SqliteSynchronous::Normal)
            .busy_timeout(Duration::from_millis(5000));
        let pool = SqlitePoolOptions::new()
            .max_connections(4)
            .connect_with(options)
            .await
            .map_err(|e| format!("cannot open {}: {e}", path.display()))?;
        let auth = Auth { pool, cost, hooks: Arc::default() };
        auth.prepare().await?;
        Ok(auth)
    }

    async fn prepare(&self) -> Result<(), String> {
        let mut tx = self.pool.begin().await.map_err(|e| e.to_string())?;
        let tables: Vec<String> = sqlx::query_scalar("select name from sqlite_master where type = 'table'")
            .fetch_all(&mut *tx)
            .await
            .map_err(|e| e.to_string())?;
        if tables.is_empty() {
            sqlx::raw_sql(SCHEMA).execute(&mut *tx).await.map_err(|e| e.to_string())?;
            sqlx::raw_sql(
                "create table schema_migrations (namespace text not null, version text not null,\
                 applied_at text not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')), primary key (namespace, version))",
            )
            .execute(&mut *tx)
            .await
            .map_err(|e| e.to_string())?;
            for v in AUTH_VERSIONS {
                sqlx::query("insert into schema_migrations (namespace, version) values ('core', ?)")
                    .bind(v)
                    .execute(&mut *tx)
                    .await
                    .map_err(|e| e.to_string())?;
            }
        } else {
            if !tables.iter().any(|t| t == "schema_migrations") {
                return Err("relay.db has tables but no schema_migrations: it is not a Relay database".into());
            }
            let done: Vec<String> = sqlx::query_scalar("select version from schema_migrations where namespace = 'core'")
                .fetch_all(&mut *tx)
                .await
                .map_err(|e| e.to_string())?;
            if let Some(missing) = AUTH_VERSIONS.iter().find(|v| !done.iter().any(|d| d == *v)) {
                return Err(format!(
                    "relay.db is older than this server can use (core migration {missing} has not run): start the Python Relay once to update it"
                ));
            }
        }
        tx.commit().await.map_err(|e| e.to_string())
    }

    /// Call `hook(user_id)` whenever every sign-in of a user has ended (password changed or reset, account turned off or
    /// deleted, "sign out everywhere"). A module holds one; core never names a module.
    pub fn on_signed_out(&self, hook: impl Fn(i64) + Send + Sync + 'static) {
        self.hooks.lock().unwrap_or_else(|e| e.into_inner()).push(Arc::new(hook));
    }

    pub(crate) fn signed_out(&self, user_id: i64) {
        let hooks: Vec<Hook> = self.hooks.lock().unwrap_or_else(|e| e.into_inner()).clone();
        for hook in hooks {
            hook(user_id);
        }
    }

    pub fn pool(&self) -> &SqlitePool {
        &self.pool
    }
}

#[cfg(unix)]
fn make_private(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::{OpenOptionsExt, PermissionsExt};
    if !path.exists() {
        std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).open(path).map_err(|e| e.to_string())?;
    }
    let _ = std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600));
    Ok(())
}

#[cfg(not(unix))]
fn make_private(_path: &Path) -> Result<(), String> {
    Ok(())
}
