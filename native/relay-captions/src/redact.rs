//! Blocklist filtering for caption text (`app/redact.py`).
//!
//! Words on the list are *removed*, not masked. Captions arrive as deltas, so a banned word can be split across them ("dam" then
//! "n"): the redactor holds back any trailing text that could still grow into a banned term and releases the rest at once.
//!
//! Matching is case-insensitive and diacritic-insensitive ("niño" is "nino"), whole words only ("ass" does not hit "class"), and
//! takes multi-word phrases. Spaces and hyphens are interchangeable inside a term.
//!
//! Python matches with one regex using look-behind; the regex crate has none, so the match is written out by hand over `char`s.
//! Offsets found in the folded text slice the original, which is why [`fold`] keeps the length one to one.

use std::fs;
use std::io::Write;
use std::path::Path;

use relay_core::hub::{Redact, RedactorFactory};
use unicode_normalization::UnicodeNormalization;

/// The most terms a blocklist applies; the rest are counted and dropped.
pub const MAX_TERMS: usize = 1000;

/// Punctuation that must not be left orphaned behind a space after a removal.
const SPACE_BEFORE_PUNCT: &str = ",.;:!?\u{2026})]}\u{bb}\u{201d}\u{2019}%";
/// A clause mark: the second of two touching marks is the one the removed word owned.
const CLAUSE: &str = ",;:";
const SENTENCE_END: &str = ".!?\u{2026}";

pub const FILE_HEADER: &str = "# Blocked words — removed from both the English and the Spanish captions.\n\
# One word or phrase per line. Lines starting with # are ignored.\n\
# Saving this file takes effect within a couple of seconds; no restart needed.\n";

/// Python's `\w`: a letter, a digit or an underscore.
fn is_w(c: char) -> bool {
    c.is_alphanumeric() || c == '_'
}

/// A character of a word as the streaming hold sees it (`[^\W_]`).
fn is_word_body(c: char) -> bool {
    c.is_alphanumeric()
}

fn is_apostrophe(c: char) -> bool {
    c == '\'' || c == '\u{2019}'
}

fn is_sep(c: char) -> bool {
    c.is_whitespace() || c == '-'
}

fn is_blank(c: char) -> bool {
    c == ' ' || c == '\t'
}

/// Lowercase and strip diacritics, one character out for each one in (so offsets match the original).
pub fn fold(text: &str) -> String {
    fold_chars(&text.chars().collect::<Vec<_>>()).into_iter().collect()
}

fn fold_chars(chars: &[char]) -> Vec<char> {
    chars
        .iter()
        .map(|&c| {
            let base = std::iter::once(c).nfd().next().unwrap_or(c);
            base.to_lowercase().next().unwrap_or(base)
        })
        .collect()
}

/// The folded words a term matches on, split at spaces and hyphens.
pub fn term_words(term: &str) -> Vec<String> {
    fold(term).split(is_sep).filter(|w| !w.is_empty()).map(str::to_owned).collect()
}

/// What `parse_terms_report` found.
#[derive(Debug, PartialEq, Eq)]
pub struct ParsedTerms {
    pub terms: Vec<String>,
    /// Entries collapsed into an earlier one.
    pub duplicates: usize,
    /// Distinct terms past [`MAX_TERMS`], not applied.
    pub dropped: usize,
}

/// A newline or comma separated string, as an operator pastes one.
pub fn split_terms(raw: &str) -> Vec<String> {
    raw.split(['\n', ',']).map(str::to_owned).collect()
}

/// Dedupe (by folded words), preserve order, cap at [`MAX_TERMS`], and say what was collapsed or cut off.
pub fn parse_terms_report<S: AsRef<str>>(parts: &[S]) -> ParsedTerms {
    let mut seen: Vec<Vec<String>> = Vec::new();
    let mut terms = Vec::new();
    let (mut duplicates, mut dropped) = (0, 0);
    for part in parts {
        let term = part.as_ref().split_whitespace().collect::<Vec<_>>().join(" ");
        let key = term_words(&term);
        if key.is_empty() {
            continue;
        }
        if seen.contains(&key) {
            duplicates += 1;
            continue;
        }
        seen.push(key);
        if terms.len() >= MAX_TERMS {
            dropped += 1;
            continue;
        }
        terms.push(term);
    }
    ParsedTerms { terms, duplicates, dropped }
}

pub fn parse_terms<S: AsRef<str>>(parts: &[S]) -> Vec<String> {
    parse_terms_report(parts).terms
}

/// Streaming blocklist filter, one per caption channel.
#[derive(Default)]
pub struct Redactor {
    terms: Vec<String>,
    folded: Vec<Vec<Vec<char>>>,
    max_words: usize,
    buf: Vec<char>,
    prev: Option<char>,
}

impl Redactor {
    pub fn new(terms: &[String]) -> Self {
        let mut r = Self::default();
        r.set_terms(terms);
        r
    }

    pub fn terms(&self) -> &[String] {
        &self.terms
    }

    pub fn active(&self) -> bool {
        !self.folded.is_empty()
    }

    fn set(&mut self, terms: &[String]) {
        self.terms = parse_terms(terms);
        self.folded = self.terms.iter().map(|t| term_words(t).iter().map(|w| w.chars().collect()).collect()).collect();
        self.max_words = self.folded.iter().map(Vec::len).max().unwrap_or(0);
    }

    fn drain(&mut self, last: bool) -> String {
        if self.buf.is_empty() {
            return String::new();
        }
        let hold_at = if last { self.buf.len() } else { self.hold_offset() };
        let mut emit: Vec<char> = self.buf[..hold_at].to_vec();
        let mut rest: Vec<char> = self.buf[hold_at..].to_vec();
        // Never emit trailing blanks: a removal in the next fragment could strand a space in front of punctuation.
        if !last {
            let kept = emit.iter().rposition(|&c| !is_blank(c)).map_or(0, |i| i + 1);
            let mut moved = emit.split_off(kept);
            moved.extend(rest);
            rest = moved;
        }
        self.buf = rest;
        if emit.is_empty() {
            return String::new();
        }
        let prev = self.prev;
        let prev_word = prev.is_some_and(|c| c.is_alphanumeric() || is_apostrophe(c));
        let cleaned = self.remove(&emit, !prev_word);
        let mut cleaned = tidy(&cleaned, prev);
        if prev.is_none_or(|c| SENTENCE_END.contains(c) || c.is_whitespace()) {
            cleaned = recase(&emit, &cleaned);
        }
        if last {
            let kept = cleaned.iter().rposition(|&c| !is_blank(c)).map_or(0, |i| i + 1);
            cleaned.truncate(kept);
        }
        if let Some(&c) = cleaned.last() {
            self.prev = Some(c);
        }
        cleaned.into_iter().collect()
    }

    /// First index of the tail that must be held back.
    fn hold_offset(&self) -> usize {
        let buf = &self.buf;
        let words = word_spans(buf);
        let Some(&(_, last_end)) = words.last() else { return buf.len() };
        let trailing_partial = last_end == buf.len();
        // The longest tail first: the largest run of trailing words that could still be extended into a term.
        for count in (1..=self.max_words.min(words.len())).rev() {
            let tail = &words[words.len() - count..];
            let folded: Vec<Vec<char>> = tail.iter().map(|&(s, e)| fold_chars(&buf[s..e])).collect();
            if self.could_extend(&folded, trailing_partial) {
                return tail[0].0;
            }
        }
        buf.len()
    }

    /// True if `seq` might be the opening of a term once more text lands.
    fn could_extend(&self, seq: &[Vec<char>], last_is_partial: bool) -> bool {
        for words in &self.folded {
            if words.len() < seq.len() {
                continue;
            }
            let n = seq.len();
            if words[..n - 1] != seq[..n - 1] {
                continue;
            }
            let last = &words[n - 1];
            if last_is_partial {
                if last.starts_with(&seq[n - 1]) {
                    return true;
                }
            } else if *last == seq[n - 1] && words.len() > n {
                return true;
            }
        }
        false
    }

    /// The spans of `folded` a term matches: whole words only, the first term that fits at a position wins.
    fn matches(&self, folded: &[char]) -> Vec<(usize, usize)> {
        let mut out = Vec::new();
        let mut i = 0;
        while i < folded.len() {
            let at_boundary = i == 0 || !is_w(folded[i - 1]);
            let end = if at_boundary { self.match_at(folded, i) } else { None };
            match end {
                Some(e) => {
                    out.push((i, e));
                    i = e;
                }
                None => i += 1,
            }
        }
        out
    }

    fn match_at(&self, folded: &[char], start: usize) -> Option<usize> {
        'terms: for words in &self.folded {
            let mut at = start;
            for (n, word) in words.iter().enumerate() {
                if n > 0 {
                    let from = at;
                    while at < folded.len() && is_sep(folded[at]) {
                        at += 1;
                    }
                    if at == from {
                        continue 'terms;
                    }
                }
                if folded.len() - at < word.len() || folded[at..at + word.len()] != word[..] {
                    continue 'terms;
                }
                at += word.len();
            }
            if at < folded.len() && is_w(folded[at]) {
                continue;
            }
            return Some(at);
        }
        None
    }

    fn remove(&self, text: &[char], allow_match_at_start: bool) -> Vec<char> {
        let folded = fold_chars(text);
        let (mut out, mut cursor, mut recap) = (Vec::new(), 0, false);
        for (s, e) in self.matches(&folded) {
            if s == 0 && !allow_match_at_start {
                // This fragment continues a word already on screen, so a match at its first character is not a word start.
                continue;
            }
            let mut before: Vec<char> = text[cursor..s].to_vec();
            if recap {
                before = capitalise_first(&before);
            }
            let before_blank = before.iter().all(|c| c.is_whitespace());
            out.extend(before);
            // A capitalised word right after a sentence end opened that sentence; the word after it inherits the capital.
            let so_far = out.iter().rposition(|&c| !is_blank(c)).map(|i| out[i]);
            recap = (so_far.is_some_and(|c| SENTENCE_END.contains(c)) && text[s].is_uppercase()) || (recap && before_blank);
            cursor = e;
        }
        let rest = &text[cursor..];
        if recap {
            out.extend(capitalise_first(rest));
        } else {
            out.extend_from_slice(rest);
        }
        out
    }
}

impl Redact for Redactor {
    /// Take a caption delta, return the text that is safe to show now.
    fn feed(&mut self, delta: &str) -> String {
        if !self.active() {
            return delta.to_owned();
        }
        self.buf.extend(delta.chars());
        self.drain(false)
    }

    /// Release everything held back (the segment ended, nothing more coming).
    fn flush(&mut self) -> String {
        if !self.active() {
            return String::new();
        }
        self.drain(true)
    }

    /// Redact a complete string in one pass, independent of stream state.
    fn whole(&mut self, text: &str) -> String {
        if !self.active() || text.is_empty() {
            return text.to_owned();
        }
        let chars: Vec<char> = text.chars().collect();
        let cleaned = self.remove(&chars, true);
        let tidied = tidy(&cleaned, None);
        recase(&chars, &tidied).into_iter().collect::<String>().trim().to_owned()
    }

    fn reset(&mut self) {
        self.buf.clear();
        self.prev = None;
    }

    fn set_terms(&mut self, terms: &[String]) {
        self.set(terms);
    }
}

/// The factory the hub makes each channel's redactor with.
pub fn factory() -> RedactorFactory {
    Box::new(|terms| Box::new(Redactor::new(terms)))
}

/// Spans of `[^\W_]+(?:['’][^\W_]+)*` in `buf`.
fn word_spans(buf: &[char]) -> Vec<(usize, usize)> {
    let mut spans = Vec::new();
    let mut i = 0;
    while i < buf.len() {
        if !is_word_body(buf[i]) {
            i += 1;
            continue;
        }
        let start = i;
        loop {
            while i < buf.len() && is_word_body(buf[i]) {
                i += 1;
            }
            if i + 1 < buf.len() && is_apostrophe(buf[i]) && is_word_body(buf[i + 1]) {
                i += 1;
            } else {
                break;
            }
        }
        spans.push((start, i));
    }
    spans
}

fn capitalise_first(text: &[char]) -> Vec<char> {
    match text.iter().position(|c| c.is_alphabetic()) {
        None => text.to_vec(),
        Some(i) => {
            let mut out = text[..i].to_vec();
            out.extend(text[i].to_uppercase());
            out.extend_from_slice(&text[i + 1..]);
            out
        }
    }
}

/// A removed word that opened a sentence took its capital with it: give it to the word that now opens the sentence.
fn recase(original: &[char], cleaned: &[char]) -> Vec<char> {
    let a = original.iter().position(|c| c.is_alphabetic());
    let b = cleaned.iter().position(|c| c.is_alphabetic());
    let (Some(a), Some(b)) = (a, b) else { return cleaned.to_vec() };
    if !original[a].is_uppercase() || !cleaned[b].is_lowercase() {
        return cleaned.to_vec();
    }
    let head = |s: &[char]| s.iter().skip_while(|c| c.is_whitespace()).take(12).copied().collect::<Vec<_>>();
    if head(original) == head(cleaned) {
        return cleaned.to_vec();
    }
    let mut out = cleaned[..b].to_vec();
    out.extend(cleaned[b].to_uppercase());
    out.extend_from_slice(&cleaned[b + 1..]);
    out
}

/// Close the gap a removal leaves, so the line still reads naturally.
fn tidy(fragment: &[char], prev: Option<char>) -> Vec<char> {
    // Two or more blanks become one.
    let mut a: Vec<char> = Vec::with_capacity(fragment.len());
    let mut i = 0;
    while i < fragment.len() {
        if is_blank(fragment[i]) {
            let mut j = i;
            while j < fragment.len() && is_blank(fragment[j]) {
                j += 1;
            }
            if j - i >= 2 {
                a.push(' ');
            } else {
                a.push(fragment[i]);
            }
            i = j;
        } else {
            a.push(fragment[i]);
            i += 1;
        }
    }
    // No blanks in front of closing punctuation.
    let mut b: Vec<char> = Vec::with_capacity(a.len());
    let mut i = 0;
    while i < a.len() {
        if is_blank(a[i]) {
            let mut j = i;
            while j < a.len() && is_blank(a[j]) {
                j += 1;
            }
            if j < a.len() && SPACE_BEFORE_PUNCT.contains(a[j]) {
                i = j;
            } else {
                b.extend_from_slice(&a[i..j]);
                i = j;
            }
        } else {
            b.push(a[i]);
            i += 1;
        }
    }
    // A mark followed by a clause mark: keep the first.
    let mut c: Vec<char> = Vec::with_capacity(b.len());
    let mut i = 0;
    while i < b.len() {
        c.push(b[i]);
        if ",;:.!?\u{2026}".contains(b[i]) {
            let mut j = i + 1;
            while j < b.len() && is_blank(b[j]) {
                j += 1;
            }
            let marks = j;
            while j < b.len() && CLAUSE.contains(b[j]) {
                j += 1;
            }
            if j > marks {
                i = j;
                continue;
            }
        }
        i += 1;
    }
    let mut fragment = c;
    if prev.is_none_or(|p| CLAUSE.contains(p) || SENTENCE_END.contains(p)) {
        // Line start, or the mark before it already arrived in an earlier fragment: a clause mark leading this one is the removed word's.
        let body_at = fragment.iter().position(|&c| !is_blank(c)).unwrap_or(fragment.len());
        if fragment.get(body_at).is_some_and(|&c| CLAUSE.contains(c)) {
            let tail: Vec<char> = fragment[body_at..].iter().copied().skip_while(|&c| CLAUSE.contains(c) || is_blank(c)).collect();
            fragment = if prev.is_some() { std::iter::once(' ').chain(tail).collect() } else { tail };
        }
    }
    if prev.is_none_or(char::is_whitespace) {
        let at = fragment.iter().position(|&c| !is_blank(c)).unwrap_or(fragment.len());
        fragment.drain(..at);
    }
    fragment
}

// ---------------------------------------------------------------- file I/O

/// Drop `#` comments, to the end of each line.
fn strip_comments(raw: &str) -> String {
    raw.lines().map(|l| l.split('#').next().unwrap_or("")).collect::<Vec<_>>().join("\n")
}

/// One term per line, the layout the file is written in.
pub fn format_terms(terms: &[String]) -> String {
    terms.join("\n")
}

/// Read a blocklist file: one per line, comma separated, or both; `#` starts a comment. A missing or unreadable file is an empty list.
pub fn read_blocklist_file(path: &Path) -> Vec<String> {
    let Ok(raw) = fs::read_to_string(path) else { return Vec::new() };
    let report = parse_terms_report(&split_terms(&strip_comments(&raw)));
    if report.dropped > 0 {
        tracing::warn!("{}: {} term(s) past the {MAX_TERMS}-term limit are not applied", path.display(), report.dropped);
    }
    report.terms
}

/// Write terms back in the documented format, keeping the comment lines already in the file (an operator may have labelled sections).
pub fn write_blocklist_file(path: &Path, terms: &[String]) -> std::io::Result<Vec<String>> {
    let terms = parse_terms(terms);
    let existing = fs::read_to_string(path).unwrap_or_default();
    let comments: Vec<&str> = existing.lines().filter(|l| l.trim_start().starts_with('#')).collect();
    let header = if comments.is_empty() { FILE_HEADER.to_owned() } else { format!("{}\n", comments.join("\n")) };
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".tmp");
    let tmp = std::path::PathBuf::from(tmp);
    let mut f = fs::File::create(&tmp)?;
    f.write_all(format!("{header}{}\n", format_terms(&terms)).as_bytes())?;
    f.sync_all()?;
    fs::rename(&tmp, path)?;
    Ok(terms)
}

/// The file's modified time in seconds since the epoch, 0 when it cannot be read.
pub fn file_mtime(path: &Path) -> f64 {
    fs::metadata(path)
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.duration_since(std::time::UNIX_EPOCH).ok())
        .map_or(0.0, |d| d.as_secs_f64())
}
