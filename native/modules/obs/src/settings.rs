//! The OBS settings (`service.py`'s `clean_*`): up to four computers, each with its address, captions, outputs to follow Start with, alert buttons, tally
//! scenes and watched audio. `passwords` is one sealed string holding `{computer id: password}` as JSON, because config.json seals only top-level string
//! fields. An existing `config.json` loads unchanged.

use rand::RngCore;
use relay_core::languages;
use relay_core::text::squeeze;
use serde_json::{json, Map, Value};

pub const DEFAULT_PORT: u16 = 4455;
pub const MAX_MACHINES: usize = 4;
pub const MAX_TALLY: usize = 16;
pub const MAX_AUDIO: usize = 8;
pub const CAPTION_DELAY_MAX: f64 = 30.0;
pub const SOURCE_LANGUAGE: &str = "source";

/// Whitespace made one space, cut to `limit` characters; `""` when it is not text.
pub fn text(value: &Value, limit: usize) -> String {
    value.as_str().map(|s| squeeze(s).chars().take(limit).collect()).unwrap_or_default()
}

/// An OBS scene or input name as OBS spells it (spaces kept), or `""` when it is not usable.
pub fn name(value: &Value, limit: usize) -> String {
    let Some(s) = value.as_str() else { return String::new() };
    let s: String = s.chars().filter(|c| !c.is_control()).collect();
    if s.trim().is_empty() || s.chars().count() > limit {
        return String::new();
    }
    s
}

fn is_id(s: &str) -> bool {
    (1..=8).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_digit() || b.is_ascii_lowercase())
}

fn is_key(s: &str) -> bool {
    s.len() == 32 && s.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

fn is_button(s: &str) -> bool {
    (1..=64).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// What may stand for a host: letters, digits, dots, hyphens and colons, starting and ending with a letter or digit.
pub fn is_host(s: &str) -> bool {
    let b = s.as_bytes();
    let edge = |c: u8| c.is_ascii_alphanumeric();
    let ok = |c: u8| c.is_ascii_alphanumeric() || c == b'.' || c == b'-' || c == b':';
    match b.len() {
        0 => false,
        1 => edge(b[0]),
        n if n <= 253 => edge(b[0]) && edge(b[n - 1]) && b.iter().all(|c| ok(*c)),
        _ => false,
    }
}

/// A new computer id: eight lowercase hex digits.
pub fn new_id() -> String {
    uuid::Uuid::new_v4().simple().to_string()[..8].to_owned()
}

/// A computer's scene-screen key: 128 random bits as 32 hex digits.
pub fn new_key() -> String {
    let mut bytes = [0u8; 16];
    rand::thread_rng().fill_bytes(&mut bytes);
    bytes.iter().map(|b| format!("{b:02x}")).collect()
}

/// The `passwords` setting as `{id: password}`: a JSON string (as stored) or an object.
pub fn passwords(raw: &Value) -> Map<String, Value> {
    let parsed;
    let d = match raw {
        Value::Object(o) => o,
        Value::String(s) if !s.is_empty() => match serde_json::from_str::<Value>(s) {
            Ok(Value::Object(o)) => {
                parsed = o;
                &parsed
            }
            _ => return Map::new(),
        },
        _ => return Map::new(),
    };
    d.iter().filter(|(k, v)| is_id(k) && v.as_str().is_some_and(|s| !s.is_empty())).map(|(k, v)| (k.clone(), json!(v.as_str().unwrap_or("").chars().take(200).collect::<String>()))).collect()
}

fn tally(raw: &Value) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    let mut seen: Vec<i64> = Vec::new();
    for t in raw.as_array().into_iter().flatten().take(MAX_TALLY) {
        let (n, scene) = (&t["n"], name(&t["scene"], 100));
        let Some(n) = n.as_i64().filter(|n| (1..=999).contains(n)) else { continue };
        if seen.contains(&n) || scene.is_empty() {
            continue;
        }
        seen.push(n);
        out.push(json!({"n": n, "scene": scene}));
    }
    out
}

fn audio(raw: &Value) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for a in raw.as_array().into_iter().flatten().take(MAX_AUDIO) {
        let n = name(&a["name"], 100);
        if n.is_empty() || out.iter().any(|o| o["name"] == json!(n)) {
            continue;
        }
        out.push(json!({"name": n, "warn_muted": a["warn_muted"] == Value::Bool(true)}));
    }
    out
}

/// A float as a JSON number, shown as Python shows it (`0.0`, `2.5`).
fn tenths(f: f64) -> Value {
    json!((f * 10.0).round() / 10.0)
}

pub fn clean_machine(m: &Value, seen: &mut Vec<String>) -> Option<Value> {
    let m = m.as_object()?;
    let mut id = m.get("id").and_then(Value::as_str).filter(|s| is_id(s)).unwrap_or("").to_owned();
    if id.is_empty() || seen.contains(&id) {
        id = new_id();
    }
    seen.push(id.clone());
    let host = m.get("host").and_then(Value::as_str).map(str::trim).filter(|h| is_host(h)).unwrap_or("").to_owned();
    let port = m.get("port").and_then(Value::as_i64).filter(|p| (1..=65535).contains(p)).unwrap_or(i64::from(DEFAULT_PORT));
    let lang = m.get("caption_lang").and_then(Value::as_str).unwrap_or(SOURCE_LANGUAGE);
    let lang = if lang == SOURCE_LANGUAGE || languages::is_target(lang) { lang } else { SOURCE_LANGUAGE };
    let delay = m.get("caption_delay").and_then(Value::as_f64).filter(|d| (0.0..=CAPTION_DELAY_MAX).contains(d)).map_or(0.0, |d| (d * 10.0).round() / 10.0);
    let key = m.get("key").and_then(Value::as_str).filter(|k| is_key(k)).unwrap_or("").to_owned();
    let button = |k: &str| m.get(k).and_then(Value::as_str).filter(|v| is_button(v)).unwrap_or("").to_owned();
    let flag = |k: &str| m.get(k) == Some(&Value::Bool(true));
    let label = text(m.get("name").unwrap_or(&Value::Null), 24);
    Some(json!({
        "id": id, "name": if label.is_empty() { "OBS".to_owned() } else { label }, "host": host, "port": port,
        "captions": flag("captions"), "caption_lang": lang, "caption_delay": if delay == 0.0 { json!(0.0) } else { tenths(delay) },
        "start_record": flag("start_record"), "start_stream": flag("start_stream"),
        "preview": flag("preview"), "alert_error": button("alert_error"), "alert_warn": button("alert_warn"),
        "tally": tally(m.get("tally").unwrap_or(&Value::Null)), "audio": audio(m.get("audio").unwrap_or(&Value::Null)),
        "screen_on": !key.is_empty() && flag("screen_on"), "key": key,
    }))
}

pub fn default_settings() -> Value {
    json!({"machines": [], "passwords": ""})
}

pub fn clean_settings(raw: &Value) -> Value {
    let mut seen: Vec<String> = Vec::new();
    let machines: Vec<Value> = raw["machines"].as_array().into_iter().flatten().take(MAX_MACHINES).filter_map(|m| clean_machine(m, &mut seen)).collect();
    let pw: Map<String, Value> = passwords(&raw["passwords"]).into_iter().filter(|(k, _)| seen.contains(k)).collect();
    let sealed = if pw.is_empty() { String::new() } else { sorted_json(&pw) };
    json!({"machines": machines, "passwords": sealed})
}

/// The passwords as JSON with keys sorted, as Python's `json.dumps(sort_keys=True)` wrote them (so a save does not churn the file).
fn sorted_json(pw: &Map<String, Value>) -> String {
    let mut keys: Vec<&String> = pw.keys().collect();
    keys.sort();
    let parts: Vec<String> = keys.iter().map(|k| format!("{}: {}", json!(k), pw[*k])).collect();
    format!("{{{}}}", parts.join(", "))
}

/// What a config read may show: no passwords, and a key reduced to whether there is one.
pub fn public(settings: &Value) -> Value {
    let mut s = clean_settings(settings);
    let pw = passwords(&s["passwords"]);
    if let Some(o) = s.as_object_mut() {
        o.remove("passwords");
    }
    for m in s["machines"].as_array_mut().into_iter().flatten() {
        let id = m["id"].as_str().unwrap_or("").to_owned();
        let has_key = m["key"].as_str().is_some_and(|k| !k.is_empty());
        if let Some(o) = m.as_object_mut() {
            o.remove("key");
            o.insert("has_password".to_owned(), json!(pw.contains_key(&id)));
            o.insert("has_key".to_owned(), json!(has_key));
        }
    }
    s
}

pub const CAPTION_LINE: usize = 32;

/// `text` split at spaces into lines of at most `width` characters; a longer word is cut.
pub fn caption_lines(text: &str, width: usize) -> Vec<String> {
    let mut out: Vec<String> = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        let mut word: Vec<char> = word.chars().collect();
        while word.len() > width {
            if !line.is_empty() {
                out.push(std::mem::take(&mut line));
            }
            out.push(word.drain(..width).collect());
        }
        if word.is_empty() {
            continue;
        }
        let word: String = word.into_iter().collect();
        if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > width {
            out.push(std::mem::replace(&mut line, word));
        } else if line.is_empty() {
            line = word;
        } else {
            line = format!("{line} {word}");
        }
    }
    if !line.is_empty() {
        out.push(line);
    }
    out
}
