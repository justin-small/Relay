//! The saved settings (`propresenter.py`'s `default_settings` and the `clean_*` functions): anything malformed is dropped rather than refused, so a
//! hand edit cannot stop Relay, and an existing `config.json` loads unchanged.

use relay_core::text::squeeze;
use relay_core::timers::timer_fields;
use serde_json::{json, Map, Value};

pub const OWNED_PREFIX: &str = "Relay \u{2013} ";
pub const MAX_COMPUTERS: usize = 8;
pub const LOBBY_INTERVAL_DEFAULT_S: f64 = 8.0;
pub const LOBBY_INTERVAL_MIN_S: f64 = 3.0;
pub const LOBBY_INTERVAL_MAX_S: f64 = 600.0;
pub const LINK_DWELL_DEFAULT_S: f64 = 10.0;
pub const LINK_DWELL_MIN_S: f64 = 1.0;
pub const LINK_DWELL_MAX_S: f64 = 300.0;
pub const LINK_MAP_MAX: usize = 60;
pub const SETTLE_DEFAULT_S: f64 = 1.5;
pub const SETTLE_MIN_S: f64 = 0.2;
pub const SETTLE_MAX_S: f64 = 30.0;
pub const DEFAULT_MATCH_THRESHOLD: f64 = 0.8;

/// A ProPresenter UUID (or a short name or index): `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`.
pub fn id_ok(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 64 && b[0].is_ascii_alphanumeric() && b.iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'_' | b'-'))
}

/// A service type id in the link: `^[A-Za-z0-9_-]{1,24}$`.
fn link_st_ok(s: &str) -> bool {
    (1..=24).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'_' | b'-'))
}

fn computer_id_ok(s: &str) -> bool {
    (1..=24).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, b'_' | b'-'))
}

pub fn s(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
}

fn num(v: &Value) -> Option<f64> {
    v.as_f64().filter(|_| v.is_number())
}

fn round_to(x: f64, places: i32) -> f64 {
    let k = 10f64.powi(places);
    (x * k).round() / k
}

fn flag(raw: &Value, key: &str) -> Option<bool> {
    raw.get(key).filter(|v| !v.is_null()).map(|v| v == &Value::Bool(true))
}

fn obj(raw: &Value) -> &Map<String, Value> {
    static EMPTY: std::sync::OnceLock<Map<String, Value>> = std::sync::OnceLock::new();
    raw.as_object().unwrap_or_else(|| EMPTY.get_or_init(Map::new))
}

/// `{**base, **top}` for two objects.
fn merged(base: &Value, top: Map<String, Value>) -> Value {
    let mut out = obj(base).clone();
    for (k, v) in top {
        out.insert(k, v);
    }
    Value::Object(out)
}

// ------------------------------------------------------------------------------------------------------------------------ defaults
pub fn default_lobby() -> Value {
    json!({"playlist": "", "interval_s": LOBBY_INTERVAL_DEFAULT_S, "loop": true})
}

pub fn default_signage() -> Value {
    json!({"enabled": false, "playlist": ""})
}

pub fn default_view() -> Value {
    json!({"timer": "", "public": false})
}

pub fn default_link() -> Value {
    json!({"follow": false, "drive": false, "dwell_s": LINK_DWELL_DEFAULT_S, "allow_back": false, "service_type": "",
           "threshold": DEFAULT_MATCH_THRESHOLD, "mapping": [], "owned": {}})
}

pub fn default_settings() -> Value {
    json!({
        "enabled": false, "host": "", "port": 0, "view_only": false,
        "computers": [], "primary": "",
        "songs_library": "Songs", "songs_only": true,
        "lyrics": {"viewer": false, "screen": true, "overlay": false, "panels": true},
        "macros_mode": "selected", "macros_allowed": [],
        "timers": {"amber_s": 120, "red_s": 30, "show": []},
        "tags": {"enabled": false, "settle_s": SETTLE_DEFAULT_S},
        "link": default_link(),
        "lobby": default_lobby(),
        "view": default_view(),
        "signage": default_signage(),
    })
}

// ---------------------------------------------------------------------------------------------------------------------- the checks
/// The lobby settings `raw` sets, each checked, with a fixed sentence for the first thing wrong ("" when nothing was). A key left out stays out.
pub fn lobby_fields(raw: &Value) -> (Map<String, Value>, &'static str) {
    let mut out = Map::new();
    let mut problem = "";
    if let Some(v) = raw.get("playlist").filter(|v| !v.is_null()) {
        let v = text_of(v).trim().to_owned();
        if !v.is_empty() && !id_ok(&v) {
            problem = "That is not a ProPresenter playlist.";
        } else {
            out.insert("playlist".into(), json!(v));
        }
    }
    if let Some(v) = raw.get("interval_s").filter(|v| !v.is_null()) {
        match num(v).filter(|f| (LOBBY_INTERVAL_MIN_S..=LOBBY_INTERVAL_MAX_S).contains(f)) {
            Some(f) => {
                out.insert("interval_s".into(), json!(round_to(f, 1)));
            }
            None => problem = "The time per slide is a number of seconds from 3 to 600.",
        }
    }
    if let Some(b) = flag(raw, "loop") {
        out.insert("loop".into(), json!(b));
    }
    (out, problem)
}

pub fn clean_lobby_settings(raw: &Value, base: &Value) -> Value {
    merged(&merged(&default_lobby(), obj(base).clone()), lobby_fields(raw).0)
}

pub fn signage_fields(raw: &Value) -> (Map<String, Value>, &'static str) {
    let mut out = Map::new();
    let mut problem = "";
    if let Some(b) = flag(raw, "enabled") {
        out.insert("enabled".into(), json!(b));
    }
    if let Some(v) = raw.get("playlist").filter(|v| !v.is_null()) {
        let v = text_of(v).trim().to_owned();
        if !v.is_empty() && !id_ok(&v) {
            problem = "That is not a ProPresenter playlist.";
        } else {
            out.insert("playlist".into(), json!(v));
        }
    }
    (out, problem)
}

pub fn clean_signage_settings(raw: &Value, base: &Value) -> Value {
    merged(&merged(&default_signage(), obj(base).clone()), signage_fields(raw).0)
}

pub fn view_fields(raw: &Value) -> (Map<String, Value>, &'static str) {
    let mut out = Map::new();
    let mut problem = "";
    if let Some(v) = raw.get("timer").filter(|v| !v.is_null()) {
        let v = text_of(v).trim().to_owned();
        if !v.is_empty() && !id_ok(&v) {
            problem = "That is not a ProPresenter timer.";
        } else {
            out.insert("timer".into(), json!(v));
        }
    }
    if let Some(b) = flag(raw, "public") {
        out.insert("public".into(), json!(b));
    }
    (out, problem)
}

pub fn clean_view_settings(raw: &Value, base: &Value) -> Value {
    merged(&merged(&default_view(), obj(base).clone()), view_fields(raw).0)
}

/// What Python's `str(v)` gives for a JSON value a form might send.
fn text_of(v: &Value) -> String {
    match v {
        Value::String(t) => t.clone(),
        Value::Null => "None".into(),
        Value::Bool(true) => "True".into(),
        Value::Bool(false) => "False".into(),
        other => other.to_string(),
    }
}

/// The link settings `raw` sets, each checked. `owned` is not settable here.
pub fn link_fields(raw: &Value) -> (Map<String, Value>, String) {
    let mut out = Map::new();
    let mut problem = String::new();
    for key in ["follow", "drive", "allow_back"] {
        if let Some(b) = flag(raw, key) {
            out.insert(key.into(), json!(b));
        }
    }
    if let Some(v) = raw.get("dwell_s").filter(|v| !v.is_null()) {
        match num(v).filter(|f| (LINK_DWELL_MIN_S..=LINK_DWELL_MAX_S).contains(f)) {
            Some(f) => {
                out.insert("dwell_s".into(), json!(round_to(f, 1)));
            }
            None => problem = "The dwell time is a number of seconds from 1 to 300.".into(),
        }
    }
    if let Some(v) = raw.get("threshold").filter(|v| !v.is_null()) {
        match num(v).filter(|f| (0.5..=1.0).contains(f)) {
            Some(f) => {
                out.insert("threshold".into(), json!(round_to(f, 2)));
            }
            None => {
                if problem.is_empty() {
                    problem = "The match strictness is a number from 0.5 to 1.".into();
                }
            }
        }
    }
    if let Some(v) = raw.get("service_type").filter(|v| !v.is_null()) {
        match v.as_str() {
            Some(t) if t.is_empty() || link_st_ok(t) => {
                out.insert("service_type".into(), json!(t));
            }
            _ => {
                if problem.is_empty() {
                    problem = "That is not a service type.".into();
                }
            }
        }
    }
    if let Some(rows) = raw.get("mapping") {
        match rows.as_array().filter(|a| a.len() <= LINK_MAP_MAX) {
            None => {
                if problem.is_empty() {
                    problem = format!("Pairings are a list of up to {LINK_MAP_MAX}.");
                }
            }
            Some(rows) => {
                let mut mapped = Vec::new();
                for r in rows {
                    let item: String = squeeze(&text_of(r.get("item").unwrap_or(&Value::Null)).replace("None", "")).chars().take(200).collect();
                    let item = if r.is_object() { item } else { String::new() };
                    let uuid = if r.is_object() { s(r, "uuid") } else { String::new() };
                    if item.is_empty() || !id_ok(&uuid) {
                        if problem.is_empty() {
                            problem = "Each pairing needs a plan item title and a ProPresenter presentation.".into();
                        }
                        continue;
                    }
                    let name: String = squeeze(&s(r, "name")).chars().take(200).collect();
                    mapped.push(json!({"item": item, "uuid": uuid, "name": name}));
                }
                out.insert("mapping".into(), Value::Array(mapped));
            }
        }
    }
    (out, problem)
}

fn clean_owned(raw: &Value) -> Value {
    let uuid = s(raw, "uuid");
    let name: String = squeeze(&s(raw, "name")).chars().take(120).collect();
    if id_ok(&uuid) && name.starts_with(OWNED_PREFIX) {
        json!({"uuid": uuid, "name": name})
    } else {
        json!({})
    }
}

pub fn clean_link_settings(raw: &Value, base: &Value) -> Value {
    let mut out = merged(base, link_fields(raw).0);
    out["owned"] = clean_owned(&base["owned"]);
    out
}

/// A bare host name or address; "" when it is anything else (a URL, a path, a space).
pub fn clean_host(value: &Value) -> String {
    let host = match value {
        Value::Null => String::new(),
        v => text_of(v).trim().to_owned(),
    };
    if host_ok(&host) {
        host
    } else {
        String::new()
    }
}

/// `^[A-Za-z0-9]([A-Za-z0-9.-]{0,251}[A-Za-z0-9])?$|^\[?[0-9A-Fa-f:]+\]?$`
fn host_ok(h: &str) -> bool {
    if h.is_empty() {
        return false;
    }
    let b = h.as_bytes();
    let name = b[0].is_ascii_alphanumeric()
        && b.len() <= 253
        && b[b.len() - 1].is_ascii_alphanumeric()
        && b.iter().all(|c| c.is_ascii_alphanumeric() || matches!(c, b'.' | b'-'));
    let v6 = {
        let inner = h.strip_prefix('[').unwrap_or(h);
        let inner = inner.strip_suffix(']').unwrap_or(inner);
        !inner.is_empty() && inner.bytes().all(|c| c.is_ascii_hexdigit() || c == b':')
    };
    name || v6
}

pub fn clean_port(value: &Value) -> i64 {
    let n = match value {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)),
        Value::String(t) => t.trim().parse::<i64>().ok(),
        Value::Bool(b) => Some(i64::from(*b)),
        _ => Some(0),
    };
    match n {
        Some(p) if (1..=65535).contains(&p) => p,
        _ => 0,
    }
}

/// `(computers, primary id)`: bounded, unique ids, one with no host dropped, a computer with no name called "ProPresenter" (then "ProPresenter 2"...).
/// `primary` is kept when it is one of them, else the first computer.
pub fn clean_computers(raw: &Value, primary: &Value) -> (Vec<Value>, String) {
    let mut out: Vec<Value> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for item in raw.as_array().map(Vec::as_slice).unwrap_or_default().iter().take(MAX_COMPUTERS) {
        if !item.is_object() {
            continue;
        }
        let host = clean_host(item.get("host").unwrap_or(&Value::Null));
        if host.is_empty() {
            continue;
        }
        let mut cid = item.get("id").and_then(Value::as_str).filter(|i| computer_id_ok(i)).unwrap_or("").to_owned();
        if cid.is_empty() || seen.contains(&cid) {
            cid = std::iter::once("main".to_owned()).chain((2..=MAX_COMPUTERS + 1).map(|n| format!("c{n}"))).find(|i| !seen.contains(i)).unwrap_or_default();
        }
        seen.push(cid.clone());
        let mut name: String = squeeze(&text_of(item.get("name").filter(|v| !v.is_null()).unwrap_or(&json!("")))).chars().take(40).collect();
        if name.is_empty() {
            name = format!("ProPresenter{}", if out.is_empty() { String::new() } else { format!(" {}", out.len() + 1) });
        }
        out.push(json!({"id": cid, "name": name, "host": host, "port": clean_port(item.get("port").unwrap_or(&Value::Null))}));
    }
    let chosen = match primary.as_str() {
        Some(p) if seen.iter().any(|i| i == p) => p.to_owned(),
        _ => out.first().map(|c| s(c, "id")).unwrap_or_default(),
    };
    (out, chosen)
}

pub fn primary_computer(st: &Value) -> Option<&Value> {
    st["computers"].as_array()?.iter().find(|c| c["id"] == st["primary"])
}

pub fn tag_fields(raw: &Value) -> (Map<String, Value>, &'static str) {
    let mut out = Map::new();
    let mut problem = "";
    if let Some(b) = flag(raw, "enabled") {
        out.insert("enabled".into(), json!(b));
    }
    if let Some(v) = raw.get("settle_s").filter(|v| !v.is_null()) {
        match num(v).filter(|f| (SETTLE_MIN_S..=SETTLE_MAX_S).contains(f)) {
            Some(f) => {
                out.insert("settle_s".into(), json!(round_to(f, 2)));
            }
            None => problem = "The settle time is a number of seconds from 0.2 to 30.",
        }
    }
    (out, problem)
}

pub fn clean_tag_settings(raw: &Value, base: &Value) -> Value {
    merged(base, tag_fields(raw).0)
}

/// `base` with what `raw` sets on top. Red cannot be later than amber, so it is held to it.
pub fn clean_timer_settings(raw: &Value, base: &Value) -> Value {
    let mut out = json!({"amber_s": base["amber_s"], "red_s": base["red_s"], "show": base["show"].as_array().cloned().unwrap_or_default()});
    for (k, v) in timer_fields(raw).0 {
        out[k] = v;
    }
    let (amber, red) = (out["amber_s"].as_i64().unwrap_or(0), out["red_s"].as_i64().unwrap_or(0));
    out["red_s"] = json!(red.min(amber));
    out
}

/// The saved settings, anything malformed dropped rather than refused so a hand edit cannot stop Relay.
pub fn clean_stored(raw: &Value) -> Value {
    let base = default_settings();
    let lyr = &raw["lyrics"];
    let lyr = if lyr.is_object() { lyr } else { &Value::Null };
    let mut listed = raw.get("computers").cloned().unwrap_or(Value::Null);
    let empty = listed.is_null() || listed.as_array().is_some_and(Vec::is_empty) || listed == json!(false) || listed == json!(0) || listed == json!("") || listed == json!({});
    if empty {
        // Before #483 one computer sat in `host` and `port`: it becomes the first, "main".
        listed = json!([{"id": "main", "name": "ProPresenter", "host": raw.get("host"), "port": raw.get("port")}]);
    }
    let (computers, primary) = clean_computers(&listed, raw.get("primary").unwrap_or(&Value::Null));
    let drives = computers.iter().find(|c| c["id"] == json!(primary));
    let lib: String = squeeze(&match raw.get("songs_library") {
        Some(Value::String(t)) if !t.is_empty() => t.clone(),
        Some(v) if !v.is_null() && v != &json!(false) && v != &json!(0) => text_of(v),
        _ => s(&base, "songs_library"),
    })
    .chars()
    .take(64)
    .collect();
    let mut lyrics = Map::new();
    for (k, default) in obj(&base["lyrics"]) {
        lyrics.insert(k.clone(), json!(match lyr.get(k) {
            None => default == &Value::Bool(true),
            Some(v) => truthy(v),
        }));
    }
    let mut allowed: Vec<String> = raw["macros_allowed"].as_array().into_iter().flatten().filter_map(Value::as_str).filter(|u| id_ok(u)).map(|u| u.chars().take(64).collect()).collect();
    allowed.sort();
    allowed.dedup();
    allowed.truncate(500);
    let link_in = &raw["link"];
    let owned = if link_in.is_object() { link_in["owned"].clone() } else { json!({}) };
    let mut link_base = merged(&base["link"], Map::new());
    link_base["owned"] = owned;
    json!({
        "enabled": truthy(raw.get("enabled").unwrap_or(&json!(false))),
        "computers": computers, "primary": primary,
        "host": drives.map_or(json!(""), |d| d["host"].clone()),
        "port": drives.map_or(json!(0), |d| d["port"].clone()),
        "view_only": truthy(raw.get("view_only").unwrap_or(&json!(false))),
        "songs_library": if lib.is_empty() { s(&base, "songs_library") } else { lib },
        "songs_only": truthy(raw.get("songs_only").unwrap_or(&json!(true))),
        "lyrics": lyrics,
        "macros_mode": if raw["macros_mode"] == "all" { "all" } else { "selected" },
        "macros_allowed": allowed,
        "timers": clean_timer_settings(&raw["timers"], &base["timers"]),
        "tags": clean_tag_settings(&raw["tags"], &base["tags"]),
        "lobby": clean_lobby_settings(&raw["lobby"], &base["lobby"]),
        "view": clean_view_settings(&raw["view"], &base["view"]),
        "signage": clean_signage_settings(&raw["signage"], &base["signage"]),
        "link": clean_link_settings(&raw["link"], &link_base),
    })
}

/// Python's `bool(v)`.
pub fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(t) => !t.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// Whether the integration is switched on with an address and port (`propresenter.configured`).
pub fn configured(st: &Value) -> bool {
    truthy(&st["enabled"]) && truthy(&st["host"]) && truthy(&st["port"])
}
