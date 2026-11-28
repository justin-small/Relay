//! The two clocks the recorder and the exports read: the wall clock, and how a moment is written in the operator's time zone.
//!
//! Python wrote run ids and export times with `time.localtime`. The crate takes the zone as a function, so a test fixes it and
//! the server hands in the machine's own (`relay-server` has the one `localtime_r` call).

use std::time::{SystemTime, UNIX_EPOCH};

/// A moment as the wall clock shows it.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Civil {
    pub year: i64,
    pub month: u32,
    pub day: u32,
    pub hour: u32,
    pub minute: u32,
    pub second: u32,
}

/// How a Unix time (seconds) reads in the zone in use.
pub type Zone = fn(f64) -> Civil;

/// Seconds since the epoch.
pub fn wall() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

/// UTC: what a test uses, and what a server without its own zone falls back to.
pub fn utc(ts: f64) -> Civil {
    let secs = ts.floor() as i64;
    let days = secs.div_euclid(86_400);
    let rem = secs.rem_euclid(86_400);
    // Howard Hinnant's civil-from-days.
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z.rem_euclid(146_097);
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = (doy - (153 * mp + 2) / 5 + 1) as u32;
    let month = if mp < 10 { mp + 3 } else { mp - 9 } as u32;
    let year = yoe + era * 400 + i64::from(month <= 2);
    Civil { year, month, day, hour: (rem / 3600) as u32, minute: (rem % 3600 / 60) as u32, second: (rem % 60) as u32 }
}

impl Civil {
    /// `%Y%m%d-%H%M%S`, a run id.
    pub fn run_stamp(&self) -> String {
        format!("{:04}{:02}{:02}-{:02}{:02}{:02}", self.year, self.month, self.day, self.hour, self.minute, self.second)
    }

    /// `%H:%M:%S`.
    pub fn clock(&self) -> String {
        format!("{:02}:{:02}:{:02}", self.hour, self.minute, self.second)
    }

    /// `%Y-%m-%d %H:%M:%S`.
    pub fn stamp(&self) -> String {
        format!("{:04}-{:02}-{:02} {}", self.year, self.month, self.day, self.clock())
    }
}

static ZONE: std::sync::RwLock<String> = std::sync::RwLock::new(String::new());

/// Name the Relay zone (config `timezone`, a US zone), for [`relay_zone`]. An unknown name is Eastern, as everywhere else.
pub fn set_zone(name: &str) {
    *ZONE.write().unwrap_or_else(|e| e.into_inner()) = name.to_owned();
}

/// How a Unix time reads in the one Relay zone: run ids, transcript times and the exports are in the operator's own time.
pub fn relay_zone(ts: f64) -> Civil {
    let name = ZONE.read().unwrap_or_else(|e| e.into_inner()).clone();
    let secs = ts.floor() as i64;
    let (date, minute) = relay_core::schedules::local_of_utc(secs.div_euclid(60), &name);
    Civil { year: date.y, month: date.m as u32, day: date.d as u32, hour: (minute / 60) as u32, minute: (minute % 60) as u32, second: secs.rem_euclid(60) as u32 }
}
