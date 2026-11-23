//! Accounts (app/users.py "accounts"): create, list, change, delete, passwords and PINs, and the two ways to prove who you
//! are. The rules that protect the system (the rank rule, the last owner, no self-delete) live here, not in the web layer.
//! An `actor` of `None` is the system itself (first-run setup, the lock-out tool) and is bound by no rank rule.

use std::sync::OnceLock;

use sqlx::sqlite::SqliteRow;
use sqlx::{Row, Sqlite, Transaction};

use crate::db::{later, now, Auth};
use crate::error::{Error, Refusal, Result};
use crate::hash;
use crate::perms::{rank, User, ROLES};
use crate::sessions::PIN_ATTEMPTS;
use crate::tokens::TOKEN_MIN_ROLE;

pub(crate) const COLS: &str = "id, username, name, role, disabled, must_change_password, (pin_hash is not null) as pin_set";
pub(crate) const COLS_U: &str =
    "u.id, u.username, u.name, u.role, u.disabled, u.must_change_password, (u.pin_hash is not null) as pin_set";

pub(crate) fn user_of(row: &SqliteRow) -> User {
    User {
        id: row.get("id"),
        username: row.get("username"),
        name: row.get("name"),
        role: row.get("role"),
        disabled: row.get::<i64, _>("disabled") != 0,
        must_change_password: row.get::<i64, _>("must_change_password") != 0,
        pin_set: row.get::<i64, _>("pin_set") != 0,
        token_id: 0,
    }
}

/// One line of the Users page (`list_users`).
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct UserRow {
    pub id: i64,
    pub username: String,
    pub name: String,
    pub role: String,
    pub disabled: bool,
    pub must_change_password: bool,
    pub pin_set: bool,
    pub last_login: Option<String>,
    pub created: String,
}

pub struct NewUser<'a> {
    pub username: &'a str,
    pub name: &'a str,
    pub role: &'a str,
    pub password: &'a str,
    /// The person must choose their own password at first sign-in.
    pub temporary: bool,
}

#[derive(Default)]
pub struct UserChanges<'a> {
    pub name: Option<&'a str>,
    pub role: Option<&'a str>,
    pub disabled: Option<bool>,
}

// ---------------------------------------------------------------- validation

pub(crate) fn clean_username(v: &str) -> Result<String> {
    let v = v.trim().to_lowercase();
    let ok = (3..=32).contains(&v.chars().count())
        && v.chars().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, '.' | '_' | '-'));
    if ok { Ok(v) } else { Err(Refusal::BadUsername.into()) }
}

pub(crate) fn clean_name(v: &str, bad: Refusal) -> Result<String> {
    let v = v.trim();
    let n = v.chars().count();
    if (1..=80).contains(&n) && !v.chars().any(|c| (c as u32) < 32 || c as u32 == 127) {
        Ok(v.to_string())
    } else {
        Err(bad.into())
    }
}

fn check_password(v: &str) -> Result<()> {
    if (8..=200).contains(&v.chars().count()) { Ok(()) } else { Err(Refusal::BadPassword.into()) }
}

fn check_pin(v: &str) -> Result<()> {
    if (4..=8).contains(&v.len()) && v.bytes().all(|b| b.is_ascii_digit()) { Ok(()) } else { Err(Refusal::BadPin.into()) }
}

fn check_role(v: &str) -> Result<()> {
    if ROLES.contains(&v) { Ok(()) } else { Err(Refusal::BadRole.into()) }
}

impl Auth {
    pub(crate) async fn hash(&self, secret: &str) -> String {
        let (secret, cost) = (secret.to_string(), self.cost);
        tokio::task::spawn_blocking(move || hash::hash_secret(&secret, cost)).await.unwrap_or_default()
    }

    pub(crate) async fn verify(&self, secret: &str, stored: Option<&str>) -> bool {
        let (secret, stored) = (secret.to_string(), stored.map(str::to_string));
        tokio::task::spawn_blocking(move || hash::verify_secret(&secret, stored.as_deref())).await.unwrap_or(false)
    }

    /// Spend the time a real check would, so an unknown username answers as slowly as a wrong password.
    async fn burn(&self) {
        static DUMMY: OnceLock<String> = OnceLock::new();
        let dummy = DUMMY.get_or_init(|| hash::hash_secret("not a real password", self.cost)).clone();
        self.verify("x", Some(&dummy)).await;
    }

    // ------------------------------------------------------------ reading

    pub async fn count_users(&self) -> Result<i64> {
        Ok(sqlx::query_scalar("select count(*) from users").fetch_one(&self.pool).await?)
    }

    pub async fn list_users(&self) -> Result<Vec<UserRow>> {
        let rows = sqlx::query(
            "select id, username, name, role, disabled, must_change_password, (pin_hash is not null) as pin_set,\
             last_login, created_at as created from users order by lower(name), id",
        )
        .fetch_all(&self.pool)
        .await?;
        Ok(rows
            .iter()
            .map(|r| UserRow {
                id: r.get("id"),
                username: r.get("username"),
                name: r.get("name"),
                role: r.get("role"),
                disabled: r.get::<i64, _>("disabled") != 0,
                must_change_password: r.get::<i64, _>("must_change_password") != 0,
                pin_set: r.get::<i64, _>("pin_set") != 0,
                last_login: r.get("last_login"),
                created: r.get("created"),
            })
            .collect())
    }

    pub async fn get_user(&self, id: i64) -> Result<Option<User>> {
        let row = sqlx::query(sqlx::AssertSqlSafe(format!("select {COLS} from users where id = ?"))).bind(id).fetch_optional(&self.pool).await?;
        Ok(row.as_ref().map(user_of))
    }

    pub async fn get_by_username(&self, username: &str) -> Result<Option<User>> {
        let row = sqlx::query(sqlx::AssertSqlSafe(format!("select {COLS} from users where username = ?")))
            .bind(username.trim().to_lowercase())
            .fetch_optional(&self.pool)
            .await?;
        Ok(row.as_ref().map(user_of))
    }

    // ------------------------------------------------------------ changing

    pub async fn create_user(&self, new: NewUser<'_>, actor: Option<&User>) -> Result<User> {
        let username = clean_username(new.username)?;
        let name = clean_name(new.name, Refusal::BadName)?;
        check_role(new.role)?;
        check_password(new.password)?;
        if actor.is_some_and(|a| rank(new.role) > rank(&a.role)) {
            return Err(Refusal::RoleTooHigh.into());
        }
        let pw = self.hash(new.password).await;
        let row = sqlx::query(sqlx::AssertSqlSafe(format!(
            "insert into users (username, name, role, password_hash, must_change_password) values (?, ?, ?, ?, ?)\
             on conflict (username) do nothing returning {COLS}"
        )))
        .bind(username)
        .bind(name)
        .bind(new.role)
        .bind(pw)
        .bind(i64::from(new.temporary))
        .fetch_optional(&self.pool)
        .await?;
        row.as_ref().map(user_of).ok_or(Refusal::UsernameTaken.into())
    }

    pub async fn update_user(&self, id: i64, changes: UserChanges<'_>, actor: Option<&User>) -> Result<User> {
        let new_name = changes.name.map(|n| clean_name(n, Refusal::BadName)).transpose()?;
        if let Some(r) = changes.role {
            check_role(r)?;
        }
        let mut tx = self.pool.begin().await?;
        let cur = target(&mut tx, id, actor).await?;
        if let Some(a) = actor {
            if changes.role.is_some_and(|r| rank(r) > rank(&a.role)) {
                return Err(Refusal::RoleTooHigh.into());
            }
            if changes.disabled == Some(true) && a.id == cur.id {
                return Err(Refusal::SelfAction.into());
            }
        }
        let final_role = changes.role.unwrap_or(&cur.role).to_string();
        let final_disabled = changes.disabled.unwrap_or(cur.disabled);
        if cur.role == "owner" && !cur.disabled && (final_role != "owner" || final_disabled) && !other_owner(&mut tx, cur.id).await? {
            return Err(Refusal::LastOwner.into());
        }
        let row = sqlx::query(sqlx::AssertSqlSafe(format!(
            "update users set name = ?, role = ?, disabled = ?, updated_at = ? where id = ? returning {COLS}"
        )))
        .bind(new_name.unwrap_or_else(|| cur.name.clone()))
        .bind(&final_role)
        .bind(i64::from(final_disabled))
        .bind(now())
        .bind(cur.id)
        .fetch_one(&mut *tx)
        .await?;
        if final_disabled {
            sqlx::query("delete from sessions where user_id = ?").bind(cur.id).execute(&mut *tx).await?;
        }
        if rank(&final_role) < rank(TOKEN_MIN_ROLE) {
            sqlx::query("delete from api_tokens where user_id = ?").bind(cur.id).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        if final_disabled {
            self.signed_out(cur.id);
        }
        Ok(user_of(&row))
    }

    pub async fn delete_user(&self, id: i64, actor: Option<&User>) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        let cur = target(&mut tx, id, actor).await?;
        if actor.is_some_and(|a| a.id == cur.id) {
            return Err(Refusal::SelfAction.into());
        }
        if cur.role == "owner" && !cur.disabled && !other_owner(&mut tx, cur.id).await? {
            return Err(Refusal::LastOwner.into());
        }
        sqlx::query("delete from users where id = ?").bind(cur.id).execute(&mut *tx).await?;
        tx.commit().await?;
        self.signed_out(cur.id);
        Ok(())
    }

    /// Reset a password and end every session of that user. A temporary one must be changed at next sign-in, and also clears
    /// the PIN (a forgotten or exposed password is no reason to keep the quicker way in).
    pub async fn set_password(&self, id: i64, new: &str, temporary: bool, actor: Option<&User>) -> Result<()> {
        check_password(new)?;
        let pw = self.hash(new).await;
        let mut tx = self.pool.begin().await?;
        let cur = target(&mut tx, id, actor).await?;
        let t = i64::from(temporary);
        sqlx::query(
            "update users set password_hash = ?, must_change_password = ?, updated_at = ?,\
             pin_hash = case when ? then null else pin_hash end,\
             pin_failures = case when ? then 0 else pin_failures end where id = ?",
        )
        .bind(pw)
        .bind(t)
        .bind(now())
        .bind(t)
        .bind(t)
        .bind(cur.id)
        .execute(&mut *tx)
        .await?;
        sqlx::query("delete from sessions where user_id = ?").bind(cur.id).execute(&mut *tx).await?;
        tx.commit().await?;
        self.signed_out(cur.id);
        Ok(())
    }

    /// The user changes their own password. Their other sessions end; `keep_token` (the cookie making this request) stays.
    pub async fn change_own_password(&self, user: &User, current: &str, new: &str, keep_token: Option<&str>) -> Result<()> {
        check_password(new)?;
        let stored: Option<String> =
            sqlx::query_scalar("select password_hash from users where id = ?").bind(user.id).fetch_optional(&self.pool).await?;
        let Some(stored) = stored else { return Err(Refusal::NotFound.into()) };
        if !self.verify(current, Some(&stored)).await {
            return Err(Refusal::WrongPassword.into());
        }
        if new == current {
            return Err(Refusal::SamePassword.into());
        }
        let pw = self.hash(new).await;
        let mut tx = self.pool.begin().await?;
        sqlx::query("update users set password_hash = ?, must_change_password = 0, updated_at = ? where id = ?")
            .bind(pw)
            .bind(now())
            .bind(user.id)
            .execute(&mut *tx)
            .await?;
        crate::sessions::end_sessions_in(&mut tx, user.id, keep_token).await?;
        tx.commit().await?;
        self.signed_out(user.id);
        Ok(())
    }

    pub async fn set_pin(&self, user: &User, password: &str, pin: &str) -> Result<()> {
        check_pin(pin)?;
        let stored: Option<String> =
            sqlx::query_scalar("select password_hash from users where id = ?").bind(user.id).fetch_optional(&self.pool).await?;
        let Some(stored) = stored else { return Err(Refusal::NotFound.into()) };
        if !self.verify(password, Some(&stored)).await {
            return Err(Refusal::WrongPassword.into());
        }
        let h = self.hash(pin).await;
        sqlx::query("update users set pin_hash = ?, pin_failures = 0, updated_at = ? where id = ?")
            .bind(h)
            .bind(now())
            .bind(user.id)
            .execute(&self.pool)
            .await?;
        Ok(())
    }

    pub async fn clear_pin(&self, id: i64) -> Result<()> {
        let mut tx = self.pool.begin().await?;
        sqlx::query("update users set pin_hash = null, pin_failures = 0, updated_at = ? where id = ?")
            .bind(now())
            .bind(id)
            .execute(&mut *tx)
            .await?;
        sqlx::query("delete from sessions where user_id = ? and via = 'pin'").bind(id).execute(&mut *tx).await?;
        tx.commit().await?;
        Ok(())
    }

    // ------------------------------------------------------------ proving who you are

    /// `bad_login` for an unknown user or a wrong password alike; `disabled` only once the password was right, so it tells
    /// nothing to someone guessing.
    pub async fn authenticate(&self, username: &str, password: &str) -> Result<User> {
        let uname = username.trim().to_lowercase();
        let row = if uname.is_empty() {
            None
        } else {
            sqlx::query(sqlx::AssertSqlSafe(format!("select {COLS}, password_hash from users where username = ?")))
                .bind(&uname)
                .fetch_optional(&self.pool)
                .await?
        };
        let Some(row) = row.filter(|_| password.chars().count() <= 200) else {
            self.burn().await;
            return Err(Refusal::BadLogin.into());
        };
        let stored: String = row.get("password_hash");
        if !self.verify(password, Some(&stored)).await {
            return Err(Refusal::BadLogin.into());
        }
        let user = user_of(&row);
        if user.disabled {
            return Err(Refusal::Disabled.into());
        }
        sqlx::query("update users set last_login = ? where id = ?").bind(now()).bind(user.id).execute(&self.pool).await?;
        if hash::needs_rehash(&stored, self.cost) {
            let h = self.hash(password).await;
            sqlx::query("update users set password_hash = ? where id = ? and password_hash = ?")
                .bind(h)
                .bind(user.id)
                .bind(&stored)
                .execute(&self.pool)
                .await?;
        }
        Ok(user)
    }

    /// Sign in with a PIN, only from a trusted device (`device_id` from [`Auth::device_for`]). The fifth wrong PIN in a row
    /// clears the PIN: `pin_cleared`.
    pub async fn authenticate_pin(&self, user_id: i64, pin: &str, device_id: Option<i64>) -> Result<User> {
        let Some(device_id) = device_id else { return Err(Refusal::NoDevice.into()) };
        let known: Option<i64> =
            sqlx::query_scalar("select 1 from trusted_devices where id = ?").bind(device_id).fetch_optional(&self.pool).await?;
        if known.is_none() {
            return Err(Refusal::NoDevice.into());
        }
        let row = sqlx::query(sqlx::AssertSqlSafe(format!("select {COLS}, pin_hash from users where id = ?")))
            .bind(user_id)
            .fetch_optional(&self.pool)
            .await?;
        let stored: Option<String> = row.as_ref().and_then(|r| r.get("pin_hash"));
        let (Some(row), Some(stored)) = (row, stored) else {
            self.burn().await;
            return Err(Refusal::WrongPin.into());
        };
        let user = user_of(&row);
        if user.disabled {
            return Err(Refusal::Disabled.into());
        }
        if pin.len() <= 16 && self.verify(pin, Some(&stored)).await {
            sqlx::query("update users set pin_failures = 0, last_login = ? where id = ?")
                .bind(now())
                .bind(user.id)
                .execute(&self.pool)
                .await?;
            return Ok(user);
        }
        let mut tx = self.pool.begin().await?;
        let failures: Option<i64> = sqlx::query_scalar(
            "update users set pin_failures = pin_failures + 1 where id = ? and pin_hash is not null returning pin_failures",
        )
        .bind(user.id)
        .fetch_optional(&mut *tx)
        .await?;
        let locked = failures.is_some_and(|n| n >= PIN_ATTEMPTS);
        if locked {
            sqlx::query("update users set pin_hash = null, pin_failures = 0 where id = ?").bind(user.id).execute(&mut *tx).await?;
            sqlx::query("delete from sessions where user_id = ? and via = 'pin'").bind(user.id).execute(&mut *tx).await?;
        }
        tx.commit().await?;
        Err(if locked { Refusal::PinCleared } else { Refusal::WrongPin }.into())
    }

    /// People who can pick their name and type a PIN on a trusted device.
    pub async fn pin_users(&self) -> Result<Vec<(i64, String)>> {
        let rows = sqlx::query("select id, name from users where not disabled and pin_hash is not null order by lower(name), id")
            .fetch_all(&self.pool)
            .await?;
        Ok(rows.iter().map(|r| (r.get("id"), r.get("name"))).collect())
    }
}

/// The row being changed, after the actor's rank rule. `not_found` / `account_above_you`.
async fn target(tx: &mut Transaction<'_, Sqlite>, id: i64, actor: Option<&User>) -> Result<User> {
    let row = sqlx::query(sqlx::AssertSqlSafe(format!("select {COLS} from users where id = ?"))).bind(id).fetch_optional(&mut **tx).await?;
    let Some(row) = row else { return Err(Refusal::NotFound.into()) };
    let cur = user_of(&row);
    if actor.is_some_and(|a| rank(&cur.role) > rank(&a.role)) {
        return Err(Refusal::AccountAboveYou.into());
    }
    Ok(cur)
}

async fn other_owner(tx: &mut Transaction<'_, Sqlite>, id: i64) -> Result<bool> {
    let any: Option<i64> =
        sqlx::query_scalar("select 1 from users where role = 'owner' and not disabled and id <> ? limit 1")
            .bind(id)
            .fetch_optional(&mut **tx)
            .await?;
    Ok(any.is_some())
}

#[allow(dead_code)]
fn _unused(_: Error) {
    let _ = later(0);
}
