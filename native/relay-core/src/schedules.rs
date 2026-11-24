//! Named schedules (#526), the part boards and screens need: `app/schedules.py`'s `find`, `ref_id` and `is_open_local`. Is a named
//! schedule open at a local wall-clock time?
//!
//! Times are wall-clock in the one Relay zone (config `timezone`, a US zone), so "10:00 AM Sunday" stays 10:00 AM local on both sides of
//! a daylight-saving change. The US rule is the one in force since 2007: DST starts at 02:00 on the second Sunday of March and ends at
//! 02:00 DST on the first Sunday of November. A time that does not exist (the spring-forward gap) moves to the next minute that does;
//! one that happens twice (fall back) takes the first occurrence, as `app/schedules.py` does.
//!
//! The validation, the Schedules page's occurrences and the Planning Center plan times are not here: a schedule that adds the plan's
//! service times has only its own windows checked until the Planning Center module is ported (#661).

use serde_json::Value;

use crate::timewin::{self, Date, DAY_MINUTES};

pub const REF_PREFIX: &str = "schedule:relay:";
pub const DEFAULT_TIMEZONE: &str = "America/New_York";

/// `(standard offset in minutes, observes DST)` of the zones Relay offers.
fn zone(name: &str) -> (i64, bool) {
    match name {
        "America/Chicago" => (-6 * 60, true),
        "America/Denver" => (-7 * 60, true),
        "America/Phoenix" => (-7 * 60, false),
        "America/Los_Angeles" => (-8 * 60, true),
        "America/Anchorage" => (-9 * 60, true),
        "Pacific/Honolulu" => (-10 * 60, false),
        _ => (-5 * 60, true),
    }
}

/// The Relay zone: `name` if it is one Relay offers, else Eastern.
pub fn clean_zone(name: &str) -> &str {
    if ["America/New_York", "America/Chicago", "America/Denver", "America/Phoenix", "America/Los_Angeles", "America/Anchorage", "Pacific/Honolulu"].contains(&name) { name } else { DEFAULT_TIMEZONE }
}

/// The date of the `n`th Sunday (1-based) of `month` in `year`.
fn nth_sunday(year: i64, month: i64, n: i64) -> Date {
    let first = Date::new(year, month, 1);
    first.plus((7 - first.weekday()).rem_euclid(7) + 7 * (n - 1))
}

/// DST, as wall-clock minutes since the epoch in standard time: starts at 02:00 on the second Sunday of March, ends at 02:00 DST
/// (01:00 standard) on the first Sunday of November.
fn dst_window(year: i64) -> (i64, i64) {
    (nth_sunday(year, 3, 2).days() * DAY_MINUTES + 2 * 60, nth_sunday(year, 11, 1).days() * DAY_MINUTES + 60)
}

/// UTC minutes of a wall-clock time that exists, `None` when `wall` (minutes since the epoch, local) falls in the spring-forward gap.
fn utc_of_wall(wall: i64, std: i64, dst: bool) -> Option<i64> {
    if !dst {
        return Some(wall - std);
    }
    let year = Date::from_days(wall.div_euclid(DAY_MINUTES)).y;
    let (start, end_std) = dst_window(year);
    // `wall` is local time. Standard-time wall clock before the change; DST wall clock after it; the repeated hour takes the first (DST).
    if wall >= start && wall < start + 60 {
        return None;
    }
    let in_dst = wall >= start + 60 && wall < end_std + 60;
    Some(wall - std - if in_dst { 60 } else { 0 })
}

/// Wall-clock minutes of a UTC instant in the zone.
fn wall_of_utc(utc: i64, std: i64, dst: bool) -> i64 {
    if !dst {
        return utc + std;
    }
    let year = Date::from_days((utc + std).div_euclid(DAY_MINUTES)).y;
    let (start, end_std) = dst_window(year);
    let (spring_utc, fall_utc) = (start - std, end_std - std);
    utc + std + if (spring_utc..fall_utc).contains(&utc) { 60 } else { 0 }
}

/// Wall-clock `date time` in the zone, as UTC minutes. A time that does not exist moves to the next minute that does.
pub fn localize(date: Date, minute: i64, tz: &str) -> i64 {
    let (std, dst) = zone(tz);
    let naive = date.days() * DAY_MINUTES + minute;
    for step in 0..=180 {
        let cand = naive + step;
        if let Some(utc) = utc_of_wall(cand, std, dst) {
            if wall_of_utc(utc, std, dst) == cand {
                return utc;
            }
        }
    }
    naive - std
}

/// The schedule id inside a ref, or `None` when `value` is not one.
pub fn ref_id(value: &str) -> Option<&str> {
    let id = value.strip_prefix(REF_PREFIX)?;
    (!id.is_empty() && id.len() <= 32 && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')).then_some(id)
}

/// The schedule a ref (or a bare id) points at, or `None`.
pub fn find<'a>(scheds: &'a Value, value: &str) -> Option<&'a Value> {
    let sid = ref_id(value).unwrap_or(value);
    scheds.as_array()?.iter().find(|s| s["id"].as_str() == Some(sid))
}

/// Whether schedule `s` has a window covering `now`, a wall-clock `(date, minute)` in the Relay zone (boards and signage work in local
/// time). A window that is not valid is skipped; a schedule needs no `enabled` here, the caller decides.
pub fn is_open_local(s: &Value, now_date: Date, now_minute: i64, tz: &str) -> bool {
    let tz = clean_zone(tz);
    let at = localize(now_date, now_minute, tz);
    for w in s["windows"].as_array().map(Vec::as_slice).unwrap_or(&[]) {
        let (Some(start), Some(stop)) = (timewin::clean_clock(&w["start_time"], false), timewin::clean_clock(&w["stop_time"], false)) else { continue };
        let (start, stop) = (timewin::minutes(&start), timewin::minutes(&stop));
        let overnight = stop <= start;
        let first = w["start_date"].as_str().and_then(Date::parse);
        let last = w["end_date"].as_str().and_then(Date::parse);
        let dates: Vec<Date> = if w["repeat"] == "once" {
            first.into_iter().collect()
        } else {
            let days: Vec<i64> = w["days"].as_array().map(|a| a.iter().filter_map(Value::as_i64).collect()).unwrap_or_default();
            // One day back: an overnight window that began yesterday is live now.
            (-1..=1)
                .map(|n| now_date.plus(n))
                .filter(|d| days.contains(&d.weekday()) && first.is_none_or(|f| *d >= f) && last.is_none_or(|l| *d <= l))
                .collect()
        };
        for d in dates {
            let (a, b) = (localize(d, start, tz), localize(if overnight { d.plus(1) } else { d }, stop, tz));
            if b > a && (a..b).contains(&at) {
                return true;
            }
        }
    }
    false
}

/// US zones only, in the order the dropdown lists them (`app/schedules.py` `US_TIMEZONES`).
pub const US_TIMEZONES: [(&str, &str); 7] = [
    ("America/New_York", "Eastern"),
    ("America/Chicago", "Central"),
    ("America/Denver", "Mountain"),
    ("America/Phoenix", "Mountain – Arizona (no DST)"),
    ("America/Los_Angeles", "Pacific"),
    ("America/Anchorage", "Alaska"),
    ("Pacific/Honolulu", "Hawaii"),
];

/// The wall-clock `(date, minute)` in the zone at a UTC instant, given as epoch minutes.
pub fn local_of_utc(utc_minutes: i64, tz: &str) -> (Date, i64) {
    let (std, dst) = zone(clean_zone(tz));
    let wall = wall_of_utc(utc_minutes, std, dst);
    (Date::from_days(wall.div_euclid(DAY_MINUTES)), wall.rem_euclid(DAY_MINUTES))
}

/// `YYYY-MM-DD` as `MM/DD/YYYY`.
pub fn fmt_date(iso: &str) -> String {
    Date::parse(iso).map_or_else(String::new, |d| format!("{:02}/{:02}/{}", d.m, d.d, d.y))
}

const DAY_ABBR: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/// One window as a line: `Every Sun, Wed · 10:00 AM – 12:00 PM`.
pub fn window_summary(w: &Value) -> String {
    let (start, stop) = (w["start_time"].as_str().unwrap_or(""), w["stop_time"].as_str().unwrap_or(""));
    let mut span = format!("{} – {}", timewin::us_clock(start), timewin::us_clock(stop));
    if timewin::minutes(stop) <= timewin::minutes(start) {
        span.push_str(" (next day)");
    }
    if w["repeat"] == "once" {
        return format!("Once on {} · {span}", fmt_date(w["start_date"].as_str().unwrap_or("")));
    }
    let days: Vec<usize> = w["days"].as_array().map(|a| a.iter().filter_map(|d| d.as_u64().map(|d| d as usize)).collect()).unwrap_or_default();
    let when = if days.len() == 7 { "Every day".to_owned() } else { format!("Every {}", days.iter().map(|d| DAY_ABBR[d % 7]).collect::<Vec<_>>().join(", ")) };
    let (from, to) = (w["start_date"].as_str().filter(|s| !s.is_empty()), w["end_date"].as_str().filter(|s| !s.is_empty()));
    let bounds = match (from, to) {
        (Some(a), Some(b)) => format!(", {} to {}", fmt_date(a), fmt_date(b)),
        (Some(a), None) => format!(", from {}", fmt_date(a)),
        (None, Some(b)) => format!(", until {}", fmt_date(b)),
        _ => String::new(),
    };
    format!("{when} · {span}{bounds}")
}

/// The schedule's windows in one line for the list.
pub fn summary(s: &Value) -> String {
    let mut parts: Vec<String> = s["windows"].as_array().into_iter().flatten().map(window_summary).collect();
    if s["plan_times"] == true {
        parts.push(format!("Planning Center service times ({} min before to {} min after)", s["plan_before"], s["plan_after"]));
    }
    parts.join("; ")
}
