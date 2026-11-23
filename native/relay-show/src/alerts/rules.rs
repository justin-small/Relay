//! What the operator may save for alerts and stage messages (`app/alerts.py` `clean_*` and `validate*`): `clean_stored` drops what
//! is malformed (a hand edit cannot stop Relay starting), `validate` refuses it with a sentence (what the admin page shows).

use rand::Rng;
use serde_json::{json, Map, Value};

use crate::text::{one_line, one_line_of, py_int, strict_int};

pub const MAX_BUTTONS: usize = 16;
pub const MAX_TARGETS: usize = 16;
pub const NAME_MAX: usize = 32;
pub const AUTO_STOP_DEFAULT_S: i64 = 120;
pub const AUTO_STOP_MIN_S: i64 = 30;
pub const AUTO_STOP_MAX_S: i64 = 600;
pub const MAX_PRESETS: usize = 16;
pub const TEXT_MAX: usize = 80;
pub const MESSAGE_DEFAULT_S: i64 = 10;
pub const MESSAGE_MIN_S: i64 = 3;
pub const MESSAGE_MAX_S: i64 = 120;
pub const BANNER_DEFAULT_S: i64 = 300;
pub const BANNER_MIN_S: i64 = 30;
pub const BANNER_MAX_S: i64 = 3600;
pub const MAX_REPLIES: usize = 6;
pub const REPLY_MAX: usize = 24;

pub const LEVELS: [&str; 3] = ["info", "attention", "urgent"];
pub const DEFAULT_ALERT_LEVEL: &str = "urgent";
pub const DEFAULT_MESSAGE_LEVEL: &str = "info";
pub const ICONS: [&str; 10] = ["bell", "mic", "headphones", "keys", "drums", "guitar", "person", "people", "video", "sliders"];
pub const DEFAULT_ICON: &str = "bell";

pub fn level_flashes(level: &str) -> bool {
    level != "info"
}

fn is_hex(s: &str, n: usize) -> bool {
    s.len() == n && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

pub fn is_button_id(s: &str) -> bool {
    is_hex(s, 8)
}
pub fn is_alert_id(s: &str) -> bool {
    is_hex(s, 16)
}
pub fn is_panel_id(s: &str) -> bool {
    is_hex(s, 32)
}

pub fn token_hex(bytes: usize) -> String {
    let mut rng = rand::thread_rng();
    (0..bytes).map(|_| format!("{:02x}", rng.gen::<u8>())).collect()
}

pub fn clean_name(v: &Value) -> String {
    one_line_of(v, NAME_MAX)
}
pub fn clean_name_str(s: &str) -> String {
    one_line(s, NAME_MAX)
}
pub fn clean_text(v: &Value) -> String {
    one_line_of(v, TEXT_MAX)
}
pub fn clean_reply(v: &Value) -> String {
    one_line_of(v, REPLY_MAX)
}

pub fn clean_icon(v: &Value) -> &'static str {
    v.as_str().and_then(|s| ICONS.iter().find(|i| **i == s).copied()).unwrap_or(DEFAULT_ICON)
}

pub fn clean_level(v: &Value, default: &'static str) -> &'static str {
    v.as_str().and_then(|s| LEVELS.iter().find(|l| **l == s).copied()).unwrap_or(default)
}

fn checked_level(item: &Map<String, Value>, label: &str, default: &'static str) -> Result<&'static str, String> {
    match item.get("level") {
        None => Ok(default),
        Some(v) => match v.as_str().and_then(|s| LEVELS.iter().find(|l| **l == s)) {
            Some(l) => Ok(l),
            None => Err(format!("{label}: the level must be one of {}", LEVELS.join(", "))),
        },
    }
}

fn seconds(raw: Option<&Value>, default: i64, lo: i64, hi: i64) -> i64 {
    raw.and_then(py_int).map(|n| n.clamp(lo, hi)).unwrap_or(default)
}

fn lower(s: &str) -> String {
    s.to_lowercase()
}

/// Repeats by lower-cased text dropped, first kept (`_unique`).
pub fn unique(items: Vec<String>) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for item in items {
        if !out.iter().any(|o| lower(o) == lower(&item)) {
            out.push(item);
        }
    }
    out
}

fn list(item: &Map<String, Value>, key: &str) -> Vec<Value> {
    item.get(key).and_then(Value::as_array).cloned().unwrap_or_default()
}

/// `(panel ids, screen names)` of a stored item; malformed entries dropped.
fn targets(item: &Map<String, Value>) -> (Vec<String>, Vec<String>) {
    let panels = unique(list(item, "panels").iter().filter_map(Value::as_str).filter(|p| is_panel_id(p)).map(str::to_owned).collect());
    let screens = unique(list(item, "screens").iter().map(clean_name).collect()).into_iter().filter(|s| !s.is_empty()).collect();
    (panels, screens)
}

fn checked_targets(item: &Map<String, Value>, label: &str) -> Result<(Vec<String>, Vec<String>), String> {
    let falsy = |v: Option<&Value>| matches!(v, None | Some(Value::Null)) || v.is_some_and(|v| v.as_array().is_some_and(Vec::is_empty));
    let panels_raw = if falsy(item.get("panels")) { Value::Array(vec![]) } else { item["panels"].clone() };
    let screens_raw = if falsy(item.get("screens")) { Value::Array(vec![]) } else { item["screens"].clone() };
    let panels: Vec<String> = match &panels_raw {
        Value::Array(a) if a.iter().all(|p| p.as_str().is_some_and(is_panel_id)) => a.iter().filter_map(Value::as_str).map(str::to_owned).collect(),
        _ => return Err(format!("{label}: panels must be panel ids")),
    };
    let screens: Vec<Value> = match &screens_raw {
        Value::Array(a) if a.iter().all(Value::is_string) => a.clone(),
        _ => return Err(format!("{label}: screens must be names")),
    };
    let panels = unique(panels);
    let screens: Vec<String> = unique(screens.iter().map(clean_name).collect()).into_iter().filter(|s| !s.is_empty()).collect();
    if panels.len() + screens.len() > MAX_TARGETS {
        return Err(format!("{label}: at most {MAX_TARGETS} panels and screens"));
    }
    Ok((panels, screens))
}

pub fn clean_replies(raw: &Value) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    for item in raw.as_array().map(Vec::as_slice).unwrap_or(&[]) {
        let text = clean_reply(item);
        if !text.is_empty() && !out.iter().any(|o| lower(o) == lower(&text)) && out.len() < MAX_REPLIES {
            out.push(text);
        }
    }
    out
}

pub fn validate_replies(raw: &Value) -> Result<Vec<String>, String> {
    if raw.is_null() {
        return Ok(vec![]);
    }
    let items = match raw.as_array() {
        Some(a) if a.iter().all(Value::is_string) => a,
        _ => return Err("replies must be a list of words".into()),
    };
    if items.len() > MAX_REPLIES {
        return Err(format!("at most {MAX_REPLIES} replies"));
    }
    let mut out: Vec<String> = Vec::new();
    for item in items {
        let text = clean_reply(item);
        if text.is_empty() {
            return Err("every reply needs words".into());
        }
        let squeezed = item.as_str().unwrap_or("").split_whitespace().collect::<Vec<_>>().join(" ");
        if squeezed.chars().count() > REPLY_MAX {
            return Err(format!("a reply is at most {REPLY_MAX} characters"));
        }
        if out.iter().any(|o| lower(o) == lower(&text)) {
            return Err(format!("two replies say \"{text}\""));
        }
        out.push(text);
    }
    Ok(out)
}

pub fn clean_messages(raw: &Value) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let mut presets: Vec<Value> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for item in raw.get("presets").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[]) {
        let Some(item) = item.as_object() else { continue };
        if presets.len() >= MAX_PRESETS {
            continue;
        }
        let text = clean_text(item.get("text").unwrap_or(&Value::Null));
        let (panels, screens) = targets(item);
        let pid = item.get("id").and_then(Value::as_str);
        let Some(pid) = pid.filter(|p| is_button_id(p) && !seen.iter().any(|s| s == p)) else { continue };
        if text.is_empty() || panels.len() + screens.len() > MAX_TARGETS {
            continue;
        }
        seen.push(pid.to_owned());
        presets.push(json!({"id": pid, "text": text, "banner": item.get("banner") == Some(&Value::Bool(true)),
            "level": clean_level(item.get("level").unwrap_or(&Value::Null), DEFAULT_MESSAGE_LEVEL), "panels": panels, "screens": screens}));
    }
    let free = raw.get("free").and_then(Value::as_object).unwrap_or(&empty);
    let (mut fp, mut fs) = targets(free);
    if fp.len() + fs.len() > MAX_TARGETS {
        fp.clear();
        fs.clear();
    }
    json!({"presets": presets,
        "free": {"panels": fp, "screens": fs, "level": clean_level(free.get("level").unwrap_or(&Value::Null), DEFAULT_MESSAGE_LEVEL)},
        "message_s": seconds(raw.get("message_s"), MESSAGE_DEFAULT_S, MESSAGE_MIN_S, MESSAGE_MAX_S),
        "banner_s": seconds(raw.get("banner_s"), BANNER_DEFAULT_S, BANNER_MIN_S, BANNER_MAX_S)})
}

pub fn validate_messages(raw: &Value) -> Result<Value, String> {
    if raw.is_null() {
        return Ok(clean_messages(&json!({})));
    }
    let Some(raw) = raw.as_object() else { return Err("messages must be an object".into()) };
    for (key, lo, hi, label, default) in [("message_s", MESSAGE_MIN_S, MESSAGE_MAX_S, "a message stays", MESSAGE_DEFAULT_S),
        ("banner_s", BANNER_MIN_S, BANNER_MAX_S, "a banner stays", BANNER_DEFAULT_S)]
    {
        let ok = match raw.get(key) {
            None => true,
            Some(v) => strict_int(v).is_some_and(|n| (lo..=hi).contains(&n)),
        };
        let _ = default;
        if !ok {
            return Err(format!("{label} {lo} to {hi} seconds"));
        }
    }
    let items = match raw.get("presets") {
        None => vec![],
        Some(Value::Array(a)) => a.clone(),
        Some(_) => return Err("presets must be a list".into()),
    };
    if items.len() > MAX_PRESETS {
        return Err(format!("at most {MAX_PRESETS} preset messages"));
    }
    let mut presets = Vec::new();
    let mut ids: Vec<String> = Vec::new();
    for item in &items {
        let Some(item) = item.as_object() else { return Err("each preset must be an object".into()) };
        let text = clean_text(item.get("text").unwrap_or(&Value::Null));
        if text.is_empty() {
            return Err("every preset message needs words".into());
        }
        let banner = match item.get("banner") {
            None => false,
            Some(Value::Bool(b)) => *b,
            Some(_) => return Err(format!("{text}: banner must be true or false")),
        };
        let mut pid = item.get("id").and_then(Value::as_str).filter(|p| is_button_id(p) && !ids.iter().any(|i| i == p)).map(str::to_owned);
        if pid.is_none() {
            pid = Some(token_hex(4));
        }
        let pid = pid.unwrap_or_default();
        ids.push(pid.clone());
        let (panels, screens) = checked_targets(item, &text)?;
        if panels.is_empty() && screens.is_empty() {
            return Err(format!("\"{text}\" goes nowhere; pick a panel or a screen"));
        }
        let level = checked_level(item, &text, DEFAULT_MESSAGE_LEVEL)?;
        presets.push(json!({"id": pid, "text": text, "banner": banner, "level": level, "panels": panels, "screens": screens}));
    }
    let free = match raw.get("free") {
        None => Map::new(),
        Some(Value::Object(o)) => o.clone(),
        Some(_) => return Err("free text must be an object".into()),
    };
    let (fp, fs) = checked_targets(&free, "Typed messages")?;
    let flevel = checked_level(&free, "Typed messages", DEFAULT_MESSAGE_LEVEL)?;
    Ok(json!({"presets": presets, "free": {"panels": fp, "screens": fs, "level": flevel},
        "message_s": raw.get("message_s").cloned().unwrap_or(json!(MESSAGE_DEFAULT_S)),
        "banner_s": raw.get("banner_s").cloned().unwrap_or(json!(BANNER_DEFAULT_S))}))
}

/// The stored `alerts` block with anything malformed dropped.
pub fn clean_stored(raw: &Value) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let auto = raw.get("auto_stop_s").filter(|v| !matches!(v, Value::Null | Value::Bool(false)) && v.as_i64() != Some(0)).and_then(py_int).unwrap_or(AUTO_STOP_DEFAULT_S);
    let mut buttons: Vec<Value> = Vec::new();
    let mut ids: Vec<String> = Vec::new();
    let mut names: Vec<String> = Vec::new();
    for item in raw.get("buttons").and_then(Value::as_array).map(Vec::as_slice).unwrap_or(&[]) {
        let Some(item) = item.as_object() else { continue };
        if buttons.len() >= MAX_BUTTONS {
            continue;
        }
        let bid = item.get("id").and_then(Value::as_str);
        let name = clean_name(item.get("name").unwrap_or(&Value::Null));
        let Some(bid) = bid.filter(|b| is_button_id(b) && !ids.iter().any(|i| i == b)) else { continue };
        if name.is_empty() || names.contains(&lower(&name)) {
            continue;
        }
        let (panels, screens) = targets(item);
        if panels.len() + screens.len() > MAX_TARGETS {
            continue;
        }
        ids.push(bid.to_owned());
        names.push(lower(&name));
        buttons.push(json!({"id": bid, "name": name, "icon": clean_icon(item.get("icon").unwrap_or(&Value::Null)),
            "panels": panels, "screens": screens, "stage": item.get("stage") == Some(&Value::Bool(true)),
            "level": clean_level(item.get("level").unwrap_or(&Value::Null), DEFAULT_ALERT_LEVEL)}));
    }
    json!({"auto_stop_s": auto.clamp(AUTO_STOP_MIN_S, AUTO_STOP_MAX_S), "buttons": buttons,
        "require_signin": raw.get("require_signin") == Some(&Value::Bool(true)),
        "messages": clean_messages(raw.get("messages").unwrap_or(&Value::Null)),
        "replies": clean_replies(raw.get("replies").unwrap_or(&Value::Null))})
}

/// The settings from admin, refused with a sentence when something is wrong; a button without an id gets one.
pub fn validate(raw: &Value) -> Result<Value, String> {
    let Some(raw) = raw.as_object() else { return Err("alert settings must be an object".into()) };
    let auto = raw.get("auto_stop_s").cloned().unwrap_or(json!(AUTO_STOP_DEFAULT_S));
    if !strict_int(&auto).is_some_and(|n| (AUTO_STOP_MIN_S..=AUTO_STOP_MAX_S).contains(&n)) {
        return Err(format!("auto-stop must be {AUTO_STOP_MIN_S} to {AUTO_STOP_MAX_S} seconds"));
    }
    let require_signin = match raw.get("require_signin") {
        None => false,
        Some(Value::Bool(b)) => *b,
        Some(_) => return Err("require sign-in must be true or false".into()),
    };
    let Some(items) = raw.get("buttons").and_then(Value::as_array) else { return Err("buttons must be a list".into()) };
    if items.len() > MAX_BUTTONS {
        return Err(format!("at most {MAX_BUTTONS} alert buttons"));
    }
    let mut buttons = Vec::new();
    let mut names: Vec<String> = Vec::new();
    let mut ids: Vec<String> = Vec::new();
    for item in items {
        let Some(item) = item.as_object() else { return Err("each button must be an object".into()) };
        let name = clean_name(item.get("name").unwrap_or(&Value::Null));
        if name.is_empty() {
            return Err("every button needs a name".into());
        }
        if names.contains(&lower(&name)) {
            return Err(format!("two buttons are called {name}"));
        }
        names.push(lower(&name));
        let bid = match item.get("id") {
            Some(Value::String(s)) if !s.is_empty() => s.clone(),
            _ => token_hex(4),
        };
        let bid = if is_button_id(&bid) && !ids.contains(&bid) { bid } else { token_hex(4) };
        ids.push(bid.clone());
        let check = |key: &str, what: &str| -> Result<Vec<Value>, String> {
            match item.get(key) {
                None | Some(Value::Null) => Ok(vec![]),
                Some(Value::Array(a)) => Ok(a.clone()),
                Some(Value::Bool(false)) => Ok(vec![]),
                Some(_) => Err(format!("{name}: {what}")),
            }
        };
        let panels_raw = check("panels", "panels must be panel ids")?;
        if !panels_raw.iter().all(|p| p.as_str().is_some_and(is_panel_id)) {
            return Err(format!("{name}: panels must be panel ids"));
        }
        let screens_raw = check("screens", "screens must be names")?;
        if !screens_raw.iter().all(Value::is_string) {
            return Err(format!("{name}: screens must be names"));
        }
        let screens: Vec<String> = screens_raw.iter().map(clean_name).filter(|s| !s.is_empty()).collect();
        let panels = unique(panels_raw.iter().filter_map(Value::as_str).map(str::to_owned).collect());
        let screens = unique(screens);
        let stage = match item.get("stage") {
            None => false,
            Some(Value::Bool(b)) => *b,
            Some(_) => return Err(format!("{name}: stage must be true or false")),
        };
        if panels.is_empty() && screens.is_empty() && !stage {
            return Err(format!("{name} flashes nothing; pick a panel or a screen"));
        }
        if panels.len() + screens.len() > MAX_TARGETS {
            return Err(format!("{name}: at most {MAX_TARGETS} panels and screens"));
        }
        let icon = match item.get("icon") {
            None => DEFAULT_ICON,
            Some(v) => match v.as_str().and_then(|s| ICONS.iter().find(|i| **i == s)) {
                Some(i) => i,
                None => return Err(format!("{name}: the icon must be one of {}", ICONS.join(", "))),
            },
        };
        let level = checked_level(item, &name, DEFAULT_ALERT_LEVEL)?;
        buttons.push(json!({"id": bid, "name": name, "icon": icon, "panels": panels, "screens": screens, "stage": stage, "level": level}));
    }
    Ok(json!({"auto_stop_s": auto, "buttons": buttons, "require_signin": require_signin,
        "messages": validate_messages(raw.get("messages").unwrap_or(&Value::Null))?,
        "replies": validate_replies(raw.get("replies").unwrap_or(&Value::Null))?}))
}
