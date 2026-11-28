//! Songs into ProPresenter (#497; `ppsongs.py`): build `.pro` files (`ppfile`) and hand them to the Relay Helper on the ProPresenter computer, which opens
//! them with ProPresenter so it imports them. ProPresenter's network API cannot create a song, so this is the one way in; the helper is what makes it work
//! when Relay runs on another machine. Looks, timers, messages, props and playlists do not need the helper: they use the network API.

use base64::Engine;
use relay_core::caps::{HelpersCap, HELPERS};
use relay_core::Host;
use serde_json::{json, Value};

use crate::ppfile::{self, Rgb, Section, Slide, SlideAction, Song};
use crate::songparse;

pub const MAX_SONGS: usize = 50;
pub const MAX_SLIDES: usize = 300;
pub const MAX_TEXT: usize = 4000;
pub const MAX_FILE: usize = 16 * 1024 * 1024;

/// The request cannot be turned into songs. The message is a fixed sentence for the operator.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SongError {
    pub message: String,
}

fn bad<T>(message: impl Into<String>) -> Result<T, SongError> {
    Err(SongError { message: message.into() })
}

fn clip(v: &Value, limit: usize) -> String {
    let s = match v {
        Value::String(t) => t.clone(),
        Value::Null | Value::Bool(false) => String::new(),
        Value::Number(n) => n.to_string(),
        other => other.to_string(),
    };
    s.chars().take(limit).collect()
}

fn text_of(v: &Value) -> String {
    clip(v, MAX_TEXT)
}

fn rgb(v: &Value) -> Result<Option<Rgb>, SongError> {
    if v.is_null() || v == "" {
        return Ok(None);
    }
    let wrong = || SongError { message: "A colour is three numbers from 0 to 1.".into() };
    let parts = v.as_array().filter(|a| a.len() == 3).ok_or_else(wrong)?;
    let n: Vec<f64> = parts.iter().map(|x| x.as_f64().or_else(|| x.as_str().and_then(|t| t.trim().parse().ok()))).collect::<Option<_>>().ok_or_else(wrong)?;
    if !n.iter().all(|x| (0.0..=1.0).contains(x)) {
        return Err(wrong());
    }
    Ok(Some((n[0], n[1], n[2])))
}

fn num(v: &Value, what: &str, hi: f64) -> Result<f64, SongError> {
    let n = match v {
        Value::Null | Value::Bool(false) => 0.0,
        Value::Bool(true) => 1.0,
        Value::Number(n) => n.as_f64().unwrap_or(0.0),
        Value::String(t) if t.trim().is_empty() => 0.0,
        Value::String(t) => t.trim().parse::<f64>().map_err(|_| SongError { message: format!("{what} must be a number.") })?,
        _ => return bad(format!("{what} must be a number.")),
    };
    if !(0.0..=hi).contains(&n) {
        return bad(format!("{what} is out of range."));
    }
    Ok(n)
}

fn slide(d: &Value) -> Result<Slide, SongError> {
    if let Some(t) = d.as_str() {
        return Ok(Slide::text(&clip(&Value::String(t.to_owned()), MAX_TEXT)));
    }
    if !d.is_object() {
        return bad("Each slide is text or an object.");
    }
    let mut actions = Vec::new();
    for a in d["actions"].as_array().into_iter().flatten().take(20) {
        if !a.is_object() {
            return bad("Each slide action is an object.");
        }
        let built = SlideAction {
            kind: clip(&a["kind"], 100),
            target: clip(&a["target"], 200),
            uuid: clip(&a["uuid"], 64),
            what: clip(&a["what"], 30),
            amount: num(&a["amount"], "A timer amount", 86400.0)?,
            screen: clip(&a["screen"], 200),
            screen_uuid: clip(&a["screen_uuid"], 64),
        };
        actions.push(built.checked().map_err(|e| SongError { message: e.message })?);
    }
    // Chords typed inline as `[G]` in the text become positions in the text without them.
    let (text, inline) = songparse::split_chords(&text_of(&d["text"]));
    let mut chords: Vec<(i64, String)> = inline.into_iter().take(200).map(|(p, n)| (p, n.chars().take(20).collect())).collect();
    for c in d["chords"].as_array().into_iter().flatten().take(200) {
        let pair = c.as_array().filter(|p| p.len() >= 2);
        let (Some(pair), true) = (pair, true) else { return bad("A chord is a position and a name.") };
        let Some(pos) = pair[0].as_i64().or_else(|| pair[0].as_f64().map(|f| f as i64)).or_else(|| pair[0].as_str().and_then(|s| s.trim().parse().ok())) else { return bad("A chord is a position and a name.") };
        chords.push((pos, clip(&pair[1], 20)));
    }
    Ok(Slide {
        text,
        translation: text_of(&d["translation"]),
        label: clip(&d["label"], 80),
        label_colour: rgb(&d["label_colour"])?,
        notes: text_of(&d["notes"]),
        chords,
        hot_key: clip(&d["hot_key"], 2),
        advance_after: num(&d["advance_after"], "The advance time", 86400.0)?,
        completion: clip(&d["completion"], 10),
        transition: num(&d["transition"], "The transition", 30.0)?,
        actions,
        enabled: d["enabled"] != Value::Bool(false),
    })
}

/// A song the operator or a feed (Planning Center) described, as a [`Song`]. Limits keep one request from building a file ProPresenter chokes on; anything
/// else Relay cannot set is refused with a fixed sentence.
pub fn song_from_payload(d: &Value) -> Result<Song, SongError> {
    if !d.is_object() {
        return bad("A song is an object with a name and sections.");
    }
    let (mut sections, mut total) = (Vec::new(), 0usize);
    for s in d["sections"].as_array().into_iter().flatten().take(100) {
        if !s.is_object() {
            return bad("Each section is an object with a name and slides.");
        }
        let slides: Vec<Slide> = s["slides"].as_array().into_iter().flatten().map(slide).collect::<Result<_, _>>()?;
        total += slides.len();
        if total > MAX_SLIDES {
            return bad(format!("A song may have at most {MAX_SLIDES} slides."));
        }
        let name = clip(&s["name"], 80);
        sections.push(Section { name: if name.is_empty() { "Slides".to_owned() } else { name }, slides, colour: rgb(&s["colour"])?, hot_key: clip(&s["hot_key"], 2) });
    }
    let mut arrangements = Vec::new();
    for a in d["arrangements"].as_array().into_iter().flatten().take(20) {
        let Some(list) = a.as_object().and_then(|_| a["sections"].as_array()) else { return bad("An arrangement has a name and a list of section names.") };
        arrangements.push((clip(&a["name"], 80), list.iter().take(200).map(|x| clip(x, 80)).collect()));
    }
    Ok(Song {
        name: clip(&d["name"], 200),
        sections,
        arrangements,
        selected_arrangement: clip(&d["selected_arrangement"], 80),
        ccli_number: num(&d["ccli_number"], "The CCLI number", 1e9)? as i64,
        author: clip(&d["author"], 300),
        publisher: clip(&d["publisher"], 300),
        copyright_year: num(&d["copyright_year"], "The copyright year", 3000.0)? as i64,
        album: clip(&d["album"], 300),
        artist_credits: clip(&d["artist_credits"], 300),
        show_ccli: crate::settings::truthy(&d["show_ccli"]),
        notes: text_of(&d["notes"]),
        category: clip(&d["category"], 80),
        music_key: clip(&d["music_key"], 6),
        original_key: clip(&d["original_key"], 6),
        background: rgb(&d["background"])?,
        bible_reference: clip(&d["bible_reference"], 100),
        slide_show_seconds: num(&d["slide_show_seconds"], "The slideshow time", 86400.0)?,
        transition: num(&d["transition"], "The transition", 30.0)?,
        announcements: crate::settings::truthy(&d["announcements"]),
        ..Song::default()
    })
}

/// A file name for a song: its title with anything unsafe taken out, and a number added if two share one.
pub fn file_name(title: &str, seen: &mut Vec<String>) -> String {
    let base: String = title.trim().chars().filter(|c| c.is_alphanumeric() || " -_().,'&".contains(*c)).collect();
    let base = base.trim_matches([' ', '.']).chars().take(120).collect::<String>();
    let base = if base.is_empty() { "Song".to_owned() } else { base };
    let (mut name, mut n) = (base.clone(), 2);
    while seen.contains(&name.to_lowercase()) {
        name = format!("{base} {n}");
        n += 1;
    }
    seen.push(name.to_lowercase());
    format!("{name}.pro")
}

/// `.pro` files for `songs`, as the helper's `open_files` wants them: `[{name, data_b64}]`. Nothing is sent.
pub fn build_files(songs: &Value) -> Result<Vec<Value>, SongError> {
    let Some(list) = songs.as_array().filter(|l| !l.is_empty()) else { return bad("Send at least one song.") };
    if list.len() > MAX_SONGS {
        return bad(format!("At most {MAX_SONGS} songs at a time."));
    }
    let mut files = Vec::new();
    let mut seen = Vec::new();
    for raw in list {
        let song = song_from_payload(raw)?;
        let data = ppfile::build_song(&song).map_err(|e| SongError { message: e.message })?;
        if data.len() > MAX_FILE {
            return bad("That song is too large to send.");
        }
        files.push(json!({"name": file_name(&song.name, &mut seen), "data_b64": base64::engine::general_purpose::STANDARD.encode(&data)}));
    }
    Ok(files)
}

/// Which helper would carry songs to ProPresenter right now, for the admin page.
pub fn helper_state(host: &Host) -> Value {
    match host.using::<HelpersCap>(HELPERS) {
        Some(h) => {
            let found = h.0.find("propresenter", "open_files");
            json!({"connected": found.is_some(), "helper": found.unwrap_or_default(), "paired": h.0.paired()})
        }
        None => json!({"connected": false, "helper": "", "paired": 0}),
    }
}

/// Why sending did not happen: the request was bad, or no helper could do it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SendError {
    Song(SongError),
    Helper(String),
}

/// Build the songs and have the helper open them in ProPresenter.
pub async fn send(host: &Host, songs: &Value) -> Result<Value, SendError> {
    let files = build_files(songs).map_err(SendError::Song)?;
    let Some(h) = host.using::<HelpersCap>(HELPERS) else {
        return Err(SendError::Helper("No connected helper can do 'propresenter'. Install the Relay Helper on that computer and pair it.".into()));
    };
    let out = h.0.call("propresenter", "open_files", json!({"files": files})).await.map_err(|e| SendError::Helper(e.0))?;
    Ok(json!({"sent": files.len(), "files": files.iter().map(|f| f["name"].clone()).collect::<Vec<_>>(), "opened": out["opened"].as_u64().unwrap_or(files.len() as u64)}))
}

fn decode(b64: &Value) -> Result<Vec<u8>, SongError> {
    let raw = match b64 {
        Value::Null => String::new(),
        Value::String(t) => t.clone(),
        other => other.to_string(),
    };
    let data = base64::engine::general_purpose::STANDARD.decode(raw.as_bytes()).map_err(|_| SongError { message: "The file is not valid base64.".into() })?;
    if data.is_empty() || data.len() > MAX_FILE {
        return bad("The file is empty or too large.");
    }
    Ok(data)
}

fn lines_of(d: &Value) -> Result<i64, SongError> {
    let n = match &d["lines"] {
        Value::Null => 4,
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(0),
        Value::String(t) => t.trim().parse::<i64>().map_err(|_| SongError { message: "Lines per slide is a number.".into() })?,
        Value::Bool(b) => i64::from(*b),
        _ => return bad("Lines per slide is a number."),
    };
    if !(0..=12).contains(&n) {
        return bad("Lines per slide is from 0 to 12.");
    }
    Ok(n)
}

/// A pasted lyric sheet, or a PDF (`pdf_b64`), read into sections of slides for the operator to review. Nothing is kept.
pub fn parse_text(d: &Value) -> Result<Value, SongError> {
    if !d.is_object() {
        return bad("Send the text or a PDF to read.");
    }
    let from_pdf = crate::settings::truthy(&d["pdf_b64"]);
    let text = if from_pdf {
        let raw = decode(&d["pdf_b64"])?;
        songparse::pdf_text(&raw).map_err(|e| SongError { message: e.message.into() })?
    } else {
        d["text"].as_str().unwrap_or("").to_owned()
    };
    let lines = lines_of(d)?;
    let flag = |k: &str| d[k] != Value::Bool(false);
    let mut out = songparse::parse_text(&text, lines, flag("blank_splits"), flag("headers"), flag("translation"), flag("chords")).map_err(|e| SongError { message: e.message.into() })?;
    out["text"] = if from_pdf { json!(text) } else { Value::Null };
    Ok(out)
}

/// The sections cut again into slides of `lines` lines. Nothing is kept.
pub fn split_song(d: &Value) -> Result<Value, SongError> {
    let Some(sections) = d["sections"].as_array() else { return bad("Send the sections to split.") };
    let lines = lines_of(d)?;
    let sections: Vec<Value> = sections.iter().take(100).filter(|s| s.is_object()).cloned().collect();
    Ok(json!({"sections": songparse::resplit(&sections, lines)}))
}

/// What is in a `.pro` file (lyrics by section, arrangements, CCLI and the rest), for showing or reusing it.
pub fn read_song(data_b64: &Value, inline: bool) -> Result<Value, SongError> {
    let raw = decode(data_b64)?;
    let song = ppfile::parse_song(&raw).map_err(|e| SongError { message: e.message })?;
    Ok(song_to_json(&song, inline))
}

fn colour(c: Option<Rgb>) -> Value {
    c.map_or(Value::Null, |(r, g, b)| json!([r, g, b]))
}

pub fn song_to_json(song: &Song, inline: bool) -> Value {
    let sections: Vec<Value> = song
        .sections
        .iter()
        .map(|sec| {
            let slides: Vec<Value> = sec
                .slides
                .iter()
                .map(|sl| {
                    let mut o = json!({
                        "text": if inline { songparse::with_chords(&sl.text, &sl.chords) } else { sl.text.clone() }, "translation": sl.translation, "label": sl.label,
                        "label_colour": colour(sl.label_colour), "notes": sl.notes, "hot_key": sl.hot_key, "advance_after": sl.advance_after, "completion": sl.completion,
                        "transition": sl.transition, "enabled": sl.enabled,
                        "actions": sl.actions.iter().map(|a| json!({"kind": a.kind, "target": a.target, "uuid": a.uuid, "what": a.what, "amount": a.amount, "screen": a.screen, "screen_uuid": a.screen_uuid})).collect::<Vec<_>>(),
                    });
                    if !inline {
                        o["chords"] = json!(sl.chords.iter().map(|(p, n)| json!([p, n])).collect::<Vec<_>>());
                    }
                    o
                })
                .collect();
            json!({"name": sec.name, "slides": slides, "colour": colour(sec.colour), "hot_key": sec.hot_key})
        })
        .collect();
    json!({
        "name": song.name, "uuid": song.uuid, "sections": sections, "arrangements": song.arrangements.iter().map(|(n, s)| json!({"name": n, "sections": s})).collect::<Vec<_>>(),
        "selected_arrangement": song.selected_arrangement, "ccli_number": song.ccli_number, "author": song.author, "publisher": song.publisher,
        "copyright_year": song.copyright_year, "album": song.album, "artist_credits": song.artist_credits, "show_ccli": song.show_ccli, "notes": song.notes,
        "category": song.category, "music_key": song.music_key, "original_key": song.original_key, "background": colour(song.background),
        "bible_reference": song.bible_reference, "slide_show_seconds": song.slide_show_seconds, "transition": song.transition, "announcements": song.announcements,
    })
}
