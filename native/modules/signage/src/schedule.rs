//! Signage schedules (#206): which playlist a screen plays right now, and why (`schedule.py`). Pure functions; the store reads the rows.
//!
//! Precedence, highest first: a takeover, a slide-note tag, an override an operator chose by hand, the schedule slot that matches now (a
//! screen's own, else its group's, else the all-screens ones; a one-off date range beats a weekly slot, and the later start wins a tie),
//! then the default (the group's playlist, else the screen's own). The takeover and tag layers belong to other modules and are not here.
//!
//! Times are wall-clock in the one Relay zone, days count from Sunday = 0, and a slot whose end is not after its start runs into the
//! next day.

use relay_core::schedules;
use relay_core::timewin::{self, Date};
use serde_json::{json, Map, Value};

pub const TARGETS: [&str; 3] = ["screen", "group", "all"];
pub const HORIZON_DAYS: i64 = 8;
const DAY_NAMES: [&str; 7] = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];

/// A slot signage will not save: a fixed sentence for the admin page.
pub type Refused = &'static str;

fn date_ok(text: &str) -> bool {
    text.len() == 10 && Date::parse(text).is_some()
}

fn int_of(v: Option<&Value>) -> Option<i64> {
    match v {
        None | Some(Value::Null) => Some(0),
        Some(Value::Bool(b)) => Some(i64::from(*b)),
        Some(Value::Number(n)) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Some(Value::String(s)) => s.trim().parse().ok(),
        _ => None,
    }
}

fn text_of(v: Option<&Value>) -> String {
    match v {
        Some(Value::String(s)) => s.trim().to_owned(),
        _ => String::new(),
    }
}

/// Validate a slot from the admin page into the columns the table holds.
pub fn clean_slot(raw: &Value) -> Result<Map<String, Value>, Refused> {
    let Some(raw) = raw.as_object() else { return Err("A slot must be an object.") };
    let target = text_of(raw.get("target"));
    let target = TARGETS.iter().find(|t| **t == target).ok_or("Choose a screen, a group or all screens.")?;
    let target_id = if *target == "all" { 0 } else { int_of(raw.get("target_id")).ok_or("Choose a screen or a group.")? };
    if *target != "all" && target_id <= 0 {
        return Err("Choose a screen or a group.");
    }
    let playlist_id = int_of(raw.get("playlist_id")).ok_or("Choose a playlist.")?;
    if playlist_id <= 0 {
        return Err("Choose a playlist.");
    }
    let mut out = Map::new();
    for (k, v) in [("target", json!(target)), ("target_id", json!(target_id)), ("playlist_id", json!(playlist_id))] {
        out.insert(k.into(), v);
    }
    let named = text_of(raw.get("schedule_ref"));
    if !named.is_empty() {
        if schedules::ref_id(&named).is_none() {
            return Err("Choose one of the named schedules.");
        }
        for (k, v) in [("days", ""), ("start_time", ""), ("end_time", ""), ("start_date", ""), ("end_date", "")] {
            out.insert(k.into(), json!(v));
        }
        out.insert("schedule_ref".into(), json!(named));
        return Ok(out);
    }
    let (start_date, end_date) = (text_of(raw.get("start_date")), text_of(raw.get("end_date")));
    for d in [&start_date, &end_date] {
        if !d.is_empty() && !(d.len() == 10 && d.bytes().enumerate().all(|(i, b)| if i == 4 || i == 7 { b == b'-' } else { b.is_ascii_digit() })) {
            return Err("Dates are written YYYY-MM-DD.");
        }
        if !d.is_empty() && !date_ok(d) {
            return Err("That date does not exist.");
        }
    }
    if start_date.is_empty() != end_date.is_empty() {
        return Err("A date range needs both a first and a last day.");
    }
    if !start_date.is_empty() && end_date < start_date {
        return Err("The range ends before it starts.");
    }
    let (start_time, end_time) = (text_of(raw.get("start_time")), text_of(raw.get("end_time")));
    if start_time.is_empty() != end_time.is_empty() {
        return Err("Give both a start and an end time, or neither for the whole day.");
    }
    for t in [&start_time, &end_time] {
        if !t.is_empty() && !timewin::is_clock(t) {
            return Err("Times are written HH:MM, 24-hour.");
        }
    }
    if !start_time.is_empty() && start_time == end_time {
        return Err("The start and end time must differ.");
    }
    let mut days: Vec<i64> = Vec::new();
    for d in raw.get("days").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[]) {
        match d {
            Value::Number(n) if n.as_i64().is_some_and(|d| (0..=6).contains(&d)) => {
                let d = n.as_i64().unwrap_or(0);
                if !days.contains(&d) {
                    days.push(d);
                }
            }
            _ => return Err("Days are 0 (Sunday) to 6 (Saturday)."),
        }
    }
    days.sort_unstable();
    if start_date.is_empty() && days.is_empty() {
        return Err("Choose the days of the week, or a range of dates.");
    }
    out.insert("days".into(), json!(days.iter().map(i64::to_string).collect::<Vec<_>>().join(",")));
    for (k, v) in [("start_time", start_time), ("end_time", end_time), ("start_date", start_date), ("end_date", end_date)] {
        out.insert(k.into(), json!(v));
    }
    out.insert("schedule_ref".into(), json!(""));
    Ok(out)
}

pub fn is_range(slot: &Value) -> bool {
    !slot["start_date"].as_str().unwrap_or("").is_empty() && slot["schedule_ref"].as_str().unwrap_or("").is_empty()
}

/// Whether a named schedule is open at a wall-clock `(date, minute)`.
pub type OpenRef<'a> = &'a dyn Fn(&str, Date, i64) -> bool;

/// When the run of `slot` that covers wall-clock `now` began (minutes since the epoch, local), or `None` if it does not cover it. A slot
/// that names a schedule is covered while that schedule is open (its start is `now`: slots only compare starts to break ties).
fn occurrence_start(slot: &Value, date: Date, minute: i64, open_ref: Option<OpenRef>) -> Option<i64> {
    if let Some(named) = slot["schedule_ref"].as_str().filter(|s| !s.is_empty()) {
        return open_ref.is_some_and(|f| f(named, date, minute)).then_some(date.days() * 1440 + minute);
    }
    let days: Vec<i64> = slot["days"].as_str().unwrap_or("").split(',').filter(|d| !d.is_empty()).filter_map(|d| d.parse().ok()).collect();
    let timed = !slot["start_time"].as_str().unwrap_or("").is_empty();
    let (starts, ends) = if timed { (timewin::minutes(slot["start_time"].as_str().unwrap_or("")), timewin::minutes(slot["end_time"].as_str().unwrap_or(""))) } else { (0, 0) };
    let (from, to) = (slot["start_date"].as_str().unwrap_or("").to_owned(), slot["end_date"].as_str().unwrap_or("").to_owned());
    let day_ok = |d: Date| {
        if !days.is_empty() && !days.contains(&d.weekday()) {
            return false;
        }
        let iso = format!("{:04}-{:02}-{:02}", d.y, d.m, d.d);
        from.is_empty() || (from <= iso && iso <= to)
    };
    timewin::window_start(date, minute, starts, ends, day_ok)
}

fn level(slot: &Value, screen: &Value) -> Option<i64> {
    match slot["target"].as_str()? {
        "screen" => (slot["target_id"] == screen["id"]).then_some(0),
        "group" => (!screen["group_id"].is_null() && slot["target_id"] == screen["group_id"]).then_some(1),
        _ => Some(2),
    }
}

/// The slots that apply to `screen` at wall-clock `now`, best first.
pub fn matching<'a>(slots: &'a [Value], screen: &Value, date: Date, minute: i64, open_ref: Option<OpenRef>) -> Vec<&'a Value> {
    let mut found: Vec<((i64, i64, i64, i64), &Value)> = Vec::new();
    for s in slots {
        let Some(level) = level(s, screen) else { continue };
        let Some(began) = occurrence_start(s, date, minute, open_ref) else { continue };
        found.push(((level, if is_range(s) { 0 } else { 1 }, -began, s["id"].as_i64().unwrap_or(0)), s));
    }
    found.sort_by_key(|p| p.0);
    found.into_iter().map(|(_, s)| s).collect()
}

/// `Sun, Wed 9:00 AM-11:30 AM`, `2026-12-20 to 2027-01-03 all day`, or the named schedule, for the admin page.
pub fn describe_slot(slot: &Value, names: &Map<String, Value>) -> String {
    if let Some(named) = slot["schedule_ref"].as_str().filter(|s| !s.is_empty()) {
        return format!("Schedule: {}", names.get(named).and_then(Value::as_str).unwrap_or("(deleted)"));
    }
    let when = match slot["start_time"].as_str().filter(|s| !s.is_empty()) {
        Some(s) => format!("{}-{}", timewin::us_clock(s), timewin::us_clock(slot["end_time"].as_str().unwrap_or(""))),
        None => "all day".to_owned(),
    };
    let ds: Vec<usize> = slot["days"].as_str().unwrap_or("").split(',').filter_map(|d| d.parse().ok()).collect();
    if is_range(slot) {
        let (a, b) = (slot["start_date"].as_str().unwrap_or(""), slot["end_date"].as_str().unwrap_or(""));
        let span = if a == b { a.to_owned() } else { format!("{a} to {b}") };
        let days = if ds.is_empty() { String::new() } else { format!("{} ", ds.iter().map(|d| DAY_NAMES[d % 7]).collect::<Vec<_>>().join(", ")) };
        return format!("{span}, {days}{when}");
    }
    let names = if ds.len() == 7 { "Every day".to_owned() } else { ds.iter().map(|d| DAY_NAMES[d % 7]).collect::<Vec<_>>().join(", ") };
    format!("{names} {when}")
}

/// The first minute after `start_utc` (epoch minutes) at which `key(wall-clock now)` differs from its value at `start_utc`, or `None`
/// when it does not change within [`HORIZON_DAYS`]. Steps in UTC, so a daylight-saving change is no special case.
pub fn next_change<K: PartialEq>(key: impl Fn(Date, i64) -> K, start_utc: i64, tz: &str) -> Option<i64> {
    let at = |t: i64| {
        let (d, m) = schedules::local_of_utc(t, tz);
        key(d, m)
    };
    let base = at(start_utc);
    (1..=HORIZON_DAYS * 24 * 60).map(|n| start_utc + n).find(|t| at(*t) != base)
}
