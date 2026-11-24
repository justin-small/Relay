//! Boards (#156): `app/boards.py`. Screens an operator builds from widgets, each at its own stable address, `/board/<slug>`.
//!
//! A board is one or more pages, each a grid ([`crate::layout`]) of widgets laid out with the shared editor. It is kept in config.json
//! under `"boards"`, so a backup carries it. Which page a screen shows is decided here ([`current`]), so every screen showing a board
//! agrees; a rotation turns from the clock.
//!
//! A widget's settings are checked against the fields its definition declares, so a browser can only choose among the values offered.
//! The core widgets' definitions are here ([`core_widgets`]); a module's are passed in. A widget whose definition is not known (its
//! module is off, or the module is not ported yet) keeps its settings, bounded to plain values.
//!
//! Where Python offers a list of choices that come from outside this crate (the audio sources modules registered, the channels a route
//! can join), the field is marked `"open"`: any plain string is kept, because the list is not known here and dropping a saved choice
//! would lose it.

use serde_json::{json, Map, Value};

use crate::alerts::token_hex;
use crate::layout::{self, clean_grid, clean_style, number, text};
use crate::schedules;
use crate::timewin::{self, Date};

pub const MAX_BOARDS: usize = 50;
pub const MAX_ITEMS: usize = 100;
pub const NAME_MAX: usize = 60;
pub const TEXT_MAX: usize = 200;
pub const MAX_PAGES: usize = 12;
pub const PAGE_NAME_MAX: usize = 40;
pub const PLACE_MAX: usize = 40;
pub const MAX_RULES: usize = 30;
pub const RULE_VALUE_MAX: usize = 80;
pub const MAX_SCREENS: usize = 50;
pub const SCREEN_NAME_MAX: usize = 40;
pub const EVERY_MIN: i64 = 5;
pub const EVERY_MAX: i64 = 3600;
pub const RATIOS: [(&str, (i64, i64)); 3] = [("16:9", (12, 8)), ("4:3", (8, 6)), ("9:16", (6, 10))];
pub const KINDS: [&str; 12] = ["button", "captions", "clock", "countdown", "text", "status", "lines", "list", "spl", "rta", "frame", "fader"];
pub const FIELD_TYPES: [&str; 8] = ["text", "area", "select", "check", "time", "number", "note", "ro"];
pub const SOURCE: &str = "SOURCE";
pub const MODES: [&str; 4] = ["one", "rotate", "schedule", "follow"];
pub const ROTATE: &str = "rotate";
pub const KEEP: &str = "keep";
pub const DAYS: [(&str, &str); 10] = [("all", "Every day"), ("weekdays", "Weekdays"), ("weekends", "Weekends"), ("mon", "Monday"), ("tue", "Tuesday"),
    ("wed", "Wednesday"), ("thu", "Thursday"), ("fri", "Friday"), ("sat", "Saturday"), ("sun", "Sunday")];
pub const MATCHES: [(&str, &str); 3] = [("presentation", "Presentation"), ("group", "Slide group"), ("label", "Slide label")];
pub const UPDATABLE: [&str; 8] = ["name", "place", "public", "ratio", "cols", "rows", "pages", "rotation"];

/// A refusal; the text is a fixed sentence fit for the page.
#[derive(Debug, PartialEq, Eq)]
pub struct BoardError(pub String);

fn err<T>(s: &str) -> Result<T, BoardError> {
    Err(BoardError(s.to_owned()))
}

// ------------------------------------------------------------------------------------------------------------------ matchers
fn bytes_ok(s: &str, min: usize, max: usize, f: impl Fn(u8) -> bool) -> bool {
    (min..=max).contains(&s.len()) && s.bytes().all(f)
}

fn slug_ok(s: &str) -> bool {
    let b = s.as_bytes();
    let ok = |c: u8| c.is_ascii_lowercase() || c.is_ascii_digit();
    !b.is_empty() && b.len() <= 40 && ok(b[0]) && ok(b[b.len() - 1]) && b.iter().all(|c| ok(*c) || *c == b'-')
}

fn item_id_ok(s: &str) -> bool {
    bytes_ok(s, 1, 40, |c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}

fn ident_ok(s: &str) -> bool {
    let b = s.as_bytes();
    !b.is_empty() && b.len() <= 32 && b[0].is_ascii_lowercase() && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || *c == b'_')
}

fn type_ok(s: &str) -> bool {
    match s.split_once('.') {
        Some((a, b)) => ident_ok(a) && ident_ok(b),
        None => ident_ok(s),
    }
}

fn page_id_ok(s: &str) -> bool {
    bytes_ok(s, 1, 12, |c| c.is_ascii_lowercase() || c.is_ascii_digit())
}

pub fn screen_id_ok(s: &str) -> bool {
    bytes_ok(s, 8, 40, |c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
}

fn place_ref_ok(s: &str) -> bool {
    s.strip_prefix("place:relay:").is_some_and(|n| bytes_ok(n, 1, 9, |c| c.is_ascii_digit()))
}

fn value_str(v: &Value) -> Option<&str> {
    v.as_str()
}

// ------------------------------------------------------------------------------------------------------------------ widgets
/// What the core widgets' choices are drawn from: the caption languages follow the configuration.
#[derive(Default, Clone)]
pub struct Choices {
    /// The configured source language's label ("English"); `""` reads as "Original".
    pub source_label: String,
    /// Where a sound widget can read besides Relay's first meter: `[id, name]`.
    pub sound_sources: Vec<(String, String)>,
}

fn field(key: &str, label: &str, kind: &str, options: Value, default: Value) -> Value {
    json!({"key": key, "label": label, "type": kind, "options": options, "default": default})
}

fn open_field(mut f: Value) -> Value {
    f["open"] = json!(true);
    f
}

fn languages_options(c: &Choices) -> Value {
    let label = if c.source_label.is_empty() { "Original" } else { &c.source_label };
    let mut out = vec![json!([SOURCE, format!("{label} (original speech)")])];
    out.extend(relay_core::languages::TARGETS.iter().map(|t| json!([t.name, t.label])));
    Value::Array(out)
}

fn sound_options(c: &Choices) -> Value {
    let mut out = vec![json!(["", "Relay's first meter"])];
    out.extend(c.sound_sources.iter().map(|(k, v)| json!([k, v])));
    Value::Array(out)
}

fn note(label: &str) -> Value {
    json!({"key": "_", "label": label, "type": "note"})
}

/// Core's widget definitions (raw: `clean_definition` checks them), asked for each time since the languages follow the configuration.
pub fn core_widgets(c: &Choices) -> Vec<Value> {
    let lines: Value = (1..=6).map(|n| json!([n.to_string(), n.to_string()])).collect();
    vec![
        json!({"type": "captions", "name": "Captions", "group": "Captions", "kind": "captions", "w": 8, "h": 3, "style": {"icon": "cc"},
            "fields": [field("lang", "Language", "select", languages_options(c), json!(SOURCE)), field("lines", "Lines shown", "select", lines, json!("3"))]}),
        json!({"type": "clock", "name": "Clock", "group": "Clock and text", "kind": "clock", "w": 3, "h": 2, "style": {"icon": "clock"},
            "fields": [field("h24", "24-hour clock", "check", json!([]), json!(false))]}),
        json!({"type": "countdown", "name": "Countdown", "group": "Clock and text", "kind": "countdown", "w": 3, "h": 2, "style": {"icon": "clock", "label": "Starts in"},
            "fields": [field("at", "Counts down to (time of day)", "time", json!([]), json!("10:30"))]}),
        json!({"type": "text", "name": "Text", "group": "Clock and text", "kind": "text", "w": 4, "h": 1,
            "fields": [json!({"key": "text", "label": "Text", "type": "area", "max": TEXT_MAX, "default": "Welcome"})]}),
        json!({"type": "link", "name": "Board link", "group": "Clock and text", "kind": "button", "w": 2, "h": 1, "style": {"icon": "arrow", "bg": "#1e3a8a", "pressed": "#3b5bdb"},
            "fields": [field("board", "Opens board", "select", json!([]), json!(""))]}),
        json!({"type": "route", "name": "Route", "group": "Routing", "kind": "button", "w": 3, "h": 1, "style": {"icon": "arrow", "bg": "#14532d", "pressed": "#16a34a"},
            "press_permission": "network.manage",
            "fields": [open_field(field("destination", "Destination (what takes the signal)", "select", json!([]), json!(""))),
                open_field(field("source", "Source (what it takes)", "select", json!([]), json!(""))),
                note("A press routes the source to the destination, as on the NDI and Blackmagic pages, and is not retried. Pressing needs someone signed in who may manage routes.")]}),
        json!({"type": "stream", "name": "Livestream", "group": "Status", "kind": "status", "w": 3, "h": 2, "style": {"icon": "cam"},
            "fields": [note("On air when any stream Relay watches is live (OBS, YouTube...).")]}),
        json!({"type": "spl", "name": "Sound level", "group": "Sound", "kind": "spl", "w": 3, "h": 2, "style": {"icon": "mic"},
            "fields": [field("weighting", "Weighting", "select", json!([["A", "Sound level, A-weighted"], ["C", "Sound level, C-weighted"]]), json!("A")),
                field("response", "Response", "select", json!([["slow", "Slow (1 s)"], ["fast", "Fast (125 ms)"], ["leq", "Average, last minute (A)"]]), json!("slow")),
                open_field(field("source", "Read from", "select", sound_options(c), json!("")))]}),
        json!({"type": "rta", "name": "Spectrum", "group": "Sound", "kind": "rta", "w": 6, "h": 3, "style": {"icon": "mic"},
            "fields": [field("view", "Shown as", "select", json!([["bars", "Room spectrum (RTA)"], ["waterfall", "Spectrogram"]]), json!("bars")),
                open_field(field("source", "Read from", "select", sound_options(c), json!("")))]}),
    ]
}

/// A widget definition as the editor and the checks use it, or `None` when it is not usable. A module's types must start with `owner.`.
pub fn clean_definition(raw: &Value, owner: &str) -> Option<Value> {
    let raw = raw.as_object()?;
    let wtype = raw.get("type")?.as_str().filter(|t| type_ok(t))?;
    if !owner.is_empty() && !wtype.starts_with(&format!("{owner}.")) {
        return None;
    }
    if owner.is_empty() && wtype.contains('.') {
        return None;
    }
    let kind = raw.get("kind").and_then(Value::as_str).filter(|k| KINDS.contains(k)).unwrap_or("text");
    let mut fields = Vec::new();
    for f in raw.get("fields").and_then(Value::as_array).into_iter().flatten() {
        let Some(f) = f.as_object() else { continue };
        let ftype = f.get("type").and_then(Value::as_str).filter(|t| FIELD_TYPES.contains(t));
        let key = f.get("key").and_then(Value::as_str).filter(|k| *k == "_" || (ident_ok(k) && !k.contains('.')));
        let (Some(ftype), Some(key)) = (ftype, key) else { continue };
        let opts: Vec<Value> = f
            .get("options")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|o| {
                let o = o.as_array().filter(|o| o.len() == 2 && o[0].is_string())?;
                Some(json!([text(&o[0], 80), text(&o[1], 80)]))
            })
            .take(200)
            .collect();
        let mut def = json!({"key": key, "label": text(f.get("label").unwrap_or(&Value::Null), 120), "type": ftype, "options": opts,
            "max": number(f.get("max").unwrap_or(&Value::Null), 1, TEXT_MAX as i64, TEXT_MAX as i64), "default": f.get("default").cloned().unwrap_or(Value::Null)});
        if f.get("open") == Some(&Value::Bool(true)) {
            def["open"] = json!(true);
        }
        fields.push(def);
    }
    let presets: Vec<Value> = raw
        .get("presets")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|p| {
            let p = p.as_object()?;
            let name = text(p.get("name").unwrap_or(&Value::Null), 60);
            (!name.is_empty()).then(|| json!({"name": name, "props": p.get("props").filter(|v| v.is_object()).cloned().unwrap_or(json!({})),
                "style": p.get("style").filter(|v| v.is_object()).cloned().unwrap_or(json!({}))}))
        })
        .take(200)
        .collect();
    let name = text(raw.get("name").unwrap_or(&Value::Null), 60);
    Some(json!({"type": wtype, "name": if name.is_empty() { wtype.to_owned() } else { name }, "group": text(raw.get("group").unwrap_or(&Value::Null), 40), "kind": kind,
        "w": number(raw.get("w").unwrap_or(&Value::Null), 1, layout::MAX_CELLS, 2), "h": number(raw.get("h").unwrap_or(&Value::Null), 1, layout::MAX_CELLS, 1),
        "style": clean_style(raw.get("style").unwrap_or(&Value::Null), None), "fields": fields, "presets": presets,
        "press_permission": raw.get("press_permission").and_then(Value::as_str).unwrap_or(""), "module": owner, "off_note": text(raw.get("off_note").unwrap_or(&Value::Null), 80)}))
}

/// Every widget type that can be placed now, by type: core's, then the switched-on modules' (already cleaned). The board link offers
/// the other boards.
pub fn catalogue(choices: &Choices, module_defs: &[Value], boards: &[Value]) -> Map<String, Value> {
    let mut out = Map::new();
    for raw in core_widgets(choices) {
        if let Some(mut d) = clean_definition(&raw, "") {
            if d["type"] == "link" {
                d["fields"][0]["options"] = Value::Array(boards.iter().map(|b| json!([b["id"], b["name"]])).collect());
            }
            out.insert(d["type"].as_str().unwrap_or("").to_owned(), d);
        }
    }
    for d in module_defs {
        let t = d["type"].as_str().unwrap_or("").to_owned();
        if !t.is_empty() && !out.contains_key(&t) {
            out.insert(t, d.clone());
        }
    }
    out
}

/// What an item is called when it has no label of its own: the preset it matches, else the chosen option of its first list setting, else
/// the widget's name.
pub fn item_title(definition: &Value, props: &Value) -> String {
    for p in definition["presets"].as_array().into_iter().flatten() {
        let pp = p["props"].as_object();
        if pp.is_some_and(|m| !m.is_empty() && m.iter().all(|(k, v)| props.get(k) == Some(v))) {
            return p["name"].as_str().unwrap_or("").to_owned();
        }
    }
    let name = definition["name"].as_str().unwrap_or("").to_owned();
    for f in definition["fields"].as_array().into_iter().flatten() {
        match f["type"].as_str() {
            Some("select") => {
                let want = &props[f["key"].as_str().unwrap_or("")];
                let label = f["options"].as_array().into_iter().flatten().find(|o| &o[0] == want).and_then(|o| o[1].as_str()).unwrap_or("");
                return if label.is_empty() { name } else { label.to_owned() };
            }
            Some("note") => {}
            _ => break,
        }
    }
    name
}

// ------------------------------------------------------------------------------------------------------------------ cleaning
/// A setting kept for a widget whose definition is not known: plain values only.
fn plain(v: &Value) -> Value {
    match v {
        Value::Bool(_) | Value::Null => v.clone(),
        Value::Number(n) => n.as_f64().filter(|f| f.is_finite()).map_or(Value::Null, |f| if let Some(i) = n.as_i64().filter(|_| f.abs() <= 1e9) { json!(i) } else { json!(f.clamp(-1e9, 1e9)) }),
        Value::String(s) => json!(s.chars().take(TEXT_MAX).collect::<String>()),
        _ => Value::Null,
    }
}

pub fn clean_props(raw: &Value, definition: Option<&Value>) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let Some(definition) = definition else {
        return Value::Object(raw.iter().take(20).filter(|(k, _)| ident_ok(k) && !k.contains('.')).map(|(k, v)| (k.clone(), plain(v))).collect());
    };
    let mut out = Map::new();
    for f in definition["fields"].as_array().into_iter().flatten() {
        let key = f["key"].as_str().unwrap_or("");
        let kind = f["type"].as_str().unwrap_or("");
        if kind == "note" || key == "_" {
            continue;
        }
        let value = raw.get(key).cloned().unwrap_or_else(|| f["default"].clone());
        let value = match kind {
            "select" => {
                let allowed: Vec<&Value> = f["options"].as_array().into_iter().flatten().map(|o| &o[0]).collect();
                let open = f["open"] == true;
                if value.is_string() && (allowed.contains(&&value) || (open && value.as_str().is_some_and(|s| s.chars().count() <= 80))) {
                    value
                } else if allowed.contains(&&f["default"]) {
                    f["default"].clone()
                } else {
                    allowed.first().map_or(json!(""), |a| (*a).clone())
                }
            }
            "check" => json!(value == true),
            "time" => match timewin::clean_clock(&value, true) {
                Some(_) => value,
                None => if timewin::clean_clock(&f["default"], true).is_some() { f["default"].clone() } else { json!("") },
            },
            "number" => json!(number(&value, -1_000_000, 1_000_000, f["default"].as_i64().unwrap_or(0))),
            "area" => {
                let max = f["max"].as_u64().unwrap_or(TEXT_MAX as u64) as usize;
                let s: String = value.as_str().map(|s| s.chars().take(max).collect::<String>().trim().to_owned()).unwrap_or_default();
                let joined = s.lines().map(|l| l.split_whitespace().collect::<Vec<_>>().join(" ")).collect::<Vec<_>>().join("\n");
                json!(joined.chars().take(max).collect::<String>())
            }
            _ => json!(text(&value, f["max"].as_u64().unwrap_or(TEXT_MAX as u64) as usize)),
        };
        out.insert(key.to_owned(), value);
    }
    Value::Object(out)
}

pub fn clean_item(raw: &Value, cat: Option<&Map<String, Value>>) -> Option<Value> {
    let raw = raw.as_object()?;
    let wtype = raw.get("type")?.as_str().filter(|t| type_ok(t))?;
    let definition = cat.and_then(|c| c.get(wtype));
    if cat.is_some() && definition.is_none() && !wtype.contains('.') {
        return None; // an unknown core type: nothing could ever draw it
    }
    let iid = raw.get("id").and_then(Value::as_str).filter(|i| item_id_ok(i)).map_or_else(|| token_hex(5), str::to_owned);
    let n = |k: &str| raw.get(k).cloned().unwrap_or(Value::Null);
    Some(json!({"id": iid, "type": wtype, "x": n("x"), "y": n("y"), "w": n("w"), "h": n("h"),
        "style": clean_style(&n("style"), definition.map(|d| &d["style"])), "props": clean_props(&n("props"), definition)}))
}

fn clean_items(raw: &Value, cat: Option<&Map<String, Value>>, cols: i64, rows: i64, seen: &mut Vec<String>) -> Vec<Value> {
    let mut items = Vec::new();
    for it in raw.as_array().map(Vec::as_slice).unwrap_or(&[]).iter().take(MAX_ITEMS) {
        let Some(mut c) = clean_item(it, cat) else { continue };
        if seen.contains(&c["id"].as_str().unwrap_or("").to_owned()) {
            c["id"] = json!(token_hex(5));
        }
        seen.push(c["id"].as_str().unwrap_or("").to_owned());
        items.push(c);
    }
    layout::place(items, cols, rows)
}

/// A board's pages: at least one, at most [`MAX_PAGES`], each `{"id", "name", "items"}`. A board saved before pages had only `items`:
/// they become page 1. Widget ids are unique across the whole board, so a press finds one.
pub fn clean_pages(raw_pages: &Value, raw_items: &Value, cat: Option<&Map<String, Value>>, cols: i64, rows: i64) -> Vec<Value> {
    let pages_in: Vec<Value> = match raw_pages.as_array() {
        Some(a) if !a.is_empty() => a.clone(),
        _ => vec![json!({"id": "p1", "name": "Page 1", "items": raw_items})],
    };
    let (mut out, mut ids, mut seen) = (Vec::new(), Vec::<String>::new(), Vec::<String>::new());
    for (n, p) in pages_in.iter().take(MAX_PAGES).enumerate() {
        let n = n + 1;
        let empty = Map::new();
        let p = p.as_object().unwrap_or(&empty);
        let mut pid = p.get("id").and_then(Value::as_str).filter(|i| page_id_ok(i) && !ids.iter().any(|x| x == i)).unwrap_or("").to_owned();
        let mut k = n;
        while pid.is_empty() || ids.contains(&pid) {
            pid = format!("p{k}");
            k += 1;
        }
        ids.push(pid.clone());
        let name = text(p.get("name").unwrap_or(&Value::Null), PAGE_NAME_MAX);
        out.push(json!({"id": pid, "name": if name.is_empty() { format!("Page {n}") } else { name },
            "items": clean_items(p.get("items").unwrap_or(&Value::Null), cat, cols, rows, &mut seen)}));
    }
    out
}

fn hhmm(v: &Value, default: &str) -> String {
    timewin::clean_clock(v, true).unwrap_or_else(|| default.to_owned())
}

/// What decides the page a board shows: one page, a rotation, a schedule by day and time, or following another program. A page that no
/// longer exists falls back to the first.
pub fn clean_rotation(raw: &Value, page_ids: &[String]) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let first = page_ids[0].clone();
    let show = |v: Option<&Value>, extra: &[&str]| -> String {
        v.and_then(Value::as_str).filter(|s| page_ids.iter().any(|p| p == s) || extra.contains(s)).map_or_else(|| first.clone(), str::to_owned)
    };
    let mut rot_pages: Vec<String> = Vec::new();
    for p in raw.get("pages").and_then(Value::as_array).into_iter().flatten().filter_map(Value::as_str) {
        if page_ids.iter().any(|i| i == p) && !rot_pages.iter().any(|r| r == p) {
            rot_pages.push(p.to_owned());
        }
    }
    let mut schedule = Vec::new();
    for r in raw.get("schedule").and_then(Value::as_array).into_iter().flatten().take(MAX_RULES) {
        let Some(r) = r.as_object() else { continue };
        let days = r.get("days").and_then(Value::as_str).filter(|d| DAYS.iter().any(|(k, _)| k == d)).unwrap_or("all");
        let named = r.get("schedule").and_then(Value::as_str).filter(|s| schedules::ref_id(s).is_some()).unwrap_or("");
        schedule.push(json!({"days": days, "from": hhmm(r.get("from").unwrap_or(&Value::Null), "09:00"), "to": hhmm(r.get("to").unwrap_or(&Value::Null), "10:00"),
            "schedule": named, "show": show(r.get("show"), &[ROTATE])}));
    }
    let follow = raw.get("follow").and_then(Value::as_object).unwrap_or(&empty);
    let mut rules = Vec::new();
    for r in follow.get("rules").and_then(Value::as_array).into_iter().flatten().take(MAX_RULES) {
        let Some(r) = r.as_object() else { continue };
        let value = text(r.get("value").unwrap_or(&Value::Null), RULE_VALUE_MAX);
        if value.is_empty() {
            continue;
        }
        let m = r.get("match").and_then(Value::as_str).filter(|m| MATCHES.iter().any(|(k, _)| k == m)).unwrap_or("presentation");
        rules.push(json!({"match": m, "value": value, "page": show(r.get("page"), &[])}));
    }
    let source = follow.get("source").and_then(Value::as_str).filter(|s| ident_ok(s) && !s.contains('.')).unwrap_or("propresenter");
    json!({"mode": raw.get("mode").and_then(Value::as_str).filter(|m| MODES.contains(m)).unwrap_or("one"),
        "every": number(raw.get("every").unwrap_or(&Value::Null), EVERY_MIN, EVERY_MAX, 20),
        "pages": if rot_pages.is_empty() { page_ids.to_vec() } else { rot_pages },
        "schedule": schedule, "other": show(raw.get("other"), &[ROTATE]),
        "follow": {"source": source, "rules": rules, "none": follow.get("none").and_then(Value::as_str).filter(|n| page_ids.iter().any(|p| p == n)).unwrap_or(KEEP)}})
}

fn clean_screen_names(raw: &Value) -> Value {
    let mut out = Map::new();
    for (k, v) in raw.as_object().into_iter().flatten() {
        let name = text(v, SCREEN_NAME_MAX);
        if screen_id_ok(k) && !name.is_empty() && out.len() < MAX_SCREENS {
            out.insert(k.clone(), json!(name));
        }
    }
    Value::Object(out)
}

fn ratio_cells(ratio: &str) -> (i64, i64) {
    RATIOS.iter().find(|(r, _)| *r == ratio).map_or((12, 8), |(_, c)| *c)
}

/// One board, bounded. With `cat` each widget's settings are checked against its definition and a widget of an unknown core type is
/// dropped; without it (config load) settings are only bounded.
pub fn clean_board(raw: &Value, cat: Option<&Map<String, Value>>, board_id: Option<&str>) -> Value {
    let empty = Map::new();
    let raw = raw.as_object().unwrap_or(&empty);
    let null = Value::Null;
    let bid = board_id.map(str::to_owned).or_else(|| raw.get("id").and_then(Value::as_str).map(str::to_owned)).unwrap_or_default();
    let ratio = raw.get("ratio").and_then(Value::as_str).filter(|r| RATIOS.iter().any(|(k, _)| k == r)).unwrap_or("16:9");
    let (cols, rows) = clean_grid(raw.get("cols").unwrap_or(&null), raw.get("rows").unwrap_or(&null), ratio_cells(ratio));
    let pages = clean_pages(raw.get("pages").unwrap_or(&null), raw.get("items").unwrap_or(&null), cat, cols, rows);
    let page_ids: Vec<String> = pages.iter().filter_map(|p| p["id"].as_str().map(str::to_owned)).collect();
    let name = text(raw.get("name").unwrap_or(&null), NAME_MAX);
    json!({"id": if slug_ok(&bid) { bid } else { String::new() }, "name": if name.is_empty() { "Board".to_owned() } else { name },
        "place": text(raw.get("place").unwrap_or(&null), PLACE_MAX),
        "place_ref": raw.get("place_ref").and_then(Value::as_str).filter(|p| place_ref_ok(p)).unwrap_or(""),
        "public": raw.get("public") == Some(&Value::Bool(true)), "ratio": ratio, "cols": cols, "rows": rows, "pages": pages,
        "rotation": clean_rotation(raw.get("rotation").unwrap_or(&null), &page_ids), "screen_names": clean_screen_names(raw.get("screen_names").unwrap_or(&null))})
}

pub fn all_items(board: &Value) -> Vec<&Value> {
    board["pages"].as_array().into_iter().flatten().flat_map(|p| p["items"].as_array().into_iter().flatten()).collect()
}

pub fn find_item<'a>(board: &'a Value, item_id: &str) -> Option<&'a Value> {
    all_items(board).into_iter().find(|i| i["id"] == item_id)
}

/// The stored boards (config load): bounded, with unique addresses; a board with no usable address is dropped.
pub fn clean_list(raw: &Value) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for b in raw.as_array().map(Vec::as_slice).unwrap_or(&[]).iter().take(MAX_BOARDS) {
        let c = clean_board(b, None, None);
        if !c["id"].as_str().unwrap_or("").is_empty() && !out.iter().any(|o| o["id"] == c["id"]) {
            out.push(c);
        }
    }
    out
}

pub fn find<'a>(boards: &'a [Value], bid: &str) -> Option<&'a Value> {
    boards.iter().find(|b| b["id"] == bid)
}

// ------------------------------------------------------------------------------------------------------------------ which page shows now
fn day_ok(days: &str, weekday: i64) -> bool {
    days == "all" || (days == "weekdays" && (1..=5).contains(&weekday)) || (days == "weekends" && (weekday == 0 || weekday == 6))
        || timewin::DAY_KEYS.iter().position(|k| *k == days).is_some_and(|p| p as i64 == weekday)
}

/// Whether the named schedule `ref` is open at a wall-clock `(date, minute)`.
pub type OpenRef<'a> = &'a dyn Fn(&str, Date, i64) -> bool;

/// The index of the first schedule row that covers `now` (local time), else `None`. A row whose end is before its start runs past
/// midnight; then the day it started on is the one checked. A row that names a schedule covers `now` while `is_open` says that schedule
/// is open (a schedule that is gone never covers it).
pub fn schedule_row(board: &Value, date: Date, minute: i64, is_open: Option<OpenRef>) -> Option<usize> {
    for (i, r) in board["rotation"]["schedule"].as_array().into_iter().flatten().enumerate() {
        if let Some(named) = r["schedule"].as_str().filter(|s| !s.is_empty()) {
            if is_open.is_some_and(|f| f(named, date, minute)) {
                return Some(i);
            }
            continue;
        }
        let days = r["days"].as_str().unwrap_or("all").to_owned();
        let (from, to) = (timewin::minutes(r["from"].as_str().unwrap_or("09:00")), timewin::minutes(r["to"].as_str().unwrap_or("10:00")));
        if timewin::window_start(date, minute, from, to, |d| day_ok(&days, d.weekday())).is_some() {
            return Some(i);
        }
    }
    None
}

/// What a schedule row or rule shows, in words: a page's number and name (only its name when `short`), or the rotation.
pub fn show_label(board: &Value, show: &str, short: bool) -> String {
    let pages = board["pages"].as_array().cloned().unwrap_or_default();
    if show == ROTATE {
        let rot = &board["rotation"];
        let nums: Vec<String> = pages.iter().enumerate().filter(|(_, p)| rot["pages"].as_array().is_some_and(|r| r.contains(&p["id"]))).map(|(n, _)| (n + 1).to_string()).collect();
        let joined = match nums.len() {
            0 => String::new(),
            1 => nums[0].clone(),
            n => format!("{} and {}", nums[..n - 1].join(", "), nums[n - 1]),
        };
        return format!("Rotate {joined}, every {} s", rot["every"]);
    }
    let label = |n: usize, p: &Value| if short { p["name"].as_str().unwrap_or("").to_owned() } else { format!("{n} · {}", p["name"].as_str().unwrap_or("")) };
    pages.iter().enumerate().find(|(_, p)| p["id"] == show).map(|(n, p)| label(n + 1, p)).unwrap_or_else(|| label(1, &pages[0]))
}

fn rotate_of(board: &Value) -> Value {
    let rot = &board["rotation"];
    let ids: Vec<&Value> = board["pages"].as_array().into_iter().flatten().map(|p| &p["id"]).collect();
    let pages: Vec<Value> = rot["pages"].as_array().into_iter().flatten().filter(|p| ids.contains(p)).cloned().collect();
    json!({"pages": if pages.is_empty() { vec![board["pages"][0]["id"].clone()] } else { pages }, "every": rot["every"]})
}

/// Which page the board shows now: `{"page", "rotate", "key", "why"}`. A rotation is turned by each screen from the clock, so every
/// screen showing the board turns together. `follow` is what the follow source shows now (`{"presentation", "group", "label"}`), `last`
/// the page shown before (kept when nothing matches and the board is set to keep it).
pub fn current(board: &Value, date: Date, minute: i64, follow: Option<&Value>, last: Option<&str>, is_open: Option<OpenRef>) -> Value {
    let rot = &board["rotation"];
    let first = board["pages"][0]["id"].as_str().unwrap_or("").to_owned();
    let ids: Vec<String> = board["pages"].as_array().into_iter().flatten().filter_map(|p| p["id"].as_str().map(str::to_owned)).collect();
    let out = |show: &str, key: &str, why: String| -> Value {
        if show == ROTATE {
            let r = rotate_of(board);
            return json!({"page": r["pages"][0], "rotate": r, "key": key, "why": why});
        }
        json!({"page": if ids.iter().any(|i| i == show) { show } else { &first }, "rotate": null, "key": key, "why": why})
    };
    let kept = || last.filter(|l| ids.iter().any(|i| i == l)).unwrap_or(&first).to_owned();
    let s = |v: &Value| v.as_str().unwrap_or("").to_owned();
    match rot["mode"].as_str().unwrap_or("one") {
        "rotate" => out(ROTATE, "rotate", show_label(board, ROTATE, false)),
        "schedule" => {
            if let Some(i) = schedule_row(board, date, minute, is_open) {
                let row = &rot["schedule"][i];
                if row["schedule"].as_str().is_some_and(|x| !x.is_empty()) {
                    let then = show_label(board, &s(&rot["other"]), true);
                    return out(&s(&row["show"]), &format!("s{i}"), format!("Schedule: {} while the named schedule is open, then {then}", show_label(board, &s(&row["show"]), true)));
                }
                let to = timewin::minutes(row["to"].as_str().unwrap_or("10:00"));
                let nxt = schedule_row(board, date, to, is_open);
                let then = show_label(board, &nxt.map_or_else(|| s(&rot["other"]), |n| s(&rot["schedule"][n]["show"])), true);
                return out(&s(&row["show"]), &format!("s{i}"), format!("Schedule: {} until {}, then {then}", show_label(board, &s(&row["show"]), true), s(&row["to"])));
            }
            out(&s(&rot["other"]), "other", format!("Schedule: {} outside the times set", show_label(board, &s(&rot["other"]), true)))
        }
        "follow" => {
            let Some(now_pp) = follow.filter(|f| f.is_object()) else { return out(&kept(), "follow-off", "Following ProPresenter, which is not connected".into()) };
            for (i, r) in rot["follow"]["rules"].as_array().into_iter().flatten().enumerate() {
                let seen = value_str(&now_pp[r["match"].as_str().unwrap_or("")]).unwrap_or("").trim().to_lowercase();
                if !seen.is_empty() && seen == s(&r["value"]).trim().to_lowercase() {
                    let label = MATCHES.iter().find(|(k, _)| *k == r["match"].as_str().unwrap_or("")).map_or("", |(_, l)| *l).to_lowercase();
                    return out(&s(&r["page"]), &format!("f{i}"), format!("Following ProPresenter: {label} {} shows {}", s(&r["value"]), show_label(board, &s(&r["page"]), true)));
                }
            }
            let none = s(&rot["follow"]["none"]);
            if none == KEEP {
                return out(&kept(), "keep", "Following ProPresenter: nothing matches, so the page stays".into());
            }
            out(&none, "none", format!("Following ProPresenter: nothing matches, so {} shows", show_label(board, &none, true)))
        }
        _ => out(&first, "one", format!("Always {}", show_label(board, &first, true))),
    }
}

// ------------------------------------------------------------------------------------------------------------------ templates
fn widget(cat: &Map<String, Value>, wtype: &str, (x, y, w, h): (i64, i64, i64, i64), props: Option<&Value>, style: Option<&Value>) -> Option<Value> {
    let d = cat.get(wtype)?;
    let mut base = Map::new();
    for f in d["fields"].as_array().into_iter().flatten() {
        if f["type"] != "note" && !f["default"].is_null() {
            base.insert(f["key"].as_str().unwrap_or("").to_owned(), f["default"].clone());
        }
    }
    base.extend(props.and_then(Value::as_object).cloned().unwrap_or_default());
    let mut st = d["style"].as_object().cloned().unwrap_or_default();
    st.extend(style.and_then(Value::as_object).cloned().unwrap_or_default());
    Some(json!({"type": wtype, "x": x, "y": y, "w": w, "h": h, "props": base, "style": st}))
}

/// Starting layouts for a new board, built from the widgets there are now: `{"id", "name", "about", "ratio", "items"}`.
pub fn templates(cat: &Map<String, Value>) -> Vec<Value> {
    let buttons: Vec<(&Value, Value)> = cat
        .values()
        .filter(|d| d["module"].as_str().is_some_and(|m| !m.is_empty()) && d["kind"] == "button")
        .flat_map(|d| {
            let presets = d["presets"].as_array().cloned().unwrap_or_default();
            if presets.is_empty() { vec![(d, json!({}))] } else { presets.into_iter().map(|p| (d, p)).collect() }
        })
        .take(4)
        .collect();
    let mut panel: Vec<Option<Value>> = buttons.iter().enumerate().map(|(n, (d, p))| widget(cat, d["type"].as_str().unwrap_or(""), (2 * n as i64, 0, 2, 2), p.get("props"), p.get("style"))).collect();
    if panel.is_empty() {
        panel = vec![widget(cat, "text", (0, 0, 8, 2), Some(&json!({"text": "Add buttons from the widget list"})), None)];
    }
    panel.push(widget(cat, "clock", (0, 4, 8, 2), None, None));
    let w = |t: &str, pos: (i64, i64, i64, i64), props: Option<Value>, style: Option<Value>| widget(cat, t, pos, props.as_ref(), style.as_ref());
    type Template<'a> = (&'a str, &'a str, &'a str, &'a str, Vec<Option<Value>>);
    let raw: Vec<Template> = vec![
        ("lobby", "Lobby welcome", "Welcome, clock, countdown, captions, livestream", "16:9", vec![w("text", (0, 0, 8, 2), Some(json!({"text": "Welcome"})), None), w("clock", (8, 0, 4, 2), None, None),
            w("stream", (8, 2, 4, 2), None, None), w("captions", (0, 2, 8, 6), None, None), w("countdown", (8, 4, 4, 4), None, None)]),
        ("stage", "Stage confidence", "Clock, time left, captions", "16:9", vec![w("clock", (0, 0, 6, 2), None, None), w("countdown", (6, 0, 6, 2), None, Some(json!({"label": "Time left"}))),
            w("captions", (0, 2, 12, 6), Some(json!({"lines": "4"})), None)]),
        ("captions", "Captions only", "Full-screen, one language", "16:9", vec![w("captions", (0, 0, 12, 8), Some(json!({"lines": "4"})), None)]),
        ("buttons", "Button panel", "Cues and links, tablet", "4:3", panel),
        ("sound", "Sound check", "Level, spectrum, clock", "16:9", vec![w("spl", (0, 0, 6, 4), None, None), w("clock", (6, 0, 6, 4), None, None), w("rta", (0, 4, 12, 4), None, None)]),
        ("blank", "Blank", "Empty grid", "16:9", vec![]),
    ];
    raw.into_iter().map(|(t, n, a, r, items)| json!({"id": t, "name": n, "about": a, "ratio": r, "items": items.into_iter().flatten().collect::<Vec<_>>()})).collect()
}

// ------------------------------------------------------------------------------------------------------------------ changes
/// A new board's address from its name: lowercase words joined by hyphens, numbered when already taken.
pub fn slug_for(name: &str, taken: &[String]) -> String {
    let mut base = String::new();
    let mut gap = true;
    for c in name.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            base.push(c);
            gap = false;
        } else if !gap {
            base.push('-');
            gap = true;
        }
    }
    let base: String = base.trim_matches('-').chars().take(32).collect::<String>().trim_matches('-').to_owned();
    let base = if base.is_empty() { "board".to_owned() } else { base };
    let (mut out, mut n) = (base.clone(), 2);
    while taken.contains(&out) {
        out = format!("{base}-{n}");
        n += 1;
    }
    out
}

/// A new board from `{name, ratio, place, public, template?, pages? or items?}`, with a fresh address.
pub fn create(boards: &[Value], payload: &Value, cat: &Map<String, Value>) -> Result<Value, BoardError> {
    if boards.len() >= MAX_BOARDS {
        return err(&format!("Relay keeps at most {MAX_BOARDS} boards."));
    }
    let mut payload = payload.as_object().cloned().unwrap_or_default();
    let name = text(payload.get("name").unwrap_or(&Value::Null), NAME_MAX);
    if name.is_empty() {
        return err("Give the board a name.");
    }
    let valid_ratio = |p: &Map<String, Value>| p.get("ratio").and_then(Value::as_str).filter(|r| RATIOS.iter().any(|(k, _)| k == r)).map(str::to_owned);
    let tpl = templates(cat).into_iter().find(|t| payload.get("template").is_some_and(|id| &t["id"] == id));
    if let Some(t) = &tpl {
        let ratio = valid_ratio(&payload).unwrap_or_else(|| t["ratio"].as_str().unwrap_or("16:9").to_owned());
        payload.insert("ratio".into(), json!(ratio));
        payload.insert("pages".into(), json!([{"id": "p1", "name": "Page 1", "items": t["items"]}]));
    }
    let ratio = valid_ratio(&payload).unwrap_or_else(|| "16:9".to_owned());
    if !payload.contains_key("cols") || tpl.is_some() {
        let (c, r) = ratio_cells(&ratio);
        payload.insert("cols".into(), json!(c));
        payload.insert("rows".into(), json!(r));
    }
    payload.remove("screen_names");
    payload.remove("place_ref");
    payload.insert("name".into(), json!(name));
    payload.insert("ratio".into(), json!(ratio));
    let taken: Vec<String> = boards.iter().filter_map(|b| b["id"].as_str().map(str::to_owned)).collect();
    Ok(clean_board(&Value::Object(payload), Some(cat), Some(&slug_for(&name, &taken))))
}

/// The board with what `payload` changes (name, place, public, ratio, cols, rows, pages, rotation; `items` alone replaces page 1's); its
/// address stays.
pub fn update(board: &Value, payload: &Value, cat: &Map<String, Value>) -> Result<Value, BoardError> {
    let payload = payload.as_object().cloned().unwrap_or_default();
    let mut merged = board.as_object().cloned().unwrap_or_default();
    for k in UPDATABLE {
        if let Some(v) = payload.get(k) {
            merged.insert(k.to_owned(), v.clone());
        }
    }
    if let (Some(items), false) = (payload.get("items"), payload.contains_key("pages")) {
        let mut pages = board["pages"].as_array().cloned().unwrap_or_default();
        if let Some(first) = pages.first_mut().and_then(Value::as_object_mut) {
            first.insert("items".into(), items.clone());
        }
        merged.insert("pages".into(), json!(pages));
    }
    if payload.contains_key("name") && text(&payload["name"], NAME_MAX).is_empty() {
        return err("Give the board a name.");
    }
    Ok(clean_board(&Value::Object(merged), Some(cat), board["id"].as_str()))
}

/// The board with a screen's name set (or cleared with `""`).
pub fn name_screen(board: &Value, screen: &str, name: &Value) -> Result<Value, BoardError> {
    if !screen_id_ok(screen) {
        return err("That is not a screen.");
    }
    let mut names: Map<String, Value> = board["screen_names"].as_object().cloned().unwrap_or_default();
    names.remove(screen);
    let n = text(name, SCREEN_NAME_MAX);
    if !n.is_empty() {
        if names.len() >= MAX_SCREENS {
            return err("This board has too many named screens.");
        }
        names.insert(screen.to_owned(), json!(n));
    }
    let mut out = board.clone();
    out["screen_names"] = Value::Object(names);
    Ok(out)
}

pub fn export(board: &Value) -> Value {
    json!({"relay_board": 2, "name": board["name"], "place": board["place"], "public": board["public"], "ratio": board["ratio"], "cols": board["cols"],
        "rows": board["rows"], "pages": board["pages"], "rotation": board["rotation"]})
}

/// A board from an exported file (this version's, or one saved before pages), under a new address. Checked like a save.
pub fn import_board(boards: &[Value], data: &Value, cat: &Map<String, Value>) -> Result<Value, BoardError> {
    if !data.is_object() || !matches!(data["relay_board"].as_i64(), Some(1 | 2)) {
        return err("That is not a Relay board file.");
    }
    let mut d = data.as_object().cloned().unwrap_or_default();
    d.remove("template");
    create(boards, &Value::Object(d), cat)
}
