//! Sign-in sessions (app/users.py "sessions"): the cookie holds a random token, only its sha256 is stored. A PIN session
//! belongs to the trusted device it came from, so forgetting the device ends it.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use sha2::{Digest, Sha256};
use sqlx::{Sqlite, Transaction};

use crate::accounts::{user_of, COLS_U};
use crate::db::{later, now, Auth};
use crate::devices::agent;
use crate::error::Result;
use crate::perms::User;

pub const PASSWORD_SESSION_SECONDS: i64 = 30 * 24 * 3600;
pub const PIN_SESSION_SECONDS: i64 = 12 * 3600;
pub const PIN_ATTEMPTS: i64 = 5;

/// A new session: the cookie text and how long it lives.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Session {
    pub token: String,
    pub max_age: i64,
}

/// 32 random bytes as URL-safe text (Python's `secrets.token_urlsafe(32)`).
pub(crate) fn random_token() -> String {
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

pub(crate) fn hash_token(token: &str) -> String {
    Sha256::digest(token.as_bytes()).iter().map(|b| format!("{b:02x}")).collect()
}

pub(crate) fn valid_token(token: &str) -> bool {
    !token.is_empty() && token.chars().count() <= 200
}

pub(crate) async fn end_sessions_in(tx: &mut Transaction<'_, Sqlite>, user_id: i64, except: Option<&str>) -> Result<()> {
    match except.filter(|t| valid_token(t)) {
        Some(t) => sqlx::query("delete from sessions where user_id = ? and token_hash <> ?").bind(user_id).bind(hash_token(t)),
        None => sqlx::query("delete from sessions where user_id = ?").bind(user_id),
    }
    .execute(&mut **tx)
    .await?;
    Ok(())
}

impl Auth {
    /// `via` is "password" or "pin"; a PIN session needs the trusted device it came from. `user_agent` is the browser's, kept
    /// so that forgetting the phone it belongs to ends this session too.
    pub async fn start_session(&self, user: &User, via: &str, device_id: Option<i64>, user_agent: &str) -> Result<Session> {
        let pin = via == "pin";
        assert!(pin || via == "password", "via must be 'password' or 'pin'");
        assert!(!pin || device_id.is_some(), "a pin session needs a device");
        let max_age = if pin { PIN_SESSION_SECONDS } else { PASSWORD_SESSION_SECONDS };
        let token = random_token();
        let mut tx = self.pool.begin().await?;
        sqlx::query("delete from sessions where expires_at < ?").bind(now()).execute(&mut *tx).await?;
        sqlx::query(
            "insert into sessions (user_id, token_hash, via, device_id, user_agent, expires_at) values (?, ?, ?, ?, ?, ?)",
        )
        .bind(user.id)
        .bind(hash_token(&token))
        .bind(via)
        .bind(if pin { device_id } else { None })
        .bind(agent(user_agent))
        .bind(later(max_age))
        .execute(&mut *tx)
        .await?;
        tx.commit().await?;
        Ok(Session { token, max_age })
    }

    /// The user a session cookie belongs to, read fresh so a disabled user or a changed role takes effect at once.
    pub async fn session_user(&self, token: &str) -> Result<Option<User>> {
        if !valid_token(token) {
            return Ok(None);
        }
        let row = sqlx::query(sqlx::AssertSqlSafe(format!(
            "select {COLS_U} from sessions s join users u on u.id = s.user_id where s.token_hash = ? and s.expires_at > ? and not u.disabled"
        )))
        .bind(hash_token(token))
        .bind(now())
        .fetch_optional(&self.pool)
        .await?;
        Ok(row.as_ref().map(user_of))
    }

    pub async fn end_session(&self, token: &str) -> Result<()> {
        if valid_token(token) {
            sqlx::query("delete from sessions where token_hash = ?").bind(hash_token(token)).execute(&self.pool).await?;
        }
        Ok(())
    }

    /// End every session of a user, except the one making this request when `except_token` is given.
    pub async fn end_sessions(&self, user_id: i64, except_token: Option<&str>) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        end_sessions_in(&mut tx, user_id, except_token).await?;
        tx.commit().await?;
        self.signed_out(user_id);
        Ok(())
    }
}
