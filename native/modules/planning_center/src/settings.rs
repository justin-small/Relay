//! The `planning_center` block of config.json (`planningcenter.clean_stored`), and the signage settings inside it (`signagesync.py`).
//! Read as stored and cleaned the way Python cleans it, so an existing config.json loads unchanged.

use serde_json::{json, Map, Value};

use crate::plan::{is_id, text};
use crate::rundown::clean_rundown;

/// The credentials, the service types Relay follows, any plan the operator picked for today, whether notes reach panels, the rundown and the
/// signage settings, validated. Off (all blank) until a token is saved.
pub fn clean_stored(raw: &Value) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let mut ids: Vec<String> = Vec::new();
    for v in raw.get("service_types").and_then(Value::as_array).into_iter().flatten() {
        let s = text(v).trim().to_owned();
        if is_id(&s) && !ids.contains(&s) {
            ids.push(s);
        }
    }
    // The operator's pick of a different plan, good for the one day it was made.
    let mut overrides = Map::new();
    for (st, o) in raw.get("plan_overrides").and_then(Value::as_object).into_iter().flatten() {
        let (plan, day) = (text(&o["plan"]), text(&o["day"]));
        if ids.contains(st) && o.is_object() && is_id(&plan) && is_day(&day) {
            overrides.insert(st.clone(), json!({"plan": plan, "day": day}));
        }
    }
    ids.truncate(200);
    json!({
        // Plan files and announcements as signage items (#209, signagesync.rs).
        "signage": clean_signage_settings(raw.get("signage").unwrap_or(&Value::Null), &json!({})),
        "app_id": text(raw.get("app_id").unwrap_or(&Value::Null)).trim(),
        "secret": text(raw.get("secret").unwrap_or(&Value::Null)).trim(),
        "service_types": ids,
        "plan_overrides": overrides,
        // Item notes reach panels only when the operator turns this on.
        "show_notes": raw.get("show_notes") == Some(&Value::Bool(true)),
        // The rundown on /screen, mics by position and the note fields it reads (#148).
        "rundown": clean_rundown(raw.get("rundown").unwrap_or(&Value::Null)),
    })
}

/// `YYYY-MM-DD` exactly.
pub fn is_day(t: &str) -> bool {
    let b = t.as_bytes();
    b.len() == 10 && b.iter().enumerate().all(|(i, c)| if i == 4 || i == 7 { *c == b'-' } else { c.is_ascii_digit() })
}

pub fn default_signage() -> Value {
    json!({"enabled": false, "service_type": "", "plan_files": true, "item_files": true, "text_slides": false, "note_category": "", "item_match": "", "playlist": false})
}

/// The settings `raw` sets, each checked, with a fixed sentence for the first thing wrong. A key left out stays out.
pub fn signage_fields(raw: &Value) -> (Map<String, Value>, &'static str) {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let mut out = Map::new();
    let mut problem = "";
    for key in ["enabled", "plan_files", "item_files", "text_slides", "playlist"] {
        if let Some(v) = raw.get(key).filter(|v| !v.is_null()) {
            out.insert(key.to_owned(), json!(v == &Value::Bool(true)));
        }
    }
    if let Some(v) = raw.get("service_type").filter(|v| !v.is_null()) {
        let v = text(v).trim().to_owned();
        if !v.is_empty() && !is_id(&v) {
            problem = "That is not a Planning Center service type.";
        } else {
            out.insert("service_type".to_owned(), json!(v));
        }
    }
    for (key, what) in [("note_category", "note category"), ("item_match", "plan item word")] {
        if let Some(v) = raw.get(key).filter(|v| !v.is_null()) {
            let v = relay_core::text::squeeze(&text(v));
            if v.chars().count() > 80 {
                if problem.is_empty() {
                    problem = if what == "note category" { "The note category is too long." } else { "The plan item word is too long." };
                }
            } else {
                out.insert(key.to_owned(), json!(v));
            }
        }
    }
    (out, problem)
}

pub fn clean_signage_settings(raw: &Value, base: &Value) -> Value {
    let mut out = default_signage();
    for src in [base.clone(), Value::Object(signage_fields(raw).0)] {
        if let (Some(o), Some(s)) = (out.as_object_mut(), src.as_object()) {
            for (k, v) in s {
                o.insert(k.clone(), v.clone());
            }
        }
    }
    out
}
