//! Slide-note tags that drive signage screens (#208). The ProPresenter module names `signage` in its `uses`.
//!
//! Which action does what (a value is `Name:Thing` for the `-screen` and `-group` forms, which split at the colon that follows a screen or group
//! name Relay knows; the plain forms act on every screen):
//!
//! ```text
//! signage-screen=Lobby TV:Welcome        a screen plays the signage playlist "Welcome"
//! signage-group=Lobby:Welcome            every screen in a group plays it
//! signage-all=Welcome                    every screen plays it
//! signage-push[-screen|-group]=...       send one picture from the media library (or one announcement slide) to play once, then the screen goes
//!                                        back to its playlist
//! signage-captions[-screen|-group]       show Relay's live captions until a resume tag
//! signage-service[-screen|-group]        show the Planning Center service rundown until a resume tag
//! signage-resume[-screen|-group]         end what tags put on the screens: playlists, pushes and live views
//! ```
//!
//! A playlist put on by a tag is the "tag" layer of signage's schedule: above a playlist chosen by hand and the schedule, below a takeover. It stays
//! until another tag, a resume tag or a restart of Relay. These actions do nothing while the signage module is off (the tag's result says so). Names are
//! not case-sensitive.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, PoisonError};

use relay_core::caps::{ControlCap, SignageControl, SIGNAGE_CONTROL};
use relay_core::text::fold;
use relay_core::Host;
use serde_json::{json, Value};

use crate::signagesync::PREFIX;

pub const OFF: &str = "Signage is switched off, so nothing was changed.";

/// Every signage action, with whether it takes a value (added to the actions the tags read, whether or not signage is on).
pub fn names() -> Vec<(String, bool)> {
    let mut out: Vec<(String, bool)> = ["signage-screen", "signage-group", "signage-all"].into_iter().map(|n| (n.to_owned(), true)).collect();
    for suffix in ["-screen", "-group", ""] {
        for kind in ["push", "captions", "service", "resume"] {
            out.push((format!("signage-{kind}{suffix}"), !suffix.is_empty() || kind == "push"));
        }
    }
    out
}

fn one_line(text: &str, n: usize) -> String {
    relay_core::text::squeeze(text).chars().take(n).collect()
}

/// The row named `name` (any case), when exactly one is.
fn named<'a>(rows: &'a [Value], name: &str) -> Option<&'a Value> {
    let want = fold(name);
    let hits: Vec<&Value> = rows.iter().filter(|r| fold(r["name"].as_str().unwrap_or("")) == want).collect();
    (hits.len() == 1).then(|| hits[0])
}

/// `Name:rest` -> (the row named `Name`, rest). The name may hold colons: every split is tried.
fn split<'a>(value: &str, rows: &'a [Value]) -> Option<(&'a Value, String)> {
    let value = relay_core::text::squeeze(value);
    for (i, ch) in value.char_indices() {
        if ch == ':' {
            if let Some(row) = named(rows, &value[..i]) {
                return Some((row, value[i + 1..].trim().to_owned()));
            }
        }
    }
    None
}

struct Aim {
    target: &'static str,
    id: i64,
    rest: String,
    label: String,
}

fn aim(kind: &str, value: &str, screens: &[Value], groups: &[Value], takes_rest: bool) -> Result<Aim, String> {
    if kind == "all" {
        return Ok(Aim { target: "all", id: 0, rest: value.trim().to_owned(), label: "every screen".into() });
    }
    let (rows, target) = if kind == "screen" { (screens, "screen") } else { (groups, "group") };
    let (row, rest) = if takes_rest {
        match split(value, rows) {
            Some((row, rest)) if !rest.is_empty() => (row, rest),
            _ => return Err(format!("Write it as {kind} name:name. Relay has no {kind} called that.")),
        }
    } else {
        match named(rows, value) {
            Some(row) => (row, String::new()),
            None => return Err(format!("Relay has no {kind} called \"{}\".", one_line(value, 40))),
        }
    };
    Ok(Aim { target, id: row["id"].as_i64().unwrap_or(0), rest, label: row["name"].as_str().unwrap_or("").to_owned() })
}

pub struct SignageTags {
    host: Host,
    /// `(target, id)` -> playlist id: what tags have put on screens. Nothing else is kept; a restart forgets it.
    picks: Mutex<BTreeMap<(String, i64), i64>>,
}

impl SignageTags {
    pub fn new(host: Host) -> Arc<Self> {
        Arc::new(Self { host, picks: Mutex::new(BTreeMap::new()) })
    }

    fn control(&self) -> Option<Arc<ControlCap>> {
        self.host.using::<ControlCap>(SIGNAGE_CONTROL)
    }

    pub fn on(&self) -> bool {
        self.control().is_some()
    }

    /// Signage's "tag" layer: the playlist a tag put on this screen, its group or every screen.
    pub fn layer(&self, screen: &Value) -> Option<Value> {
        let picks = self.picks.lock().unwrap_or_else(PoisonError::into_inner);
        let group = screen["group_id"].as_i64().unwrap_or(0);
        for key in [("screen".to_owned(), screen["id"].as_i64().unwrap_or(0)), ("group".to_owned(), group), ("all".to_owned(), 0)] {
            if let Some(pid) = picks.get(&key).filter(|p| **p > 0) {
                return Some(json!({"playlist_id": pid, "why": "Put on by a ProPresenter slide-note tag"}));
            }
        }
        None
    }

    pub fn forget(&self) {
        self.picks.lock().unwrap_or_else(PoisonError::into_inner).clear();
    }

    /// What tags have put on screens now, for the tag panel: `[{target, name, playlist}]`.
    pub async fn state(&self) -> Value {
        let picks = self.picks.lock().unwrap_or_else(PoisonError::into_inner).clone();
        let Some(c) = self.control() else { return json!({"on": false, "held": []}) };
        if picks.is_empty() {
            return json!({"on": true, "held": []});
        }
        let (Ok(screens), Ok(groups), Ok(lists)) = (c.0.screens().await, c.0.groups().await, c.0.playlists().await) else { return json!({"on": true, "held": []}) };
        let by = |rows: &[Value]| -> BTreeMap<i64, String> { rows.iter().map(|r| (r["id"].as_i64().unwrap_or(0), r["name"].as_str().unwrap_or("").to_owned())).collect() };
        let (screens, groups, lists) = (by(&screens), by(&groups), by(&lists));
        let mut held = Vec::new();
        for ((target, tid), pid) in &picks {
            let name = match target.as_str() {
                "screen" => screens.get(tid).cloned(),
                "group" => groups.get(tid).cloned(),
                _ => Some("every screen".to_owned()),
            };
            if let (Some(name), Some(playlist)) = (name, lists.get(pid)) {
                held.push(json!({"target": target, "name": name, "playlist": playlist}));
            }
        }
        json!({"on": true, "held": held})
    }

    async fn playlist(c: &dyn SignageControl, name: &str) -> Result<Value, String> {
        let rows = c.playlists().await.map_err(|e| e.0)?;
        named(&rows, name).cloned().ok_or_else(|| format!("Signage has no playlist called \"{}\".", one_line(name, 40)))
    }

    async fn media(c: &dyn SignageControl, name: &str) -> Result<Value, String> {
        let rows: Vec<Value> = c.media_list().await.map_err(|e| e.0)?.into_iter().filter(|m| m["kind"] == "image").collect();
        // An announcement slide is named "ProPresenter: <presentation> <n>"; allow the name without the prefix.
        named(&rows, name).or_else(|| named(&rows, &format!("{PREFIX}{name}"))).cloned().ok_or_else(|| format!("The media library has no picture called \"{}\".", one_line(name, 40)))
    }

    /// One signage action: what it did in a sentence, or why it could not.
    pub async fn run(&self, action: &str, value: Option<&str>) -> Result<String, String> {
        let Some(cap) = self.control() else { return Err(OFF.into()) };
        let c = &*cap.0;
        let value = value.unwrap_or("");
        let rest = action.strip_prefix("signage-").unwrap_or(action);
        let (kind, suffix) = rest.split_once('-').unwrap_or((rest, ""));
        // signage-screen / -group / -all are the playlist actions; the others are kind[-screen|-group].
        let (what, target) = if matches!(action, "signage-screen" | "signage-group" | "signage-all") { ("playlist", rest) } else { (kind, if suffix.is_empty() { "all" } else { suffix }) };
        let (screens, groups) = (c.screens().await.map_err(|e| e.0)?, c.groups().await.map_err(|e| e.0)?);
        let takes_rest = matches!(what, "playlist" | "push") && target != "all";
        let a = aim(target, value, &screens, &groups, takes_rest)?;
        match what {
            "playlist" => {
                let pl = Self::playlist(c, if target == "all" { value } else { &a.rest }).await?;
                self.picks.lock().unwrap_or_else(PoisonError::into_inner).insert((a.target.to_owned(), a.id), pl["id"].as_i64().unwrap_or(0));
                c.bump();
                Ok(format!("{} now plays {}.", a.label, pl["name"].as_str().unwrap_or("")))
            }
            "push" => {
                let m = Self::media(c, if target == "all" { value } else { &a.rest }).await?;
                c.push_set(a.target, a.id, m["id"].as_str().unwrap_or("")).await.map_err(|e| e.0)?;
                Ok(format!("Sent {} to {}.", m["name"].as_str().unwrap_or(""), a.label))
            }
            "captions" | "service" => {
                c.live_view_set(a.target, a.id, what).await.map_err(|e| e.0)?;
                Ok(format!("{} shows the live {what} until a signage-resume tag.", a.label))
            }
            _ => {
                if a.target == "all" {
                    self.picks.lock().unwrap_or_else(PoisonError::into_inner).clear();
                    let n = c.takeover_clear_all().await.map_err(|e| e.0)?;
                    c.bump();
                    return Ok(format!("Every screen is back on its playlist ({n} takeover{} ended).", if n == 1 { "" } else { "s" }));
                }
                self.picks.lock().unwrap_or_else(PoisonError::into_inner).remove(&(a.target.to_owned(), a.id));
                c.takeover_clear(a.target, a.id).await.map_err(|e| e.0)?;
                c.bump();
                Ok(format!("{} is back on its playlist.", a.label))
            }
        }
    }
}
