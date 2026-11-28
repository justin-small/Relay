//! Match Planning Center plan items to ProPresenter presentations (#138, the Planning Center link; `plmatch.py`).
//!
//! Pure functions, no network and no state, so the rules are easy to test and to read. `pclink` feeds them what it read from both systems.
//!
//! The order of trust, per plan item:
//!
//! 1. `mapping`: an operator-set pairing of a plan item's title to a presentation, for items whose presentation is named differently
//!    ("Message" -> "Sermon: Grace"). The operator's word beats every guess.
//! 2. `synced`: ProPresenter's own playlist synced from Planning Center, in its order. Each plan item takes the next playlist entry (from where the last
//!    one matched) whose name fits. A PCO entry that was never linked to a presentation is a placeholder: reported, never guessed.
//! 3. exact: the same title once both are normalised (case, accents, punctuation, "(Live)", key and arrangement suffixes).
//! 4. fuzzy: the closest candidate at or above `threshold`, only when it is clearly ahead of the runner-up. A tie is reported as ambiguous, not picked.
//!
//! Headers have nothing to match and are left out ("skipped").

use std::collections::BTreeMap;

use relay_core::text::strip_accents;
use serde_json::{json, Value};

pub const DEFAULT_THRESHOLD: f64 = 0.8;
/// The best fuzzy candidate must beat the runner-up (another presentation) by this much.
pub const MARGIN: f64 = 0.03;

fn text_of(v: &Value) -> String {
    match v {
        Value::String(t) => t.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

/// `[a-g](?:#|b|♯|♭)?m?` over text whose punctuation is already gone.
fn is_key(s: &str) -> bool {
    let b = s.as_bytes();
    if b.is_empty() || !(b'a'..=b'g').contains(&b[0]) {
        return false;
    }
    let mut i = 1;
    if i < b.len() && b[i] == b'b' {
        i += 1;
    }
    if i < b.len() && b[i] == b'm' {
        i += 1;
    }
    i == b.len()
}

/// A tail after a dash that says which version of the song, not which song: a key, "live", an arrangement.
fn version_tail(t: &str) -> bool {
    if is_key(t) || matches!(t, "live" | "reprise" | "acoustic" | "radio" | "radio edit" | "radio version") {
        return true;
    }
    if let Some(rest) = t.strip_prefix("key of ").or_else(|| t.strip_prefix("key ")) {
        if is_key(rest) {
            return true;
        }
    }
    let rest = ["default ", "alt ", "alternate ", "short ", "long ", "full ", "original "].iter().find_map(|p| t.strip_prefix(p)).unwrap_or(t);
    let named = |r: &str| ["arr", "arrangement", "version", "edit"].iter().any(|w| r == *w || r.strip_prefix(w).is_some_and(|x| x.starts_with(' ')));
    named(rest) || named(t)
}

/// Every run of characters outside `0-9a-z` made one space.
fn nonword(s: &str) -> String {
    let mut out = String::new();
    let mut gap = false;
    for c in s.chars() {
        if c.is_ascii_digit() || c.is_ascii_lowercase() {
            out.push(c);
            gap = false;
        } else if !gap {
            out.push(' ');
            gap = true;
        }
    }
    out
}

/// `\([^)]*\)|\[[^\]]*\]` made a space.
fn without_parens(s: &str) -> String {
    let chars: Vec<char> = s.chars().collect();
    let (mut out, mut i) = (String::new(), 0);
    while i < chars.len() {
        let close = match chars[i] {
            '(' => Some(')'),
            '[' => Some(']'),
            _ => None,
        };
        match close.and_then(|c| chars[i + 1..].iter().position(|x| *x == c)) {
            Some(n) => {
                out.push(' ');
                i += n + 2;
            }
            None => {
                out.push(chars[i]);
                i += 1;
            }
        }
    }
    out
}

/// A title reduced to what makes it the same song: lower case without accents or punctuation, no "(Live)", no key ("- G", "in G", "(Key of G)") and no
/// arrangement or version suffix.
pub fn normalise(title: &str) -> String {
    let text = strip_accents(title).to_lowercase().replace('&', " and ");
    let text = without_parens(&text);
    let text: String = text.chars().filter(|c| !matches!(c, '\'' | '\u{2018}' | '\u{2019}' | '`' | '\u{b4}')).collect();
    // split at `\s[-–—]\s`
    let padded: Vec<char> = format!(" {} ", text.trim()).chars().collect();
    let mut parts: Vec<String> = Vec::new();
    let (mut cur, mut i) = (String::new(), 0);
    while i < padded.len() {
        if i + 2 < padded.len() && padded[i].is_whitespace() && matches!(padded[i + 1], '-' | '\u{2013}' | '\u{2014}') && padded[i + 2].is_whitespace() {
            parts.push(std::mem::take(&mut cur));
            i += 3;
            continue;
        }
        cur.push(padded[i]);
        i += 1;
    }
    parts.push(cur);
    let mut parts: Vec<String> = parts.iter().map(|p| p.trim().to_owned()).collect();
    while parts.len() > 1 && version_tail(nonword(parts.last().map_or("", String::as_str)).trim()) {
        parts.pop();
    }
    let joined = parts.join(" ");
    let mut words: Vec<String> = nonword(&joined).split_whitespace().map(str::to_owned).collect();
    // `\s+(?:in|key of)\s+KEY$`
    let n = words.len();
    if n >= 3 && is_key(&words[n - 1]) && words[n - 2] == "in" {
        words.truncate(n - 2);
    } else if n >= 4 && is_key(&words[n - 1]) && words[n - 2] == "of" && words[n - 3] == "key" {
        words.truncate(n - 3);
    }
    if words.len() > 1 && words.last().is_some_and(|w| w == "live") {
        words.pop();
    }
    words.join(" ")
}

/// difflib's `SequenceMatcher(None, a, b).ratio()`: twice the characters in the matching blocks over both lengths.
pub fn ratio(a: &str, b: &str) -> f64 {
    let (a, b): (Vec<char>, Vec<char>) = (a.chars().collect(), b.chars().collect());
    let total = a.len() + b.len();
    if total == 0 {
        return 1.0;
    }
    let mut b2j: BTreeMap<char, Vec<usize>> = BTreeMap::new();
    for (j, c) in b.iter().enumerate() {
        b2j.entry(*c).or_default().push(j);
    }
    fn longest(a: &[char], b2j: &BTreeMap<char, Vec<usize>>, (alo, ahi, blo, bhi): (usize, usize, usize, usize)) -> (usize, usize, usize) {
        let (mut besti, mut bestj, mut bestsize) = (alo, blo, 0);
        let mut j2len: BTreeMap<usize, usize> = BTreeMap::new();
        for (i, c) in a.iter().enumerate().take(ahi).skip(alo) {
            let mut newj2len: BTreeMap<usize, usize> = BTreeMap::new();
            for &j in b2j.get(c).map_or(&[][..], Vec::as_slice) {
                if j < blo {
                    continue;
                }
                if j >= bhi {
                    break;
                }
                let k = j.checked_sub(1).and_then(|p| j2len.get(&p)).copied().unwrap_or(0) + 1;
                newj2len.insert(j, k);
                if k > bestsize {
                    (besti, bestj, bestsize) = (i + 1 - k, j + 1 - k, k);
                }
            }
            j2len = newj2len;
        }
        (besti, bestj, bestsize)
    }
    let mut matched = 0;
    let mut queue = vec![(0, a.len(), 0, b.len())];
    while let Some(range) = queue.pop() {
        let (alo, ahi, blo, bhi) = range;
        let (i, j, k) = longest(&a, &b2j, range);
        if k > 0 {
            matched += k;
            if alo < i && blo < j {
                queue.push((alo, i, blo, j));
            }
            if i + k < ahi && j + k < bhi {
                queue.push((i + k, ahi, j + k, bhi));
            }
        }
    }
    2.0 * matched as f64 / total as f64
}

/// 0 to 1: the better of the character match and the word overlap of two normalised titles.
pub fn similarity(a: &str, b: &str) -> f64 {
    if a.is_empty() || b.is_empty() {
        return 0.0;
    }
    if a == b {
        return 1.0;
    }
    let (ta, tb): (std::collections::BTreeSet<&str>, std::collections::BTreeSet<&str>) = (a.split_whitespace().collect(), b.split_whitespace().collect());
    let dice = if ta.is_empty() || tb.is_empty() { 0.0 } else { 2.0 * ta.intersection(&tb).count() as f64 / (ta.len() + tb.len()) as f64 };
    ratio(a, b).max(dice)
}

fn pres(uuid: &Value, name: &Value) -> Value {
    json!({"uuid": text_of(uuid), "name": text_of(name)})
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

fn row(item: &Value, status: &str, method: &str, score: f64, presentation: Option<Value>, note: &str, options: Vec<Value>) -> Value {
    let kind = text_of(&item["type"]);
    json!({
        "item_id": text_of(&item["id"]), "title": text_of(&item["title"]), "type": if kind.is_empty() { "item".to_owned() } else { kind }, "status": status,
        "method": method, "score": round3(score), "presentation": presentation, "note": note, "options": options,
    })
}

fn fits(norm_title: &str, norm_name: &str, threshold: f64) -> bool {
    !norm_title.is_empty() && (norm_title == norm_name || similarity(norm_title, norm_name) >= threshold)
}

/// The operator's pairings as `{normalised plan item title: {uuid, name}}`. Anything malformed is dropped.
pub fn clean_mapping(raw: &Value) -> BTreeMap<String, Value> {
    let mut out = BTreeMap::new();
    for m in raw.as_array().into_iter().flatten().filter(|m| m.is_object()) {
        let key = normalise(&text_of(&m["item"]));
        let uuid = text_of(&m["uuid"]).trim().to_owned();
        if !key.is_empty() && !uuid.is_empty() {
            out.insert(key, pres(&json!(uuid), &m["name"]));
        }
    }
    out
}

/// One row per plan item, in plan order.
///
/// `items`: `[{id, title, type}]`. `candidates`: presentations ProPresenter has, best source first `[{uuid, name}]`. `mapping`: the operator's list
/// `[{item, uuid, name}]`. `synced`: the entries of ProPresenter's Planning-Center-synced playlist, in order `[{name, type: "presentation"|"placeholder",
/// uuid}]`. A row's status is matched, unmatched, ambiguous, placeholder or skipped.
pub fn match_items(items: &[Value], candidates: &[Value], mapping: &Value, synced: Option<&[Value]>, threshold: f64) -> Vec<Value> {
    let maps = clean_mapping(mapping);
    let pool: Vec<(&Value, String)> = candidates.iter().filter(|c| !text_of(&c["uuid"]).is_empty()).map(|c| (c, normalise(&text_of(&c["name"])))).collect();
    let entries: Vec<(&Value, String)> = synced.unwrap_or(&[]).iter().filter(|s| matches!(s["type"].as_str(), Some("presentation" | "placeholder"))).map(|s| (s, normalise(&text_of(&s["name"])))).collect();
    let mut pointer = 0;
    let mut rows = Vec::new();
    for item in items {
        if item["type"] == "header" {
            rows.push(row(item, "skipped", "", 0.0, None, "A header has no presentation.", vec![]));
            continue;
        }
        let nt = normalise(&text_of(&item["title"]));
        // 1. the operator's pairing
        if let Some(m) = maps.get(&nt) {
            rows.push(row(item, "matched", "mapping", 1.0, Some(m.clone()), "Set by the operator.", vec![]));
            continue;
        }
        // 2. ProPresenter's playlist synced from Planning Center, walked in order
        if let Some((i, (s, ns))) = entries.iter().enumerate().skip(pointer).find(|(_, (_, ns))| fits(&nt, ns, threshold)) {
            pointer = i + 1;
            if s["type"] == "placeholder" || text_of(&s["uuid"]).is_empty() {
                rows.push(row(item, "placeholder", "synced", 1.0, None, "ProPresenter's synced playlist has this item but it is not linked to a presentation.", vec![]));
            } else {
                rows.push(row(item, "matched", "synced", similarity(&nt, ns), Some(pres(&s["uuid"], &s["name"])), "From ProPresenter's playlist synced from Planning Center.", vec![]));
            }
            continue;
        }
        // 3. the same title once normalised
        let exact: Vec<&Value> = pool.iter().filter(|(_, nc)| !nt.is_empty() && *nc == nt).map(|(c, _)| *c).collect();
        if let Some(first) = exact.first() {
            let same: std::collections::BTreeSet<String> = exact.iter().map(|c| text_of(&c["uuid"])).collect();
            let note = if same.len() == 1 { "Same title.".to_owned() } else { format!("{} presentations share this title; the first was used.", same.len()) };
            rows.push(row(item, "matched", "exact", 1.0, Some(pres(&first["uuid"], &first["name"])), &note, vec![]));
            continue;
        }
        // 4. the closest title, if it is clearly the closest
        let mut best: Vec<(String, f64, &Value)> = Vec::new(); // in first-seen order, the way a dict keeps it
        for (c, nc) in &pool {
            let s = similarity(&nt, nc);
            let uuid = text_of(&c["uuid"]);
            match best.iter_mut().find(|b| b.0 == uuid) {
                Some(b) => {
                    if s > b.1 {
                        b.1 = s;
                        b.2 = c;
                    }
                }
                None => {
                    if s > 0.0 {
                        best.push((uuid, s, c));
                    }
                }
            }
        }
        best.sort_by(|a, b| b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal));
        let option = |(_, s, c): &(String, f64, &Value)| json!({"uuid": c["uuid"], "name": text_of(&c["name"]), "score": round3(*s)});
        let close: Vec<&(String, f64, &Value)> = best.iter().filter(|b| b.1 >= threshold).collect();
        if close.is_empty() {
            let near: Vec<Value> = best.iter().take(3).filter(|b| b.1 >= 0.5).map(option).collect();
            rows.push(row(item, "unmatched", "", 0.0, None, "No presentation has a similar title.", near));
        } else if close.len() > 1 && close[0].1 - close[1].1 < MARGIN {
            rows.push(row(item, "ambiguous", "", close[0].1, None, "More than one presentation fits equally well; pick one.", close.iter().take(4).map(|b| option(b)).collect()));
        } else {
            let (_, s, c) = close[0];
            rows.push(row(item, "matched", "fuzzy", *s, Some(pres(&c["uuid"], &c["name"])), &format!("Similar title ({}%).", (s * 100.0).round() as i64), vec![]));
        }
    }
    rows
}

/// The plan items (ids, plan order) ProPresenter's presentation `uuid` stands for. A presentation the rows do not know by UUID falls back to its
/// normalised name against the plan item titles.
pub fn items_for(rows: &[Value], uuid: &str, name: &str) -> Vec<String> {
    if !uuid.is_empty() {
        let found: Vec<String> = rows.iter().filter(|r| r["status"] == "matched" && r["presentation"].is_object() && r["presentation"]["uuid"] == uuid).map(|r| text_of(&r["item_id"])).collect();
        if !found.is_empty() {
            return found;
        }
    }
    let nn = normalise(name);
    if nn.is_empty() {
        return Vec::new();
    }
    rows.iter().filter(|r| matches!(r["status"].as_str(), Some("unmatched" | "ambiguous")) && normalise(&text_of(&r["title"])) == nn).map(|r| text_of(&r["item_id"])).collect()
}
