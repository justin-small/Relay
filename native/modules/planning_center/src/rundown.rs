//! The rundown (#148): today's plan as a production team reads it, for /screen and the admin page (`rundown.py`).
//!
//! Built from what the follower already holds for a service type: the plan, its items, the LIVE state, and who is scheduled. Nothing here
//! calls Planning Center.
//!
//! * Clock times. Each item's planned start is worked out from the chosen service time and the lengths before it (pre-service items count
//!   back from the start, post-service items run on from the end). Once LIVE is running, items already played show when they really started,
//!   and everything after the current item is re-projected from the current item's actual start.
//! * People. Who is scheduled for that service time, on which team and position, and whether they confirmed.
//! * Channels. The operator adds channels (from a soundboard module that offers them, else by name) and assigns each to positions of one or
//!   more service types. Older per-position mic lists still count.
//! * Mics. A position with several mics and several confirmed people gives each person one of its mics, drawn at random once per plan and
//!   service time and then kept: the draw only changes when someone confirms or drops out, or when the operator presses Redraw.
//! * Song leader. From the item note the operator names (first line), else whoever holds the leader position.
//! * Sermon notes. The item note the operator names, from the current item or the next one that has it.
//!
//! Names, photos and notes are personal data: the rundown goes to /screen (the production team's display, by design, decided 2026-11-14)
//! and the admin page, never to the panels' Service feed or the audience caption pages.

use std::collections::{BTreeMap, BTreeSet};

use rand::seq::SliceRandom;
use rand::RngCore;
use relay_core::caps::{channel_id, parse_item, position_ref, team_ref, team_of, ChannelCatalog, Directory, PCO};
use relay_core::text::same_name;
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use crate::follower::Track;
use crate::plan::{self, f, i, s, text, Zone};

pub const MAX_MAPPINGS: usize = 64;
pub const MAX_MICS: usize = 24;
pub const MAX_CHANNELS: usize = 96;
pub const MAX_ALSO: usize = 7;
pub const MAX_ASSIGNMENTS: usize = 32;
pub const MAX_DRAWS: usize = 200;
pub const NOTE_MAX: usize = 4000;

/// What the rundown may ask of the rest of Relay: the directory and the channels. Either is `None` until Relay has one, and every rule here then
/// keeps what was saved.
#[derive(Clone, Copy, Default)]
pub struct Cx<'a> {
    pub directory: Option<&'a dyn Directory>,
    pub channels: Option<&'a dyn ChannelCatalog>,
}

/// `re.sub(r"\s+", " ", str(v or "")).strip()[:n]`.
pub fn text_n(v: &Value, n: usize) -> String {
    let t = match v {
        Value::Number(x) if x.as_f64() == Some(0.0) => String::new(),
        other => text(other),
    };
    relay_core::text::squeeze(&t).chars().take(n).collect()
}

/// A team or position ref as saved (#525), or `""` for anything that is not one.
fn saved_ref(raw: &Value, kind: &str) -> String {
    match raw.as_str().and_then(parse_item) {
        Some((k, provider, _)) if k == kind && provider == PCO => raw.as_str().unwrap_or("").to_owned(),
        _ => String::new(),
    }
}

fn label_char(c: char) -> bool {
    c.is_alphanumeric() || matches!(c, '_' | ' ' | '.' | '#' | '/' | '+' | '-')
}

/// `^[\w .#/+-]{1,12}$`.
pub fn label_ok(x: &str) -> bool {
    let n = x.chars().count();
    (1..=12).contains(&n) && x.chars().all(label_char)
}

/// `(team ref, team, position ref, position)` for a saved team and position. Reading settings keeps what was saved and never asks the
/// directory. With `live`, a ref is given the directory's current name, and a name with no ref is matched to one and stays a name when
/// nothing matches.
fn pick(entry: &Value, team: &str, pos: &str, live: Option<&Cx>) -> (String, String, String, String) {
    let mut tref = saved_ref(&entry["team_ref"], "team");
    let mut pref = saved_ref(&entry["position_ref"], "position");
    let (mut team, mut pos) = (team.to_owned(), pos.to_owned());
    let Some(cx) = live else { return (tref, team, pref, pos) };
    if pref.is_empty() && !pos.is_empty() {
        if let Some(d) = cx.directory {
            let found: Vec<_> = d.positions().into_iter().filter(|p| !p.missing && same_name(&p.name, &pos) && (team.is_empty() || same_name(&p.team, &team))).collect();
            if found.len() == 1 {
                pref = found[0].reference.clone();
            }
        }
    }
    if !pref.is_empty() && tref.is_empty() {
        tref = team_of(&pref).unwrap_or_default();
    }
    if !tref.is_empty() {
        team = describe(cx, &tref, &team).name;
    }
    if !pref.is_empty() {
        pos = describe(cx, &pref, &pos).name;
    }
    (tref, team, pref, pos)
}

/// How to show something saved as a ref plus a name (`directory.describe`).
pub fn describe(cx: &Cx, reference: &str, saved: &str) -> relay_core::caps::Described {
    match cx.directory {
        Some(d) => d.describe(reference, saved),
        None => relay_core::caps::Described { name: saved.to_owned(), missing: false, unmatched: reference.is_empty() && !saved.is_empty() },
    }
}

fn set_if_changed(entry: &mut Value, new: &[(&str, Value)]) -> bool {
    if new.iter().all(|(k, v)| entry.get(*k) == Some(v)) {
        return false;
    }
    for (k, v) in new {
        entry[*k] = v.clone();
    }
    true
}

fn refresh_entry(entry: &mut Value, cx: &Cx) -> bool {
    let (tref, team, pref, pos) = pick(entry, &s(entry, "team"), &s(entry, "position"), Some(cx));
    set_if_changed(entry, &[("team_ref", json!(tref)), ("team", json!(team)), ("position_ref", json!(pref)), ("position", json!(pos))])
}

/// Bring the saved refs and names of the mic mappings and channel assignments up to date with the directory: adopt a ref for a name that now
/// matches, and rewrite a name Planning Center renamed. True when anything changed.
pub fn refresh_saved(rs: &mut Value, cx: &Cx) -> bool {
    let mut changed = false;
    if let Some(mics) = rs.get_mut("mics").and_then(Value::as_array_mut) {
        for e in mics {
            changed |= refresh_entry(e, cx);
        }
    }
    if let Some(chs) = rs.get_mut("channels").and_then(Value::as_array_mut) {
        for c in chs {
            if let Some(ps) = c.get_mut("positions").and_then(Value::as_array_mut) {
                for e in ps {
                    changed |= refresh_entry(e, cx);
                }
            }
        }
    }
    changed = refresh_channels(rs, cx) || changed;
    // The song leader's position (#521): matched to a ref like the mic mappings, and renamed with Planning Center.
    let (_, _, pref, pos) = pick(&json!({"position_ref": rs["leader_position_ref"]}), "", &s(rs, "leader_position"), Some(cx));
    if (s(rs, "leader_position_ref"), s(rs, "leader_position")) != (pref.clone(), pos.clone()) {
        rs["leader_position_ref"] = json!(pref);
        rs["leader_position"] = json!(pos);
        changed = true;
    }
    changed
}

/// The ref of the one present core audio channel that has this name, or `""` when none or several do (#542).
fn match_channel(name: &str, cx: &Cx) -> String {
    let (false, Some(c)) = (name.is_empty(), cx.channels) else { return String::new() };
    let found: Vec<_> = c.audio().into_iter().filter(|c| !c.missing && same_name(&c.shown, name)).collect();
    if found.len() == 1 { found[0].reference.clone() } else { String::new() }
}

/// Bring each channel on the board, and each mic label, up to date with core channels (#542): a typed name that matches one present channel
/// adopts its ref once, and a linked channel takes its current name and number. A channel that is gone keeps its saved name and is flagged.
fn refresh_channels(rs: &mut Value, cx: &Cx) -> bool {
    let Some(catalog) = cx.channels else { return false };
    let mut changed = false;
    if let Some(chs) = rs.get_mut("channels").and_then(Value::as_array_mut) {
        for ch in chs {
            let mut got = catalog.by_ref(&s(ch, "channel_ref"));
            let reference = match &got {
                Some(g) => g.reference.clone(),
                None => match_channel(&s(ch, "name"), cx),
            };
            if got.is_none() {
                got = catalog.by_ref(&reference);
            }
            let mut new = vec![("channel_ref", json!(if got.is_some() { reference } else { String::new() }))];
            if let Some(g) = got.as_ref().filter(|g| !g.missing) {
                new.push(("name", json!(text_n(&json!(g.shown), 40))));
                if let Some(n) = &g.number {
                    new.push(("number", json!(text_n(&json!(n), 12))));
                }
            }
            changed |= set_if_changed(ch, &new);
        }
    }
    if let Some(mics) = rs.get_mut("mics").and_then(Value::as_array_mut) {
        for mp in mics {
            if !mp["mic_refs"].is_object() {
                mp["mic_refs"] = json!({});
            }
            let labels: Vec<String> = mp["mics"].as_array().into_iter().flatten().map(text).collect();
            for label in labels {
                let have = s(&mp["mic_refs"], &label);
                let got = catalog.by_ref(&have).or_else(|| catalog.by_ref(&match_channel(&label, cx)));
                if let Some(g) = got {
                    if have != g.reference {
                        mp["mic_refs"][label.as_str()] = json!(g.reference);
                        changed = true;
                    }
                }
            }
        }
    }
    changed
}

/// Whether a plan member holds the song leader's position: by Planning Center's ids when it was picked from the list (a rename there changes
/// nothing), else by the typed name.
pub fn is_leader(rs: &Value, member: &Value) -> bool {
    let reference = s(rs, "leader_position_ref");
    if !reference.is_empty() {
        return position_ref(&s(member, "team_id"), &s(member, "position_id")) == reference;
    }
    !s(rs, "leader_position").is_empty() && same_name(&s(member, "position"), &s(rs, "leader_position"))
}

fn default_rundown() -> Value {
    json!({"screen": false, "leader_note": "Leader", "leader_position": "Worship Leader", "leader_position_ref": "", "sermon_note": "Sermon Notes", "mics": [], "channels": [], "draws": {}})
}

/// The `rundown` settings under `planning_center` in config.json.
pub fn clean_rundown(raw: &Value) -> Value {
    let def = default_rundown();
    let pick_text = |key: &str, n: usize| text_n(raw.get(key).unwrap_or(&def[key]), n);
    let mut mics: Vec<Value> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for m in raw.get("mics").and_then(Value::as_array).into_iter().flatten() {
        if !m.is_object() || mics.len() >= MAX_MAPPINGS {
            continue;
        }
        let (tref, team, pref, pos) = pick(m, &text_n(&m["team"], 80), &text_n(&m["position"], 80), None);
        let mut labels: Vec<String> = Vec::new();
        for x in m.get("mics").and_then(Value::as_array).into_iter().flatten() {
            let x = text_n(x, 12);
            if label_ok(&x) && !labels.contains(&x) {
                labels.push(x);
            }
        }
        let key = if pref.is_empty() { format!("name:{team}\u{0}{pos}") } else { format!("ref:{pref}") };
        if pos.is_empty() || seen.contains(&key) || labels.is_empty() {
            continue;
        }
        seen.insert(key);
        labels.truncate(MAX_MICS);
        let mut refs = Map::new();
        for x in &labels {
            let r = m["mic_refs"].get(x.as_str()).and_then(Value::as_str).unwrap_or("");
            if channel_id(r).is_some() {
                refs.insert(x.clone(), json!(r));
            }
        }
        mics.push(json!({"team": team, "team_ref": tref, "position": pos, "position_ref": pref, "mics": labels, "sings": m["sings"] != Value::Bool(false),
                         "label": text_n(&m["label"], 24), "mic_refs": refs}));
    }
    let channels = clean_channels(raw.get("channels").unwrap_or(&Value::Null));
    let mut draws = Map::new();
    if let Some(src) = raw.get("draws").and_then(Value::as_object) {
        let skip = src.len().saturating_sub(MAX_DRAWS);
        for (k, v) in src.iter().skip(skip) {
            let (true, Some(v)) = (k.chars().count() < 240, v.as_object()) else { continue };
            let mut one = Map::new();
            for (a, b) in v.iter().take(MAX_MICS) {
                if !a.is_empty() && a.bytes().all(|c| c.is_ascii_digit()) {
                    one.insert(a.chars().take(20).collect(), json!(text_n(b, 12)));
                }
            }
            draws.insert(k.clone(), Value::Object(one));
        }
    }
    json!({
        "screen": raw.get("screen") == Some(&Value::Bool(true)),
        "leader_note": pick_text("leader_note", 60),
        "leader_position": pick_text("leader_position", 80),
        "leader_position_ref": saved_ref(raw.get("leader_position_ref").unwrap_or(&Value::Null), "position"),
        "sermon_note": pick_text("sermon_note", 60),
        "mics": mics,
        "channels": channels,
        "draws": draws,
    })
}

// -- channels ----------------------------------------------------------------------------------------------------------------------
/// A channel is one input on the soundboard that the operator put on the board. It is assigned to any number of positions, each on one service
/// type, so a mic shared between Sunday and Youth is set up once. The draw still decides which confirmed person holds which channel when a
/// position has several of each.
pub fn clean_channels(raw: &Value) -> Value {
    let mut out: Vec<Value> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    for c in raw.as_array().into_iter().flatten() {
        if !c.is_object() || out.len() >= MAX_CHANNELS {
            continue;
        }
        let (name, number) = (text_n(&c["name"], 40), text_n(&c["number"], 12));
        let cid = Some(text_n(&c["id"], 40)).filter(|t| !t.is_empty()).unwrap_or_else(|| format!("c{}", out.len() + 1));
        if !(!name.is_empty() || !number.is_empty()) || seen.contains(&cid) {
            continue;
        }
        seen.insert(cid.clone());
        let mut keys: BTreeSet<(String, String, String)> = BTreeSet::new();
        let mut positions: Vec<Value> = Vec::new();
        for a in c.get("positions").and_then(Value::as_array).into_iter().flatten() {
            if !a.is_object() || positions.len() >= MAX_ASSIGNMENTS {
                continue;
            }
            let st = text_n(&a["service_type"], 40);
            let (tref, team, pref, pos) = pick(a, &text_n(&a["team"], 80), &text_n(&a["position"], 80), None);
            let k = (st.clone(), if pref.is_empty() { team.clone() } else { pref.clone() }, pos.clone());
            if !st.is_empty() && !pos.is_empty() && keys.insert(k) {
                positions.push(json!({"service_type": st, "team": team, "team_ref": tref, "position": pos, "position_ref": pref}));
            }
        }
        let link = c.get("channel_ref").and_then(Value::as_str).filter(|r| channel_id(r).is_some()).unwrap_or("").to_owned();
        let mut also: Vec<String> = Vec::new();
        for r in c.get("also_refs").and_then(Value::as_array).into_iter().flatten() {
            if let Some(r) = r.as_str() {
                if r != link && !also.iter().any(|x| x == r) && channel_id(r).is_some() && also.len() < MAX_ALSO {
                    also.push(r.to_owned());
                }
            }
        }
        out.push(json!({"id": cid, "name": name, "number": number, "channel_ref": link, "also_refs": if link.is_empty() { Vec::new() } else { also }, "positions": positions}));
    }
    Value::Array(out)
}

/// The short mic label shown on /screen: the console number, else the first characters of the name.
pub fn channel_label(channel: &Value) -> String {
    for t in [s(channel, "number"), s(channel, "name")] {
        let cleaned: String = t.chars().filter(|c| label_char(*c)).collect();
        let label: String = cleaned.trim().chars().take(12).collect::<String>().trim().to_owned();
        if !label.is_empty() && label_ok(&label) {
            return label;
        }
    }
    String::new()
}

/// What a mic's holder's input channel is drawn from: one ref (an older per-position mic) or several (a board entry's).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Refs {
    One(String),
    Many(Vec<String>),
}

impl Refs {
    fn list(&self) -> Vec<String> {
        match self {
            Refs::One(r) if r.is_empty() => Vec::new(),
            Refs::One(r) => vec![r.clone()],
            Refs::Many(v) => v.clone(),
        }
    }
}

/// A position and the mics it draws from.
#[derive(Clone, Debug)]
pub struct Mapping {
    pub team: String,
    pub team_ref: String,
    pub position: String,
    pub position_ref: String,
    pub mics: Vec<String>,
    pub sings: bool,
    pub label: String,
    pub names: BTreeMap<String, String>,
    pub refs: BTreeMap<String, Refs>,
}

impl Mapping {
    fn key(&self) -> String {
        if self.position_ref.is_empty() { format!("{}|{}", self.team, self.position) } else { self.position_ref.clone() }
    }

    fn cover(&self) -> (String, String) {
        (if self.position_ref.is_empty() { self.team.clone() } else { self.position_ref.clone() }, self.position.clone())
    }
}

/// The position-to-mics mappings for one service type: the older per-position lists, plus one mapping per position that has channels assigned
/// (each channel contributing its label as a mic).
pub fn effective_mics(rs: &Value, service_type: &str) -> Vec<Mapping> {
    let mut out: Vec<Mapping> = rs["mics"]
        .as_array()
        .into_iter()
        .flatten()
        .map(|m| Mapping {
            team: s(m, "team"),
            team_ref: s(m, "team_ref"),
            position: s(m, "position"),
            position_ref: s(m, "position_ref"),
            mics: m["mics"].as_array().into_iter().flatten().map(text).collect(),
            sings: m["sings"] != Value::Bool(false),
            label: s(m, "label"),
            names: BTreeMap::new(),
            refs: m["mic_refs"].as_object().into_iter().flatten().map(|(k, v)| (k.clone(), Refs::One(text(v)))).collect(),
        })
        .collect();
    let mut order: Vec<(String, String)> = Vec::new();
    let mut by_position: BTreeMap<(String, String), Mapping> = BTreeMap::new();
    for ch in rs["channels"].as_array().into_iter().flatten() {
        let label = channel_label(ch);
        for a in ch["positions"].as_array().into_iter().flatten() {
            if s(a, "service_type") != service_type || label.is_empty() {
                continue;
            }
            let k = (if s(a, "position_ref").is_empty() { s(a, "team") } else { s(a, "position_ref") }, s(a, "position"));
            if !by_position.contains_key(&k) {
                order.push(k.clone());
            }
            let mp = by_position.entry(k).or_insert_with(|| Mapping {
                team: s(a, "team"),
                team_ref: s(a, "team_ref"),
                position: s(a, "position"),
                position_ref: s(a, "position_ref"),
                mics: Vec::new(),
                sings: true,
                label: String::new(),
                names: BTreeMap::new(),
                refs: BTreeMap::new(),
            });
            if !mp.mics.contains(&label) && mp.mics.len() < MAX_MICS {
                mp.mics.push(label.clone());
                mp.names.insert(label.clone(), s(ch, "name"));
                let refs: Vec<String> = std::iter::once(s(ch, "channel_ref")).chain(ch["also_refs"].as_array().into_iter().flatten().map(text)).filter(|r| !r.is_empty()).collect();
                mp.refs.insert(label.clone(), Refs::Many(refs));
            }
        }
    }
    let covered: BTreeSet<(String, String)> = out.iter().map(Mapping::cover).collect();
    for k in order {
        if !covered.contains(&k) {
            if let Some(m) = by_position.remove(&k) {
                out.push(m);
            }
        }
    }
    out
}

// -- service time ------------------------------------------------------------------------------------------------------------------
/// The plan's service times with the clock time of each in `zone`.
pub fn service_times(plan: Option<&Value>, zone: &Zone) -> Vec<Value> {
    let empty = json!({});
    plan::service_times(plan.unwrap_or(&empty))
        .into_iter()
        .map(|t| {
            let at = f(&t, "starts_at").unwrap_or(0.0);
            json!({"id": t["id"], "starts_at": t["starts_at"], "ends_at": t["ends_at"], "hhmm": zone.hhmm(at), "name": t["name"]})
        })
        .collect()
}

/// The service time the rundown is for: the one asked for (HHMM, local), else the one LIVE is running, else the first not yet over, else the last.
pub fn pick_time(plan: Option<&Value>, live: Option<&Value>, now: f64, zone: &Zone, want: &str) -> Option<Value> {
    let times = service_times(plan, zone);
    if times.is_empty() {
        return None;
    }
    if !want.is_empty() {
        if let Some(hit) = times.iter().find(|t| t["hhmm"] == want) {
            return Some(hit.clone());
        }
    }
    if let Some(cur) = live.map(|l| &l["current"]).filter(|c| c.is_object()) {
        if plan::running(live, now) {
            if let Some(hit) = times.iter().find(|t| t["id"] == cur["plan_time"]) {
                return Some(hit.clone());
            }
        }
    }
    for t in &times {
        let start = f(t, "starts_at").unwrap_or(0.0);
        let end = f(t, "ends_at").filter(|e| *e > start).unwrap_or(start + plan::SERVICE_DEFAULT_S);
        if end > now {
            return Some(t.clone());
        }
    }
    times.last().cloned()
}

// -- people and mics ---------------------------------------------------------------------------------------------------------------
fn serves(member: &Value, time_id: &str) -> bool {
    match member["times"].as_array() {
        None => true,
        Some(ts) => ts.iter().any(|t| t == time_id),
    }
}

/// Whether a plan member holds the mapped position: by Planning Center's ids when the mapping has them (so a rename there changes nothing),
/// else by name.
fn matches(mapping: &Mapping, member: &Value) -> bool {
    if !mapping.position_ref.is_empty() {
        return position_ref(&s(member, "team_id"), &s(member, "position_id")) == mapping.position_ref;
    }
    same_name(&s(member, "position"), &mapping.position) && (mapping.team.is_empty() || same_name(&s(member, "team"), &mapping.team))
}

/// Mic per confirmed person for one position. Keeps every earlier draw that still holds and draws only for people without a mic; extras get
/// none. Returns `(member id, mic)` pairs.
pub fn draw_mics(mapping: &Mapping, people: &[&Value], kept: &Value, rng: &mut dyn RngCore) -> Vec<(String, String)> {
    let mut ids: Vec<String> = people.iter().filter(|p| p["status"] == "confirmed").map(|p| s(p, "id")).collect();
    ids.sort();
    let mut out: Vec<(String, String)> = kept
        .as_object()
        .into_iter()
        .flatten()
        .filter(|(i, m)| ids.contains(i) && m.as_str().is_some_and(|m| mapping.mics.iter().any(|x| x == m)))
        .map(|(i, m)| (i.clone(), text(m)))
        .collect();
    let distinct: BTreeSet<&String> = out.iter().map(|(_, m)| m).collect();
    if distinct.len() != out.len() {
        out.clear();
    }
    let mut free: Vec<String> = mapping.mics.iter().filter(|m| !out.iter().any(|(_, o)| o == *m)).cloned().collect();
    free.shuffle(rng);
    for i in ids {
        if !out.iter().any(|(o, _)| *o == i) {
            if let Some(m) = free.pop() {
                out.push((i, m));
            }
        }
    }
    out
}

pub fn draw_key(plan_id: &str, time_id: &str, mapping: &Mapping) -> String {
    format!("{plan_id}:{time_id}:{}", mapping.key())
}

/// The one input channel a mic's holder is on. A board entry may use several input channels (#546): the holder gets one at random, drawn from
/// `seed` (the draw, the mic and the person) so it stays put for the service time and only changes when someone else holds the mic.
pub fn pick_ref(refs: &Refs, seed: &str) -> String {
    let list = refs.list();
    match list.len() {
        0 => String::new(),
        1 => list[0].clone(),
        n => {
            if let Refs::One(r) = refs {
                return r.clone();
            }
            let digest = Sha1::digest(seed.as_bytes());
            let n64 = u64::from_be_bytes([digest[0], digest[1], digest[2], digest[3], digest[4], digest[5], digest[6], digest[7]]);
            list[(n64 % n as u64) as usize].clone()
        }
    }
}

// -- the rundown -------------------------------------------------------------------------------------------------------------------
fn note(item: &Value, category: &str) -> String {
    if category.is_empty() {
        return String::new();
    }
    for n in item["notes"].as_array().into_iter().flatten() {
        if same_name(&s(n, "category"), category) && !s(n, "content").trim().is_empty() {
            return s(n, "content").trim().to_owned();
        }
    }
    String::new()
}

fn item_time_of<'a>(item: &'a Value, time_id: &str) -> Option<&'a Value> {
    item["times"].as_array()?.iter().find(|t| t.is_object() && t["plan_time"] == time_id)
}

fn mic_sort(mic: Option<&str>) -> (u8, i64, String) {
    let m = mic.unwrap_or("");
    let digits: String = m.chars().skip_while(|c| !c.is_ascii_digit()).take_while(char::is_ascii_digit).collect();
    match digits.parse::<i64>() {
        Ok(n) => (0, n, m.to_owned()),
        Err(_) => (1, 0, m.to_owned()),
    }
}

fn empty_rundown(st: &str, now: f64, zone: &Zone, track: &Track) -> Value {
    json!({"type": "rundown", "service_type": st, "now": now, "zone": zone.name, "plan": null, "items": [], "times": [], "time": null, "header": null,
           "people": [], "mics": [], "notes": null,
           "live": track.live.as_ref().is_some_and(|l| !l["controller"].is_null()),
           "controller": track.live.as_ref().and_then(|l| l["controller"]["name"].as_str().map(str::to_owned))})
}

/// The rundown for one followed service type, and the mic draws as they now stand (the caller saves them when they changed).
pub fn build(track: &Track, settings: &Value, now: f64, zone: &Zone, want_time: &str, rng: &mut dyn RngCore) -> (Value, Value) {
    let rs = &settings["rundown"];
    let (plan, live) = (track.plan.as_ref(), track.live.as_ref());
    let mut out = empty_rundown(&track.st, now, zone, track);
    let mut draws: Map<String, Value> = rs["draws"].as_object().cloned().unwrap_or_default();
    let Some(plan) = plan else { return (out, Value::Object(draws)) };
    let title = [s(plan, "title"), s(plan, "series_title")].into_iter().find(|t| !t.is_empty()).unwrap_or_else(|| "Today's plan".to_owned());
    out["plan"] = json!({"id": plan["id"], "title": title, "dates": plan["dates"]});
    out["times"] = Value::Array(service_times(Some(plan), zone).iter().map(|t| json!({"id": t["id"], "hhmm": t["hhmm"], "starts_at": t["starts_at"]})).collect());
    let Some(pt) = pick_time(Some(plan), live, now, zone, want_time) else { return (out, Value::Object(draws)) };
    out["time"] = json!({"id": pt["id"], "hhmm": pt["hhmm"], "starts_at": pt["starts_at"], "chosen": !want_time.is_empty()});
    let tid = s(&pt, "id");
    let pt_start = f(&pt, "starts_at").unwrap_or(0.0);

    // Items for this service time, in order (an item excluded from this time is left out).
    let mut sorted: Vec<&Value> = track.items.iter().collect();
    sorted.sort_by_key(|i| plan::i(i, "sequence"));
    let items: Vec<&Value> = sorted.into_iter().filter(|it| !item_time_of(it, &tid).is_some_and(|t| t["exclude"] == Value::Bool(true))).collect();
    let cur_live = live.map(|l| &l["current"]).filter(|c| c.is_object());
    let running = cur_live.is_some_and(|c| plan::running(live, now) && c["plan_time"] == tid.as_str());
    let cur_id: Option<String> = if running { cur_live.map(|c| s(c, "item")) } else { None };

    // Planned starts.
    let len = |it: &Value| f(it, "length").unwrap_or(0.0);
    let pre: Vec<&Value> = items.iter().copied().filter(|i| i["position"] == "pre").collect();
    let during: Vec<&Value> = items.iter().copied().filter(|i| i["position"] == "during").collect();
    let post: Vec<&Value> = items.iter().copied().filter(|i| i["position"] == "post").collect();
    let mut planned: BTreeMap<String, f64> = BTreeMap::new();
    let mut t = pt_start - pre.iter().map(|i| len(i)).sum::<f64>();
    for it in &pre {
        planned.insert(s(it, "id"), t);
        t += len(it);
    }
    t = pt_start;
    for it in &during {
        planned.insert(s(it, "id"), t);
        t += len(it);
    }
    let planned_end = t;
    for it in &post {
        planned.insert(s(it, "id"), t);
        t += len(it);
    }
    let order: Vec<&Value> = pre.iter().chain(during.iter()).chain(post.iter()).copied().collect();

    // Actual starts so far, and the projection from the current item on.
    let ix = cur_id.as_ref().and_then(|c| order.iter().position(|i| s(i, "id") == *c));
    let mut projected: BTreeMap<String, f64> = BTreeMap::new();
    let live_start = cur_live.and_then(|c| f(c, "live_start_at")).unwrap_or(0.0);
    if let Some(ix) = ix {
        let mut t = (live_start + len(order[ix])).max(now);
        for it in &order[ix + 1..] {
            projected.insert(s(it, "id"), t);
            t += len(it);
        }
    }
    let mut proj_end = planned_end;
    if ix.is_some() {
        if let Some(last) = order.iter().rev().find(|i| i["position"] == "during") {
            let lid = s(last, "id");
            if let Some(p) = projected.get(&lid) {
                proj_end = p + len(last);
            } else if Some(&lid) == cur_id.as_ref() {
                proj_end = (live_start + len(last)).max(now);
            }
        }
    }

    // People serving this time.
    let members: Vec<&Value> = track.team.iter().filter(|m| serves(m, &tid)).collect();
    let mut mic_of: BTreeMap<String, String> = BTreeMap::new();
    let mut mapping_of: BTreeMap<String, usize> = BTreeMap::new();
    let mappings = effective_mics(rs, &track.st);
    for (n, mp) in mappings.iter().enumerate() {
        let people: Vec<&Value> = members.iter().copied().filter(|m| matches(mp, m)).collect();
        let key = draw_key(&s(plan, "id"), &tid, mp);
        let got = draw_mics(mp, &people, draws.get(&key).unwrap_or(&Value::Null), rng);
        if !got.is_empty() {
            draws.insert(key, Value::Object(got.iter().map(|(a, b)| (a.clone(), json!(b))).collect()));
        } else {
            draws.retain(|k, _| *k != key);
        }
        for (mid, mic) in got {
            mic_of.insert(mid.clone(), mic);
            mapping_of.insert(mid, n);
        }
    }
    // Keep only the newest draws.
    if draws.len() > MAX_DRAWS {
        let skip = draws.len() - MAX_DRAWS;
        draws = draws.into_iter().skip(skip).collect();
    }

    let leader_default = members.iter().find(|m| is_leader(rs, m) && m["status"] != "declined").copied();
    let person = |m: &Value| json!({"id": m["id"], "name": m["name"], "status": m["status"], "photo": m["photo"], "team": m["team"], "position": m["position"], "mic": mic_of.get(&s(m, "id"))});

    let mut singers: Vec<Value> = members.iter().filter(|m| mic_of.contains_key(&s(m, "id")) && mappings[mapping_of[&s(m, "id")]].sings).map(|m| person(m)).collect();
    singers.sort_by_key(|p| mic_sort(p["mic"].as_str()));

    let song = |item: &Value| -> Value {
        let lead: String = note(item, &s(rs, "leader_note")).lines().next().unwrap_or("").trim().chars().take(60).collect();
        let lead = if lead.is_empty() { leader_default.map(|m| s(m, "name")).unwrap_or_default() } else { lead };
        json!({"key": item["key"], "leader": lead, "mics": singers.iter().map(|p| json!({"mic": p["mic"], "name": p["name"]})).collect::<Vec<_>>()})
    };

    let mut rows = Vec::new();
    for (n, it) in order.iter().enumerate() {
        let itime = item_time_of(it, &tid);
        let live_end = itime.and_then(|t| f(t, "live_end_at"));
        let state = if ix.is_some_and(|x| n < x) {
            "done"
        } else if Some(n) == ix {
            "current"
        } else if ix.is_some_and(|x| n == x + 1) {
            "next"
        } else if ix.is_none() && live_end.is_some_and(|v| v != 0.0) {
            "done"
        } else {
            ""
        };
        let id = s(it, "id");
        rows.push(json!({
            "id": id, "title": it["title"], "kind": it["type"], "section": it["position"],
            "length": it["length"], "planned": planned.get(&id),
            "actual": if state == "done" || state == "current" { itime.and_then(|t| t.get("live_start_at")).cloned().unwrap_or(Value::Null) } else { Value::Null },
            "projected": projected.get(&id), "state": state,
            "song": if it["type"] == "song" { song(it) } else { Value::Null },
        }));
    }
    out["items"] = Value::Array(rows);

    // Header: what is up, how long it has left, how the service is running.
    let cur_item = ix.map(|x| order[x]);
    let nxt = ix.and_then(|x| order.get(x + 1).copied());
    let timers = if running { plan::timers(Some(plan), &track.items, live, now, track.service_start.get(&tid).copied()) } else { json!({}) };
    out["header"] = json!({
        "current": cur_item.map(|c| c["title"].clone()),
        "kind": cur_item.map(|c| c["type"].clone()),
        "song": cur_item.filter(|c| c["type"] == "song").map(&song),
        "item_started_at": if running { json!(live_start) } else { Value::Null },
        "item_length": cur_item.map(|c| c["length"].clone()),
        "next": nxt.map(|n| n["title"].clone()),
        "planned_end": planned_end,
        "projected_end": proj_end,
        "over_s": if running { timers["service_over_s"].clone() } else { Value::Null },
    });

    // Mic channels, in mic order: who holds each mic, and whether it is in use for the item that is up.
    let mut channels: Vec<Value> = Vec::new();
    for mp in &mappings {
        let dk = draw_key(&s(plan, "id"), &tid, mp);
        let holders: BTreeMap<String, String> = draws.get(&dk).and_then(Value::as_object).into_iter().flatten().map(|(mid, mic)| (text(mic), mid.clone())).collect();
        for mic in &mp.mics {
            let m = holders.get(mic).and_then(|hid| members.iter().find(|x| s(x, "id") == *hid)).copied();
            let on = cur_item.is_some_and(|c| m.is_some() && ((c["type"] == "song") == mp.sings));
            // channel_ref names the core input channel (#542), so a soundboard module can show the holder's name on it.
            let refs = mp.refs.get(mic).cloned().unwrap_or(Refs::One(String::new()));
            let seed = format!("{dk}:{mic}:{}", m.map(|x| s(x, "id")).unwrap_or_default());
            channels.push(json!({
                "mic": mic, "label": mp.names.get(mic).filter(|n| !n.is_empty()).cloned().unwrap_or_else(|| mp.label.clone()), "position": mp.position, "team": mp.team,
                "channel_ref": pick_ref(&refs, &seed), "channel_refs": refs.list(),
                "person": m.map(person), "on": on,
            }));
        }
    }
    channels.sort_by_key(|c| mic_sort(c["mic"].as_str()));
    out["mics"] = Value::Array(channels);

    // The roster, grouped by team in Planning Center's order, with positions still to fill.
    let mut people: Vec<Value> = members.iter().map(|m| person(m)).collect();
    for need in &track.needed {
        if need["time"].is_null() || need["time"] == tid.as_str() {
            for _ in 0..i(need, "quantity") {
                people.push(json!({"id": "", "name": "", "status": "unfilled", "photo": "", "team": need["team"], "position": need["position"], "mic": null}));
            }
        }
    }
    let singing: BTreeSet<String> = if cur_item.is_some_and(|c| c["type"] == "song") { singers.iter().map(|p| s(p, "id")).collect() } else { BTreeSet::new() };
    for p in &mut people {
        let sing = singing.contains(&s(p, "id"));
        p["singing"] = json!(sing);
    }
    out["people"] = Value::Array(people);

    // Sermon notes: from the current item, else the next one that has them.
    for it in &order[ix.unwrap_or(0)..] {
        let body = note(it, &s(rs, "sermon_note"));
        if !body.is_empty() {
            let id = s(it, "id");
            out["notes"] = json!({"item": it["title"], "planned": planned.get(&id), "projected": projected.get(&id), "text": body.chars().take(NOTE_MAX).collect::<String>()});
            break;
        }
    }
    (out, Value::Object(draws))
}

/// `(team, position, team ref, position ref)` of a plan row, `""` for what Planning Center gave no id for.
fn refs_of(m: &Value) -> (String, String) {
    let (tid, pid) = (s(m, "team_id"), s(m, "position_id"));
    (if tid.is_empty() { String::new() } else { team_ref(&tid) }, if tid.is_empty() || pid.is_empty() { String::new() } else { position_ref(&tid, &pid) })
}

fn rows_of(tr: &Track) -> impl Iterator<Item = &Value> {
    tr.team.iter().chain(tr.needed.iter()).filter(|m| !s(m, "position").is_empty())
}

/// Every team and position on today's plans (scheduled or still to fill), for the mic map in admin.
pub fn positions(tracks: &[Track]) -> Value {
    let mut seen: BTreeSet<(String, String, String, String)> = BTreeSet::new();
    for tr in tracks {
        for m in rows_of(tr) {
            let (t, p) = refs_of(m);
            seen.insert((s(m, "team"), s(m, "position"), t, p));
        }
    }
    Value::Array(seen.into_iter().map(|(t, p, tr, pr)| json!({"team": t, "position": p, "team_ref": tr, "position_ref": pr})).collect())
}

/// Every team and position on today's plans with its service type, for assigning channels in admin.
pub fn type_positions(tracks: &[Track]) -> Value {
    let mut seen: BTreeSet<(String, String, String, String, String)> = BTreeSet::new();
    for tr in tracks {
        for m in rows_of(tr) {
            let (t, p) = refs_of(m);
            seen.insert((tr.st.clone(), s(m, "team"), s(m, "position"), t, p));
        }
    }
    Value::Array(seen.into_iter().map(|(st, t, p, tr, pr)| json!({"service_type": st, "team": t, "position": p, "team_ref": tr, "position_ref": pr})).collect())
}

/// What changed, ignoring the clock.
pub fn signature(msg: &Value) -> String {
    let mut m = msg.clone();
    if let Some(o) = m.as_object_mut() {
        o.remove("now");
    }
    m.to_string()
}

/// A random generator for the draws.
pub fn rng() -> rand::rngs::ThreadRng {
    rand::thread_rng()
}
