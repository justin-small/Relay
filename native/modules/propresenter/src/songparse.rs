//! Reading a lyric sheet into a song (#633; `ppsongparse.py`): paste or PDF text in, sections of slides out, and the same slides cut again at N lines.
//! Pure functions: the songs page calls them through `/songs/parse` and `/songs/split`, and the operator reviews the result before anything is kept.
//! Chords stay inline as `[G]` in a slide's text; `songs` turns them into the file's chord positions when the song is sent.
//!
//! A pasted sheet is untrusted text, so every step here is a plain walk over the characters: a long line costs no more than its length.

use std::collections::BTreeMap;

use lopdf::content::Content;
use lopdf::{Document, Encoding, Object};
use serde_json::{json, Value};

pub const MAX_TEXT: usize = 200_000;
pub const MAX_PAGES: usize = 30;
const NAMES: [&str; 16] = ["verse", "chorus", "pre-chorus", "prechorus", "pre chorus", "bridge", "tag", "intro", "outro", "interlude", "instrumental", "refrain", "ending", "coda", "turnaround", "vamp"];

/// The text cannot be read. The message is a fixed sentence for the operator.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ParseError {
    pub message: &'static str,
}

fn err<T>(message: &'static str) -> Result<T, ParseError> {
    Err(ParseError { message })
}

fn short(word: &str) -> Option<&'static str> {
    match word {
        "v" => Some("Verse"),
        "c" => Some("Chorus"),
        "b" => Some("Bridge"),
        "pc" => Some("Pre-Chorus"),
        _ => None,
    }
}

/// `[G]` markers: `(char index of the `[`, char index after the `]`, the name)`. A marker is 1 to 20 characters with no bracket or line break inside.
fn inline_chords(chars: &[char]) -> Vec<(usize, usize, String)> {
    let mut out = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '[' {
            let mut j = i + 1;
            while j < chars.len() && !matches!(chars[j], '[' | ']' | '\n') && j - i - 1 <= 20 {
                j += 1;
            }
            if j < chars.len() && chars[j] == ']' && (1..=20).contains(&(j - i - 1)) {
                out.push((i, j + 1, chars[i + 1..j].iter().collect()));
                i = j + 1;
                continue;
            }
        }
        i += 1;
    }
    out
}

/// `I [G]love You` -> (`I love You`, `[(2, "G")]`): the text without markers, and where each chord sits in it.
pub fn split_chords(text: &str) -> (String, Vec<(i64, String)>) {
    let chars: Vec<char> = text.chars().collect();
    let (mut out, mut chords, mut last, mut removed) = (String::new(), Vec::new(), 0usize, 0usize);
    for (start, end, name) in inline_chords(&chars) {
        out.extend(&chars[last..start]);
        chords.push(((start - removed) as i64, name.trim().to_owned()));
        removed += end - start;
        last = end;
    }
    out.extend(&chars[last..]);
    (out, chords)
}

pub fn strip_chords(text: &str) -> String {
    split_chords(text).0
}

/// The reverse of [`split_chords`]: markers put back into a file's text so the operator can edit them.
pub fn with_chords(text: &str, chords: &[(i64, String)]) -> String {
    let mut chars: Vec<char> = text.chars().collect();
    let mut sorted: Vec<&(i64, String)> = chords.iter().collect();
    sorted.sort_by(|a, b| b.cmp(a));
    for (pos, name) in sorted {
        let at = usize::try_from(*pos).unwrap_or(0).min(chars.len());
        let marker: Vec<char> = format!("[{name}]").chars().collect();
        chars.splice(at..at, marker);
    }
    chars.into_iter().collect()
}

/// The section name a line stands for ("Verse 1"), or "" when it is not a section heading.
pub fn is_header(line: &str) -> String {
    let t = line.trim();
    if t.is_empty() || t.chars().count() > 24 {
        return String::new();
    }
    let t = t.trim_matches(['[', ']', '(', ')', ':']).trim().to_lowercase();
    let cut = t.trim_end_matches(|c: char| c.is_ascii_digit()).len();
    let (word, num) = if cut < t.len() { (t[..cut].trim().to_owned(), t[cut..].to_owned()) } else { (t.clone(), String::new()) };
    if num.len() > 2 {
        return String::new();
    }
    if let Some(name) = short(&word) {
        // "v", "c", "b" and "pc" need a number: "[C]" is a chord
        return if num.is_empty() { String::new() } else { format!("{name} {num}") };
    }
    if !NAMES.contains(&word.as_str()) {
        return String::new();
    }
    let base = if word.starts_with("pre") {
        "Pre-Chorus".to_owned()
    } else {
        let mut c = word.chars();
        c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default()
    };
    if num.is_empty() {
        base
    } else {
        format!("{base} {num}")
    }
}

/// `^[A-G][#b]?(?:maj|min|m|M|dim|aug|sus|add|\+)?\d*(?:\([^)]*\))?(?:/[A-G][#b]?)?$`
fn is_chord(tok: &str) -> bool {
    let c: Vec<char> = tok.chars().collect();
    if c.is_empty() || !('A'..='G').contains(&c[0]) {
        return false;
    }
    let mut i = 1;
    if i < c.len() && matches!(c[i], '#' | 'b') {
        i += 1;
    }
    let after_root = i;
    let rest_ok = |mut i: usize| -> bool {
        while i < c.len() && c[i].is_ascii_digit() {
            i += 1;
        }
        if i < c.len() && c[i] == '(' {
            match c[i..].iter().position(|x| *x == ')') {
                Some(n) => i += n + 1,
                None => return false,
            }
        }
        if i < c.len() && c[i] == '/' {
            i += 1;
            if i < c.len() && ('A'..='G').contains(&c[i]) {
                i += 1;
                if i < c.len() && matches!(c[i], '#' | 'b') {
                    i += 1;
                }
            } else {
                return false;
            }
        }
        i == c.len()
    };
    let mut quality_ends = vec![after_root];
    for q in ["maj", "min", "m", "M", "dim", "aug", "sus", "add", "+"] {
        if c[after_root..].iter().collect::<String>().starts_with(q) {
            quality_ends.push(after_root + q.chars().count());
        }
    }
    quality_ends.into_iter().any(rest_ok)
}

fn is_chord_line(line: &str) -> bool {
    let toks: Vec<&str> = line.split_whitespace().collect();
    !toks.is_empty() && toks.iter().all(|t| is_chord(t))
}

/// Chords written above a lyric line, put into it at the same columns as `[G]`.
fn merge_chords(chord_line: &str, lyric: &str) -> String {
    let chars: Vec<char> = chord_line.chars().collect();
    let mut at: Vec<(usize, String)> = Vec::new();
    let mut i = 0;
    while i < chars.len() {
        if chars[i].is_whitespace() {
            i += 1;
            continue;
        }
        let start = i;
        while i < chars.len() && !chars[i].is_whitespace() {
            i += 1;
        }
        at.push((start, chars[start..i].iter().collect()));
    }
    let mut lyric: Vec<char> = lyric.chars().collect();
    let width = at.iter().map(|(p, _)| *p).max().unwrap_or(0);
    while lyric.len() < width {
        lyric.push(' ');
    }
    for (pos, name) in at.iter().rev() {
        let marker: Vec<char> = format!("[{name}]").chars().collect();
        lyric.splice(*pos..*pos, marker);
    }
    lyric.into_iter().collect::<String>().trim_end().to_owned()
}

fn blocks(lines: &[String]) -> Vec<Vec<String>> {
    let (mut out, mut cur) = (Vec::new(), Vec::new());
    for ln in lines {
        if !ln.trim().is_empty() {
            cur.push(ln.clone());
        } else if !cur.is_empty() {
            out.push(std::mem::take(&mut cur));
        }
    }
    if !cur.is_empty() {
        out.push(cur);
    }
    out
}

fn chunk(pairs: &[(String, String)], n: i64) -> Vec<Value> {
    let step = if n > 0 { n as usize } else { pairs.len().max(1) };
    pairs
        .chunks(step)
        .map(|part| {
            let text = part.iter().map(|p| p.0.as_str()).collect::<Vec<_>>().join("\n");
            let translation = part.iter().map(|p| p.1.as_str()).filter(|t| !t.is_empty()).collect::<Vec<_>>().join("\n");
            json!({"text": text, "translation": translation})
        })
        .collect()
}

/// A pasted lyric sheet as `{sections: [{name, slides: [{text, translation}]}], arrangement: [names], warnings: [...]}`. `lines` is how many lines go on a
/// slide (0: no limit); a blank line ends a slide when `blank_splits`; a line that is just a section name starts a section when `headers`; a line starting
/// with `/` is the translation of the line above it when `translation`; a row of chord names above a lyric line goes into the lyric as `[G]` when `chords`.
pub fn parse_text(text: &str, lines: i64, blank_splits: bool, headers: bool, translation: bool, chords: bool) -> Result<Value, ParseError> {
    if text.trim().is_empty() {
        return err("Paste some lyrics first.");
    }
    if text.chars().count() > MAX_TEXT {
        return err("That text is too long.");
    }
    let raw: Vec<String> = text.replace("\r\n", "\n").replace('\r', "\n").replace('\t', "    ").split('\n').map(str::to_owned).collect();
    let mut parts: Vec<(String, Vec<String>)> = vec![(String::new(), Vec::new())];
    for ln in &raw {
        let name = if headers { is_header(ln) } else { String::new() };
        if name.is_empty() {
            if let Some(last) = parts.last_mut() {
                last.1.push(ln.trim_end().to_owned());
            }
        } else {
            parts.push((name, Vec::new()));
        }
    }
    let (mut sections, mut order, mut warnings): (Vec<Value>, Vec<String>, Vec<String>) = (Vec::new(), Vec::new(), Vec::new());
    let mut seen: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for (name, body) in parts {
        if name.is_empty() && !body.iter().any(|x| !x.trim().is_empty()) {
            continue;
        }
        let mut slides: Vec<Value> = Vec::new();
        let groups = if blank_splits { blocks(&body) } else { vec![body.iter().filter(|x| !x.trim().is_empty()).cloned().collect()] };
        for block in groups {
            let mut pairs: Vec<(String, String)> = Vec::new();
            let mut i = 0;
            while i < block.len() {
                let ln = &block[i];
                if chords && is_chord_line(ln) && i + 1 < block.len() && !is_chord_line(&block[i + 1]) && !block[i + 1].trim_start().starts_with('/') {
                    pairs.push((merge_chords(ln, &block[i + 1]), String::new()));
                    i += 2;
                    continue;
                }
                if translation && ln.trim_start().starts_with('/') && !pairs.is_empty() {
                    if let Some(prev) = pairs.last_mut() {
                        let extra = ln.trim_start()[1..].trim().to_owned();
                        prev.1 = if prev.1.is_empty() { extra } else { format!("{}\n{extra}", prev.1) };
                    }
                } else if !ln.trim().is_empty() {
                    pairs.push((if chords { ln.trim_end().trim_start().to_owned() } else { ln.trim().to_owned() }, String::new()));
                }
                i += 1;
            }
            slides.extend(chunk(&pairs, lines));
        }
        let mut title = if name.is_empty() { "Verse 1".to_owned() } else { name };
        let sig: Vec<String> = slides.iter().map(|s| s["text"].as_str().unwrap_or("").to_owned()).collect();
        if seen.get(&title) == Some(&sig) {
            order.push(title.clone());
            warnings.push(format!("\u{201c}{title}\u{201d} appears again with the same words. Kept once; it repeats in the arrangement."));
            continue;
        }
        if seen.contains_key(&title) {
            let mut k = 2;
            while seen.contains_key(&format!("{title} {k}")) {
                k += 1;
            }
            title = format!("{title} {k}");
        }
        seen.insert(title.clone(), sig);
        order.push(title.clone());
        if slides.is_empty() {
            warnings.push(format!("\u{201c}{title}\u{201d} has no lines."));
            slides.push(json!({"text": "", "translation": ""}));
        }
        sections.push(json!({"name": title, "slides": slides}));
    }
    if sections.is_empty() {
        return err("Nothing in that text looks like lyrics.");
    }
    Ok(json!({"sections": sections, "arrangement": order, "warnings": warnings}))
}

// ---------------------------------------------------------------------------------------------------------------------------- PDF
/// A piece of text and where it sits on the page.
struct Run {
    x: f64,
    y: f64,
    size: f64,
    text: String,
}

fn number(o: &Object) -> f64 {
    match o {
        Object::Integer(i) => *i as f64,
        Object::Real(r) => f64::from(*r),
        _ => 0.0,
    }
}

fn collect(text: &mut String, encoding: &Encoding, operands: &[Object]) {
    for o in operands {
        match o {
            Object::String(bytes, _) => text.push_str(&Document::decode_text(encoding, bytes).unwrap_or_else(|_| bytes.iter().map(|b| char::from(*b)).collect())),
            Object::Array(items) => collect(text, encoding, items),
            Object::Integer(i) if *i < -100 => text.push(' '),
            Object::Real(r) if *r < -100.0 => text.push(' '),
            _ => {}
        }
    }
}

/// The runs of text on one page with their positions: the text matrix and `Td`, `TD`, `Tm`, `T*`, `'` and `"` move the point, `Tj` and `TJ` draw.
fn page_runs(doc: &Document, page: lopdf::ObjectId) -> Result<Vec<Run>, ParseError> {
    let bad = ParseError { message: "That PDF could not be read." };
    let fonts = doc.get_page_fonts(page).map_err(|_| bad.clone())?;
    let encodings: BTreeMap<Vec<u8>, Encoding> = fonts.into_iter().filter_map(|(name, font)| font.get_font_encoding(doc).ok().map(|e| (name, e))).collect();
    let data = doc.get_page_content(page);
    let content = Content::decode(&data).map_err(|_| bad.clone())?;
    let (mut line_x, mut line_y, mut x, mut y, mut leading, mut size) = (0.0f64, 0.0f64, 0.0f64, 0.0f64, 0.0f64, 12.0f64);
    let mut encoding: Option<&Encoding> = None;
    let mut runs = Vec::new();
    for op in &content.operations {
        let o = &op.operands;
        match op.operator.as_str() {
            "BT" => (line_x, line_y, x, y) = (0.0, 0.0, 0.0, 0.0),
            "Tf" => {
                if let Some(name) = o.first().and_then(|n| n.as_name().ok()) {
                    encoding = encodings.get(name);
                }
                size = o.get(1).map_or(size, number).abs().max(1.0);
            }
            "TL" => leading = o.first().map_or(leading, number),
            "Td" | "TD" if o.len() >= 2 => {
                line_x += number(&o[0]);
                line_y += number(&o[1]);
                if op.operator == "TD" {
                    leading = -number(&o[1]);
                }
                (x, y) = (line_x, line_y);
            }
            "Tm" if o.len() >= 6 => {
                line_x = number(&o[4]);
                line_y = number(&o[5]);
                (x, y) = (line_x, line_y);
            }
            "T*" => {
                line_y -= leading;
                (x, y) = (line_x, line_y);
            }
            "'" | "\"" | "Tj" | "TJ" => {
                if matches!(op.operator.as_str(), "'" | "\"") {
                    line_y -= leading;
                    (x, y) = (line_x, line_y);
                }
                let mut text = String::new();
                if let Some(enc) = encoding {
                    collect(&mut text, enc, o);
                }
                if !text.is_empty() {
                    x += 0.0;
                    runs.push(Run { x, y, size, text: text.clone() });
                    // the pen moves on by about half the size for each character, so a later run on the same line lands to the right of this one
                    x += size * 0.5 * text.chars().count() as f64;
                }
            }
            _ => {}
        }
    }
    Ok(runs)
}

/// The text of a PDF lyric or chord sheet, laid out as printed so chords above a line stay above it. A file that makes the reader panic is one that could not
/// be read, whatever it was meant to do.
pub fn pdf_text(data: &[u8]) -> Result<String, ParseError> {
    std::panic::catch_unwind(|| read_pdf(data)).unwrap_or(Err(ParseError { message: "That PDF could not be read." }))
}

fn read_pdf(data: &[u8]) -> Result<String, ParseError> {
    let cannot = ParseError { message: "That PDF could not be read." };
    let doc = Document::load_mem(data).map_err(|_| cannot.clone())?;
    if doc.is_encrypted() {
        return err("That PDF is locked with a password.");
    }
    let mut pages = Vec::new();
    for (_, id) in doc.get_pages().into_iter().take(MAX_PAGES) {
        let mut runs = page_runs(&doc, id)?;
        // top of the page first, then left to right; runs on one line sit within a third of a character height of each other
        runs.sort_by(|a, b| b.y.partial_cmp(&a.y).unwrap_or(std::cmp::Ordering::Equal).then(a.x.partial_cmp(&b.x).unwrap_or(std::cmp::Ordering::Equal)));
        let mut lines: Vec<(f64, f64, Vec<&Run>)> = Vec::new();
        for r in &runs {
            match lines.last_mut() {
                Some(l) if (l.0 - r.y).abs() <= l.1 / 3.0 => l.2.push(r),
                _ => lines.push((r.y, r.size, vec![r])),
            }
        }
        let mut out = String::new();
        let mut last_y: Option<f64> = None;
        for (y, size, mut row) in lines {
            if let Some(prev) = last_y {
                // a gap of more than a line and a half is a blank line
                let gaps = ((prev - y) / (size * 1.5)).floor() as i64;
                for _ in 0..gaps.clamp(0, 3) {
                    out.push('\n');
                }
            }
            last_y = Some(y);
            row.sort_by(|a, b| a.x.partial_cmp(&b.x).unwrap_or(std::cmp::Ordering::Equal));
            let mut text = String::new();
            for r in row {
                let col = (r.x / (r.size * 0.5)).round().max(0.0) as usize;
                while text.chars().count() < col {
                    text.push(' ');
                }
                text.push_str(r.text.trim_end());
            }
            out.push_str(&text);
            out.push('\n');
        }
        pages.push(out);
    }
    let joined = pages.join("\n\n");
    let text = joined.lines().map(str::trim_end).collect::<Vec<_>>().join("\n");
    if text.trim().is_empty() {
        return err("That PDF has no text in it (a scan or a picture). Paste the words instead.");
    }
    let margin = text.lines().filter(|x| !x.trim().is_empty()).map(|x| x.chars().count() - x.trim_start().chars().count()).min().unwrap_or(0);
    Ok(text.lines().map(|x| x.chars().skip(margin).collect::<String>()).collect::<Vec<_>>().join("\n"))
}

/// The same sections with every slide's lines cut again into slides of `lines` (0: one slide per section). Each lyric line stays with its translation line;
/// the first slide of a section keeps that section's first slide's label, hot key and timing.
pub fn resplit(sections: &[Value], lines: i64) -> Vec<Value> {
    let mut out = Vec::new();
    for s in sections {
        let (mut pairs, mut first): (Vec<(String, String)>, serde_json::Map<String, Value>) = (Vec::new(), serde_json::Map::new());
        for (k, sl) in s["slides"].as_array().into_iter().flatten().enumerate() {
            if k == 0 {
                for key in ["label", "hot_key", "advance_after", "completion", "notes"] {
                    let v = &sl[key];
                    if crate::settings::truthy(v) {
                        first.insert(key.to_owned(), v.clone());
                    }
                }
            }
            let text = sl["text"].as_str().unwrap_or("");
            let l: Vec<&str> = text.split('\n').collect();
            let t: Vec<&str> = sl["translation"].as_str().filter(|t| !t.is_empty()).map(|t| t.split('\n').collect()).unwrap_or_default();
            for (j, ln) in l.iter().enumerate() {
                if !ln.trim().is_empty() || l.len() == 1 {
                    pairs.push(((*ln).to_owned(), t.get(j).copied().unwrap_or("").to_owned()));
                }
            }
        }
        let mut slides = chunk(&pairs, lines);
        if slides.is_empty() {
            slides.push(json!({"text": "", "translation": ""}));
        }
        if let Some(head) = slides[0].as_object_mut() {
            for (k, v) in first {
                head.insert(k, v);
            }
        }
        let mut row = s.as_object().cloned().unwrap_or_default();
        row.insert("slides".into(), Value::Array(slides));
        out.push(Value::Object(row));
    }
    out
}
