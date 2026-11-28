//! A timer's settings, checked one way for a caption panel's own and for ProPresenter's global ones (`app/panels.py`'s `timer_fields`).

use serde_json::{json, Map, Value};

/// A panel has room for a few large timers, not a list.
pub const MAX_TIMERS_SHOWN: usize = 4;
/// A day less a second.
pub const MAX_THRESHOLD_S: f64 = 86399.0;

/// A timer's id: a ProPresenter UUID, name or index.
pub fn timer_id_ok(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 64 && b[0].is_ascii_alphanumeric() && b.iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'_' | b'-'))
}

/// What `raw` sets: `amber_s` and `red_s` (seconds left, 0 to a day) and `show` (timer ids), each checked. A key left out stays out and
/// one that is wrong is dropped. Returns them with a fixed sentence for the first thing that was wrong ("" when nothing was).
pub fn timer_fields(raw: &Value) -> (Map<String, Value>, String) {
    let mut out = Map::new();
    let mut problem = String::new();
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    for (key, label) in [("amber_s", "amber"), ("red_s", "red")] {
        let Some(v) = raw.get(key).filter(|v| !v.is_null()) else { continue };
        match v {
            Value::Number(n) if n.as_f64().is_some_and(|f| (0.0..=MAX_THRESHOLD_S).contains(&f)) => {
                out.insert(key.to_owned(), json!(n.as_f64().map_or(0, |f| f as i64)));
            }
            _ => {
                if problem.is_empty() {
                    problem = format!("The {label} threshold is a number of seconds from 0 to {}.", MAX_THRESHOLD_S as i64);
                }
            }
        }
    }
    if let Some(show) = raw.get("show").filter(|v| !v.is_null()) {
        let good: Vec<String> = show.as_array().map(|a| a.iter().filter_map(Value::as_str).filter(|u| timer_id_ok(u)).map(str::to_owned).collect()).unwrap_or_default();
        if show.as_array().is_none_or(|a| a.len() != good.len()) && problem.is_empty() {
            problem = "Timers to show is a list of timer IDs.".into();
        }
        let mut seen: Vec<String> = Vec::new();
        for g in good {
            if !seen.contains(&g) {
                seen.push(g);
            }
        }
        seen.truncate(MAX_TIMERS_SHOWN);
        out.insert("show".into(), json!(seen));
    }
    (out, problem)
}
