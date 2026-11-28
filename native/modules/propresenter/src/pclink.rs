//! The Planning Center link (#138, stage 6; `pclink.py`): ProPresenter and Planning Center looking at the same service.
//!
//! Four things, all behind switches that start off:
//!
//! * Matching: the current plan's items against what ProPresenter has (`plmatch` has the rules). Relay reads ProPresenter's libraries and playlists, caches
//!   them for a couple of minutes and shows the result, the unmatched items and the operator's own pairings in admin. Reading only.
//! * Setlist swap: on the operator's click, and only when not view-only, Relay builds a playlist it created and named "Relay - <plan date>" in the plan's
//!   order. It never writes to any other playlist: the one UUID Relay made is kept in the config (`link.owned`), and every write checks that UUID and the
//!   name against ProPresenter first.
//! * Follow the plan (`follow`): ProPresenter's live presentation is matched to a plan item and shown beside Planning Center's LIVE item in admin; the
//!   Service feed tells panels which item ProPresenter has up (`pp_item`). When they disagree both are shown. Lyrics reach panels through the lyrics feed as
//!   before: they are not put in the Service message, whose firmware parser reads by key name.
//! * ProPresenter drives Services LIVE (`drive`): once a presentation has been up `dwell_s` seconds (default 10), the matching Planning Center item is moved
//!   to with the same Next / Previous presses a panel uses. Never takes control: it moves LIVE only while Relay's own token already holds it, and says so in
//!   admin when it does not. One try per activation of a presentation; a quick skip through several restarts the wait. Going back to an earlier item needs
//!   `allow_back` and stops at the matched item.
//!
//! Everything written to ProPresenter goes through the client (view-only refuses it there). Planning Center is reached only as the capability it provides
//! (`plan.link`), so while that module is off the link follows nothing.

use std::collections::{BTreeMap, BTreeSet, VecDeque};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use relay_core::caps::{LinkTrack, PlanLink, FEED_REFRESH, PLAN_LINK};
use relay_core::text::squeeze;
use serde_json::{json, Value};

use crate::client::{text, Kind, ProError};
use crate::control::seg;
use crate::follower::{now, Follower};
use crate::plmatch;
use crate::settings::{self, clean_stored, configured, link_fields, truthy, OWNED_PREFIX};
use crate::KEY;

pub const TICK: Duration = Duration::from_millis(500);
/// ProPresenter's lists are read again after this long while the link is on.
pub const CACHE_S: f64 = 120.0;
/// After a read failed.
pub const RETRY_S: f64 = 20.0;
/// Playlists read for the match; ProPresenter can stall, so the work is bounded.
pub const MAX_PLAYLISTS: usize = 40;
pub const MAX_LIBRARIES: usize = 8;
pub const MAX_CANDIDATES: usize = 6000;
pub const LOG_MAX: usize = 30;

/// What the admin page says. Fixed sentences: nothing from an exception reaches them.
fn sentence(code: &str) -> &'static str {
    match code {
        "advanced" => "Moved Planning Center forward to \"{item}\" ({steps}).",
        "went_back" => "Moved Planning Center back to \"{item}\" ({steps}).",
        "already" => "Planning Center is already on \"{item}\".",
        "nobody" => "Nobody holds LIVE control in Planning Center, so Relay did not move it. Relay never takes control by itself.",
        "someone_else" => "{who} holds LIVE control in Planning Center, so Relay did not move it.",
        "behind" => "\"{item}\" is behind Planning Center's current item, and going back is switched off.",
        "not_in_plan" => "\"{item}\" is not after the current item in the plan.",
        "overshot" => "Planning Center stepped past \"{item}\" and Relay stopped ({steps}).",
        "too_far" => "\"{item}\" is too many steps away; Relay stopped after {steps}.",
        "lost_control" => "Control was lost while moving, so Relay stopped ({steps}).",
        "unknown_item" => "Planning Center's current item is not one Relay knows; it did not move.",
        "no_plan" => "There is no plan followed for today, so there is nothing to move.",
        "not_connected" => "Planning Center is not connected.",
        "unreachable" => "Planning Center did not answer.",
        "refused" => "Planning Center refused the request.",
        "no_match" => "\"{pres}\" matches no item in the plan, so Planning Center was not moved.",
        "unread" => "Could not read ProPresenter's lists to match \"{pres}\"; trying again.",
        _ => "Planning Center was not moved.",
    }
}

fn read_error(kind: Kind) -> &'static str {
    match kind {
        Kind::Network => "Could not reach ProPresenter.",
        Kind::Timeout => "ProPresenter did not answer in time.",
        Kind::Http => "ProPresenter refused a request.",
        Kind::Bad => "ProPresenter answered in a way Relay did not expect.",
        _ => "Could not read ProPresenter.",
    }
}

/// What the operator is told when the link refuses something: fixed sentences, looked up by key, so no exception text ever reaches the browser.
pub fn message(key: &str) -> &'static str {
    match key {
        "pick_both" => "Pick a plan item and a ProPresenter presentation.",
        "no_plan" => "There is no plan to build a playlist from.",
        "nothing_matched" => "No plan item is matched to a presentation yet, so there is nothing to put in a playlist.",
        "name_taken" => "A playlist with today's Relay playlist name is already in ProPresenter and Relay did not create it, so Relay will not change it. Rename or remove it there first.",
        "not_identified" => "ProPresenter made the playlist but Relay could not tell which one it is, so it left it alone.",
        "not_ours" => "Relay will only change a playlist it made itself.",
        _ => "The Planning Center link could not do that.",
    }
}

/// Why a link call did not happen.
#[derive(Debug, Clone)]
pub enum LinkFail {
    /// Something the operator should be told: the key names a fixed sentence in [`message`].
    Told(&'static str),
    /// A bad argument.
    Invalid,
    Pro(ProError),
}

impl From<ProError> for LinkFail {
    fn from(e: ProError) -> Self {
        Self::Pro(e)
    }
}

/// Every playlist (not folders) in ProPresenter's playlist tree, in order.
fn flatten(tree: &Value, out: &mut Vec<Value>) {
    for o in tree.as_array().into_iter().flatten().filter(|o| o.is_object()) {
        let ident = if o["id"].is_object() { &o["id"] } else { &Value::Null };
        let kind = ["field_type", "type"].iter().map(|k| text(o, k)).find(|t| !t.is_empty()).unwrap_or_else(|| "playlist".into());
        let kids = if o["children"].is_null() { &o["playlists"] } else { &o["children"] };
        if kind == "group" {
            flatten(kids, out);
        } else {
            out.push(json!({"uuid": text(ident, "uuid"), "name": text(ident, "name").trim()}));
        }
    }
}

fn playlists_in(tree: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    flatten(tree, &mut out);
    out
}

/// A playlist's items as the matcher wants them.
fn entries(body: &Value) -> Vec<Value> {
    let mut out = Vec::new();
    for o in body["items"].as_array().into_iter().flatten().filter(|o| o.is_object()) {
        let ident = if o["id"].is_object() { &o["id"] } else { &Value::Null };
        let info = if o["presentation_info"].is_object() { &o["presentation_info"] } else { &Value::Null };
        let kind = text(o, "type");
        let uuid = if kind == "presentation" { Some(text(info, "presentation_uuid")).filter(|t| !t.is_empty()).unwrap_or_else(|| text(o, "target_uuid")) } else { String::new() };
        out.push(json!({"name": text(ident, "name").trim(), "type": kind, "pco": truthy(&o["is_pco"]), "uuid": uuid}));
    }
    out
}

#[derive(Default)]
struct State {
    /// Presentations `{uuid, name, source}`, best source first.
    pool: Vec<Value>,
    /// A presentation's name exactly as ProPresenter holds it (trailing spaces and all). The matcher works on trimmed names, but a playlist PUT finds each item
    /// by this exact name and answers 404 to anything else.
    exact_names: BTreeMap<String, String>,
    /// `[{uuid, name, items}]` (the church's own, read only).
    playlists: Vec<Value>,
    loaded_at: f64,
    load_error: String,
    retry_at: f64,
    rows_key: Option<String>,
    rows: Vec<Value>,
    synced_name: String,
    pp_item: Option<String>,
    act_uuid: Option<String>,
    act_name: String,
    act_n: u64,
    act_since: f64,
    fired: u64,
    drive_on: bool,
    firing: bool,
    log: VecDeque<Value>,
    last: Option<Value>,
}

pub type Clock = Arc<dyn Fn() -> f64 + Send + Sync>;

pub struct Link {
    f: Arc<Follower>,
    clock: Clock,
    wall: Clock,
    state: Mutex<State>,
    loading: AtomicBool,
    refresh_lock: tokio::sync::Mutex<()>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

fn monotonic() -> f64 {
    static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    START.get_or_init(std::time::Instant::now).elapsed().as_secs_f64()
}

fn plan_item_ids(tr: &LinkTrack) -> Vec<String> {
    tr.items.iter().map(|i| text(i, "id")).collect()
}

impl Link {
    pub fn new(f: Arc<Follower>) -> Arc<Self> {
        Self::with_clocks(f, Arc::new(monotonic), Arc::new(now))
    }

    /// With clocks of the caller's (a test moves them): the monotonic one drives waits, the wall one stamps what admin shows.
    pub fn with_clocks(f: Arc<Follower>, clock: Clock, wall: Clock) -> Arc<Self> {
        let link = Arc::new(Self { f: Arc::clone(&f), clock, wall, state: Mutex::new(State::default()), loading: AtomicBool::new(false), refresh_lock: tokio::sync::Mutex::new(()), task: Mutex::new(None) });
        let weak = Arc::downgrade(&link);
        f.on_change(Arc::new(move || {
            if let Some(l) = weak.upgrade() {
                l.changed();
            }
        }));
        link
    }

    fn st(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// What Planning Center provides (#523), none while it is off.
    fn plan(&self) -> Option<Arc<PlanLink>> {
        self.f.host().using::<PlanLink>(PLAN_LINK)
    }

    fn pc_on(&self) -> bool {
        self.plan().is_some_and(|p| (p.connected)())
    }

    fn tracks(&self) -> Vec<LinkTrack> {
        self.plan().map(|p| (p.tracks)()).unwrap_or_default()
    }

    // -- lifecycle -------------------------------------------------------------------------------------------------------------
    pub fn start(self: &Arc<Self>) {
        let me = Arc::clone(self);
        let handle = tokio::spawn(async move {
            loop {
                me.tick(None).await;
                tokio::time::sleep(TICK).await;
            }
        });
        *self.task.lock().unwrap_or_else(PoisonError::into_inner) = Some(handle);
    }

    pub fn cancel(&self) {
        if let Some(t) = self.task.lock().unwrap_or_else(PoisonError::into_inner).take() {
            t.abort();
        }
    }

    // -- settings and the plan -------------------------------------------------------------------------------------------------
    fn settings(&self) -> Value {
        let s = self.f.settings();
        if s["link"].is_object() {
            s["link"].clone()
        } else {
            settings::default_link()
        }
    }

    pub fn active(&self) -> bool {
        let s = self.settings();
        (truthy(&s["follow"]) || truthy(&s["drive"])) && self.f.configured() && self.pc_on()
    }

    /// The service type and its track the link works on: the operator's pick, else the first with a plan.
    pub fn track(&self) -> Option<LinkTrack> {
        let tracks = self.tracks();
        let want = text(&self.settings(), "service_type");
        if !want.is_empty() {
            if let Some(t) = tracks.iter().find(|t| t.service_type == want) {
                return Some(t.clone());
            }
        }
        tracks.into_iter().find(|t| t.plan.is_some())
    }

    // -- reading ProPresenter --------------------------------------------------------------------------------------------------
    /// The presentations the last read found, for the operator's pairing picker (names and UUIDs only).
    pub fn pool(&self) -> Vec<Value> {
        self.st().pool.clone()
    }

    /// Read ProPresenter's libraries and playlists (reads only) and rebuild the pool of presentations. Fails when ProPresenter cannot be read; the previous
    /// pool stays.
    pub async fn refresh(&self) -> Result<(), ProError> {
        let Ok(_one) = self.refresh_lock.try_lock() else {
            let _wait = self.refresh_lock.lock().await; // another read is on: wait for it, and take what it found
            return Ok(());
        };
        self.loading.store(true, Ordering::SeqCst);
        let done = self.read_lists().await;
        self.loading.store(false, Ordering::SeqCst);
        match done {
            Ok(()) => {
                self.changed();
                Ok(())
            }
            Err(e) => {
                tracing::info!("Link: could not read ProPresenter ({:?})", e.kind);
                let mut st = self.st();
                st.load_error = read_error(e.kind).to_owned();
                st.retry_at = (self.clock)() + RETRY_S;
                Err(e)
            }
        }
    }

    async fn read_lists(&self) -> Result<(), ProError> {
        let client = self.f.need_client()?;
        let bad = |_| ProError::new(Kind::Bad, "bad id");
        let tree = client.read("/v1/playlists").await?;
        let owned = text(&self.settings()["owned"], "uuid");
        let lists: Vec<Value> = playlists_in(&tree).into_iter().filter(|p| !text(p, "uuid").is_empty() && text(p, "uuid") != owned).take(MAX_PLAYLISTS).collect();
        let mut playlists = Vec::new();
        for p in &lists {
            let body = client.read(&format!("/v1/playlist/{}", seg(&text(p, "uuid")).map_err(|_| bad(()))?)).await?;
            let mut row = p.clone();
            row["items"] = json!(entries(&body));
            playlists.push(row);
        }
        let libs = client.read("/v1/libraries").await?;
        let mut pool: Vec<Value> = Vec::new();
        let mut exact: BTreeMap<String, String> = BTreeMap::new();
        for p in &playlists {
            for e in p["items"].as_array().into_iter().flatten().filter(|e| e["type"] == "presentation" && !text(e, "uuid").is_empty()) {
                pool.push(json!({"uuid": e["uuid"], "name": e["name"], "source": format!("playlist: {}", text(p, "name"))}));
            }
        }
        for lib in libs.as_array().into_iter().flatten().take(MAX_LIBRARIES) {
            let lid = if lib.is_object() { text(lib, "uuid") } else { String::new() };
            if lid.is_empty() {
                continue;
            }
            let body = client.read(&format!("/v1/library/{}", seg(&lid).map_err(|_| bad(()))?)).await?;
            for o in body["items"].as_array().into_iter().flatten().filter(|o| o.is_object()) {
                if !text(o, "uuid").is_empty() && !o["name"].is_null() {
                    exact.entry(text(o, "uuid")).or_insert_with(|| text(o, "name"));
                }
            }
            for o in body["items"].as_array().into_iter().flatten().filter(|o| o.is_object() && !text(o, "uuid").is_empty()) {
                pool.push(json!({"uuid": text(o, "uuid"), "name": text(o, "name").trim(), "source": format!("library: {}", text(lib, "name"))}));
            }
        }
        let mut seen = BTreeSet::new();
        let pool: Vec<Value> = pool.into_iter().filter(|c| seen.insert(text(c, "uuid"))).take(MAX_CANDIDATES).collect();
        let mut st = self.st();
        st.pool = pool;
        st.exact_names = exact;
        st.playlists = playlists;
        st.loaded_at = (self.wall)();
        st.load_error.clear();
        st.rows_key = None;
        Ok(())
    }

    // -- matching --------------------------------------------------------------------------------------------------------------
    /// The plan's items matched to presentations (cached until the plan, the pool or the settings change).
    pub fn rows(&self) -> Vec<Value> {
        let tr = self.track();
        let s = self.settings();
        let items: Vec<Value> = tr.as_ref().map(|t| t.items.iter().map(|i| json!({"id": i["id"], "title": i["title"], "type": i["type"]})).collect()).unwrap_or_default();
        let (pool, playlists, loaded_at) = {
            let st = self.st();
            (st.pool.clone(), st.playlists.clone(), st.loaded_at)
        };
        let key = json!([tr.as_ref().map(|t| &t.service_type), tr.as_ref().and_then(|t| t.plan.as_ref().map(|p| &p["id"])), items, loaded_at, s["mapping"], s["threshold"]]).to_string();
        if self.st().rows_key.as_deref() == Some(key.as_str()) {
            return self.st().rows.clone();
        }
        let threshold = s["threshold"].as_f64().unwrap_or(plmatch::DEFAULT_THRESHOLD);
        let (mut best_rows, mut best_n, mut name) = (Vec::new(), -1_i64, String::new());
        // None is title matching alone; each playlist that came from Planning Center is tried as the synced one, and the one the plan fits best wins (a tie keeps
        // the earlier, so title matching wins a 0-0 tie).
        let candidates: Vec<Option<&Value>> = std::iter::once(None).chain(playlists.iter().filter(|p| p["items"].as_array().into_iter().flatten().any(|e| truthy(&e["pco"]))).map(Some)).collect();
        for p in candidates {
            let synced: Option<Vec<Value>> = p.map(|p| p["items"].as_array().cloned().unwrap_or_default());
            let rows = plmatch::match_items(&items, &pool, &s["mapping"], synced.as_deref(), threshold);
            let n = rows.iter().filter(|r| r["method"] == "synced").count() as i64;
            if n > best_n {
                (best_rows, best_n, name) = (rows, n, p.map_or_else(String::new, |p| text(p, "name")));
            }
        }
        let mut st = self.st();
        st.rows_key = Some(key);
        st.rows = best_rows.clone();
        st.synced_name = if best_n > 0 { name } else { String::new() };
        best_rows
    }

    /// Pair a plan item title with a presentation (uuid), or remove the pairing (uuid empty). Saved in config.
    pub fn set_mapping(&self, item: &str, uuid: &str, name: &str) -> Result<Value, LinkFail> {
        let s = self.settings();
        let key = plmatch::normalise(item);
        let mut kept: Vec<Value> = s["mapping"].as_array().into_iter().flatten().filter(|m| plmatch::normalise(&text(m, "item")) != key).cloned().collect();
        if !uuid.is_empty() {
            kept.push(json!({"item": squeeze(item).chars().take(200).collect::<String>(), "uuid": uuid, "name": name}));
        }
        let (fields, problem) = link_fields(&json!({"mapping": kept}));
        if !problem.is_empty() || key.is_empty() {
            return Err(LinkFail::Told("pick_both"));
        }
        self.save(&json!({"mapping": fields["mapping"]}))?;
        Ok(self.settings())
    }

    fn save(&self, fields: &Value) -> Result<(), LinkFail> {
        let saved = self.f.settings();
        let mut link = saved["link"].clone();
        for (k, v) in fields.as_object().into_iter().flatten() {
            link[k] = v.clone();
        }
        let mut next = saved;
        next["link"] = link;
        let clean = clean_stored(&next);
        self.f.host().replace_settings(KEY, &clean).map_err(|_| LinkFail::Pro(ProError::new(Kind::Bad, "the settings could not be saved")))?;
        self.st().rows_key = None;
        Ok(())
    }

    // -- follow the plan -------------------------------------------------------------------------------------------------------
    /// ProPresenter's live presentation and Planning Center's LIVE item side by side.
    pub fn follow(&self) -> Value {
        let tr = self.track();
        let pres = self.f.presentation().filter(|_| self.f.is_up());
        let titles: BTreeMap<String, String> = tr.iter().flat_map(|t| t.items.iter().map(|i| (text(i, "id"), text(i, "title")))).collect();
        let live = tr.as_ref().and_then(|t| t.live.clone()).unwrap_or_else(|| json!({}));
        let cur = live["current"]["item"].as_str().map(str::to_owned);
        let mine = pres.as_ref().map(|p| plmatch::items_for(&self.rows(), &p.uuid, &p.name)).unwrap_or_default();
        let pick: Option<String> = if cur.as_ref().is_some_and(|c| mine.contains(c)) {
            cur.clone()
        } else {
            // the matching item nearest after Planning Center's, else the first
            let order: Vec<String> = tr.as_ref().map(plan_item_ids).unwrap_or_default();
            let at: i64 = cur.as_ref().and_then(|c| order.iter().position(|o| o == c)).map_or(-1, |p| p as i64);
            let after = mine.iter().find(|m| order.iter().position(|o| o == *m).is_some_and(|p| p as i64 > at));
            after.or_else(|| mine.first()).cloned()
        };
        let agree = if pres.is_some() && cur.is_some() { json!(pick.is_some() && pick == cur) } else { Value::Null };
        json!({
            "service_type": tr.as_ref().map(|t| t.service_type.clone()),
            "pp": pres.as_ref().map(|p| json!({"uuid": p.uuid, "name": p.name, "song": self.f.is_song(), "slide": self.f.current().text})),
            "pp_item": pick.as_ref().map(|p| json!({"id": p, "title": titles.get(p).cloned().unwrap_or_default()})),
            "pc_item": cur.as_ref().map(|c| json!({"id": c, "title": titles.get(c).cloned().unwrap_or_default()})),
            "controller": live["controller"]["name"],
            "agree": agree,
        })
    }

    /// Added to the Service feed message for panels: which plan item ProPresenter has up. Nothing unless the plan is being followed and ProPresenter is up;
    /// lyrics come by the lyrics feed.
    pub fn feed_extra(&self, st: &str) -> Option<Value> {
        let s = self.settings();
        let mine = self.track().map(|t| t.service_type);
        if !(truthy(&s["follow"]) || truthy(&s["drive"])) || mine.as_deref() != Some(st) || !self.f.is_up() {
            return None;
        }
        let f = self.follow();
        Some(json!({"pp_item": f["pp_item"]["id"], "pp_agree": f["agree"]}))
    }

    /// Tell the Service feed when the item ProPresenter has up changed.
    pub fn changed(&self) {
        if !self.active() {
            return;
        }
        let item = self.follow()["pp_item"]["id"].as_str().map(str::to_owned);
        let moved = {
            let mut st = self.st();
            if item == st.pp_item {
                false
            } else {
                st.pp_item = item;
                true
            }
        };
        if moved {
            self.f.host().announce(FEED_REFRESH, "");
        }
    }

    // -- ProPresenter drives Services LIVE -------------------------------------------------------------------------------------
    fn wait_left(&self, at: f64) -> f64 {
        (self.settings()["dwell_s"].as_f64().unwrap_or(settings::LINK_DWELL_DEFAULT_S) - (at - self.st().act_since)).max(0.0)
    }

    /// One look at what ProPresenter has up. Cheap when nothing changed; the one place a LIVE move starts.
    pub async fn tick(&self, at: Option<f64>) {
        let at = at.unwrap_or_else(|| (self.clock)());
        let s = self.settings();
        let all = self.f.settings();
        let pres = self.f.presentation().filter(|_| self.f.is_up() && configured(&all));
        let uuid = pres.as_ref().map(|p| p.uuid.clone());
        {
            let mut st = self.st();
            if uuid != st.act_uuid {
                st.act_uuid = uuid.clone();
                st.act_name = pres.as_ref().map(|p| p.name.clone()).unwrap_or_default();
                st.act_since = at;
                if uuid.is_some() {
                    st.act_n += 1; // a new activation: one try each, so a re-triggered slide of the same one is not
                }
            }
        }
        let on = truthy(&s["drive"]) && configured(&all) && self.pc_on();
        {
            let mut st = self.st();
            if on && !st.drive_on {
                st.act_since = at; // switched on with something already up: the wait starts now
            }
            st.drive_on = on;
        }
        let due = {
            let st = self.st();
            self.active() && !self.loading.load(Ordering::SeqCst) && at >= st.retry_at && (st.loaded_at == 0.0 || (self.wall)() - st.loaded_at > CACHE_S) && self.f.is_up()
        };
        if due {
            let _ = self.refresh().await;
        }
        let go = {
            let st = self.st();
            !(!on || uuid.is_none() || st.fired == st.act_n || st.firing || at - st.act_since < s["dwell_s"].as_f64().unwrap_or(settings::LINK_DWELL_DEFAULT_S))
        };
        if go {
            self.fire(at).await;
        }
    }

    async fn fire(&self, at: f64) {
        let (n, uuid, name) = {
            let mut st = self.st();
            st.firing = true;
            (st.act_n, st.act_uuid.clone().unwrap_or_default(), st.act_name.clone())
        };
        self.fire_inner(at, n, &uuid, &name).await;
        self.st().firing = false;
    }

    async fn fire_inner(&self, at: f64, n: u64, uuid: &str, name: &str) {
        let tr = self.track();
        let plan = self.plan();
        let (Some(tr), Some(plan)) = (tr.filter(|t| t.plan.is_some()), plan) else {
            self.st().fired = n;
            return self.note("no_plan", name, "", 0, "", true);
        };
        if self.st().loaded_at == 0.0 {
            if at < self.st().retry_at {
                return;
            }
            if self.refresh().await.is_err() {
                return self.note("unread", name, "", 0, "", false);
            }
        }
        if self.st().act_n != n {
            return; // ProPresenter moved on while Relay was reading: this activation is over
        }
        let mut targets = plmatch::items_for(&self.rows(), uuid, name);
        let known = self.st().pool.iter().any(|c| text(c, "uuid") == uuid);
        if targets.is_empty() && !known && (self.wall)() - self.st().loaded_at > 15.0 {
            // a presentation added since the last read: look again once before saying it matches nothing
            let _ = self.refresh().await;
            if self.st().act_n != n {
                return;
            }
            targets = plmatch::items_for(&self.rows(), uuid, name);
        }
        self.st().fired = n; // one try for this activation, whatever it comes to
        if targets.is_empty() {
            return self.note("no_match", name, "", 0, "", true);
        }
        let titles: BTreeMap<String, String> = tr.items.iter().map(|i| (text(i, "id"), text(i, "title"))).collect();
        let order = plan_item_ids(&tr);
        let cur = tr.live.as_ref().and_then(|l| l["current"]["item"].as_str().map(str::to_owned));
        let at_pos: i64 = cur.as_ref().and_then(|c| order.iter().position(|o| o == c)).map_or(-1, |p| p as i64);
        let near = targets.iter().find(|t| order.iter().position(|o| o == *t).is_some_and(|p| p as i64 >= at_pos)).or_else(|| targets.first()).cloned().unwrap_or_default();
        let res = (plan.drive)(tr.service_type.clone(), order, targets, truthy(&self.settings()["allow_back"])).await;
        self.note(&res.result, name, titles.get(&near).map_or("", String::as_str), res.steps, &res.who, true);
    }

    fn note(&self, code: &str, pres: &str, item: &str, steps: u32, who: &str, fin: bool) {
        let words = sentence(code)
            .replace("{item}", if item.is_empty() { "that item" } else { item })
            .replace("{pres}", pres)
            .replace("{who}", if who.is_empty() { "Someone" } else { who })
            .replace("{steps}", &format!("{steps} step{}", if steps == 1 { "" } else { "s" }));
        let entry = json!({"at": (self.wall)(), "presentation": pres, "result": code, "text": words});
        {
            let mut st = self.st();
            st.last = Some(entry.clone());
            if fin {
                st.log.push_front(entry);
                st.log.truncate(LOG_MAX);
            }
        }
        tracing::info!("Link: {} -> {code}", pres.chars().take(60).collect::<String>());
        self.changed();
    }

    // -- the setlist swap ------------------------------------------------------------------------------------------------------
    pub fn playlist_name(day: &str) -> String {
        format!("{OWNED_PREFIX}{}", if day.is_empty() { "plan" } else { day })
    }

    /// The presentation's name as ProPresenter holds it: from the libraries read at refresh, else read once.
    async fn exact_name(&self, uuid: &str, fallback: &str) -> String {
        if let Some(n) = self.st().exact_names.get(uuid) {
            return n.clone();
        }
        let Ok(client) = self.f.need_client() else { return fallback.to_owned() };
        let Ok(path) = seg(uuid) else { return fallback.to_owned() };
        let Ok(body) = client.read(&format!("/v1/presentation/{path}")).await else { return fallback.to_owned() };
        let detail = if body.get("presentation").is_some() { &body["presentation"] } else { &body };
        match detail["id"]["name"].as_str().filter(|n| !n.trim().is_empty()) {
            Some(n) => {
                self.st().exact_names.insert(uuid.to_owned(), n.to_owned());
                n.to_owned()
            }
            None => fallback.to_owned(),
        }
    }

    /// Make (or remake) the Relay-owned ProPresenter playlist for the followed plan: matched presentations in plan order, headers kept. Refuses any playlist
    /// Relay did not create. Writes go through the client, so view-only is refused there before any request.
    pub async fn build_playlist(&self) -> Result<Value, LinkFail> {
        let Some(tr) = self.track().filter(|t| t.plan.is_some()) else { return Err(LinkFail::Told("no_plan")) };
        let client = self.f.need_client()?;
        if client.is_view_only() {
            return Err(ProError::view_only("build a playlist").into());
        }
        self.refresh().await?;
        let rows = self.rows();
        let mut list: Vec<Value> = Vec::new();
        let mut left_out: Vec<String> = Vec::new();
        for r in &rows {
            if r["type"] == "header" {
                list.push(json!({"id": {"uuid": "", "name": r["title"], "index": list.len()}, "type": "header", "is_hidden": false, "is_pco": false}));
            } else if r["status"] == "matched" && r["presentation"].is_object() {
                let u = text(&r["presentation"], "uuid");
                // ProPresenter 22 finds the item by this name, exactly, and ignores the UUIDs (404 otherwise), and rejects the whole body (400) unless
                // presentation_info carries both arrangement fields; empty means the presentation's own arrangement. Both checked live.
                let fallback = Some(text(&r["presentation"], "name")).filter(|n| !n.is_empty()).unwrap_or_else(|| text(r, "title"));
                let exact = self.exact_name(&u, &fallback).await;
                list.push(json!({
                    "id": {"uuid": "", "name": exact, "index": list.len()}, "type": "presentation", "is_hidden": false, "is_pco": false,
                    "presentation_info": {"presentation_uuid": u, "arrangement_name": "", "arrangement_uuid": ""}, "target_uuid": u,
                }));
            } else {
                left_out.push(text(r, "title"));
            }
        }
        if !list.iter().any(|e| e["type"] == "presentation") {
            return Err(LinkFail::Told("nothing_matched"));
        }
        let name = Self::playlist_name(&tr.day);
        let owned = self.settings()["owned"].clone();
        let tree = playlists_in(&client.read("/v1/playlists").await?);
        let uuid = if truthy(&owned["uuid"]) && tree.iter().any(|p| p["uuid"] == owned["uuid"] && p["name"] == owned["name"]) && owned["name"] == name.as_str() {
            text(&owned, "uuid")
        } else if tree.iter().any(|p| text(p, "name") == name) {
            tracing::warn!("Planning Center link: a playlist named {name:?} is already in ProPresenter and is not Relay's");
            return Err(LinkFail::Told("name_taken"));
        } else {
            let made = client.post("/v1/playlists", Some(&json!({"name": name, "type": "playlist"}))).await?;
            let mut uuid = uuid_of(&made);
            if uuid.is_empty() {
                // the answer did not say: find the playlist that was not there before
                let after = playlists_in(&client.read("/v1/playlists").await?);
                let before: BTreeSet<String> = tree.iter().map(|t| text(t, "uuid")).collect();
                uuid = after.iter().find(|p| text(p, "name") == name && !before.contains(&text(p, "uuid"))).map(|p| text(p, "uuid")).unwrap_or_default();
            }
            if uuid.is_empty() {
                return Err(LinkFail::Told("not_identified"));
            }
            self.save(&json!({"owned": {"uuid": uuid, "name": name}}))?;
            uuid
        };
        // The one write to an existing playlist: only ever the one Relay made.
        let owned = self.settings()["owned"].clone();
        if !(!uuid.is_empty() && text(&owned, "uuid") == uuid && text(&owned, "name").starts_with(OWNED_PREFIX)) {
            return Err(LinkFail::Told("not_ours"));
        }
        let path = seg(&uuid).map_err(|_| LinkFail::Invalid)?;
        client.put(&format!("/v1/playlist/{path}"), &Value::Array(list.clone())).await?;
        let back = client.read(&format!("/v1/playlist/{path}")).await?;
        let got: Vec<String> = entries(&back).iter().filter(|e| e["type"] == "presentation").map(|e| text(e, "uuid")).collect();
        let want: Vec<String> = list.iter().filter(|e| e["type"] == "presentation").map(|e| text(e, "target_uuid")).collect();
        Ok(json!({"playlist": {"uuid": uuid, "name": name}, "items": list.len(), "presentations": want.len(), "verified": got == want, "left_out": left_out}))
    }

    // -- what admin shows ------------------------------------------------------------------------------------------------------
    pub fn drive_status(&self, at: Option<f64>) -> Value {
        let at = at.unwrap_or_else(|| (self.clock)());
        let s = self.settings();
        if !truthy(&s["drive"]) {
            return json!({"code": "off", "text": "ProPresenter does not move Planning Center. This is off."});
        }
        if !self.f.configured() || !self.pc_on() {
            return json!({"code": "unavailable", "text": "Needs both ProPresenter and Planning Center to be connected."});
        }
        if !self.f.is_up() {
            return json!({"code": "pp_down", "text": "ProPresenter is not connected."});
        }
        let st = self.st();
        if st.act_uuid.is_none() {
            return json!({"code": "idle", "text": "Nothing is up in ProPresenter."});
        }
        if let (true, Some(last)) = (st.fired == st.act_n, &st.last) {
            return json!({"code": last["result"], "text": last["text"]});
        }
        if let Some(last) = st.last.as_ref().filter(|l| l["result"] == "unread" && at < st.retry_at) {
            return json!({"code": "unread", "text": last["text"]});
        }
        let (name, since) = (st.act_name.clone(), st.act_since);
        drop(st);
        let left = self.wait_left(at);
        json!({"code": "waiting", "text": format!("\"{name}\" has been up {:.0} s; Planning Center moves in {left:.0} s if it stays.", at - since)})
    }

    pub fn state(&self) -> Value {
        let tr = self.track();
        let s = self.settings();
        let mut rows = if tr.is_some() { self.rows() } else { Vec::new() };
        let mut counts: BTreeMap<String, i64> = BTreeMap::new();
        for r in &rows {
            *counts.entry(text(r, "status")).or_insert(0) += 1;
        }
        let cur = tr.as_ref().and_then(|t| t.live.as_ref()).and_then(|l| l["current"]["item"].as_str().map(str::to_owned));
        for r in &mut rows {
            r["current"] = json!(cur.as_ref().is_some_and(|c| r["item_id"] == c.as_str()));
        }
        let service_types: Vec<Value> = self.tracks().iter().map(|t| json!({"id": t.service_type, "name": if t.name.is_empty() { format!("Service type {}", t.service_type) } else { t.name.clone() }})).collect();
        let view_only = self.f.client().map_or_else(|| self.f.settings()["view_only"] == Value::Bool(true), |c| c.is_view_only());
        let mut drive = self.drive_status(None);
        drive["log"] = json!(self.st().log.iter().take(10).cloned().collect::<Vec<_>>());
        drive["dwell_s"] = s["dwell_s"].clone();
        let follow = if self.f.is_up() && tr.is_some() { Some(self.follow()) } else { None };
        let st = self.st();
        let plan = tr.as_ref().and_then(|t| t.plan.as_ref().map(|p| json!({"title": p["title"], "dates": p["dates"], "day": t.day})));
        json!({
            "settings": s,
            "service_types": service_types,
            "service_type": tr.as_ref().map(|t| t.service_type.clone()),
            "plan": plan,
            "pc_connected": self.pc_on(),
            "pp_connected": self.f.configured() && self.f.is_up(),
            "view_only": view_only,
            "rows": rows,
            "counts": counts,
            "synced_playlist": st.synced_name,
            "loaded": {"at": if st.loaded_at > 0.0 { json!(st.loaded_at) } else { Value::Null }, "presentations": st.pool.len(), "playlists": st.playlists.len(), "error": st.load_error},
            "follow": follow,
            "drive": drive,
            "playlist_name": tr.as_ref().filter(|t| t.plan.is_some()).map_or(String::new(), |t| Self::playlist_name(&t.day)),
        })
    }
}

fn uuid_of(body: &Value) -> String {
    for o in [body, &body["playlist"]] {
        if let Some(u) = o["id"]["uuid"].as_str().filter(|u| !u.is_empty()) {
            return u.to_owned();
        }
    }
    String::new()
}
