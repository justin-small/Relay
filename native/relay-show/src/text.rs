//! The one-line cleaners (`app/alerts.py` `clean_name`, `clean_text`, `clean_reply`; `app/panels.py` `_text`; `app/screens.py`
//! `_name`). A name Python wrote reads the same here, so the cleaners follow Python's `str.isprintable` and `str.split`.

use serde_json::Value;

/// Python's `str.isprintable` for one character: not a control, format, separator (other than a space), surrogate,
/// private-use or unassigned character. The format characters that show up in pasted text are listed; the rest of Cf is rare.
pub fn printable(c: char) -> bool {
    if c == ' ' {
        return true;
    }
    if c.is_control() || c.is_whitespace() {
        return false;
    }
    !matches!(c, '\u{00AD}' | '\u{0600}'..='\u{0605}' | '\u{061C}' | '\u{06DD}' | '\u{070F}' | '\u{180E}'
        | '\u{200B}'..='\u{200F}' | '\u{202A}'..='\u{202E}' | '\u{2060}'..='\u{2064}' | '\u{2066}'..='\u{206F}'
        | '\u{FEFF}' | '\u{FFF9}'..='\u{FFFB}' | '\u{E000}'..='\u{F8FF}')
}

/// Printable characters only (anything else becomes a space), one line, at most `limit` characters.
pub fn one_line(value: &str, limit: usize) -> String {
    let replaced: String = value.chars().map(|c| if printable(c) { c } else { ' ' }).collect();
    replaced.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(limit).collect()
}

/// `one_line` of a JSON value, empty for anything but a string.
pub fn one_line_of(value: &Value, limit: usize) -> String {
    value.as_str().map(|s| one_line(s, limit)).unwrap_or_default()
}

/// Panels' `_text`: control characters dropped (not turned into spaces), trimmed, at most `limit` characters.
pub fn bounded(value: &Value, limit: usize) -> String {
    match value.as_str() {
        Some(s) => s.chars().filter(|c| printable(*c)).collect::<String>().trim().chars().take(limit).collect(),
        None => String::new(),
    }
}

/// Python's `int()` of a JSON value: an integer, a float truncated, a bool as 0 or 1, a numeric string; otherwise `None`.
pub fn py_int(value: &Value) -> Option<i64> {
    match value {
        Value::Bool(b) => Some(i64::from(*b)),
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f.trunc() as i64)),
        Value::String(s) => s.trim().parse::<i64>().ok(),
        _ => None,
    }
}

/// A true integer (not a bool): what `isinstance(v, int) and not isinstance(v, bool)` accepts.
pub fn strict_int(value: &Value) -> Option<i64> {
    match value {
        Value::Number(n) => n.as_i64(),
        _ => None,
    }
}

/// Two names are the same name when they match ignoring case (`app/text.py` `same_name`).
pub fn same_name(a: &str, b: &str) -> bool {
    let fold = |s: &str| s.split_whitespace().collect::<Vec<_>>().join(" ").to_lowercase();
    let fa = fold(a);
    !fa.is_empty() && fa == fold(b)
}
