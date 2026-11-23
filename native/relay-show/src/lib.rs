//! Alerts, stage messages, panels and screens in Rust (#660, part of #650): `app/alerts.py`, `app/panels.py`, `app/screens.py`
//! and `app/text.py`'s cleaners, with no HTTP in the crate. `relay-server`'s `show.rs` mounts the routes.
//!
//! * [`text`] is the one-line cleaners every name and sentence goes through;
//! * [`alerts`] is the alert and stage-message state machine, its settings rules and its wire frames;
//! * [`panels`] is the registry of caption panels: check-ins, feeds, rotation, lock, firmware offers;
//! * [`screens`] is the `screens`, `places` and `screen_keys` tables on `relay.db`.
//!
//! Time is injected ([`Clock`]), so a test moves a clock instead of sleeping.

pub mod alerts;
pub mod firmware;
pub mod panels;
pub mod screens;
pub mod text;

use std::sync::Arc;

/// The monotonic clock the alert and panel timers read (seconds), and the wall clock the history is stamped with.
pub trait Clock: Send + Sync + 'static {
    fn now(&self) -> f64;
    /// `YYYY-MM-DDTHH:MM:SSZ`, as `app/db.py`'s `now()`.
    fn stamp(&self) -> String;
    /// Epoch seconds, for "changed N seconds ago".
    fn epoch(&self) -> f64;
}

/// The real clock.
pub struct SystemClock {
    start: std::time::Instant,
}

impl SystemClock {
    pub fn shared() -> Arc<dyn Clock> {
        Arc::new(Self { start: std::time::Instant::now() })
    }
}

impl Clock for SystemClock {
    fn now(&self) -> f64 {
        self.start.elapsed().as_secs_f64()
    }
    fn stamp(&self) -> String {
        stamp_of(self.epoch())
    }
    fn epoch(&self) -> f64 {
        std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs_f64()).unwrap_or(0.0)
    }
}

/// `YYYY-MM-DDTHH:MM:SSZ` for epoch seconds (civil-from-days, no time crate).
pub fn stamp_of(epoch: f64) -> String {
    let secs = epoch.max(0.0) as i64;
    let (days, rem) = (secs.div_euclid(86_400), secs.rem_euclid(86_400));
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m = if mp < 10 { mp + 3 } else { mp - 9 };
    let y = if m <= 2 { y + 1 } else { y };
    format!("{y:04}-{m:02}-{d:02}T{:02}:{:02}:{:02}Z", rem / 3600, rem % 3600 / 60, rem % 60)
}
