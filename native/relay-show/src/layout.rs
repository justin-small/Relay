//! The shared layout schema (#421): `app/layout.py`. A grid of items, each with a place on the grid and a look. Pure functions, so a
//! look and a place are checked the same way for boards and every other surface, and nothing a browser sends reaches a page as CSS.

use serde_json::{json, Map, Value};

pub const MAX_CELLS: i64 = 24;
pub const SHAPES: [&str; 4] = ["rounded", "rect", "pill", "circle"];
/// The icons the renderer can draw (`layout-render.js` has the same list); `""` is none.
pub const ICONS: [&str; 12] = ["", "light", "mic", "cam", "star", "music", "cc", "clock", "arrow", "bell", "play", "stop"];
pub const LABEL_MAX: usize = 60;

pub fn default_style() -> Value {
    json!({"label": "", "shape": "rounded", "bg": "#18181b", "fg": "#fafafa", "pressed": "#3f3f46", "icon": ""})
}

/// Plain text on one line: whitespace squeezed, cut to `limit`, `""` for anything that is not a string.
pub fn text(value: &Value, limit: usize) -> String {
    value.as_str().map(|s| s.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(limit).collect()).unwrap_or_default()
}

pub fn text_str(value: &str, limit: usize) -> String {
    text(&Value::String(value.to_owned()), limit)
}

fn color(value: Option<&Value>, default: &str) -> String {
    match value.and_then(Value::as_str) {
        Some(s) if s.len() == 7 && s.starts_with('#') && s[1..].bytes().all(|b| b.is_ascii_hexdigit()) => s.to_lowercase(),
        _ => default.to_owned(),
    }
}

/// `int()` of a number clamped to `lo..=hi`; `default` for anything that is not a finite number (a bool is not).
pub fn number(value: &Value, lo: i64, hi: i64, default: i64) -> i64 {
    match value {
        Value::Number(n) => match n.as_i64() {
            Some(i) => i.clamp(lo, hi),
            None => n.as_f64().filter(|f| f.is_finite()).map_or(default, |f| (f.trunc() as i64).clamp(lo, hi)),
        },
        _ => default,
    }
}

/// A look that is safe to put in a style attribute. `defaults` fills what `raw` lacks or gets wrong. The older lighting names
/// (`color`, `text_color`) are still read, so a page saved before #421 keeps its colours.
pub fn clean_style(raw: &Value, defaults: Option<&Value>) -> Value {
    let mut d = default_style();
    if let (Some(base), Some(over)) = (d.as_object_mut(), defaults.and_then(Value::as_object)) {
        base.extend(over.clone());
    }
    let empty = Map::new();
    let r = raw.as_object().unwrap_or(&empty);
    let pick = |key: &str, set: &[&str]| -> Value {
        match r.get(key).and_then(Value::as_str) {
            Some(s) if set.contains(&s) => json!(s),
            _ => d[key].clone(),
        }
    };
    json!({
        "label": text(r.get("label").unwrap_or(&Value::Null), LABEL_MAX),
        "shape": pick("shape", &SHAPES),
        "bg": color(r.get("bg").or_else(|| r.get("color")), d["bg"].as_str().unwrap_or("")),
        "fg": color(r.get("fg").or_else(|| r.get("text_color")), d["fg"].as_str().unwrap_or("")),
        "pressed": color(r.get("pressed"), d["pressed"].as_str().unwrap_or("")),
        "icon": pick("icon", &ICONS),
    })
}

pub fn clean_grid(cols: &Value, rows: &Value, default: (i64, i64)) -> (i64, i64) {
    (number(cols, 1, MAX_CELLS, default.0), number(rows, 1, MAX_CELLS, default.1))
}

fn free(taken: &std::collections::HashSet<(i64, i64)>, x: i64, y: i64, w: i64, h: i64) -> bool {
    (x..x + w).all(|cx| (y..y + h).all(|cy| !taken.contains(&(cx, cy))))
}

fn first_free(taken: &std::collections::HashSet<(i64, i64)>, cols: i64, rows: i64, w: i64, h: i64) -> Option<(i64, i64)> {
    (0..=rows - h).flat_map(|y| (0..=cols - w).map(move |x| (x, y))).find(|(x, y)| free(taken, *x, *y, w, h))
}

/// Each item with whole-cell `x`, `y`, `w`, `h` inside the grid and over no other item. An item that does not fit where it asks is
/// moved to the first free spot of its size, then of one cell; with no room left it is dropped. Other keys are kept as they are.
pub fn place(items: Vec<Value>, cols: i64, rows: i64) -> Vec<Value> {
    let mut out = Vec::new();
    let mut taken = std::collections::HashSet::new();
    for it in items {
        let Some(obj) = it.as_object() else { continue };
        let mut w = number(obj.get("w").unwrap_or(&Value::Null), 1, cols, 1);
        let mut h = number(obj.get("h").unwrap_or(&Value::Null), 1, rows, 1);
        let mut x = number(obj.get("x").unwrap_or(&Value::Null), 0, cols - w, 0);
        let mut y = number(obj.get("y").unwrap_or(&Value::Null), 0, rows - h, 0);
        if !free(&taken, x, y, w, h) {
            let spot = first_free(&taken, cols, rows, w, h).or_else(|| {
                w = 1;
                h = 1;
                first_free(&taken, cols, rows, 1, 1)
            });
            let Some((sx, sy)) = spot else { continue };
            x = sx;
            y = sy;
        }
        for cx in x..x + w {
            for cy in y..y + h {
                taken.insert((cx, cy));
            }
        }
        let mut item = obj.clone();
        item.insert("x".into(), json!(x));
        item.insert("y".into(), json!(y));
        item.insert("w".into(), json!(w));
        item.insert("h".into(), json!(h));
        out.push(Value::Object(item));
    }
    out
}
