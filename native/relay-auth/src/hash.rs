//! Password and PIN hashes. New hashes are argon2id (`$argon2id$v=19$m=…,t=…,p=…$salt$hash`, PHC text). The hashes the Python
//! server wrote are scrypt (`scrypt$N$r$p$salt$hash`, 32-byte key, standard base64) and are still verified, so every existing
//! user signs in unchanged; [`needs_rehash`] says a verified one should be rewritten as argon2id. A scrypt hash is a one-way
//! door: Python cannot read argon2id, so a rolled-back install asks affected people for a new password.

use argon2::password_hash::{PasswordHash, PasswordHasher, PasswordVerifier, SaltString};
use argon2::{Algorithm, Argon2, Params, Version};
use base64::engine::general_purpose::STANDARD;
use base64::Engine;
use subtle::ConstantTimeEq;

/// How hard a hash is to compute. [`Cost::standard`] is for use; [`Cost::fast`] keeps tests quick.
#[derive(Clone, Copy, Debug)]
pub struct Cost {
    pub memory_kib: u32,
    pub passes: u32,
}

impl Cost {
    /// 19 MiB and 2 passes: the OWASP minimum for argon2id, about 30 ms here.
    pub const fn standard() -> Self {
        Self { memory_kib: 19 * 1024, passes: 2 }
    }

    pub const fn fast() -> Self {
        Self { memory_kib: 8, passes: 1 }
    }

    fn argon(self) -> Argon2<'static> {
        let params = Params::new(self.memory_kib, self.passes, 1, None).unwrap_or_default();
        Argon2::new(Algorithm::Argon2id, Version::V0x13, params)
    }
}

pub fn hash_secret(secret: &str, cost: Cost) -> String {
    let salt = SaltString::generate(&mut rand::rngs::OsRng);
    match cost.argon().hash_password(secret.as_bytes(), &salt) {
        Ok(h) => h.to_string(),
        Err(e) => {
            // Cannot happen for these parameters; refuse to store something weaker than asked for.
            tracing::error!("argon2 failed: {e}");
            String::new()
        }
    }
}

fn verify_scrypt(secret: &str, stored: &str) -> bool {
    let parts: Vec<&str> = stored.split('$').collect();
    let [scheme, n, r, p, salt, want] = parts[..] else { return false };
    let (Ok(n), Ok(r), Ok(p)) = (n.parse::<u64>(), r.parse::<u32>(), p.parse::<u32>()) else { return false };
    if scheme != "scrypt" || n < 2 || n & (n - 1) != 0 || n > 1 << 20 || !(1..=32).contains(&r) || !(1..=16).contains(&p) {
        return false;
    }
    let (Ok(salt), Ok(want)) = (STANDARD.decode(salt), STANDARD.decode(want)) else { return false };
    let Ok(params) = scrypt::Params::new(n.trailing_zeros() as u8, r, p, 32) else { return false };
    let mut got = [0u8; 32];
    if scrypt::scrypt(secret.as_bytes(), &salt, &params, &mut got).is_err() {
        return false;
    }
    want.len() == got.len() && bool::from(got.ct_eq(&want))
}

pub fn verify_secret(secret: &str, stored: Option<&str>) -> bool {
    let Some(stored) = stored else { return false };
    if stored.starts_with("$argon2") {
        return PasswordHash::new(stored)
            .map(|h| Argon2::default().verify_password(secret.as_bytes(), &h).is_ok())
            .unwrap_or(false);
    }
    verify_scrypt(secret, stored)
}

/// True for a hash that verified but is the old scrypt kind, or argon2id with less than `cost`.
pub fn needs_rehash(stored: &str, cost: Cost) -> bool {
    if !stored.starts_with("$argon2") {
        return true;
    }
    PasswordHash::new(stored)
        .ok()
        .and_then(|h| Params::try_from(&h).ok())
        .is_none_or(|p| p.m_cost() < cost.memory_kib || p.t_cost() < cost.passes)
}
