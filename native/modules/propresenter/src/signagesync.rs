//! Announcement slides as signage items (#208; `signagesync.py`): the slides of one ProPresenter playlist, kept as pictures in the signage media library
//! and as one signage playlist, "ProPresenter announcements".
//!
//! The operator picks the playlist (ProPresenter's API cannot say which one holds announcements). Relay reads each presentation's slides in the
//! playlist's own arrangement, fetches a large thumbnail of each from ProPresenter, stores it in the media library under a name that starts with
//! [`PREFIX`], and replaces that signage playlist's items with them. A slide whose picture has not changed keeps its library row, and an item keeps its
//! duration, dates and transition when the same picture is still in the playlist. A picture no item names any more is deleted, but only a library row
//! whose name starts with [`PREFIX`]: a file an operator uploaded, or renamed, is never touched.
//!
//! It looks again every [`CHECK_S`] seconds and when the operator presses Sync now. Nothing is fetched while ProPresenter is down or signage is off; the
//! pictures already stored keep playing. Slides are licensed content, so this is off until the operator switches it on.

use std::collections::{BTreeMap, BTreeSet};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use axum::body::Body;
use relay_core::caps::{ContentCap, SignageContent, SIGNAGE_CONTENT};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};

use crate::client::text;
use crate::control::{Control, THUMB_QUALITY};
use crate::follower::{now, Follower};
use crate::settings::default_signage;

pub const PREFIX: &str = "ProPresenter: ";
pub const PLAYLIST_NAME: &str = "ProPresenter announcements";
pub const CHECK_S: f64 = 120.0;
/// Longest side in pixels: the signage player shows it full screen.
pub const SLIDE_QUALITY: i64 = 1280;
pub const SLIDES_MAX: usize = 150;
pub const DURATION_DEFAULT_S: i64 = 10;

/// The fixed sentence for a sync error key.
pub fn message(key: &str) -> &'static str {
    match key {
        "no_playlist" => "Pick the announcement playlist first.",
        "not_connected" => "ProPresenter is not connected.",
        "off" => "Signage is switched off.",
        "empty" => "That playlist has no presentations Relay can read slides from.",
        "too_many" => "That playlist has more than 150 slides, more than Relay will copy.",
        "failed" => "ProPresenter did not give Relay the slides; it will try again.",
        "store" => "Relay could not save the slides into signage.",
        _ => "",
    }
}

#[derive(Default)]
struct Report {
    /// What the last good sync was made from.
    rev: String,
    /// Wall-clock time of the last good sync.
    at: f64,
    slides: usize,
    /// A message key, "" when the last try was fine.
    error: &'static str,
    checked: f64,
}

pub struct Sync {
    f: Arc<Follower>,
    ctl: Arc<Control>,
    report: Mutex<Report>,
    guard: tokio::sync::Mutex<()>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

type Slide = (String, Vec<u8>, Vec<u8>);

impl Sync {
    pub fn new(f: Arc<Follower>, ctl: Arc<Control>) -> Arc<Self> {
        Arc::new(Self { f, ctl, report: Mutex::new(Report::default()), guard: tokio::sync::Mutex::new(()), task: Mutex::new(None) })
    }

    fn report(&self) -> std::sync::MutexGuard<'_, Report> {
        self.report.lock().unwrap_or_else(PoisonError::into_inner)
    }

    // -- lifecycle -------------------------------------------------------------------------------------------------------------
    pub fn start(self: &Arc<Self>) {
        let me = Arc::clone(self);
        let handle = tokio::spawn(async move {
            loop {
                me.sync(false).await;
                tokio::time::sleep(Duration::from_secs_f64(CHECK_S)).await;
            }
        });
        *self.task.lock().unwrap_or_else(PoisonError::into_inner) = Some(handle);
    }

    pub fn cancel(&self) {
        if let Some(t) = self.task.lock().unwrap_or_else(PoisonError::into_inner).take() {
            t.abort();
        }
    }

    fn settings(&self) -> Value {
        let s = self.f.settings();
        if s["signage"].is_object() {
            s["signage"].clone()
        } else {
            default_signage()
        }
    }

    fn content(&self) -> Option<Arc<ContentCap>> {
        self.f.host().using::<ContentCap>(SIGNAGE_CONTENT)
    }

    // -- one pass --------------------------------------------------------------------------------------------------------------
    /// Look at the playlist and, when it changed (or `force`), copy its slides into signage. Returns the state. Quiet when switched off. A failure is kept
    /// as a message key and tried again on the next pass.
    pub async fn sync(&self, force: bool) -> Value {
        let s = self.settings();
        if s["enabled"] != Value::Bool(true) {
            self.report().error = "";
            return self.state();
        }
        {
            let _one = self.guard.lock().await;
            self.report().checked = now();
            let error = match self.pass(&text(&s, "playlist"), force).await {
                Ok(()) => "",
                Err(key) => key,
            };
            self.report().error = error;
        }
        self.state()
    }

    async fn pass(&self, playlist: &str, force: bool) -> Result<(), &'static str> {
        if playlist.is_empty() {
            return Err("no_playlist");
        }
        let Some(content) = self.content() else { return Err("off") };
        if !(self.f.is_up() && self.f.client().is_some()) {
            return Err("not_connected");
        }
        let body = self.ctl.playlist_items(playlist).await.map_err(|_| "failed")?;
        let items: Vec<Value> = body["items"].as_array().into_iter().flatten().filter(|i| i["type"] == "presentation" && i["hidden"] != true && !text(i, "presentation").is_empty() && i["index"].is_i64()).cloned().collect();
        if items.is_empty() {
            return Err("empty");
        }
        let mut plan: Vec<(Value, Value, Vec<Value>)> = Vec::new(); // (item, cues response, enabled cues)
        let mut total = 0;
        for it in items {
            let got = self.ctl.playlist_item_cues(playlist, &it["index"]).await.map_err(|_| "failed")?;
            let cues: Vec<Value> = got["cues"].as_array().into_iter().flatten().filter(|c| c["enabled"] == Value::Bool(true)).cloned().collect();
            total += cues.len();
            if total > SLIDES_MAX {
                return Err("too_many");
            }
            plan.push((it, got, cues));
        }
        let ident: Vec<Value> = plan.iter().map(|(it, got, cues)| json!([it["index"], it["presentation"], got["rev"], cues.iter().map(|c| c["index"].clone()).collect::<Vec<_>>()])).collect();
        let digest = Sha1::digest(json!([playlist, ident]).to_string().as_bytes());
        let rev: String = digest.iter().map(|b| format!("{b:02x}")).collect::<String>().chars().take(16).collect();
        if rev == self.report().rev && !force {
            return Ok(());
        }
        let mut slides: Vec<Slide> = Vec::new();
        for (it, got, cues) in &plan {
            for c in cues {
                let marker = text(got, "rev");
                let big = self.ctl.playlist_thumbnail(playlist, &it["index"], &c["index"], &marker, SLIDE_QUALITY).await.map_err(|_| "failed")?;
                let small = self.ctl.playlist_thumbnail(playlist, &it["index"], &c["index"], &marker, THUMB_QUALITY).await.map_err(|_| "failed")?;
                let n = c["index"].as_i64().unwrap_or(0) + 1;
                slides.push((format!("{PREFIX}{} {n}", text(it, "name")), big.1, small.1));
            }
        }
        self.store(content.0.as_ref(), &slides).await.map_err(|why| {
            tracing::warn!("Announcement sync could not store the slides: {}", why.0);
            "store"
        })?;
        {
            let mut r = self.report();
            (r.rev, r.at, r.slides) = (rev, now(), slides.len());
        }
        tracing::info!("Announcement sync: {} slide(s) from {} presentation(s)", slides.len(), plan.len());
        Ok(())
    }

    async fn store(&self, content: &dyn SignageContent, slides: &[Slide]) -> Result<(), relay_core::caps::ContentError> {
        let mut by_sha: BTreeMap<String, Value> = content.media_list().await?.into_iter().filter(|m| text(m, "name").starts_with(PREFIX)).map(|m| (text(&m, "sha256"), m)).collect();
        let mut refs: Vec<String> = Vec::new();
        for (name, big, small) in slides {
            let Some((sha, ext, size)) = content.receive(Body::from(big.clone())).await? else {
                return Err(relay_core::caps::ContentError("signage refused the picture".into()));
            };
            let row = match by_sha.get(&sha) {
                Some(r) => r.clone(),
                None => {
                    let r = content.media_add(name, &sha, &ext, size).await?;
                    by_sha.insert(sha, r.clone());
                    let _ = content.media_thumb(&text(&r, "id"), small).await;
                    r
                }
            };
            refs.push(text(&row, "id"));
        }
        Self::set_playlist(content, &refs).await
    }

    async fn set_playlist(content: &dyn SignageContent, refs: &[String]) -> Result<(), relay_core::caps::ContentError> {
        let lists = content.playlists().await?;
        let mine = lists.iter().find(|p| text(p, "name") == PLAYLIST_NAME);
        let pl = match mine {
            Some(p) => p.clone(),
            None => content.playlist_create(PLAYLIST_NAME).await?,
        };
        let mut keep: BTreeMap<String, &Value> = BTreeMap::new();
        for it in mine.map_or(&[][..], |p| p["items"].as_array().map_or(&[][..], Vec::as_slice)) {
            if it["source"] == "media" {
                keep.entry(text(it, "ref")).or_insert(it);
            }
        }
        let items: Vec<Value> = refs
            .iter()
            .map(|r| {
                let old = keep.get(r);
                let pick = |k: &str, def: Value| old.map_or(def, |o| o[k].clone());
                json!({"source": "media", "ref": r, "duration_s": pick("duration_s", json!(DURATION_DEFAULT_S)), "start_date": pick("start_date", json!("")),
                       "end_date": pick("end_date", json!("")), "transition": pick("transition", json!("fade")), "fit": pick("fit", json!("fit"))})
            })
            .collect();
        content.playlist_set_items(pl["id"].as_i64().unwrap_or(0), &Value::Array(items)).await?;
        // Pictures nothing names any more. media_delete refuses one a playlist still uses, which is fine.
        let used: BTreeSet<String> = content.playlists().await?.iter().flat_map(|p| p["items"].as_array().cloned().unwrap_or_default()).filter(|it| it["source"] == "media").map(|it| text(&it, "ref")).collect();
        for m in content.media_list().await? {
            if text(&m, "name").starts_with(PREFIX) && !used.contains(&text(&m, "id")) {
                let _ = content.media_delete(&text(&m, "id")).await;
            }
        }
        Ok(())
    }

    // -- state -----------------------------------------------------------------------------------------------------------------
    pub fn state(&self) -> Value {
        let s = self.settings();
        let r = self.report();
        json!({
            "enabled": s["enabled"], "playlist": s["playlist"], "playlist_name": PLAYLIST_NAME, "slides": r.slides, "synced_at": r.at, "checked_at": r.checked,
            "error": message(r.error), "signage_on": self.content().is_some(),
        })
    }
}
