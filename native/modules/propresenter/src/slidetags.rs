//! Slide-note tags (#138): a tag in a ProPresenter slide's notes triggers a Relay action when that slide goes live.
//!
//! ```text
//! [relay:action]            on a slide: runs when that slide has stayed live for a moment
//! [relay:action=value]      the same, for an action that takes a value
//! [relay-deck:action...]    in ANY slide's notes: a default for every slide of this presentation that has no [relay:...] tag of its own
//! ```
//!
//! Action names are not case-sensitive. Anything else in the notes is ignored. An action Relay does not know is ignored and listed in admin as
//! a warning. A value may hold spaces, and `]` inside it as a balanced `[...]` pair or as `\]`; a value stops at the end of the line.
//!
//! Relay never writes notes: this reads what the follower already knows (the current slide's notes from `status/slide`, and every slide's notes
//! from the one `presentation/{uuid}` read made when the presentation changes). Two rules keep a tag from firing too much:
//!
//! * **Settle**: an action runs only once the slide has stayed live for `settle_s`, so a fast run through the deck fires for the slide it stops
//!   on, not for every slide it passed.
//! * **Same set, once**: when the next slide that settles resolves to the same set (a deck-wide `[relay-deck:lyrics]` seen from every slide of a
//!   song) nothing runs again. A slide that resolves to nothing is a set of its own, so a tag that comes back after an untagged slide runs again.
//!
//! This file knows nothing about what an action does: [`Tags::set_action`] is filled in by `tagactions`.

use std::collections::{BTreeMap, VecDeque};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use relay_core::future::BoxFuture;
use relay_core::languages;
use serde_json::{json, Value};
use sha1::{Digest, Sha1};

use crate::signagetags;

pub const VALUE_MAX: usize = 200;
pub const MAX_WARNINGS: usize = 20;
pub const HISTORY_KEEP: usize = 30;
pub const DECKS_KEEP: usize = 32;
/// Characters of one slide's notes read; a tag is short, a notes box can be huge.
pub const NOTES_SCAN_MAX: usize = 20000;

/// What an action does: its value in, a short sentence of what it did (or why it could not) out.
pub type Action = Arc<dyn Fn(Option<String>) -> BoxFuture<'static, Result<String, String>> + Send + Sync>;

/// Every action Relay reads, with whether it takes a value ("lang=es", "alert=Sound desk").
pub fn actions_table() -> Vec<(String, bool)> {
    let mut out: Vec<(String, bool)> = [
        ("capture-start", false),
        ("capture-stop", false),
        ("lyrics", false),
        ("captions", false),
        ("lang", true),
        ("alert", true),
        ("light", true),
        ("obs", true),
        ("reaper", true),
    ]
    .into_iter()
    .map(|(n, t)| (n.to_owned(), t))
    .collect();
    out.extend(signagetags::names());
    out
}

fn takes_value(action: &str) -> Option<bool> {
    actions_table().into_iter().find(|(n, _)| n == action).map(|(_, t)| t)
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Tag {
    pub action: String,
    pub value: Option<String>,
}

impl Tag {
    pub fn label(&self) -> String {
        match &self.value {
            None => self.action.clone(),
            Some(v) => format!("{}={v}", self.action),
        }
    }
}

pub struct Parsed {
    pub slide: Vec<Tag>,
    pub deck: Vec<Tag>,
    pub warnings: Vec<String>,
}

fn squeeze(s: &str) -> String {
    relay_core::text::squeeze(s)
}

fn take(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// The value that starts at `i` (after the `=`) and the position after the closing `]`, or `None` when the line ends first. `[`...`]` inside nest,
/// and a backslash makes the next character plain (`\]`).
fn read_value(text: &[char], mut i: usize) -> Option<(String, usize)> {
    let mut out = String::new();
    let mut depth = 0;
    while i < text.len() {
        let ch = text[i];
        if ch == '\r' || ch == '\n' {
            return None;
        }
        if ch == '\\' && i + 1 < text.len() && matches!(text[i + 1], '[' | ']' | '\\') {
            out.push(text[i + 1]);
            i += 2;
            continue;
        }
        if ch == '[' {
            depth += 1;
        } else if ch == ']' {
            if depth == 0 {
                return Some((out, i + 1));
            }
            depth -= 1;
        }
        out.push(ch);
        i += 1;
    }
    None
}

/// A tag made valid (the value cleaned and the language named the way Relay names it), or a warning.
fn check(action: &str, value: Option<&str>) -> Result<Tag, String> {
    let Some(takes) = takes_value(action) else { return Err(format!("Unknown action \"{action}\" ignored.")) };
    if !takes {
        if let Some(v) = value {
            return Err(format!("{action} takes no value, so [relay:{action}={}] was ignored.", take(v, 30)));
        }
        return Ok(Tag { action: action.to_owned(), value: None });
    }
    let value = squeeze(value.unwrap_or(""));
    if value.is_empty() {
        return Err(format!("{action} needs a value, like [relay:{action}={}].", if action == "lang" { "es" } else { "Sound desk needed" }));
    }
    if action == "lang" {
        let name = language_name(&value);
        if name.is_empty() {
            return Err(format!("\"{}\" is not a language Relay translates into, so lang was ignored.", take(&value, 30)));
        }
        return Ok(Tag { action: action.to_owned(), value: Some(name) });
    }
    Ok(Tag { action: action.to_owned(), value: Some(take(&value, VALUE_MAX)) })
}

/// The target's name in `languages` ("SPANISH") for a code ("es"), a name ("Spanish") or the name as written there; "" when it is none of them.
pub fn language_name(value: &str) -> String {
    let v = value.trim();
    if v.is_empty() {
        return String::new();
    }
    if let Some(t) = languages::TARGETS.iter().find(|t| t.name == v.to_uppercase()) {
        return t.name.to_owned();
    }
    let low = v.to_lowercase();
    languages::TARGETS.iter().find(|t| t.iso == low || t.label.to_lowercase() == low).map_or_else(String::new, |t| t.name.to_owned())
}

fn starts_with_at(low: &[char], at: usize, pat: &str) -> bool {
    pat.chars().enumerate().all(|(k, c)| low.get(at + k) == Some(&c))
}

/// Every tag in one slide's notes. A tag that is broken (no closing `]` on its line, no action name) is skipped with a warning and the rest of the
/// notes are still read.
pub fn parse(notes: &str) -> Parsed {
    let text: Vec<char> = notes.chars().take(NOTES_SCAN_MAX).collect();
    let low: Vec<char> = text.iter().map(char::to_ascii_lowercase).collect();
    let (mut slide, mut deck, mut warnings) = (Vec::new(), Vec::new(), Vec::new());
    let mut i = 0;
    while let Some(found) = (i..low.len()).find(|&k| starts_with_at(&low, k, "[relay")) {
        i = found;
        let (scope, start) = if starts_with_at(&low, i, "[relay-deck:") {
            ("deck", i + "[relay-deck:".len())
        } else if starts_with_at(&low, i, "[relay:") {
            ("slide", i + "[relay:".len())
        } else {
            i += 1;
            continue;
        };
        let mut j = start;
        while j < text.len() && !matches!(text[j], '=' | ']' | '\r' | '\n') {
            j += 1;
        }
        let action: String = text[start..j.min(text.len())].iter().collect::<String>().trim().to_lowercase();
        if j >= text.len() || matches!(text[j], '\r' | '\n') || !action_name_ok(&action) {
            let head: String = text[i..(i + 24).min(text.len())].iter().collect();
            warnings.push(format!("A tag that starts \"{}\" is not complete, so it was ignored.", head.lines().next().unwrap_or("")));
            i = start;
            continue;
        }
        let (value, end) = if text[j] == '=' {
            match read_value(&text, j + 1) {
                Some((v, end)) => (Some(v), end),
                None => {
                    warnings.push(format!("[relay:{action}=... has no closing ] on its line, so it was ignored."));
                    i = start;
                    continue;
                }
            }
        } else {
            (None, j + 1)
        };
        i = end;
        match check(&action, value.as_deref()) {
            Ok(tag) => {
                if scope == "deck" {
                    deck.push(tag);
                } else {
                    slide.push(tag);
                }
            }
            Err(w) => warnings.push(w),
        }
    }
    warnings.truncate(MAX_WARNINGS);
    Parsed { slide, deck, warnings }
}

/// `^[a-z][a-z0-9_-]{0,31}$`
fn action_name_ok(a: &str) -> bool {
    let b = a.as_bytes();
    !b.is_empty() && b.len() <= 32 && b[0].is_ascii_lowercase() && b.iter().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, b'_' | b'-'))
}

/// What a whole presentation's notes say: its deck-wide defaults and the warnings found reading them.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Deck {
    pub tags: Vec<Tag>,
    pub warnings: Vec<String>,
    pub sig: String,
}

fn notes_sig(notes: &[String]) -> String {
    let mut h = Sha1::new();
    h.update(notes.join("\x1f").as_bytes());
    h.finalize().iter().take(6).map(|b| format!("{b:02x}")).collect()
}

/// The `[relay-deck:...]` tags of every slide's notes, in slide order, each once.
pub fn parse_deck(notes: &[String]) -> Deck {
    let (mut tags, mut warnings) = (Vec::new(), Vec::new());
    for (n, text) in notes.iter().enumerate() {
        let got = parse(text);
        for t in got.deck {
            if !tags.contains(&t) {
                tags.push(t);
            }
        }
        warnings.extend(got.warnings.into_iter().map(|w| format!("Slide {}: {w}", n + 1)));
    }
    warnings.truncate(MAX_WARNINGS);
    Deck { tags, warnings, sig: notes_sig(notes) }
}

/// The actions for a slide: its own valid tags, or the deck's when it has none. Each once, in the order written.
pub fn resolve(slide: &[Tag], deck: &[Tag]) -> Vec<Tag> {
    let chosen = if slide.is_empty() { deck } else { slide };
    let mut out: Vec<Tag> = Vec::new();
    for t in chosen {
        if !out.contains(t) {
            out.push(t.clone());
        }
    }
    out
}

// ------------------------------------------------------------------------------------------------------------ the watcher
type Key = (Option<String>, Option<i64>, String, String);

#[derive(Default)]
struct Inner {
    decks: VecDeque<((String, String), Arc<Deck>)>,
    deck_reads: u64,
    history: VecDeque<Value>,
    seen: Option<Value>,
    key: Option<Key>,
    pending: Option<Vec<Tag>>,
    settled: Option<Vec<Tag>>,
    timer: Option<tokio::task::JoinHandle<()>>,
}

/// What the follower tells `observe` is live.
pub struct Observed<'a> {
    pub enabled: bool,
    pub settle_s: f64,
    pub uuid: Option<&'a str>,
    pub title: &'a str,
    pub index: Option<i64>,
    pub notes: &'a str,
    pub deck: Option<&'a Deck>,
}

#[derive(Default)]
pub struct Tags {
    inner: Mutex<Inner>,
    actions: Mutex<BTreeMap<String, Action>>,
    on_change: Mutex<Option<Arc<dyn Fn() + Send + Sync>>>,
    run_lock: tokio::sync::Mutex<()>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(PoisonError::into_inner)
}

fn now() -> f64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0.0, |d| d.as_secs_f64())
}

impl Tags {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    pub fn set_action(&self, name: &str, action: Action) {
        lock(&self.actions).insert(name.to_owned(), action);
    }

    pub fn clear_actions(&self) {
        lock(&self.actions).clear();
    }

    pub fn set_on_change(&self, f: Option<Arc<dyn Fn() + Send + Sync>>) {
        *lock(&self.on_change) = f;
    }

    pub fn deck_reads(&self) -> u64 {
        lock(&self.inner).deck_reads
    }

    /// The deck-wide tags of a presentation, parsed once per (uuid, hash of its notes): the follower reads the presentation when it changes, and
    /// coming back to one with the same notes costs nothing.
    pub fn deck_for(&self, uuid: &str, notes: &[String]) -> Arc<Deck> {
        let key = (uuid.to_owned(), notes_sig(notes));
        let mut inner = lock(&self.inner);
        if let Some(at) = inner.decks.iter().position(|(k, _)| *k == key) {
            let hit = inner.decks.remove(at).unwrap_or_else(|| unreachable!("found a moment ago"));
            let deck = Arc::clone(&hit.1);
            inner.decks.push_back(hit);
            return deck;
        }
        let deck = Arc::new(parse_deck(notes));
        inner.deck_reads += 1;
        inner.decks.push_back((key, Arc::clone(&deck)));
        while inner.decks.len() > DECKS_KEEP {
            inner.decks.pop_front();
        }
        deck
    }

    /// Forget what was settled and anything waiting (tags switched off, or ProPresenter gone).
    pub fn reset(&self) {
        let changed = {
            let mut inner = lock(&self.inner);
            if let Some(t) = inner.timer.take() {
                t.abort();
            }
            inner.pending = None;
            inner.settled = None;
            inner.key = None;
            inner.seen.take().is_some()
        };
        if changed {
            self.changed();
        }
    }

    /// The follower's slide changed (or may have). Costs a comparison when nothing did.
    pub fn observe(self: &Arc<Self>, o: &Observed<'_>) {
        if !o.enabled {
            let dirty = {
                let inner = lock(&self.inner);
                inner.key.is_some() || inner.seen.is_some() || inner.timer.is_some()
            };
            if dirty {
                self.reset();
            }
            return;
        }
        let key: Key = (o.uuid.map(str::to_owned), o.index, o.notes.to_owned(), o.deck.map(|d| d.sig.clone()).unwrap_or_default());
        {
            let mut inner = lock(&self.inner);
            if inner.key.as_ref() == Some(&key) {
                return;
            }
            inner.key = Some(key);
            if let Some(t) = inner.timer.take() {
                t.abort();
            }
        }
        let (slide_tags, deck_tags, warnings) = match o.uuid {
            None => (Vec::new(), Vec::new(), Vec::new()),
            Some(_) => {
                let got = parse(o.notes);
                // The tags of the slide that is up count as deck tags even if the deck has not been read (yet).
                let mut deck_tags: Vec<Tag> = o.deck.map(|d| d.tags.clone()).unwrap_or_default();
                for t in got.deck {
                    if !deck_tags.contains(&t) {
                        deck_tags.push(t);
                    }
                }
                let mut warnings: Vec<String> = Vec::new();
                for w in got.warnings.into_iter().chain(o.deck.map(|d| d.warnings.clone()).unwrap_or_default()) {
                    if !warnings.contains(&w) {
                        warnings.push(w);
                    }
                }
                warnings.truncate(MAX_WARNINGS);
                (got.slide, deck_tags, warnings)
            }
        };
        let resolved = resolve(&slide_tags, &deck_tags);
        let labels = |v: &[Tag]| v.iter().map(Tag::label).collect::<Vec<_>>();
        {
            let mut inner = lock(&self.inner);
            inner.pending = Some(resolved.clone());
            inner.seen = Some(json!({
                "at": now(), "presentation": if o.uuid.is_some() { o.title } else { "" }, "slide": o.index.map(|i| i + 1),
                "slide_tags": labels(&slide_tags), "deck_tags": labels(&deck_tags), "resolved": labels(&resolved),
                "warnings": warnings, "settling": true,
            }));
        }
        self.changed();
        if tokio::runtime::Handle::try_current().is_ok() {
            let me = Arc::clone(self);
            let settle = Duration::from_secs_f64(o.settle_s.max(0.0));
            let handle = tokio::spawn(async move {
                tokio::time::sleep(settle).await;
                me.due();
            });
            lock(&self.inner).timer = Some(handle);
        }
    }

    /// The slide stayed up long enough. Run its set of actions unless it is the set that last ran.
    fn due(self: &Arc<Self>) {
        let run = {
            let mut inner = lock(&self.inner);
            inner.timer = None;
            let Some(resolved) = inner.pending.take() else { return };
            if let Some(seen) = inner.seen.as_mut() {
                seen["settling"] = Value::Bool(false);
            }
            if inner.settled.as_ref() == Some(&resolved) {
                None
            } else {
                inner.settled = Some(resolved.clone());
                Some(resolved)
            }
        };
        self.changed();
        if let Some(resolved) = run.filter(|r| !r.is_empty()) {
            let me = Arc::clone(self);
            tokio::spawn(async move { me.run(resolved).await });
        }
    }

    async fn run(self: &Arc<Self>, resolved: Vec<Tag>) {
        let _one_at_a_time = self.run_lock.lock().await;
        for tag in resolved {
            let action = lock(&self.actions).get(&tag.action).cloned();
            let mut row = json!({"at": now(), "action": tag.action, "value": tag.value});
            match action {
                None => {
                    row["ok"] = json!(false);
                    row["result"] = json!("Relay has nothing wired to that action.");
                }
                Some(f) => match f(tag.value.clone()).await {
                    Ok(done) => {
                        row["ok"] = json!(true);
                        row["result"] = json!(if done.is_empty() { "Done.".to_owned() } else { done });
                    }
                    Err(why) => {
                        tracing::warn!("Slide tag {} failed: {why}", tag.label());
                        row["ok"] = json!(false);
                        row["result"] = json!(if why.is_empty() { "That did not work.".to_owned() } else { why });
                    }
                },
            }
            tracing::info!("Slide tag {}: {}", tag.label(), row["result"]);
            {
                let mut inner = lock(&self.inner);
                inner.history.push_front(row);
                inner.history.truncate(HISTORY_KEEP);
            }
            self.changed();
        }
    }

    fn changed(&self) {
        let f = lock(&self.on_change).clone();
        if let Some(f) = f {
            f();
        }
    }

    pub fn state(&self) -> Value {
        let inner = lock(&self.inner);
        json!({
            "seen": inner.seen,
            "fired": inner.history.iter().cloned().collect::<Vec<_>>(),
            "actions": actions_table().into_iter().map(|(n, v)| json!({"name": n, "value": v})).collect::<Vec<_>>(),
        })
    }
}
