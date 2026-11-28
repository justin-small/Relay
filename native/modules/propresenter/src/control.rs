//! ProPresenter control (#138, stage 2; `ppcontrol.py`): next/previous, the slide grid and its thumbnails, the clear dock, playlists, macros, looks,
//! timers, messages and the media transport.
//!
//! Everything goes through [`Client`] (reached as the follower's client), so view-only mode refuses every write there before a socket is opened, and
//! the bulk timer calls are never sent (`Client::action` refuses them). This decides WHICH calls to make and shapes what comes back; it has no HTTP of
//! its own. A caller catches [`ControlError`] and answers a fixed sentence by its kind.

use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, PoisonError};

use serde_json::{json, Value};
use sha1::{Digest, Sha1};
use tokio::sync::Semaphore;

use crate::client::{cue_list, hex_color, text, Client, Kind, ProError};
use crate::follower::Follower;
use crate::settings::id_ok;

pub const LAYERS: [&str; 7] = ["slide", "media", "props", "messages", "audio", "video_input", "announcements"];
pub const TIMER_OPS: [&str; 3] = ["start", "stop", "reset"];
pub const THUMB_QUALITY: i64 = 320;
/// The slide view draws the live and next slide large: the longest side in pixels.
pub const LIVE_QUALITY: i64 = 960;
pub const THUMB_MAX_ENTRIES: usize = 600;
pub const THUMB_MAX_BYTES: usize = 48 * 1024 * 1024;
/// ProPresenter can stall: a page of thumbnails must not tie up every worker.
pub const THUMB_PARALLEL: usize = 3;
pub const MAX_INDEX: i64 = 5000;
/// Presentations looked up when checking a playlist (3 at a time).
pub const MAX_DIAG_CHECKS: usize = 150;
pub const TOKEN_TEXT_MAX: usize = 500;

/// Why a control call did not happen.
#[derive(Debug, Clone)]
pub enum ControlError {
    /// ProPresenter's side: answered by [`ProError::operator_message`].
    Pro(ProError),
    /// A bad argument.
    Invalid,
    /// A macro outside the operator-safe selection.
    Forbidden,
    /// No playlist is active or focused.
    NoPlaylist,
}

impl From<ProError> for ControlError {
    fn from(e: ProError) -> Self {
        Self::Pro(e)
    }
}

pub type Done<T = Option<Value>> = Result<T, ControlError>;

/// One path segment: a UUID, a name or an index. Refuses anything that could change the path.
fn seg(value: &str) -> Result<String, ControlError> {
    let t = value.trim();
    if id_ok(t) {
        Ok(t.to_owned())
    } else {
        Err(ControlError::Invalid)
    }
}

fn as_text(v: &Value) -> String {
    match v {
        Value::String(t) => t.clone(),
        Value::Null => String::new(),
        other => other.to_string(),
    }
}

pub fn seg_of(v: &Value) -> Result<String, ControlError> {
    seg(&as_text(v))
}

/// A whole number from 0 to `MAX_INDEX` (Python's `int(value)`: a number, or text holding one; never a bool).
pub fn index_of(v: &Value) -> Result<i64, ControlError> {
    let n = match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().filter(|f| f.is_finite()).map(|f| f as i64)),
        Value::String(t) => t.trim().parse::<i64>().ok(),
        _ => None,
    };
    n.filter(|n| (0..=MAX_INDEX).contains(n)).ok_or(ControlError::Invalid)
}

fn rev(parts: &Value) -> String {
    let mut h = Sha1::new();
    h.update(parts.to_string().as_bytes());
    h.finalize().iter().take(5).map(|b| format!("{b:02x}")).collect()
}

pub fn rev_of(parts: &Value) -> String {
    rev(parts)
}

fn id_row(obj: &Value) -> Value {
    let i = if obj["id"].is_object() { &obj["id"] } else { obj };
    let i = if i.is_object() { i } else { &Value::Null };
    json!({"uuid": text(i, "uuid"), "name": text(i, "name").trim(), "index": i["index"]})
}

fn truthy(v: &Value) -> bool {
    crate::settings::truthy(v)
}

#[derive(Default)]
struct Thumbs {
    map: HashMap<String, (String, Vec<u8>)>,
    order: VecDeque<String>,
    bytes: usize,
}

pub struct Control {
    f: Arc<Follower>,
    thumbs: Mutex<Thumbs>,
    sem: Semaphore,
    /// The playlist an operator last triggered an item of through Relay. ProPresenter 22 leaves `playlist/active` empty after an API trigger (and
    /// `playlist/focused` names no playlist), so without this Next item would always say no playlist is active right after Relay itself started one.
    last_playlist: Mutex<Option<String>>,
    pub thumb_hits: AtomicU64,
    pub thumb_fetches: AtomicU64,
}

impl Control {
    pub fn new(f: Arc<Follower>) -> Arc<Self> {
        Arc::new(Self {
            f,
            thumbs: Mutex::new(Thumbs::default()),
            sem: Semaphore::new(THUMB_PARALLEL),
            last_playlist: Mutex::new(None),
            thumb_hits: AtomicU64::new(0),
            thumb_fetches: AtomicU64::new(0),
        })
    }

    pub fn follower(&self) -> &Arc<Follower> {
        &self.f
    }

    // -- plumbing ------------------------------------------------------------------------------------------------------------
    fn client(&self) -> Result<Client, ProError> {
        self.f.need_client()
    }

    async fn get(&self, path: &str) -> Result<Value, ProError> {
        self.client()?.read(path).await
    }

    async fn act(&self, path: &str) -> Result<(), ProError> {
        self.client()?.action(path).await.map(|_| ())
    }

    // -- slides --------------------------------------------------------------------------------------------------------------
    /// The cues of a presentation (the active one by default): group name and colour, text, and a `rev` that changes when the slides do, which keys
    /// the thumbnail cache.
    pub async fn slides(&self, uuid: Option<&str>) -> Done<Value> {
        let uuid = match uuid.filter(|u| !u.is_empty()) {
            Some(u) => u.to_owned(),
            None => match self.f.presentation() {
                Some(p) => p.uuid,
                None => return Ok(json!({"uuid": "", "name": "", "rev": "", "cues": []})),
            },
        };
        let body = self.get(&format!("/v1/presentation/{}", seg(&uuid)?)).await?;
        let detail = if body.get("presentation").is_some() { body["presentation"].clone() } else { body };
        let detail = if detail.is_object() { detail } else { json!({}) };
        let cues: Vec<Value> = cue_list(&detail, None).into_iter().map(without_notes).collect();
        Ok(json!({"uuid": uuid, "name": id_row(&detail)["name"], "rev": rev(&json!(cues)), "cues": cues}))
    }

    /// A slide's thumbnail, from the cache when it has been fetched for this presentation, index, change marker and size. Browsers never see
    /// ProPresenter: they ask Relay, and Relay asks once.
    pub async fn thumbnail(&self, uuid: &str, index: &Value, marker: &str, quality: i64) -> Done<(String, Vec<u8>)> {
        let n = index_of(index)?;
        if !(64..=2048).contains(&quality) {
            return Err(ControlError::Invalid);
        }
        let marker: String = marker.chars().take(32).collect();
        let key = if quality == THUMB_QUALITY { format!("{uuid}\x1f{n}\x1f{marker}") } else { format!("{uuid}\x1f{n}\x1f{marker}\x1f{quality}") };
        self.cached(key, format!("/v1/presentation/{}/thumbnail/{n}?quality={quality}&thumbnail_type=jpeg", seg(uuid)?)).await
    }

    /// A cue of a playlist item. ProPresenter draws it for the item's own arrangement, so a chorus that the arrangement repeats has a thumbnail at
    /// each place. Same cache and same rule as presentation thumbnails. `quality` is the longest side in pixels (signage asks for a large one).
    pub async fn playlist_thumbnail(&self, playlist: &str, item: &Value, cue: &Value, marker: &str, quality: i64) -> Done<(String, Vec<u8>)> {
        let (i, c) = (index_of(item)?, index_of(cue)?);
        if !(64..=2048).contains(&quality) {
            return Err(ControlError::Invalid);
        }
        let marker: String = marker.chars().take(32).collect();
        let key = format!("playlist\x1f{playlist}\x1f{i}\x1f{c}\x1f{marker}");
        let key = if quality == THUMB_QUALITY { key } else { format!("{key}\x1f{quality}") };
        self.cached(key, format!("/v1/playlist/{}/{i}/thumbnail/{c}?quality={quality}&thumbnail_type=jpeg", seg(playlist)?)).await
    }

    fn hit(&self, key: &str) -> Option<(String, Vec<u8>)> {
        let mut t = self.thumbs.lock().unwrap_or_else(PoisonError::into_inner);
        let hit = t.map.get(key).cloned()?;
        t.order.retain(|k| k != key);
        t.order.push_back(key.to_owned());
        self.thumb_hits.fetch_add(1, Ordering::SeqCst);
        Some(hit)
    }

    async fn cached(&self, key: String, path: String) -> Done<(String, Vec<u8>)> {
        if let Some(hit) = self.hit(&key) {
            return Ok(hit);
        }
        let got = {
            let _permit = self.sem.acquire().await;
            if let Some(hit) = self.hit(&key) {
                return Ok(hit); // another request may have fetched it while this one waited
            }
            let got = self.client()?.read_image(&path).await?;
            self.thumb_fetches.fetch_add(1, Ordering::SeqCst);
            got
        };
        if !got.0.starts_with("image/") {
            return Err(ControlError::Pro(ProError::new(Kind::Bad, "ProPresenter sent something that is not an image.")));
        }
        let mut t = self.thumbs.lock().unwrap_or_else(PoisonError::into_inner);
        t.bytes += got.1.len();
        t.map.insert(key.clone(), got.clone());
        t.order.retain(|k| *k != key);
        t.order.push_back(key);
        while !t.order.is_empty() && (t.map.len() > THUMB_MAX_ENTRIES || t.bytes > THUMB_MAX_BYTES) {
            if let Some(old) = t.order.pop_front() {
                if let Some((_, data)) = t.map.remove(&old) {
                    t.bytes -= data.len();
                }
            }
        }
        Ok(got)
    }

    pub async fn step(&self, direction: &str) -> Done {
        if !matches!(direction, "next" | "previous") {
            return Err(ControlError::Invalid);
        }
        self.act(&format!("/v1/trigger/{direction}")).await?;
        Ok(None)
    }

    /// The next or previous ITEM of the active playlist, where `step` moves a slide. Not `presentation/active/next/trigger`: that is the next CUE (tried
    /// live, it moved a slide). The active playlist is the one ProPresenter reports in `playlist/active`; without one, the playlist Relay last triggered an
    /// item of; without that, the focused playlist.
    pub async fn step_item(&self, direction: &str) -> Done {
        if !matches!(direction, "next" | "previous") {
            return Err(ControlError::Invalid);
        }
        let active = self.f.data("playlist/active");
        let from_pro = active["presentation"]["playlist"]["uuid"].as_str().filter(|u| !u.is_empty()).map(str::to_owned);
        let uuid = from_pro.or_else(|| self.last_playlist.lock().unwrap_or_else(PoisonError::into_inner).clone());
        match uuid {
            Some(u) => self.act(&format!("/v1/playlist/{}/{direction}/trigger", seg(&u)?)).await?,
            None => match self.act(&format!("/v1/playlist/focused/{direction}/trigger")).await {
                Err(e) if e.status == 404 => return Err(ControlError::NoPlaylist), // ProPresenter answers 404 when no playlist is focused
                other => other?,
            },
        }
        Ok(None)
    }

    pub async fn next_presentation(&self) -> Done {
        self.step_item("next").await
    }

    pub async fn trigger_slide(&self, uuid: &Value, index: &Value) -> Done {
        let (u, n) = (seg_of(uuid)?, index_of(index)?);
        self.act(&format!("/v1/presentation/{u}/{n}/trigger")).await?;
        Ok(None)
    }

    pub async fn clear(&self, layer: &str) -> Done {
        if !LAYERS.contains(&layer) {
            return Err(ControlError::Invalid);
        }
        self.act(&format!("/v1/clear/layer/{layer}")).await?;
        Ok(None)
    }

    // -- playlists -----------------------------------------------------------------------------------------------------------
    pub async fn playlists(&self) -> Done<Value> {
        fn tree(items: &Value) -> Vec<Value> {
            items
                .as_array()
                .into_iter()
                .flatten()
                .filter(|o| o.is_object())
                .map(|o| {
                    let kids = if o["children"].is_null() { &o["playlists"] } else { &o["children"] };
                    let kind = ["field_type", "type"].iter().map(|k| text(o, k)).find(|t| !t.is_empty()).unwrap_or_else(|| "playlist".into());
                    let mut row = id_row(o);
                    row["type"] = json!(if kind == "group" { "group" } else { "playlist" });
                    row["children"] = json!(tree(kids));
                    row
                })
                .collect()
        }
        let body = self.get("/v1/playlists").await?;
        Ok(json!({"playlists": tree(&body), "active": self.f.data("playlist/active")}))
    }

    /// Every item, headers and section markers included, in order.
    pub async fn playlist_items(&self, playlist: &str) -> Done<Value> {
        let body = self.get(&format!("/v1/playlist/{}", seg(playlist)?)).await?;
        let body = if body.is_object() { body } else { json!({}) };
        let mut items = Vec::new();
        for (n, o) in body["items"].as_array().into_iter().flatten().enumerate() {
            if !o.is_object() {
                continue;
            }
            let info = if o["presentation_info"].is_object() { &o["presentation_info"] } else { &Value::Null };
            let ident = id_row(o);
            let kind = text(o, "type");
            let presentation = if kind == "presentation" {
                Some(text(info, "presentation_uuid")).filter(|t| !t.is_empty()).unwrap_or_else(|| text(o, "target_uuid"))
            } else {
                String::new()
            };
            items.push(json!({
                "index": if ident["index"].is_null() { json!(n) } else { ident["index"].clone() }, "uuid": ident["uuid"], "name": ident["name"], "type": kind,
                "hidden": truthy(&o["is_hidden"]), "pco": truthy(&o["is_pco"]), "color": hex_color(&o["header_color"]), "duration": o["duration"],
                "presentation": presentation, "arrangement": text(info, "arrangement_name"), "arrangement_uuid": text(info, "arrangement_uuid"),
            }));
        }
        Ok(json!({"playlist": id_row(&body), "items": items}))
    }

    /// The cues of one playlist item in ITS arrangement (reusing the slide grid's cue list), so a repeated chorus has a cue, and a thumbnail, at each
    /// place it is played. `rev` keys the thumbnails.
    pub async fn playlist_item_cues(&self, playlist: &str, item: &Value) -> Done<Value> {
        let n = index_of(item)?;
        let listing = self.playlist_items(playlist).await?;
        let found = listing["items"].as_array().into_iter().flatten().find(|i| i["index"].as_i64() == Some(n)).cloned();
        let Some(found) = found.filter(|f| f["type"] == "presentation" && !text(f, "presentation").is_empty()) else { return Err(ControlError::Invalid) };
        let body = self.get(&format!("/v1/presentation/{}", seg(&text(&found, "presentation"))?)).await?;
        let detail = if body.get("presentation").is_some() { body["presentation"].clone() } else { body };
        let detail = if detail.is_object() { detail } else { json!({}) };
        let arrangement = Some(text(&found, "arrangement_uuid")).filter(|t| !t.is_empty()).unwrap_or_else(|| text(&found, "arrangement"));
        let cues: Vec<Value> = cue_list(&detail, Some(&arrangement)).into_iter().map(without_notes).collect();
        Ok(json!({
            "playlist": playlist, "index": n, "name": found["name"], "arrangement": found["arrangement"], "presentation": found["presentation"],
            "rev": rev(&json!([cues, arrangement])), "cues": cues,
        }))
    }

    /// What is in a playlist and what is wrong with it: counts by type, and for each presentation item whether ProPresenter still has the presentation it
    /// points at. A lookup that fails for another reason than "not found" (a stall) is "unknown", never "missing".
    pub async fn playlist_diagnostics(&self, playlist: &str) -> Done<Value> {
        let data = self.playlist_items(playlist).await?;
        let items: Vec<Value> = data["items"].as_array().cloned().unwrap_or_default();
        let uuids: BTreeSet<String> = items.iter().map(|i| text(i, "presentation")).filter(|u| !u.is_empty()).collect();
        let checked: Vec<String> = uuids.into_iter().take(MAX_DIAG_CHECKS).collect();
        let sem = Arc::new(Semaphore::new(THUMB_PARALLEL));
        let looks = checked.iter().map(|u| {
            let sem = Arc::clone(&sem);
            let u = u.clone();
            async move {
                let _p = sem.acquire().await;
                let status = match seg(&u) {
                    Err(_) => "missing",
                    Ok(s) => match self.get(&format!("/v1/presentation/{s}")).await {
                        Ok(_) => "ok",
                        Err(e) if e.status == 404 => "missing",
                        Err(_) => "unknown",
                    },
                };
                (u, status)
            }
        });
        let status: BTreeMap<String, &str> = futures_util::future::join_all(looks).await.into_iter().collect();
        let rows: Vec<Value> = items
            .iter()
            .map(|i| {
                let st = if i["type"] == "presentation" {
                    let p = text(i, "presentation");
                    if p.is_empty() { "none" } else { status.get(&p).copied().unwrap_or("unchecked") }
                } else {
                    ""
                };
                json!({"index": i["index"], "name": i["name"], "type": i["type"], "uuid": i["uuid"], "presentation": i["presentation"], "hidden": i["hidden"],
                       "pco": i["pco"], "arrangement": i["arrangement"], "status": st})
            })
            .collect();
        let mut types: BTreeMap<String, u64> = BTreeMap::new();
        for i in &items {
            *types.entry(text(i, "type")).or_insert(0) += 1;
        }
        Ok(json!({
            "playlist": data["playlist"], "count": items.len(), "types": types, "hidden": items.iter().filter(|i| i["hidden"] == true).count(),
            "checked": checked.len(), "problems": rows.iter().filter(|r| matches!(r["status"].as_str(), Some("missing" | "none"))).count(), "items": rows,
        }))
    }

    pub async fn trigger_playlist_item(&self, playlist: &str, index: &Value) -> Done {
        let (p, n) = (seg(playlist)?, index_of(index)?);
        self.act(&format!("/v1/playlist/{p}/{n}/trigger")).await?;
        *self.last_playlist.lock().unwrap_or_else(PoisonError::into_inner) = Some(playlist.to_owned());
        Ok(None)
    }

    /// A cue of the presentation that is up on the announcements layer (the lobby rotation's next slide).
    pub async fn trigger_announcement_cue(&self, index: i64) -> Done {
        let n = index_of(&json!(index))?;
        self.act(&format!("/v1/announcement/active/{n}/trigger")).await?;
        Ok(None)
    }

    // -- macros and looks ----------------------------------------------------------------------------------------------------
    pub fn macro_allowed(&self, uuid: &str) -> bool {
        let s = self.f.settings();
        s["macros_mode"] == "all" || s["macros_allowed"].as_array().into_iter().flatten().any(|u| u == uuid)
    }

    /// The macros an operator may use. `everything` (the admin picker) lists them all, marking which are in the operator-safe selection.
    pub async fn macros(&self, everything: bool) -> Done<Value> {
        let body = self.get("/v1/macros").await?;
        let s = self.f.settings();
        let allowed: BTreeSet<String> = s["macros_allowed"].as_array().into_iter().flatten().filter_map(|u| u.as_str().map(str::to_owned)).collect();
        let mut out = Vec::new();
        for o in body.as_array().into_iter().flatten() {
            let ident = id_row(o);
            let uuid = text(&ident, "uuid");
            if uuid.is_empty() {
                continue;
            }
            let shown = s["macros_mode"] == "all" || allowed.contains(&uuid);
            if shown || everything {
                let mut row = ident.clone();
                row["color"] = json!(hex_color(&o["color"]));
                row["selected"] = json!(allowed.contains(&uuid));
                row["shown"] = json!(shown);
                out.push(row);
            }
        }
        Ok(json!({"macros": out, "mode": s["macros_mode"], "allowed": allowed}))
    }

    pub async fn trigger_macro(&self, uuid: &str) -> Done {
        let s = seg(uuid)?;
        if !self.macro_allowed(uuid) {
            return Err(ControlError::Forbidden);
        }
        self.act(&format!("/v1/macro/{s}/trigger")).await?;
        Ok(None)
    }

    pub async fn looks(&self) -> Done<Value> {
        let body = self.get("/v1/looks").await?;
        Ok(json!({"looks": body.as_array().into_iter().flatten().filter(|o| o.is_object()).map(id_row).collect::<Vec<_>>()}))
    }

    pub async fn trigger_look(&self, look: &str) -> Done {
        self.act(&format!("/v1/look/{}/trigger", seg(look)?)).await?;
        Ok(None)
    }

    // -- timers --------------------------------------------------------------------------------------------------------------
    pub async fn timers(&self) -> Done<Value> {
        let body = self.get("/v1/timers").await?;
        let mut out = Vec::new();
        for o in body.as_array().into_iter().flatten().filter(|o| o.is_object()) {
            let has = |k: &str| o.get(k).is_some();
            let kind = if has("countdown") {
                "countdown"
            } else if has("count_down_to_time") {
                "to_time"
            } else if has("count_up") || has("elapsed") {
                "elapsed"
            } else {
                "timer"
            };
            let mut row = id_row(o);
            row["kind"] = json!(kind);
            out.push(row);
        }
        Ok(json!({"timers": out}))
    }

    /// One timer. The bulk `timers/start` starts every timer the church has; `Client::action` refuses it.
    pub async fn timer(&self, timer: &str, op: &str) -> Done {
        if !TIMER_OPS.contains(&op) {
            return Err(ControlError::Invalid);
        }
        self.act(&format!("/v1/timer/{}/{op}", seg(timer)?)).await?;
        Ok(None)
    }

    // -- messages ------------------------------------------------------------------------------------------------------------
    pub async fn messages(&self) -> Done<Value> {
        let body = self.get("/v1/messages").await?;
        let mut out = Vec::new();
        for o in body.as_array().into_iter().flatten().filter(|o| o.is_object()) {
            let mut toks = Vec::new();
            for t in o["tokens"].as_array().into_iter().flatten() {
                if t.is_object() && truthy(&t["name"]) {
                    let kind = if t.get("text").is_some() {
                        "text"
                    } else if t.get("timer").is_some() {
                        "timer"
                    } else if t.get("clock").is_some() {
                        "clock"
                    } else {
                        "other"
                    };
                    let val = if kind == "text" { text(&t["text"], "text") } else { String::new() };
                    toks.push(json!({"name": as_text(&t["name"]).chars().take(64).collect::<String>(), "kind": kind, "text": val}));
                }
            }
            let mut row = id_row(o);
            row["message"] = json!(text(o, "message"));
            row["tokens"] = json!(toks);
            row["active"] = json!(truthy(&o["is_active"]));
            out.push(row);
        }
        Ok(json!({"messages": out}))
    }

    /// Show a prepared message, filling its text tokens: `[{"name": "text", "text": "Sound desk"}]`.
    pub async fn trigger_message(&self, message: &str, tokens: &Value) -> Done {
        let mut body = Vec::new();
        for t in tokens.as_array().into_iter().flatten() {
            if !t.is_object() || text(t, "name").trim().is_empty() {
                return Err(ControlError::Invalid);
            }
            let words: String = relay_core::text::squeeze(&text(t, "text")).chars().take(TOKEN_TEXT_MAX).collect();
            body.push(json!({"name": text(t, "name").trim().chars().take(64).collect::<String>(), "text": {"text": words}}));
        }
        let path = format!("/v1/message/{}/trigger", seg(message)?);
        self.client()?.post(&path, Some(&Value::Array(body))).await?;
        Ok(None)
    }

    pub async fn clear_message(&self, message: &str) -> Done {
        self.act(&format!("/v1/message/{}/clear", seg(message)?)).await?;
        Ok(None)
    }

    // -- screens -------------------------------------------------------------------------------------------------------------
    /// Audience or stage screens on or off: `PUT /v1/status/{kind}_screens` with a bare true or false (any other content type, or an object, is a 400 live).
    pub async fn set_screens(&self, kind: &Value, on: &Value) -> Done {
        let (Some(kind), Some(on)) = (kind.as_str().filter(|k| matches!(*k, "audience" | "stage")), on.as_bool()) else { return Err(ControlError::Invalid) };
        self.client()?.put(&format!("/v1/status/{kind}_screens"), &json!(on)).await?;
        Ok(None)
    }

    // -- transport -----------------------------------------------------------------------------------------------------------
    /// Move the presentation's media to a time. The reference says `PUT /v1/transport/{layer}/time` with a bare number, not the `go_to_time` name the
    /// issue used.
    pub async fn go_to_time(&self, seconds: &Value) -> Done {
        let t = match seconds {
            Value::Number(n) => n.as_f64(),
            Value::String(t) => t.trim().parse::<f64>().ok(),
            _ => None,
        };
        let Some(t) = t.filter(|t| t.is_finite() && (0.0..=86400.0).contains(t)) else { return Err(ControlError::Invalid) };
        self.client()?.put("/v1/transport/presentation/time", &json!(t)).await?;
        Ok(None)
    }
}

fn without_notes(mut cue: Value) -> Value {
    if let Some(o) = cue.as_object_mut() {
        o.remove("notes");
    }
    cue
}
