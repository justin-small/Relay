//! Shared time helpers (#522): `app/timewin.py`. **Days count from Sunday = 0.** Clock times are `HH:MM`, 24-hour. A window whose end is
//! not after its start runs past midnight, and the day it started on is the one that counts.
//!
//! Dates and times here are plain numbers (a [`Date`] and minutes after midnight): the one Relay zone and its daylight-saving rule are
//! in [`crate::schedules`].

use serde_json::Value;

pub const DAY_KEYS: [&str; 7] = ["sun", "mon", "tue", "wed", "thu", "fri", "sat"];
pub const DAY_MINUTES: i64 = 24 * 60;

/// A calendar date.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Date {
    pub y: i64,
    pub m: i64,
    pub d: i64,
}

impl Date {
    pub fn new(y: i64, m: i64, d: i64) -> Self {
        Self { y, m, d }
    }

    /// Days since 1970-01-01.
    pub fn days(self) -> i64 {
        let y = self.y - i64::from(self.m <= 2);
        let era = y.div_euclid(400);
        let yoe = y.rem_euclid(400);
        let doy = (153 * (if self.m > 2 { self.m - 3 } else { self.m + 9 }) + 2) / 5 + self.d - 1;
        let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
        era * 146_097 + doe - 719_468
    }

    pub fn from_days(z: i64) -> Self {
        let z = z + 719_468;
        let era = z.div_euclid(146_097);
        let doe = z.rem_euclid(146_097);
        let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        Self { y: yoe + era * 400 + i64::from(m <= 2), m, d }
    }

    /// The day of the week, Sunday = 0.
    pub fn weekday(self) -> i64 {
        (self.days() + 4).rem_euclid(7)
    }

    pub fn plus(self, days: i64) -> Self {
        Self::from_days(self.days() + days)
    }

    /// `YYYY-MM-DD`, as `date.fromisoformat` reads it.
    pub fn parse(text: &str) -> Option<Self> {
        let p: Vec<&str> = text.split('-').collect();
        if p.len() != 3 || p[0].len() != 4 || p[1].len() != 2 || p[2].len() != 2 {
            return None;
        }
        let (y, m, d) = (p[0].parse().ok()?, p[1].parse().ok()?, p[2].parse().ok()?);
        let date = Self { y, m, d };
        ((1..=12).contains(&m) && d >= 1 && Self::from_days(date.days()) == date).then_some(date)
    }
}

/// `text` as `HH:MM`, or `None` when it is not a clock time. `loose` also takes a one-digit hour (`9:30`).
pub fn clean_clock(text: &Value, loose: bool) -> Option<String> {
    let s = text.as_str()?.trim();
    let (h, m) = s.split_once(':')?;
    let hour_ok = if loose { (1..=2).contains(&h.len()) } else { h.len() == 2 };
    if !hour_ok || m.len() != 2 || !h.bytes().all(|b| b.is_ascii_digit()) || !m.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    let (h, m): (i64, i64) = (h.parse().ok()?, m.parse().ok()?);
    ((0..24).contains(&h) && (0..60).contains(&m)).then(|| format!("{h:02}:{m:02}"))
}

pub fn clean_clock_str(text: &str, loose: bool) -> Option<String> {
    clean_clock(&Value::String(text.to_owned()), loose)
}

/// Minutes after midnight of a checked `HH:MM`.
pub fn minutes(hhmm: &str) -> i64 {
    hhmm.get(..2).and_then(|h| h.parse::<i64>().ok()).unwrap_or(0) * 60 + hhmm.get(3..5).and_then(|m| m.parse::<i64>().ok()).unwrap_or(0)
}

/// Whether a run of a daily window covers the wall-clock `(date, minute)` now: the run's start as minutes since the epoch, or `None`.
/// `end` at or before `start` runs past midnight into the next day, and `start == end` is the whole day. `day_ok(d)` says whether a
/// run may start on date `d`.
pub fn window_start(now_date: Date, now_minute: i64, start: i64, end: i64, day_ok: impl Fn(Date) -> bool) -> Option<i64> {
    let (start, end) = if start == end { (0, DAY_MINUTES) } else { (start, end) };
    let crossing = end <= start;
    let now = now_date.days() * DAY_MINUTES + now_minute;
    let mut candidates = vec![now_date];
    if crossing {
        candidates.push(now_date.plus(-1));
    }
    for d in candidates {
        if !day_ok(d) {
            continue;
        }
        let midnight = d.days() * DAY_MINUTES;
        let (begin, finish) = (midnight + start, midnight + end + if crossing { DAY_MINUTES } else { 0 });
        if (begin..finish).contains(&now) {
            return Some(begin);
        }
    }
    None
}

/// A checked `HH:MM` as the pages show it: `9:30 AM`, `12:05 AM`, `8:30 PM`.
pub fn us_clock(hhmm: &str) -> String {
    let mins = minutes(hhmm);
    let (h, m) = (mins / 60, mins % 60);
    format!("{}:{m:02} {}", if h % 12 == 0 { 12 } else { h % 12 }, if h < 12 { "AM" } else { "PM" })
}

/// Whether `text` is a clock time written `HH:MM`, 24-hour.
pub fn is_clock(text: &str) -> bool {
    clean_clock_str(text, false).is_some_and(|c| c == text)
}

/// `YYYY-MM-DDTHH:MM:SSZ` for epoch seconds: the text every time column holds.
pub fn iso(epoch: i64) -> String {
    let (days, rem) = (epoch.div_euclid(86_400), epoch.rem_euclid(86_400));
    let d = Date::from_days(days);
    format!("{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z", d.y, d.m, d.d, rem / 3600, rem % 3600 / 60, rem % 60)
}
