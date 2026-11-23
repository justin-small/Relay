//! API tokens (app/users.py "API tokens", #174): `Authorization: Bearer relay_…` on /api/admin routes. A token acts as its
//! user less `TOKEN_BARRED`, and is checked against the user row on every request, so turning the user off stops it at once.
//! Only the sha256 is stored; the secret is shown once, when it is made.

use sqlx::sqlite::SqliteRow;
use sqlx::Row;

use crate::accounts::{clean_name, user_of, COLS_U};
use crate::db::{later, now, Auth};
use crate::error::{Refusal, Result};
use crate::perms::User;
use crate::sessions::{hash_token, random_token};

pub const TOKEN_PREFIX: &str = "relay_";
/// Who holds `tokens.own`; a user moved below it loses their tokens.
pub const TOKEN_MIN_ROLE: &str = "admin";
/// Days a token lives, or never.
pub const TOKEN_EXPIRY_DAYS: [Option<i64>; 4] = [Some(30), Some(90), Some(365), None];
pub const TOKEN_DEFAULT_DAYS: i64 = 90;
pub const TOKENS_PER_USER: i64 = 25;
const HINT_LEN: usize = 6;

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct TokenRow {
    pub id: i64,
    pub name: String,
    pub hint: String,
    pub created: String,
    pub expires: Option<String>,
    pub last_used: Option<String>,
    pub user_id: i64,
    pub user_name: String,
    pub username: String,
}

const LIST: &str = "select t.id, t.name, t.hint, t.created_at as created, t.expires_at as expires, t.last_used, \
                    t.user_id, u.name as user_name, u.username from api_tokens t join users u on u.id = t.user_id";

fn row_of(r: &SqliteRow) -> TokenRow {
    TokenRow {
        id: r.get("id"),
        name: r.get("name"),
        hint: r.get("hint"),
        created: r.get("created"),
        expires: r.get("expires"),
        last_used: r.get("last_used"),
        user_id: r.get("user_id"),
        user_name: r.get("user_name"),
        username: r.get("username"),
    }
}

impl Auth {
    /// A new token for `user` (a signed-in person, not a token). Returns (secret, row); the secret is never stored or returned
    /// again. `days` is 30, 90, 365 or `None` for never.
    pub async fn create_token(&self, user: &User, name: &str, days: Option<i64>) -> Result<(String, TokenRow)> {
        if user.id <= 0 || user.is_token() || !user.can("tokens.own") {
            return Err(Refusal::NotAllowed.into());
        }
        let name = clean_name(name, Refusal::BadTokenName)?;
        if !TOKEN_EXPIRY_DAYS.contains(&days) {
            return Err(Refusal::BadExpiry.into());
        }
        let secret = format!("{TOKEN_PREFIX}{}", random_token());
        let expires = days.map(|d| later(d * 24 * 3600));
        let mut tx = self.pool.begin().await?;
        sqlx::query("delete from api_tokens where expires_at is not null and expires_at < ?").bind(now()).execute(&mut *tx).await?;
        let have: i64 = sqlx::query_scalar("select count(*) from api_tokens where user_id = ?").bind(user.id).fetch_one(&mut *tx).await?;
        if have >= TOKENS_PER_USER {
            return Err(Refusal::TooManyTokens.into());
        }
        let hint: String = secret[TOKEN_PREFIX.len()..].chars().take(HINT_LEN).collect();
        let id: i64 = sqlx::query_scalar(
            "insert into api_tokens (user_id, name, token_hash, hint, expires_at) values (?, ?, ?, ?, ?) returning id",
        )
        .bind(user.id)
        .bind(name)
        .bind(hash_token(&secret))
        .bind(hint)
        .bind(expires)
        .fetch_one(&mut *tx)
        .await?;
        let row = sqlx::query(sqlx::AssertSqlSafe(format!("{LIST} where t.id = ?"))).bind(id).fetch_one(&mut *tx).await?;
        tx.commit().await?;
        Ok((secret, row_of(&row)))
    }

    /// The user a token acts as, or `None` when it is unknown, expired, or its user is turned off. Notes when it was used, at
    /// most once a minute.
    pub async fn token_user(&self, secret: &str) -> Result<Option<User>> {
        if !secret.starts_with(TOKEN_PREFIX) || secret.chars().count() > 200 {
            return Ok(None);
        }
        let row = sqlx::query(sqlx::AssertSqlSafe(format!(
            "select {COLS_U}, t.id as token_id, (t.last_used is null or t.last_used < ?) as stale \
             from api_tokens t join users u on u.id = t.user_id \
             where t.token_hash = ? and (t.expires_at is null or t.expires_at > ?) and not u.disabled"
        )))
        .bind(later(-60))
        .bind(hash_token(secret))
        .bind(now())
        .fetch_optional(&self.pool)
        .await?;
        let Some(row) = row else { return Ok(None) };
        let token_id: i64 = row.get("token_id");
        if row.get::<i64, _>("stale") != 0 {
            sqlx::query("update api_tokens set last_used = ? where id = ?").bind(now()).bind(token_id).execute(&self.pool).await?;
        }
        let mut user = user_of(&row);
        user.token_id = token_id;
        Ok(Some(user))
    }

    /// Tokens that have not expired, newest first: one user's, or everyone's (`None`).
    pub async fn list_tokens(&self, user_id: Option<i64>) -> Result<Vec<TokenRow>> {
        let mut sql = format!("{LIST} where (t.expires_at is null or t.expires_at > ?)");
        if user_id.is_some() {
            sql.push_str(" and t.user_id = ?");
        }
        sql.push_str(" order by t.created_at desc, t.id desc");
        let mut q = sqlx::query(sqlx::AssertSqlSafe(sql.clone())).bind(now());
        if let Some(id) = user_id {
            q = q.bind(id);
        }
        Ok(q.fetch_all(&self.pool).await?.iter().map(row_of).collect())
    }

    /// Anyone may revoke their own tokens; only `tokens.manage` (Owners) may revoke someone else's. A token that is not yours
    /// and not revocable by you is "not found", so its existence is not given away.
    pub async fn revoke_token(&self, token_id: i64, actor: &User) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        let owner: Option<i64> = sqlx::query_scalar("select user_id from api_tokens where id = ?").bind(token_id).fetch_optional(&mut *tx).await?;
        match owner {
            Some(u) if u == actor.id || actor.can("tokens.manage") => {
                sqlx::query("delete from api_tokens where id = ?").bind(token_id).execute(&mut *tx).await?;
                tx.commit().await?;
                Ok(())
            }
            _ => Err(Refusal::NotFound.into()),
        }
    }
}
