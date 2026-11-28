//! Read and write ProPresenter's own files (#497; `ppfile.py`): songs (`.pro`), from the community Pro7 definitions.
//!
//! The message types are built at run time from `pro7/pro7.desc` (a descriptor set compiled from greyshirtguy's MIT `.proto` files), so no generated code
//! is tied to a protobuf version. Nothing here touches the disk: bytes in, bytes out. Writing ProPresenter's files is unsupported by Renewed Vision, so a
//! song is built by cloning one slide ProPresenter itself exported (`pro7/lyric_slide.bin`).

use std::borrow::Cow;
use std::sync::OnceLock;

use prost::Message;
use prost_reflect::{DescriptorPool, DynamicMessage, Kind, MessageDescriptor, ReflectMessage, Value as Pv};

const DESCRIPTORS: &[u8] = include_bytes!("../../../../app/modules/propresenter/pro7/pro7.desc");
const LYRIC_SLIDE: &[u8] = include_bytes!("../../../../app/modules/propresenter/pro7/lyric_slide.bin");

pub type Rgb = (f64, f64, f64);

/// ProPresenter's group colours (red, green, blue), by the names a song's sections usually carry.
pub fn section_colour(name: &str) -> Rgb {
    let plain: String = name.to_lowercase().chars().filter(|c| c.is_ascii_lowercase() || *c == '-').collect();
    match plain.as_str() {
        "verse" => (0.0, 0.46, 0.89),
        "chorus" => (0.89, 0.15, 0.25),
        "bridge" => (0.55, 0.15, 0.82),
        "pre-chorus" | "prechorus" => (0.95, 0.55, 0.1),
        "tag" => (0.1, 0.65, 0.35),
        _ => (0.5, 0.5, 0.5),
    }
}

/// A ProPresenter file Relay cannot read, or a song it cannot write. The message is a fixed sentence, safe to show.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct FileError {
    pub message: String,
}

impl FileError {
    pub fn new(message: impl Into<String>) -> Self {
        Self { message: message.into() }
    }
}

impl std::fmt::Display for FileError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for FileError {}

type Fallible<T> = Result<T, FileError>;

fn pool() -> &'static DescriptorPool {
    static POOL: OnceLock<DescriptorPool> = OnceLock::new();
    POOL.get_or_init(|| DescriptorPool::decode(DESCRIPTORS).unwrap_or_else(|_| unreachable!("pro7.desc is the descriptor set compiled into this program")))
}

fn desc(name: &str) -> MessageDescriptor {
    pool().get_message_by_name(name).unwrap_or_else(|| unreachable!("pro7.desc has {name}"))
}

fn new_uuid() -> String {
    uuid::Uuid::new_v4().to_string().to_uppercase()
}

// ------------------------------------------------------------------------------------------------------------------ a path into a message
fn step<'a>(cur: Cow<'a, Pv>, seg: &str) -> Option<Cow<'a, Pv>> {
    match cur {
        Cow::Borrowed(Pv::Message(m)) => m.get_field_by_name(seg),
        Cow::Borrowed(Pv::List(l)) => l.get(seg.parse::<usize>().ok()?).map(Cow::Borrowed),
        Cow::Owned(Pv::Message(m)) => m.get_field_by_name(seg).map(|c| Cow::Owned(c.into_owned())),
        Cow::Owned(Pv::List(mut l)) => {
            let i = seg.parse::<usize>().ok()?;
            (i < l.len()).then(|| Cow::Owned(l.swap_remove(i)))
        }
        _ => None,
    }
}

/// The value at `path` (`a.b.0.c`: field names, and list positions as numbers), or `None` when the path does not lead anywhere.
fn peek<'a>(root: &'a DynamicMessage, path: &str) -> Option<Cow<'a, Pv>> {
    let mut segs = path.split('.');
    let mut cur = root.get_field_by_name(segs.next()?)?;
    for seg in segs {
        cur = step(cur, seg)?;
    }
    Some(cur)
}

fn gs(root: &DynamicMessage, path: &str) -> String {
    peek(root, path).and_then(|v| v.as_str().map(str::to_owned)).unwrap_or_default()
}

fn gnum(root: &DynamicMessage, path: &str) -> f64 {
    peek(root, path).map_or(0.0, |v| match &*v {
        Pv::F32(x) => f64::from(*x),
        Pv::F64(x) => *x,
        Pv::I32(x) | Pv::EnumNumber(x) => f64::from(*x),
        Pv::I64(x) => *x as f64,
        Pv::U32(x) => f64::from(*x),
        Pv::U64(x) => *x as f64,
        _ => 0.0,
    })
}

fn gb(root: &DynamicMessage, path: &str) -> bool {
    peek(root, path).and_then(|v| v.as_bool()).unwrap_or(false)
}

fn gbytes(root: &DynamicMessage, path: &str) -> Vec<u8> {
    peek(root, path).and_then(|v| v.as_bytes().map(|b| b.to_vec())).unwrap_or_default()
}

fn glist(root: &DynamicMessage, path: &str) -> Vec<Pv> {
    peek(root, path).and_then(|v| v.as_list().map(<[Pv]>::to_vec)).unwrap_or_default()
}

fn gmsgs(root: &DynamicMessage, path: &str) -> Vec<DynamicMessage> {
    glist(root, path).into_iter().filter_map(|v| if let Pv::Message(m) = v { Some(m) } else { None }).collect()
}

/// Whether the message at `path` is set (a field of a oneof is set when it is the one chosen).
fn has(root: &DynamicMessage, path: &str) -> bool {
    match path.rsplit_once('.') {
        None => root.has_field_by_name(path),
        Some((parent, last)) => peek(root, parent).and_then(|p| p.as_message().map(|m| m.has_field_by_name(last))).unwrap_or(false),
    }
}

fn enum_name(root: &DynamicMessage, path: &str) -> String {
    let (parent, last) = path.rsplit_once('.').map_or((None, path), |(p, l)| (Some(p), l));
    let holder: Option<DynamicMessage> = match parent {
        None => Some(root.clone()),
        Some(p) => peek(root, p).and_then(|v| v.as_message().cloned()),
    };
    let Some(m) = holder else { return String::new() };
    let Some(fd) = m.descriptor().get_field_by_name(last) else { return String::new() };
    let Kind::Enum(ed) = fd.kind() else { return String::new() };
    let n = m.get_field(&fd).as_enum_number().unwrap_or(0);
    ed.get_value(n).map(|v| v.name().to_owned()).unwrap_or_default()
}

/// The message at `path`, made (empty) along the way, for writing into.
fn at_mut<'a>(root: &'a mut DynamicMessage, path: &str) -> Option<&'a mut DynamicMessage> {
    if path.is_empty() {
        return Some(root);
    }
    let mut segs = path.split('.');
    let mut cur: &mut Pv = root.get_field_by_name_mut(segs.next()?)?;
    for seg in segs {
        cur = match cur {
            Pv::Message(m) => m.get_field_by_name_mut(seg)?,
            Pv::List(l) => l.get_mut(seg.parse::<usize>().ok()?)?,
            _ => return None,
        };
    }
    match cur {
        Pv::Message(m) => Some(m),
        Pv::List(l) => l.last_mut().and_then(Pv::as_message_mut),
        _ => None,
    }
}

fn split(path: &str) -> (&str, &str) {
    path.rsplit_once('.').unwrap_or(("", path))
}

fn put(root: &mut DynamicMessage, path: &str, value: Pv) -> Fallible<()> {
    let (parent, last) = split(path);
    let m = at_mut(root, parent).ok_or_else(|| FileError::new("ProPresenter's format has no such field."))?;
    m.try_set_field_by_name(last, value).map_err(|_| FileError::new("ProPresenter's format has no such field."))
}

fn put_str(root: &mut DynamicMessage, path: &str, v: &str) -> Fallible<()> {
    put(root, path, Pv::String(v.to_owned()))
}

fn put_bytes(root: &mut DynamicMessage, path: &str, v: &[u8]) -> Fallible<()> {
    put(root, path, Pv::Bytes(prost::bytes::Bytes::copy_from_slice(v)))
}

fn put_bool(root: &mut DynamicMessage, path: &str, v: bool) -> Fallible<()> {
    put(root, path, Pv::Bool(v))
}

/// A number written as the field's own type.
fn put_num(root: &mut DynamicMessage, path: &str, v: f64) -> Fallible<()> {
    let (parent, last) = split(path);
    let m = at_mut(root, parent).ok_or_else(|| FileError::new("ProPresenter's format has no such field."))?;
    let fd = m.descriptor().get_field_by_name(last).ok_or_else(|| FileError::new("ProPresenter's format has no such field."))?;
    let value = match fd.kind() {
        Kind::Float => Pv::F32(v as f32),
        Kind::Double => Pv::F64(v),
        Kind::Int32 | Kind::Sint32 | Kind::Sfixed32 => Pv::I32(v as i32),
        Kind::Int64 | Kind::Sint64 | Kind::Sfixed64 => Pv::I64(v as i64),
        Kind::Uint32 | Kind::Fixed32 => Pv::U32(v as u32),
        Kind::Uint64 | Kind::Fixed64 => Pv::U64(v as u64),
        Kind::Enum(_) => Pv::EnumNumber(v as i32),
        _ => return Err(FileError::new("ProPresenter's format has no such number.")),
    };
    m.try_set_field_by_name(last, value).map_err(|_| FileError::new("ProPresenter's format has no such field."))
}

fn enum_number(m: &DynamicMessage, field: &str, value_name: &str) -> Fallible<i32> {
    let fd = m.descriptor().get_field_by_name(field).ok_or_else(|| FileError::new("ProPresenter's format has no such field."))?;
    match fd.kind() {
        Kind::Enum(ed) => ed.get_value_by_name(value_name).map(|v| v.number()).ok_or_else(|| FileError::new("ProPresenter's format has no such value.")),
        _ => Err(FileError::new("ProPresenter's format has no such value.")),
    }
}

fn put_enum(root: &mut DynamicMessage, path: &str, value_name: &str) -> Fallible<()> {
    let (parent, last) = split(path);
    let m = at_mut(root, parent).ok_or_else(|| FileError::new("ProPresenter's format has no such field."))?;
    let n = enum_number(m, last, value_name)?;
    put(root, path, Pv::EnumNumber(n))
}

/// A new, empty message appended to the repeated field at `path`.
fn add<'a>(root: &'a mut DynamicMessage, path: &str) -> Fallible<&'a mut DynamicMessage> {
    let (parent, last) = split(path);
    let bad = || FileError::new("ProPresenter's format has no such field.");
    let holder = at_mut(root, parent).ok_or_else(bad)?;
    let md = match holder.descriptor().get_field_by_name(last).ok_or_else(bad)?.kind() {
        Kind::Message(md) => md,
        _ => return Err(bad()),
    };
    let list = holder.get_field_by_name_mut(last).and_then(Pv::as_list_mut).ok_or_else(bad)?;
    list.push(Pv::Message(DynamicMessage::new(md)));
    list.last_mut().and_then(Pv::as_message_mut).ok_or_else(bad)
}

fn add_uuid(root: &mut DynamicMessage, path: &str, id: &str) -> Fallible<()> {
    let m = add(root, path)?;
    put_str(m, "string", id)
}

// ------------------------------------------------------------------------------------------------------------ slide text: RTF
fn cp1252(b: u8) -> char {
    const HIGH: [char; 32] = [
        '\u{20ac}', '\u{81}', '\u{201a}', '\u{192}', '\u{201e}', '\u{2026}', '\u{2020}', '\u{2021}', '\u{2c6}', '\u{2030}', '\u{160}', '\u{2039}', '\u{152}', '\u{8d}', '\u{17d}', '\u{8f}', '\u{90}', '\u{2018}',
        '\u{2019}', '\u{201c}', '\u{201d}', '\u{2022}', '\u{2013}', '\u{2014}', '\u{2dc}', '\u{2122}', '\u{161}', '\u{203a}', '\u{153}', '\u{9d}', '\u{17e}', '\u{178}',
    ];
    if (0x80..0xa0).contains(&b) {
        HIGH[usize::from(b - 0x80)]
    } else {
        char::from(b)
    }
}

/// `\\([a-zA-Z]+)(-?\d+)? ?` at `i`: the word, its number, and where the match ends.
fn control_word(s: &[char], i: usize) -> Option<(String, Option<i64>, usize)> {
    let mut j = i + 1;
    let start = j;
    while j < s.len() && s[j].is_ascii_alphabetic() {
        j += 1;
    }
    if j == start {
        return None;
    }
    let word: String = s[start..j].iter().collect();
    let num_start = j;
    if j < s.len() && s[j] == '-' {
        j += 1;
    }
    let digits_from = j;
    while j < s.len() && s[j].is_ascii_digit() {
        j += 1;
    }
    let arg = if j > digits_from { s[num_start..j].iter().collect::<String>().parse::<i64>().ok() } else { j = num_start; None };
    if j < s.len() && s[j] == ' ' {
        j += 1;
    }
    Some((word, arg, j))
}

/// The plain text of a slide's RTF: groups and control words dropped, `\par` and `\` as line breaks, `\'xx` and `\uN` decoded.
pub fn rtf_to_text(rtf: &[u8]) -> String {
    let s: Vec<char> = rtf.iter().map(|b| char::from(*b)).collect();
    let n = s.len();
    let mut out = String::new();
    let (mut depth, mut skip_at, mut i) = (0i64, None::<i64>, 0usize);
    let starts = |at: usize, pat: &str| pat.chars().enumerate().all(|(k, c)| s.get(at + k) == Some(&c));
    while i < n {
        let ch = s[i];
        match ch {
            '{' => {
                depth += 1;
                if starts(i, "{\\*") || ["fonttbl", "colortbl", "stylesheet", "info"].iter().any(|w| starts(i, &format!("{{\\{w}"))) {
                    skip_at = skip_at.or(Some(depth));
                }
            }
            '}' => {
                if skip_at == Some(depth) {
                    skip_at = None;
                }
                depth -= 1;
            }
            '\\' => {
                if let Some((word, arg, end)) = control_word(&s, i) {
                    let advance = end - i;
                    if skip_at.is_none() {
                        match (word.as_str(), arg) {
                            ("par" | "line", _) => out.push('\n'),
                            ("tab", _) => out.push('\t'),
                            ("u", Some(code)) => {
                                let code = if code >= 0 { code } else { code + 65536 };
                                out.push(u32::try_from(code).ok().and_then(char::from_u32).unwrap_or('\u{fffd}'));
                                // a `\uN` is followed by one fallback character that is not text
                                if end < n && !matches!(s[end], '\\' | '{' | '}') {
                                    i += 1;
                                }
                            }
                            _ => {}
                        }
                    }
                    i += advance;
                    continue;
                }
                let nxt = s.get(i + 1).copied();
                if nxt == Some('\'') && i + 3 < n + 1 {
                    if skip_at.is_none() {
                        let hex: String = s.get(i + 2..i + 4).map_or(String::new(), |h| h.iter().collect());
                        out.push(u8::from_str_radix(&hex, 16).map_or('\u{fffd}', cp1252));
                    }
                    i += 4;
                    continue;
                }
                match nxt {
                    Some('\n' | '\r') => {
                        if skip_at.is_none() {
                            out.push('\n');
                        }
                    }
                    Some(c @ ('\\' | '{' | '}')) if skip_at.is_none() => out.push(c),
                    _ => {}
                }
                i += 2;
                continue;
            }
            '\r' | '\n' => {}
            c if skip_at.is_none() => out.push(c),
            _ => {}
        }
        i += 1;
    }
    out.trim_matches(['\n', ' ']).split('\n').map(str::trim).collect::<Vec<_>>().join("\n").trim().to_owned()
}

fn rtf_escape(text: &str) -> String {
    let mut out = String::new();
    for ch in text.chars() {
        match ch {
            '\\' | '{' | '}' => {
                out.push('\\');
                out.push(ch);
            }
            c if (c as u32) < 128 => out.push(c),
            c => {
                let code = c as u32 as i64;
                out.push_str(&format!("\\u{}?", if code < 32768 { code } else { code - 65536 }));
            }
        }
    }
    out
}

/// `template`'s RTF with its text replaced: the formatting stays ProPresenter's own, the lines are ours.
pub fn text_to_rtf(template: &[u8], text: &str) -> Vec<u8> {
    const MARKER: &[u8] = b"\\strokec0 ";
    let mut head: Vec<u8> = match template.windows(MARKER.len()).position(|w| w == MARKER) {
        Some(at) => template[..at + MARKER.len()].to_vec(),
        None => template.to_vec(),
    };
    let body: Vec<String> = text.split('\n').map(rtf_escape).collect();
    head.extend(body.join("\\\n").chars().map(|c| u8::try_from(c as u32).unwrap_or(b'?')));
    head.push(b'}');
    head
}

fn plain_rtf(text: &str) -> Vec<u8> {
    let body: Vec<String> = text.split('\n').map(rtf_escape).collect();
    let mut out = b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2870\n{\\fonttbl\\f0\\fswiss\\fcharset0 Helvetica;}\n{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\pardirnatural\\partightenfactor0\n\n\\f0\\fs48 \\cf1 ".to_vec();
    out.extend(body.join("\\\n").chars().map(|c| u8::try_from(c as u32).unwrap_or(b'?')));
    out.push(b'}');
    out
}

const DEFAULT_RTF: &[u8] = b"{\\rtf1\\ansi\\ansicpg1252\\cocoartf2870\n{\\fonttbl\\f0\\fswiss\\fcharset0 Arial-Black;}\n{\\colortbl;\\red255\\green255\\blue255;}\n\\pard\\qc\n\\f0\\b\\fs120 \\cf1 \\strokec0 ";

// ------------------------------------------------------------------------------------------------------------------ the song model
/// What a slide can do besides show words: ProPresenter's action kinds, by the name Relay uses: `(ActionType enum name, the Action.<Type> message)`.
pub fn action_kind(kind: &str) -> Option<(&'static str, &'static str)> {
    Some(match kind {
        "macro" => ("ACTION_TYPE_MACRO", "MacroType"),
        "look" => ("ACTION_TYPE_AUDIENCE_LOOK", "AudienceLookType"),
        "prop" => ("ACTION_TYPE_PROP", "PropType"),
        "message" => ("ACTION_TYPE_MESSAGE", "MessageType"),
        "timer" => ("ACTION_TYPE_TIMER", "TimerType"),
        "clear" => ("ACTION_TYPE_CLEAR", "ClearType"),
        "clear_group" => ("ACTION_TYPE_CLEAR_GROUP", "ClearGroupType"),
        "stage" => ("ACTION_TYPE_STAGE_LAYOUT", "StageLayoutType"),
        _ => return None,
    })
}

const KINDS: [&str; 8] = ["macro", "look", "prop", "message", "timer", "clear", "clear_group", "stage"];
pub const TIMER_ACTIONS: [&str; 6] = ["start", "stop", "reset", "reset_and_start", "stop_and_reset", "increment"];
pub const CLEAR_LAYERS: [&str; 9] = ["all", "audio", "background", "live_video", "prop", "slide", "logo", "messages", "audio_effects"];
const KEYS: &str = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789";

/// Something a slide does when it is shown: fire a macro or look, start a timer, clear a layer, show a prop or message, or pick a stage layout. `target`
/// is the thing's name; `uuid` its id in the workspace when known.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct SlideAction {
    pub kind: String,
    pub target: String,
    pub uuid: String,
    /// Timer: one of [`TIMER_ACTIONS`]; clear: one of [`CLEAR_LAYERS`].
    pub what: String,
    /// Timer: seconds to add for "increment".
    pub amount: f64,
    /// Stage: the stage screen's name; `target` is then the layout.
    pub screen: String,
    pub screen_uuid: String,
}

impl SlideAction {
    pub fn new(kind: &str) -> Fallible<Self> {
        if !KINDS.contains(&kind) {
            return Err(FileError::new(format!("Unknown slide action '{kind}'.")));
        }
        Ok(Self { kind: kind.to_owned(), ..Self::default() })
    }

    /// Check the parts against what ProPresenter has.
    pub fn checked(self) -> Fallible<Self> {
        if !KINDS.contains(&self.kind.as_str()) {
            return Err(FileError::new(format!("Unknown slide action '{}'.", self.kind)));
        }
        if self.kind == "timer" && !self.what.is_empty() && !TIMER_ACTIONS.contains(&self.what.as_str()) {
            return Err(FileError::new(format!("Unknown timer action '{}'.", self.what)));
        }
        if self.kind == "clear" && !self.what.is_empty() && !CLEAR_LAYERS.contains(&self.what.as_str()) {
            return Err(FileError::new(format!("Unknown clear layer '{}'.", self.what)));
        }
        Ok(self)
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Slide {
    pub text: String,
    /// A second line of text under the first, smaller (another language).
    pub translation: String,
    /// The name under the slide's thumbnail.
    pub label: String,
    pub label_colour: Option<Rgb>,
    /// The slide's notes.
    pub notes: String,
    /// `(character offset into text, chord)`.
    pub chords: Vec<(i64, String)>,
    /// One of the letters and digits ProPresenter takes.
    pub hot_key: String,
    /// Seconds, then go on as `completion` says.
    pub advance_after: f64,
    /// "" none, "next", "first" (loop) or "random".
    pub completion: String,
    /// Seconds; 0 keeps the song's own.
    pub transition: f64,
    pub actions: Vec<SlideAction>,
    pub enabled: bool,
}

impl Default for Slide {
    fn default() -> Self {
        Self { text: String::new(), translation: String::new(), label: String::new(), label_colour: None, notes: String::new(), chords: Vec::new(), hot_key: String::new(), advance_after: 0.0, completion: String::new(), transition: 0.0, actions: Vec::new(), enabled: true }
    }
}

impl Slide {
    pub fn text(text: &str) -> Self {
        Self { text: text.to_owned(), ..Self::default() }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Section {
    pub name: String,
    pub slides: Vec<Slide>,
    pub colour: Option<Rgb>,
    pub hot_key: String,
}

impl Section {
    pub fn new(name: &str, slides: Vec<Slide>) -> Self {
        Self { name: name.to_owned(), slides, colour: None, hot_key: String::new() }
    }
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Song {
    pub name: String,
    pub uuid: String,
    pub sections: Vec<Section>,
    /// `(name, section names in order)`.
    pub arrangements: Vec<(String, Vec<String>)>,
    pub selected_arrangement: String,
    pub ccli_number: i64,
    pub author: String,
    pub publisher: String,
    pub copyright_year: i64,
    pub album: String,
    pub artist_credits: String,
    /// Show the CCLI details on the song's first or last slide.
    pub show_ccli: bool,
    pub notes: String,
    pub category: String,
    /// E.g. "G" or "F#m".
    pub music_key: String,
    /// The key the song was written in.
    pub original_key: String,
    pub background: Option<Rgb>,
    /// E.g. "John 3:16" for a scripture presentation (display text).
    pub bible_reference: String,
    /// Play the slides on their own, this long each.
    pub slide_show_seconds: f64,
    /// Seconds, for the whole song.
    pub transition: f64,
    /// Show in the announcements layer, not the global one.
    pub announcements: bool,
}

impl Song {
    pub fn lyrics(&self) -> String {
        self.sections.iter().map(|s| format!("{}\n{}", s.name, s.slides.iter().map(|x| x.text.clone()).collect::<Vec<_>>().join("\n\n"))).collect::<Vec<_>>().join("\n\n")
    }
}

const KEY_NAMES: [&str; 21] = ["Ab", "A", "A#", "Bb", "B", "B#", "Cb", "C", "C#", "Db", "D", "D#", "Eb", "E", "E#", "Fb", "F", "F#", "Gb", "G", "G#"];

/// `G`, `F#m`, `Bb` as (MusicKey, MusicScale) numbers, or `None` for a key Relay does not know.
fn key_to_proto(key: &str) -> Option<(i32, i32)> {
    let k = key.trim();
    let mut chars = k.chars();
    let letter = chars.next().filter(|c| matches!(c.to_ascii_uppercase(), 'A'..='G'))?;
    let rest: String = chars.collect();
    let (acc, scale) = match rest.as_str() {
        "" => ("", 0),
        "#" => ("#", 0),
        "b" => ("b", 0),
        "m" | "min" | "minor" => ("", 1),
        r => {
            let (a, s) = r.split_at(1);
            if !matches!(a, "#" | "b") || !matches!(s, "m" | "min" | "minor") {
                return None;
            }
            (a, 1)
        }
    };
    let name = format!("{}{acc}", letter.to_ascii_uppercase());
    KEY_NAMES.iter().position(|n| *n == name).map(|i| (i as i32, scale))
}

fn key_from_proto(key: i64, scale: i64) -> String {
    usize::try_from(key).ok().and_then(|k| KEY_NAMES.get(k)).map_or_else(String::new, |n| format!("{n}{}", if scale != 0 { "m" } else { "" }))
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

fn colour_at(root: &DynamicMessage, path: &str) -> Rgb {
    (round3(gnum(root, &format!("{path}.red"))), round3(gnum(root, &format!("{path}.green"))), round3(gnum(root, &format!("{path}.blue"))))
}

fn set_colour(root: &mut DynamicMessage, path: &str, rgb: Rgb) -> Fallible<()> {
    put_num(root, &format!("{path}.red"), rgb.0)?;
    put_num(root, &format!("{path}.green"), rgb.1)?;
    put_num(root, &format!("{path}.blue"), rgb.2)?;
    put_num(root, &format!("{path}.alpha"), 1.0)
}

fn hot_key_name(root: &DynamicMessage, path: &str) -> String {
    let name = enum_name(root, &format!("{path}.code"));
    match name.strip_prefix("KEY_CODE_ANSI_") {
        Some(k) if name.len() == 15 => k.to_owned(),
        _ => String::new(),
    }
}

fn set_hot_key(root: &mut DynamicMessage, path: &str, key: &str) -> Fallible<()> {
    let key = key.trim().to_uppercase();
    if key.is_empty() {
        return Ok(());
    }
    if key.chars().count() != 1 || !KEYS.contains(&key) {
        return Err(FileError::new(format!("'{key}' is not a hot key Relay can set (use a letter or digit).")));
    }
    put_enum(root, &format!("{path}.code"), &format!("KEY_CODE_ANSI_{key}"))
}

// ------------------------------------------------------------------------------------------------------------------------------ reading
fn slide_elements(cue: &DynamicMessage) -> Vec<DynamicMessage> {
    for action in gmsgs(cue, "actions") {
        if has(&action, "slide.presentation") {
            return gmsgs(&action, "slide.presentation.base_slide.elements").iter().filter_map(|e| peek(e, "element").and_then(|v| v.as_message().cloned())).filter(|el| has(el, "text") && !gbytes(el, "text.rtf_data").is_empty()).collect();
        }
    }
    Vec::new()
}

fn read_action(action: &DynamicMessage) -> Option<SlideAction> {
    let ty = enum_name(action, "type");
    for kind in KINDS {
        let (type_name, msg_name) = action_kind(kind)?;
        if ty != type_name {
            continue;
        }
        // the Action field whose message type is rv.data.Action.<msg_name>
        let field = action.descriptor().fields().find(|fd| matches!(fd.kind(), Kind::Message(md) if md.full_name() == format!("rv.data.Action.{msg_name}")))?.name().to_owned();
        let body = peek(action, &field).and_then(|v| v.as_message().cloned())?;
        let mut out = SlideAction { kind: kind.to_owned(), ..SlideAction::default() };
        for ident in ["identification", "message_identificaton", "timer_identification"] {
            if body.descriptor().get_field_by_name(ident).is_some() && body.has_field_by_name(ident) {
                out.uuid = gs(&body, &format!("{ident}.parameter_uuid.string"));
                out.target = gs(&body, &format!("{ident}.parameter_name"));
            }
        }
        if kind == "stage" {
            if let Some(asg) = gmsgs(&body, "stage_screen_assignments").first() {
                out.screen = gs(asg, "screen.parameter_name");
                out.screen_uuid = gs(asg, "screen.parameter_uuid.string");
                out.target = gs(asg, "layout.parameter_name");
                out.uuid = gs(asg, "layout.parameter_uuid.string");
            }
        }
        if kind == "timer" {
            out.what = enum_name(&body, "action_type").replace("ACTION_", "").to_lowercase();
            out.amount = gnum(&body, "increment_amount");
        }
        if kind == "clear" {
            out.what = enum_name(&body, "target_layer").replace("CLEAR_TARGET_LAYER_", "").to_lowercase();
        }
        return Some(out);
    }
    None
}

fn read_slide(cue: &DynamicMessage) -> Slide {
    let els = slide_elements(cue);
    let mut slide = Slide { enabled: gb(cue, "isEnabled"), ..Slide::default() };
    if let Some(first) = els.first() {
        slide.text = rtf_to_text(&gbytes(first, "text.rtf_data"));
        slide.chords = gmsgs(first, "text.attributes.custom_attributes").iter().filter(|a| has(a, "chord")).map(|a| (gnum(a, "range.start") as i64, gs(a, "chord"))).collect();
        if let Some(second) = els.get(1) {
            slide.translation = rtf_to_text(&gbytes(second, "text.rtf_data"));
        }
    }
    slide.hot_key = hot_key_name(cue, "hot_key");
    if gnum(cue, "completion_action_type") as i64 == 3 {
        // after time
        slide.advance_after = gnum(cue, "completion_time");
        slide.completion = match gnum(cue, "completion_target_type") as i64 {
            1 => "next",
            4 => "first",
            2 => "random",
            _ => "",
        }
        .to_owned();
    }
    for action in gmsgs(cue, "actions") {
        if enum_name(&action, "type") == "ACTION_TYPE_PRESENTATION_SLIDE" {
            let label = gs(&action, "label.text");
            if !label.is_empty() {
                slide.label = label;
                if has(&action, "label.color") {
                    slide.label_colour = Some(colour_at(&action, "label.color"));
                }
            }
            let notes = gbytes(&action, "slide.presentation.notes.rtf_data");
            if has(&action, "slide.presentation.notes") && !notes.is_empty() {
                slide.notes = rtf_to_text(&notes);
            }
            if has(&action, "slide.presentation.transition") {
                slide.transition = gnum(&action, "slide.presentation.transition.duration");
            }
        } else if let Some(sa) = read_action(&action) {
            slide.actions.push(sa);
        }
    }
    slide
}

/// A `.pro` file as a [`Song`]: sections with their slides (text, chords, notes, labels, actions), arrangements, CCLI and the song's own settings. A file
/// that is not a presentation is refused.
pub fn parse_song(data: &[u8]) -> Fallible<Song> {
    let not_a_song = || FileError::new("This is not a ProPresenter song file.");
    let pres = DynamicMessage::decode(desc("rv.data.Presentation"), data).map_err(|_| not_a_song())?;
    if gs(&pres, "name").is_empty() && glist(&pres, "cue_groups").is_empty() && glist(&pres, "cues").is_empty() {
        return Err(not_a_song());
    }
    let cues: std::collections::BTreeMap<String, DynamicMessage> = gmsgs(&pres, "cues").into_iter().map(|c| (gs(&c, "uuid.string"), c)).collect();
    let mut song = Song { name: gs(&pres, "name"), uuid: gs(&pres, "uuid.string"), notes: gs(&pres, "notes"), category: gs(&pres, "category"), ..Song::default() };
    let mut by_id: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for cg in gmsgs(&pres, "cue_groups") {
        let name = Some(gs(&cg, "group.name")).filter(|n| !n.is_empty()).unwrap_or_else(|| "Slides".to_owned());
        let mut sec = Section { name: name.clone(), ..Section::default() };
        if has(&cg, "group.color") {
            sec.colour = Some(colour_at(&cg, "group.color"));
        }
        if has(&cg, "group.hotKey") {
            sec.hot_key = hot_key_name(&cg, "group.hotKey");
        }
        for ident in gmsgs(&cg, "cue_identifiers") {
            if let Some(cue) = cues.get(&gs(&ident, "string")) {
                sec.slides.push(read_slide(cue));
            }
        }
        by_id.insert(gs(&cg, "group.uuid.string"), name);
        song.sections.push(sec);
    }
    let selected = gs(&pres, "selected_arrangement.string");
    for arr in gmsgs(&pres, "arrangements") {
        let order = gmsgs(&arr, "group_identifiers").iter().map(|g| by_id.get(&gs(g, "string")).cloned().unwrap_or_default()).collect();
        song.arrangements.push((gs(&arr, "name"), order));
        if gs(&arr, "uuid.string") == selected {
            song.selected_arrangement = gs(&arr, "name");
        }
    }
    song.ccli_number = gnum(&pres, "ccli.song_number") as i64;
    song.author = gs(&pres, "ccli.author");
    song.publisher = gs(&pres, "ccli.publisher");
    song.copyright_year = gnum(&pres, "ccli.copyright_year") as i64;
    song.album = gs(&pres, "ccli.album");
    song.artist_credits = gs(&pres, "ccli.artist_credits");
    song.show_ccli = gb(&pres, "ccli.display");
    if has(&pres, "music") {
        if has(&pres, "music.original") {
            song.original_key = key_from_proto(gnum(&pres, "music.original.music_key") as i64, gnum(&pres, "music.original.music_scale") as i64);
        }
        if has(&pres, "music.user") {
            song.music_key = key_from_proto(gnum(&pres, "music.user.music_key") as i64, gnum(&pres, "music.user.music_scale") as i64);
        }
    }
    if song.music_key.is_empty() {
        song.music_key = gs(&pres, "music_key");
    }
    if has(&pres, "background") && gb(&pres, "background.is_enabled") && has(&pres, "background.color") {
        song.background = Some(colour_at(&pres, "background.color"));
    }
    let book = gs(&pres, "bible_reference.book_name");
    if !book.is_empty() {
        let (ch, vs, ve) = (gnum(&pres, "bible_reference.chapter_range.start") as i64, gnum(&pres, "bible_reference.verse_range.start") as i64, gnum(&pres, "bible_reference.verse_range.end") as i64);
        song.bible_reference = format!("{book} {ch}") + &if vs > 0 { format!(":{vs}{}", if ve > vs { format!("-{ve}") } else { String::new() }) } else { String::new() };
    }
    song.slide_show_seconds = if has(&pres, "slide_show_duration") { gnum(&pres, "slide_show_duration") } else { 0.0 };
    if has(&pres, "transition") {
        song.transition = gnum(&pres, "transition.duration");
    }
    song.announcements = gnum(&pres, "content_destination") as i64 == 1;
    Ok(song)
}

// ------------------------------------------------------------------------------------------------------------------------------ writing
/// Give every `uuid` in a cloned message a new value, so two slides never share an identity.
fn fresh_uuids(m: &mut DynamicMessage) {
    let fields: Vec<_> = m.descriptor().fields().collect();
    for fd in fields {
        let Kind::Message(md) = fd.kind() else { continue };
        if md.is_map_entry() || !m.has_field(&fd) {
            continue;
        }
        let is_uuid = matches!(fd.name(), "uuid" | "id") && md.full_name() == "rv.data.UUID";
        let one = |sub: &mut DynamicMessage| {
            if is_uuid {
                let _ = sub.try_set_field_by_name("string", Pv::String(new_uuid()));
            } else {
                fresh_uuids(sub);
            }
        };
        match m.get_field_mut(&fd) {
            Pv::Message(sub) => one(sub),
            Pv::List(list) => {
                for item in list {
                    if let Pv::Message(sub) = item {
                        one(sub);
                    }
                }
            }
            _ => {}
        }
    }
}

fn slide_action(a: &SlideAction) -> Fallible<DynamicMessage> {
    let (type_name, msg_name) = action_kind(&a.kind).ok_or_else(|| FileError::new("ProPresenter's format has no such action."))?;
    let mut action = DynamicMessage::new(desc("rv.data.Action"));
    put_str(&mut action, "uuid.string", &new_uuid())?;
    put_bool(&mut action, "isEnabled", true)?;
    put_enum(&mut action, "type", type_name)?;
    let field = action.descriptor().fields().find(|fd| matches!(fd.kind(), Kind::Message(md) if md.full_name() == format!("rv.data.Action.{msg_name}"))).ok_or_else(|| FileError::new("ProPresenter's format has no such action."))?.name().to_owned();
    // touching the field makes the (empty) message present
    at_mut(&mut action, &field).ok_or_else(|| FileError::new("ProPresenter's format has no such action."))?;
    if a.kind == "stage" {
        let asg = add(&mut action, &format!("{field}.stage_screen_assignments"))?;
        put_str(asg, "screen.parameter_uuid.string", &if a.screen_uuid.is_empty() { new_uuid() } else { a.screen_uuid.clone() })?;
        put_str(asg, "screen.parameter_name", &a.screen)?;
        put_str(asg, "layout.parameter_uuid.string", &if a.uuid.is_empty() { new_uuid() } else { a.uuid.clone() })?;
        put_str(asg, "layout.parameter_name", &a.target)?;
    }
    let body_desc = action.descriptor().get_field_by_name(&field).and_then(|fd| if let Kind::Message(md) = fd.kind() { Some(md) } else { None });
    if let Some(md) = body_desc {
        if let Some(ident) = ["identification", "message_identificaton", "timer_identification"].into_iter().find(|i| md.get_field_by_name(i).is_some()) {
            put_str(&mut action, &format!("{field}.{ident}.parameter_uuid.string"), &if a.uuid.is_empty() { new_uuid() } else { a.uuid.clone() })?;
            put_str(&mut action, &format!("{field}.{ident}.parameter_name"), &a.target)?;
        }
    }
    if a.kind == "timer" {
        put_enum(&mut action, &format!("{field}.action_type"), &format!("ACTION_{}", if a.what.is_empty() { "start".to_owned() } else { a.what.to_uppercase() }))?;
        put_num(&mut action, &format!("{field}.increment_amount"), a.amount)?;
    }
    if a.kind == "clear" {
        put_enum(&mut action, &format!("{field}.target_layer"), &format!("CLEAR_TARGET_LAYER_{}", if a.what.is_empty() { "ALL".to_owned() } else { a.what.to_uppercase() }))?;
    }
    if a.kind == "prop" {
        at_mut(&mut action, &format!("{field}.trigger")).ok_or_else(|| FileError::new("ProPresenter's format has no such action."))?;
    }
    Ok(action)
}

fn blank_cue(slide: &Slide) -> Fallible<DynamicMessage> {
    let mut cue = DynamicMessage::decode(desc("rv.data.Cue"), LYRIC_SLIDE).map_err(|_| FileError::new("This install's slide template could not be read."))?;
    fresh_uuids(&mut cue);
    put_bool(&mut cue, "isEnabled", slide.enabled)?;
    if !slide.hot_key.is_empty() {
        set_hot_key(&mut cue, "hot_key", &slide.hot_key)?;
    }
    if slide.advance_after > 0.0 {
        put_num(&mut cue, "completion_action_type", 3.0)?;
        put_num(&mut cue, "completion_time", slide.advance_after)?;
        put_num(&mut cue, "completion_target_type", match slide.completion.as_str() {
            "first" => 4.0,
            "random" => 2.0,
            _ => 1.0,
        })?;
    } else if !slide.completion.is_empty() {
        return Err(FileError::new("A slide needs a time to go on to the next one."));
    }
    let actions = glist(&cue, "actions").len();
    let mut main_path = None;
    for ai in 0..actions {
        if !has(&cue, &format!("actions.{ai}.slide.presentation")) {
            continue;
        }
        let base = format!("actions.{ai}");
        if !slide.label.is_empty() {
            put_str(&mut cue, &format!("{base}.label.text"), &slide.label)?;
            if let Some(c) = slide.label_colour {
                set_colour(&mut cue, &format!("{base}.label.color"), c)?;
            }
        }
        if !slide.notes.is_empty() {
            put_bytes(&mut cue, &format!("{base}.slide.presentation.notes.rtf_data"), &plain_rtf(&slide.notes))?;
        }
        if slide.transition > 0.0 {
            put_num(&mut cue, &format!("{base}.slide.presentation.transition.duration"), slide.transition)?;
        }
        let els = glist(&cue, &format!("{base}.slide.presentation.base_slide.elements"));
        main_path = (0..els.len()).find(|ei| has(&cue, &format!("{base}.slide.presentation.base_slide.elements.{ei}.element.text"))).map(|ei| format!("{base}.slide.presentation.base_slide.elements.{ei}.element"));
    }
    if let Some(main) = main_path {
        let rtf = gbytes(&cue, &format!("{main}.text.rtf_data"));
        let template = if rtf.is_empty() { DEFAULT_RTF.to_vec() } else { rtf };
        put_bytes(&mut cue, &format!("{main}.text.rtf_data"), &text_to_rtf(&template, &slide.text))?;
        let len = slide.text.chars().count() as i64;
        for (pos, chord) in &slide.chords {
            if !(0..=len).contains(pos) {
                return Err(FileError::new("A chord sits outside the slide's text."));
            }
            let attr = add(&mut cue, &format!("{main}.text.attributes.custom_attributes"))?;
            put_num(attr, "range.start", *pos as f64)?;
            put_num(attr, "range.end", (*pos + 1).min(len) as f64)?;
            put_str(attr, "chord", chord)?;
        }
        if !slide.chords.is_empty() {
            put_bool(&mut cue, &format!("{main}.text.chord_pro.enabled"), true)?;
        }
        if !slide.translation.is_empty() {
            let mut second = peek(&cue, &main).and_then(|v| v.as_message().cloned()).ok_or_else(|| FileError::new("This install's slide template could not be read."))?;
            put_str(&mut second, "uuid.string", &new_uuid())?;
            at_mut(&mut second, "text.attributes").ok_or_else(|| FileError::new("This install's slide template could not be read."))?.clear_field_by_name("custom_attributes");
            put_bool(&mut second, "text.chord_pro.enabled", false)?;
            let smaller: Vec<u8> = String::from_utf8_lossy(&gbytes(&cue, &format!("{main}.text.rtf_data"))).replace("\\fs120", "\\fs72").chars().map(|c| u8::try_from(c as u32).unwrap_or(b'?')).collect();
            put_bytes(&mut second, "text.rtf_data", &text_to_rtf(&smaller, &slide.translation))?;
            let size = gnum(&cue, &format!("{main}.text.attributes.font.size"));
            put_num(&mut second, "text.attributes.font.size", (size * 0.6).max(1.0))?;
            let h = gnum(&cue, &format!("{main}.bounds.size.height"));
            let y = gnum(&cue, &format!("{main}.bounds.origin.y"));
            put_num(&mut cue, &format!("{main}.bounds.size.height"), h * 0.58)?;
            put_num(&mut second, "bounds.origin.y", y + h * 0.6)?;
            put_num(&mut second, "bounds.size.height", h * 0.4)?;
            let elements = add(&mut cue, "actions.0.slide.presentation.base_slide.elements")?;
            let slot = at_mut(elements, "element").ok_or_else(|| FileError::new("This install's slide template could not be read."))?;
            *slot = second;
        }
    }
    for act in &slide.actions {
        let built = slide_action(act)?;
        let target = add(&mut cue, "actions")?;
        *target = built;
    }
    Ok(cue)
}

/// A `.pro` file for `song` that ProPresenter opens. Needs a name and at least one slide.
pub fn build_song(song: &Song) -> Fallible<Vec<u8>> {
    if song.name.trim().is_empty() {
        return Err(FileError::new("A song needs a name."));
    }
    if !song.sections.iter().any(|s| !s.slides.is_empty()) {
        return Err(FileError::new("A song needs at least one slide."));
    }
    let mut pres = DynamicMessage::new(desc("rv.data.Presentation"));
    put_str(&mut pres, "uuid.string", &if song.uuid.is_empty() { new_uuid() } else { song.uuid.clone() })?;
    put_str(&mut pres, "name", song.name.trim())?;
    put_num(&mut pres, "application_info.platform", 1.0)?;
    put_num(&mut pres, "application_info.application", 1.0)?;
    put_num(&mut pres, "application_info.application_version.major_version", 22.0)?;
    put_num(&mut pres, "chord_chart.platform", 1.0)?;
    put_str(&mut pres, "category", &song.category)?;
    let mut group_ids: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for (si, sec) in song.sections.iter().enumerate() {
        let gid = group_ids.entry(sec.name.clone()).or_insert_with(new_uuid).clone();
        let mut cues = Vec::new();
        for slide in &sec.slides {
            let cue = blank_cue(slide)?;
            cues.push((gs(&cue, "uuid.string"), cue));
        }
        let cg = add(&mut pres, "cue_groups")?;
        put_str(cg, "group.uuid.string", &gid)?;
        put_str(cg, "group.name", &sec.name)?;
        set_colour(cg, "group.color", sec.colour.unwrap_or_else(|| section_colour(&sec.name)))?;
        if !sec.hot_key.is_empty() {
            set_hot_key(cg, "group.hotKey", &sec.hot_key)?;
        }
        for (id, _) in &cues {
            add_uuid(cg, "cue_identifiers", id)?;
        }
        let list = pres.get_field_by_name_mut("cues").and_then(Pv::as_list_mut).ok_or_else(|| FileError::new("ProPresenter's format has no such field."))?;
        list.extend(cues.into_iter().map(|(_, c)| Pv::Message(c)));
        let _ = si;
    }
    let mut arr_ids: std::collections::BTreeMap<String, String> = std::collections::BTreeMap::new();
    for (name, order) in &song.arrangements {
        let id = arr_ids.entry(name.clone()).or_insert_with(new_uuid).clone();
        let mut gids = Vec::new();
        for sec_name in order {
            gids.push(group_ids.get(sec_name).cloned().ok_or_else(|| FileError::new(format!("The arrangement '{name}' names a section the song does not have: {sec_name}.")))?);
        }
        let arr = add(&mut pres, "arrangements")?;
        put_str(arr, "uuid.string", &id)?;
        put_str(arr, "name", name)?;
        for g in gids {
            add_uuid(arr, "group_identifiers", &g)?;
        }
    }
    if !song.selected_arrangement.is_empty() {
        let id = arr_ids.get(&song.selected_arrangement).ok_or_else(|| FileError::new("The selected arrangement is not one of the song's arrangements."))?;
        put_str(&mut pres, "selected_arrangement.string", id)?;
    }
    if song.ccli_number != 0 || !song.author.is_empty() || !song.publisher.is_empty() || song.copyright_year != 0 || !song.album.is_empty() || !song.artist_credits.is_empty() {
        put_str(&mut pres, "ccli.song_title", song.name.trim())?;
        put_num(&mut pres, "ccli.song_number", song.ccli_number.max(0) as f64)?;
        put_str(&mut pres, "ccli.author", &song.author)?;
        put_str(&mut pres, "ccli.publisher", &song.publisher)?;
        put_num(&mut pres, "ccli.copyright_year", song.copyright_year.max(0) as f64)?;
        put_str(&mut pres, "ccli.album", &song.album)?;
        put_str(&mut pres, "ccli.artist_credits", &song.artist_credits)?;
        put_bool(&mut pres, "ccli.display", song.show_ccli)?;
    }
    if !song.notes.is_empty() {
        put_str(&mut pres, "notes", &song.notes)?;
    }
    for (label, attr) in [(&song.music_key, "user"), (&song.original_key, "original")] {
        if !label.is_empty() {
            let (key, scale) = key_to_proto(label).ok_or_else(|| FileError::new(format!("'{label}' is not a musical key Relay knows (try G, F#m or Bb).")))?;
            put_num(&mut pres, &format!("music.{attr}.music_key"), f64::from(key))?;
            put_num(&mut pres, &format!("music.{attr}.music_scale"), f64::from(scale))?;
        }
    }
    if !song.music_key.is_empty() {
        put_str(&mut pres, "music_key", &song.music_key)?;
    }
    if let Some(bg) = song.background {
        put_bool(&mut pres, "background.is_enabled", true)?;
        set_colour(&mut pres, "background.color", bg)?;
    }
    if !song.bible_reference.is_empty() {
        let bad = || FileError::new("A scripture reference looks like John 3:16 or Psalm 23.");
        let text = song.bible_reference.trim();
        let (book, rest) = text.rsplit_once(char::is_whitespace).ok_or_else(bad)?;
        let book = book.trim();
        let (chapter, verses) = match rest.split_once(':') {
            Some((c, v)) => (c, Some(v)),
            None => (rest, None),
        };
        let digits = |s: &str| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit());
        if book.is_empty() || !digits(chapter) {
            return Err(bad());
        }
        let (vs, ve) = match verses {
            None => (None, None),
            Some(v) => match v.split_once('-') {
                Some((a, b)) if digits(a) && digits(b) => (Some(a), Some(b)),
                None if digits(v) => (Some(v), None),
                _ => return Err(bad()),
            },
        };
        put_str(&mut pres, "bible_reference.book_name", book)?;
        let ch: f64 = chapter.parse().map_err(|_| bad())?;
        put_num(&mut pres, "bible_reference.chapter_range.start", ch)?;
        put_num(&mut pres, "bible_reference.chapter_range.end", ch)?;
        if let Some(v) = vs {
            let start: f64 = v.parse().map_err(|_| bad())?;
            let end: f64 = ve.unwrap_or(v).parse().map_err(|_| bad())?;
            put_num(&mut pres, "bible_reference.verse_range.start", start)?;
            put_num(&mut pres, "bible_reference.verse_range.end", end)?;
        }
    }
    if song.slide_show_seconds > 0.0 {
        put_num(&mut pres, "slide_show_duration", song.slide_show_seconds)?;
    }
    if song.transition > 0.0 {
        put_num(&mut pres, "transition.duration", song.transition)?;
    }
    if song.announcements {
        put_num(&mut pres, "content_destination", 1.0)?;
    }
    Ok(pres.encode_to_vec())
}
