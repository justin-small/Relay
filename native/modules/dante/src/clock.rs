//! The time as Python's `db.now()` writes it: `2026-11-28T14:03:09Z`, UTC.

use std::time::{SystemTime, UNIX_EPOCH};

use relay_core::timewin::Date;

/// Seconds since 1970 now.
pub fn epoch() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// `secs` since 1970 as `%Y-%m-%dT%H:%M:%SZ`.
pub fn iso(secs: i64) -> String {
    let days = secs.div_euclid(86_400);
    let rest = secs.rem_euclid(86_400);
    let d = Date::from_days(days);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", d.y, d.m, d.d, rest / 3600, rest % 3600 / 60, rest % 60)
}

/// `db.now()`.
pub fn now() -> String {
    iso(epoch() as i64)
}
