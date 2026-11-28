//! The lowest layer of the crate: the column lists, row mapping, name checks, token hashing and the two limits the account,
//! session, device and token modules all use. It uses none of them, so they can use each other in one direction only.

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use sha2::{Digest, Sha256};
use sqlx::sqlite::SqliteRow;
use sqlx::Row;

use crate::error::{Refusal, Result};
use crate::perms::User;

const UA_MAX: usize = 200;

pub const PIN_ATTEMPTS: i64 = 5;
/// Who holds `tokens.own`; a user moved below it loses their tokens.
pub const TOKEN_MIN_ROLE: &str = "admin";

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

pub(crate) fn clean_name(v: &str, bad: Refusal) -> Result<String> {
    let v = v.trim();
    let n = v.chars().count();
    if (1..=80).contains(&n) && !v.chars().any(|c| (c as u32) < 32 || c as u32 == 127) {
        Ok(v.to_string())
    } else {
        Err(bad.into())
    }
}

/// A browser's user agent, tidied to what is stored.
pub(crate) fn agent(raw: &str) -> String {
    raw.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(UA_MAX).collect()
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

