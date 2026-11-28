//! Plan files and announcements as signage items (#209, `signagesync.py`): the files attached to the plan Relay follows, and optionally a text
//! slide for each plan item, kept in the signage media library and in one signage playlist.
//!
//! Relay follows one chosen service type's current plan (today's, or the next one coming, as the follower picks it). Each pass lists the files
//! attached to the plan and to its items, copies the pictures and videos signage can play into the media library under a name that starts with
//! [`PREFIX`], and (if the operator asked for it) rewrites the signage playlist [`PLAYLIST_NAME`] from them. A plan item's title, with one of its
//! notes, can be a text slide too: a signage source of kind [`KIND`] that draws the words as a page. The operator can limit all of it to plan
//! items whose title holds a word, such as "Announcements".
//!
//! It looks again every [`CHECK_S`] seconds and when the operator presses Sync now; a changed plan changes the next look. A file whose Planning
//! Center `updated_at` has not changed is not downloaded again. Pictures and texts nothing wants any more are deleted, but only a library row
//! whose name starts with [`PREFIX`]: a file an operator uploaded or renamed is never touched. Nothing is fetched while Planning Center is
//! unreachable or signage is off; what is stored keeps playing. Off until the operator switches it on.

use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use relay_core::caps::{ContentCap, SignageContent, SIGNAGE_CONTENT};
use relay_core::screens::SignageSource;
use relay_core::Host;
use serde_json::{json, Map, Value};
use sha1::{Digest, Sha1};

use crate::client::{Op, PcError, Services};
use crate::follower::{now, Follower};
use crate::plan::s;
use crate::settings::default_signage;

pub const PREFIX: &str = "Planning Center: ";
pub const PLAYLIST_NAME: &str = "Planning Center plan";
pub const KIND: &str = "pc_text";
pub const CHECK_S: f64 = 300.0;
pub const ITEMS_MAX: usize = 60;
pub const SLIDES_MAX: usize = 150;
pub const DURATION_DEFAULT_S: i64 = 10;
pub const TEXT_DURATION_S: i64 = 12;

/// The fixed sentence for a sync error key.
pub fn message(key: &str) -> &'static str {
    match key {
        "no_type" => "Pick the service type first.",
        "not_followed" => "That service type is not one Relay follows. Follow it above first.",
        "not_connected" => "Planning Center is not connected.",
        "off" => "Signage is switched off.",
        "no_plan" => "That service type has no current or upcoming plan.",
        "too_many" => "That plan has more than 150 files and slides, more than Relay will copy.",
        "failed" => "Planning Center did not give Relay the plan's files; it will try again.",
        "store" => "Relay could not save the files into signage.",
        _ => "",
    }
}

fn playable(content_type: &str) -> bool {
    matches!(content_type, "image/jpeg" | "image/jpg" | "image/png" | "image/webp" | "image/gif" | "video/mp4" | "video/webm")
}

// -- the text slides ---------------------------------------------------------------------------------------------------------------
/// `html.escape`: `& < > " '`.
pub fn escape(text: &str) -> String {
    text.chars()
        .map(|c| match c {
            '&' => "&amp;".to_owned(),
            '<' => "&lt;".to_owned(),
            '>' => "&gt;".to_owned(),
            '"' => "&quot;".to_owned(),
            '\'' => "&#x27;".to_owned(),
            c => c.to_string(),
        })
        .collect()
}

/// `urllib.parse.quote`: everything but letters, digits, `_.-~` and `/` is percent-encoded as UTF-8.
pub fn quote(text: &str) -> String {
    text.bytes().map(|b| if b.is_ascii_alphanumeric() || matches!(b, b'_' | b'.' | b'-' | b'~' | b'/') { (b as char).to_string() } else { format!("%{b:02X}") }).collect()
}

/// The words of one text slide as a full-screen page. Everything typed in Planning Center is escaped.
pub fn text_page(title: &str, body: &str) -> String {
    let b: String = body.lines().filter(|l| !l.trim().is_empty()).map(|l| format!("<p>{}</p>", escape(l))).collect();
    format!(
        "<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>\
         <style>html,body{{margin:0;height:100%;background:#0b1220;color:#f8fafc;font-family:system-ui,sans-serif}}\
         main{{box-sizing:border-box;height:100%;display:flex;flex-direction:column;justify-content:center;\
         align-items:center;text-align:center;padding:6vh 8vw;gap:3vh}}\
         h1{{font-size:7vw;margin:0;line-height:1.1}}p{{font-size:3.6vw;margin:0;color:#cbd5e1;line-height:1.3}}\
         </style><main><h1>{}</h1>{b}</main>",
        escape(title)
    )
}

/// The files and texts held in memory.
type Held = (Map<String, Value>, Map<String, Value>);

/// What survives a restart: which Planning Center file became which library picture, and the words of each text slide (a signage item names
/// a text only by its short id).
#[derive(Default)]
pub struct Memory {
    file: Option<PathBuf>,
    data: Mutex<Option<Held>>,
}

impl Memory {
    pub fn new() -> Self {
        Self::default()
    }

    /// Keep it in `file` instead of the state folder: a test's.
    pub fn at(file: PathBuf) -> Self {
        Self { file: Some(file), data: Mutex::new(None) }
    }

    fn path(&self) -> PathBuf {
        self.file.clone().unwrap_or_else(|| relay_core::paths::state_dir().join("planning_center").join("signage.json"))
    }

    fn with<R>(&self, f: impl FnOnce(&mut Held) -> R) -> R {
        let mut guard = self.data.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if guard.is_none() {
            let raw: Value = std::fs::read_to_string(self.path()).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Value::Null);
            let files = raw["files"].as_object().cloned().unwrap_or_default();
            let texts = raw["texts"].as_object().cloned().unwrap_or_default();
            *guard = Some((files, texts));
        }
        f(guard.as_mut().unwrap_or_else(|| unreachable!("filled above")))
    }

    pub fn files(&self) -> Map<String, Value> {
        self.with(|d| d.0.clone())
    }

    pub fn texts(&self) -> Map<String, Value> {
        self.with(|d| d.1.clone())
    }

    fn set(&self, files: Map<String, Value>, texts: Map<String, Value>) {
        self.with(|d| *d = (files, texts));
    }

    pub fn save(&self) {
        let path = self.path();
        let body = self.with(|d| json!({"files": d.0, "texts": d.1}).to_string());
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
            #[cfg(unix)]
            {
                use std::os::unix::fs::PermissionsExt;
                let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
            }
        }
        let tmp = path.with_extension("tmp");
        if std::fs::write(&tmp, body).is_ok() {
            let _ = std::fs::rename(&tmp, &path);
        }
    }

    pub fn forget(&self) {
        *self.data.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = None;
    }

    fn text_for(&self, reference: &str) -> Option<Value> {
        self.with(|d| d.1.get(reference).filter(|r| r.is_object()).cloned())
    }
}

/// The signage source for the text slides: resolved, described and offered by short id.
pub fn source(memory: Arc<Memory>) -> SignageSource {
    let (m1, m2, m3) = (Arc::clone(&memory), Arc::clone(&memory), memory);
    SignageSource {
        label: "Planning Center plan item".to_owned(),
        resolve: Box::new(move |reference| {
            let row = m1.text_for(reference)?;
            let page = text_page(&s(&row, "title"), &s(&row, "body"));
            Some(json!({"type": "page", "url": format!("data:text/html;charset=utf-8,{}", quote(&page))}))
        }),
        describe: Box::new(move |reference| match m2.text_for(reference) {
            None => json!({"name": "Deleted slide", "thumb": "", "missing": true}),
            Some(row) => json!({"name": format!("{PREFIX}{}", [s(&row, "title"), reference.to_owned()].into_iter().find(|t| !t.is_empty()).unwrap_or_default()), "thumb": ""}),
        }),
        choices: Box::new(move || {
            m3.texts()
                .iter()
                .filter(|(_, v)| v.is_object())
                .map(|(k, v)| json!({"ref": k, "name": format!("{PREFIX}{}", [s(v, "title"), k.clone()].into_iter().find(|t| !t.is_empty()).unwrap_or_default()), "thumb": ""}))
                .collect()
        }),
    }
}

#[derive(Default)]
struct Report {
    rev: String,
    at: f64,
    files: usize,
    texts: usize,
    plan_title: String,
    error: String,
    checked: f64,
}

pub type MakeClient = Arc<dyn Fn(&Value) -> Option<Arc<dyn Services>> + Send + Sync>;

pub struct PlanSync {
    host: Host,
    follower: Arc<Follower>,
    make_client: MakeClient,
    pub memory: Arc<Memory>,
    report: Mutex<Report>,
    gate: tokio::sync::Mutex<()>,
}

impl PlanSync {
    pub fn new(host: Host, follower: Arc<Follower>, make_client: MakeClient, memory: Arc<Memory>) -> Arc<Self> {
        Arc::new(Self { host, follower, make_client, memory, report: Mutex::new(Report::default()), gate: tokio::sync::Mutex::new(()) })
    }

    fn report(&self) -> std::sync::MutexGuard<'_, Report> {
        self.report.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Look again at once, even if nothing seems to have changed since the last pass.
    pub fn look_again(&self) {
        self.report().rev.clear();
    }

    pub fn settings(&self) -> Value {
        let s = self.follower.settings();
        if s["signage"].is_object() { s["signage"].clone() } else { default_signage() }
    }

    fn content(&self) -> Option<Arc<dyn SignageContent>> {
        self.host.using::<ContentCap>(SIGNAGE_CONTENT).map(|c| Arc::clone(&c.0))
    }

    pub fn signage_on(&self) -> bool {
        self.content().is_some()
    }

    /// Run [`PlanSync::sync`] every [`CHECK_S`] seconds until shutdown.
    pub async fn run(self: Arc<Self>) {
        let shutdown = self.host.shutdown.clone();
        loop {
            self.sync(false).await;
            tokio::select! {
                () = shutdown.wait() => return,
                () = tokio::time::sleep(Duration::from_secs_f64(CHECK_S)) => {}
            }
        }
    }

    // -- one pass --------------------------------------------------------------------------------------------------------------------
    pub async fn sync(&self, force: bool) -> Value {
        let settings = self.settings();
        if settings["enabled"] != Value::Bool(true) {
            self.report().error.clear();
            return self.state();
        }
        let _turn = self.gate.lock().await;
        self.report().checked = now();
        let error = match self.pass(&settings, force).await {
            Ok(()) => String::new(),
            Err(e) => e.key().to_owned(),
        };
        self.report().error = error;
        self.state()
    }

    fn plan_of(&self, st: &str) -> Result<(Value, Vec<Value>), SyncError> {
        if st.is_empty() {
            return Err(SyncError::Key("no_type"));
        }
        let tr = self.follower.track(st).ok_or(SyncError::Key("not_followed"))?;
        let plan = tr.plan.clone().or_else(|| tr.plans.first().cloned()).ok_or(SyncError::Key("no_plan"))?;
        Ok((plan, tr.items))
    }

    fn wanted<'a>(items: &'a [Value], settings: &Value) -> Vec<&'a Value> {
        let word = s(settings, "item_match").to_lowercase();
        items.iter().filter(|i| i["type"] != "header" && (word.is_empty() || s(i, "title").to_lowercase().contains(&word))).collect()
    }

    /// Everything the plan would put on screen, in order, as `{"kind": "file" | "text", ...}`.
    async fn collect(&self, client: &dyn Services, st: &str, plan: &Value, items: &[Value], settings: &Value) -> Result<Vec<Value>, PcError> {
        let wanted = Self::wanted(items, settings);
        let mut out: Vec<Value> = Vec::new();
        let merge = |label: &str, a: &Value| {
            let mut o = a.as_object().cloned().unwrap_or_default();
            o.insert("kind".to_owned(), json!("file"));
            o.insert("label".to_owned(), json!(label));
            Value::Object(o)
        };
        let pid = s(plan, "id");
        if settings["item_files"] == Value::Bool(true) {
            for it in wanted.iter().take(ITEMS_MAX) {
                let found = client.call(Op::ItemAttachments { st: st.to_owned(), plan: pid.clone(), item: s(it, "id") }).await?;
                for a in found.as_array().into_iter().flatten() {
                    out.push(merge(&s(it, "title"), a));
                }
            }
        }
        if settings["text_slides"] == Value::Bool(true) {
            let cat = s(settings, "note_category").to_lowercase();
            for it in &wanted {
                if s(it, "title").trim().is_empty() {
                    continue;
                }
                let body = if cat.is_empty() { String::new() } else { it["notes"].as_array().into_iter().flatten().find(|n| s(n, "category").to_lowercase() == cat).map(|n| s(n, "content")).unwrap_or_default() };
                let digest = Sha1::digest(format!("{st}:{}", s(it, "id")).as_bytes());
                let key: String = digest.iter().map(|b| format!("{b:02x}")).collect::<String>().chars().take(16).collect();
                out.push(json!({"kind": "text", "key": key, "title": s(it, "title").trim().chars().take(120).collect::<String>(),
                                "body": body.trim().chars().take(600).collect::<String>()}));
            }
        }
        if settings["plan_files"] == Value::Bool(true) && s(settings, "item_match").is_empty() {
            let label = if s(plan, "title").is_empty() { "Plan".to_owned() } else { s(plan, "title") };
            let found = client.call(Op::PlanAttachments { st: st.to_owned(), plan: pid }).await?;
            for a in found.as_array().into_iter().flatten() {
                out.push(merge(&label, a));
            }
        }
        // A file shown on two items is one file.
        let mut seen: BTreeSet<(String, String)> = BTreeSet::new();
        Ok(out.into_iter().filter(|x| seen.insert((s(x, "kind"), if x["kind"] == "file" { s(x, "id") } else { s(x, "key") }))).collect())
    }

    async fn pass(&self, settings: &Value, force: bool) -> Result<(), SyncError> {
        let (plan, items) = self.plan_of(&s(settings, "service_type"))?;
        let Some(content) = self.content() else { return Err(SyncError::Key("off")) };
        let client = (self.make_client)(&self.follower.settings()).ok_or(SyncError::Key("not_connected"))?;
        let st = s(settings, "service_type");
        let found = self.collect(client.as_ref(), &st, &plan, &items, settings).await?;
        let keep: Vec<Value> = found.into_iter().filter(|x| x["kind"] == "text" || (playable(&s(x, "content_type")) && x["linked"] != Value::Bool(true) && !s(x, "url").is_empty())).collect();
        if keep.len() > SLIDES_MAX {
            return Err(SyncError::Key("too_many"));
        }
        let ident: Vec<Value> = keep.iter().map(|x| json!([x["kind"], if x["id"].is_null() { &x["key"] } else { &x["id"] }, x["updated_at"], x["title"], x["body"]])).collect();
        let digest = Sha1::digest(json!([settings, plan["id"], ident]).to_string().as_bytes());
        let rev: String = digest.iter().map(|b| format!("{b:02x}")).collect::<String>().chars().take(16).collect();
        if rev == self.report().rev && !force {
            return Ok(());
        }
        let refs = self.store(client.as_ref(), content.as_ref(), &keep, settings).await?;
        let title = [s(&plan, "title"), s(&plan, "dates")].into_iter().find(|t| !t.is_empty()).unwrap_or_else(|| "Plan".to_owned());
        {
            let mut r = self.report();
            r.rev = rev;
            r.at = now();
            r.plan_title = title;
            r.files = refs.iter().filter(|(k, _)| k == "media").count();
            r.texts = refs.iter().filter(|(k, _)| k == KIND).count();
        }
        tracing::info!("Plan files sync: {} file(s), {} text slide(s) from plan {}", self.report().files, self.report().texts, s(&plan, "id"));
        Ok(())
    }

    /// The file's bytes into signage's library: `None` for a type signage does not play, or one too big.
    async fn download(&self, client: &dyn Services, content: &dyn SignageContent, x: &Value) -> Result<Option<(String, String, u64)>, SyncError> {
        let limit = content.limit(s(x, "content_type").starts_with("image/"));
        let mut rx = client.download(&s(x, "url"), limit).await?;
        let (tx, body_rx) = tokio::sync::mpsc::channel::<Result<bytes::Bytes, std::io::Error>>(8);
        let pump = async {
            while let Some(chunk) = rx.recv().await {
                let chunk = chunk.map_err(|e| std::io::Error::other(e.to_string()));
                let failed = chunk.is_err();
                if tx.send(chunk).await.is_err() || failed {
                    break;
                }
            }
            drop(tx);
        };
        let body = Body::from_stream(futures_util::stream::unfold(body_rx, |mut r| async move { r.recv().await.map(|c| (c, r)) }));
        let (_, got) = tokio::join!(pump, content.receive(body));
        got.map_err(|_| SyncError::Key("store"))
    }

    async fn store(&self, client: &dyn Services, content: &dyn SignageContent, keep: &[Value], settings: &Value) -> Result<Vec<(String, String)>, SyncError> {
        let store = |_: relay_core::caps::ContentError| SyncError::Key("store");
        let mut rows: BTreeMap<String, Value> = content.media_list().await.map_err(store)?.into_iter().filter(|m| s(m, "name").starts_with(PREFIX)).map(|m| (s(&m, "id"), m)).collect();
        let mut by_sha: BTreeMap<String, Value> = rows.values().map(|m| (s(m, "sha256"), m.clone())).collect();
        let known = self.memory.files();
        let mut refs: Vec<(String, String)> = Vec::new();
        let mut files = Map::new();
        let mut texts = Map::new();
        for x in keep {
            if x["kind"] == "text" {
                texts.insert(s(x, "key"), json!({"title": x["title"], "body": x["body"]}));
                refs.push((KIND.to_owned(), s(x, "key")));
                continue;
            }
            let id = s(x, "id");
            if let Some(k) = known.get(&id).filter(|k| s(k, "updated") == s(x, "updated_at") && rows.contains_key(&s(k, "media"))) {
                files.insert(id, k.clone());
                refs.push(("media".to_owned(), s(k, "media")));
                continue;
            }
            // A type signage does not play, or too big: skipped.
            let Some((sha, ext, size)) = self.download(client, content, x).await? else { continue };
            let row = match by_sha.get(&sha) {
                Some(r) => r.clone(),
                None => {
                    let name = format!("{PREFIX}{} {}", s(x, "label"), s(x, "name")).trim().to_owned();
                    let r = content.media_add(&name, &sha, &ext, size).await.map_err(store)?;
                    by_sha.insert(sha, r.clone());
                    rows.insert(s(&r, "id"), r.clone());
                    r
                }
            };
            files.insert(id, json!({"updated": x["updated_at"], "media": row["id"]}));
            refs.push(("media".to_owned(), s(&row, "id")));
        }
        self.memory.set(files, texts);
        self.memory.save();
        self.finish(content, &refs, settings["playlist"] == Value::Bool(true)).await.map_err(store)?;
        Ok(refs)
    }

    async fn finish(&self, content: &dyn SignageContent, refs: &[(String, String)], playlist: bool) -> Result<(), relay_core::caps::ContentError> {
        if playlist {
            let lists = content.playlists().await?;
            let mine = lists.iter().find(|p| s(p, "name") == PLAYLIST_NAME);
            let pl = match mine {
                Some(p) => p.clone(),
                None => content.playlist_create(PLAYLIST_NAME).await?,
            };
            let mut keep: BTreeMap<(String, String), &Value> = BTreeMap::new();
            for it in mine.map_or(&[][..], |p| p["items"].as_array().map_or(&[][..], Vec::as_slice)) {
                keep.entry((s(it, "source"), s(it, "ref"))).or_insert(it);
            }
            let items: Vec<Value> = refs
                .iter()
                .map(|(source, reference)| {
                    let old = keep.get(&(source.clone(), reference.clone()));
                    let pick = |k: &str, def: Value| old.map_or(def, |o| o[k].clone());
                    json!({"source": source, "ref": reference,
                           "duration_s": pick("duration_s", json!(if source == KIND { TEXT_DURATION_S } else { DURATION_DEFAULT_S })),
                           "start_date": pick("start_date", json!("")), "end_date": pick("end_date", json!("")),
                           "transition": pick("transition", json!("fade")), "fit": pick("fit", json!("fit"))})
                })
                .collect();
            content.playlist_set_items(pl["id"].as_i64().unwrap_or(0), &Value::Array(items)).await?;
        }
        // Pictures nothing wants any more and no playlist uses. media_delete refuses one a playlist still uses.
        let wanted: BTreeSet<&str> = refs.iter().filter(|(k, _)| k == "media").map(|(_, r)| r.as_str()).collect();
        for m in content.media_list().await? {
            if s(&m, "name").starts_with(PREFIX) && !wanted.contains(s(&m, "id").as_str()) {
                let _ = content.media_delete(&s(&m, "id")).await;
            }
        }
        Ok(())
    }

    // -- state -----------------------------------------------------------------------------------------------------------------------
    pub fn state(&self) -> Value {
        let settings = self.settings();
        let r = self.report();
        json!({"enabled": settings["enabled"], "settings": settings, "playlist_name": PLAYLIST_NAME, "plan": r.plan_title, "files": r.files, "texts": r.texts,
               "synced_at": r.at, "checked_at": r.checked, "error": message(&r.error), "signage_on": self.signage_on()})
    }

    /// The saved error key, for a test.
    pub fn error_key(&self) -> String {
        self.report().error.clone()
    }
}

enum SyncError {
    Key(&'static str),
    Pc,
}

impl SyncError {
    fn key(&self) -> &'static str {
        match self {
            SyncError::Key(k) => k,
            SyncError::Pc => "failed",
        }
    }
}

impl From<PcError> for SyncError {
    fn from(e: PcError) -> Self {
        tracing::info!("Plan files sync: {}", e.detail());
        SyncError::Pc
    }
}
