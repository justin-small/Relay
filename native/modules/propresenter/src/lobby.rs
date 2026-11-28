//! Lobby announcements (#138, stage 7; `pplobby.py`): Relay steps a ProPresenter announcement playlist along by itself, one slide every `interval_s`
//! seconds, for lobby screens.
//!
//! The API says nothing about which playlists are announcement playlists (a playlist has no destination field), so the operator picks one. Each step is
//! one of two calls, both through the client so view-only mode refuses them before a socket is opened: the next cue of the announcement that is up
//! (`GET /v1/announcement/active/{n}/trigger`), or the next item of the playlist (`GET /v1/playlist/{playlist}/{index}/trigger`).
//!
//! Rotation never starts by itself: the operator presses Start (a restart of Relay does not resume it). While it runs, each tick compares what is on the
//! announcements layer with what Relay last put there:
//!
//! * the same: carry on, and step when `interval_s` has passed;
//! * a slide of one of the playlist's own presentations, but not the one Relay put up (the operator nudged it): Relay carries on from where the
//!   operator left it;
//! * something else (a presentation or media that is not in the playlist): the rotation PAUSES and writes nothing. It resumes by itself once the
//!   operator's content is gone (the layer empty for `RESUME_S`), or when the operator puts one of the playlist's own slides up again;
//! * the layer empty although Relay had something up (the operator pressed Clear): the rotation pauses and stays paused until Resume or Stop, because
//!   a deliberate clear should not be undone.
//!
//! A ProPresenter that drops off and comes back does not end the rotation: nothing is written while it is away, and on return Relay starts the
//! playlist over if the layer came back empty.

use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use serde_json::{json, Value};

use crate::client::{text, Kind};
use crate::control::{Control, ControlError};
use crate::follower::Follower;
use crate::settings::{self, configured};

pub const TICK: Duration = Duration::from_secs(1);
/// The follower reads the announcement once a second: a step is not judged before it has seen it.
pub const GRACE_S: f64 = 2.5;
/// How long the layer must stay empty before a rotation paused by the operator carries on.
pub const RESUME_S: f64 = 5.0;
/// Headers, placeholders, audio and live video have nothing to show.
pub const ROTATE_TYPES: [&str; 2] = ["presentation", "media"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Refusal {
    NoPlaylist,
    NotConnected,
    Empty,
}

impl Refusal {
    /// A fixed sentence that is safe to show.
    pub fn message(self) -> &'static str {
        match self {
            Self::NoPlaylist => "Pick the announcement playlist first.",
            Self::NotConnected => "ProPresenter is not connected.",
            Self::Empty => "That playlist has nothing Relay can show (presentations and media only).",
        }
    }
}

const VIEW_ONLY: &str = "View-only mode is on, so Relay did not change anything in ProPresenter.";
const FAILED: &str = "ProPresenter did not take the last step; Relay will try again.";

/// Why a lobby call did not happen.
#[derive(Debug, Clone)]
pub enum LobbyError {
    Refused(Refusal),
    Control(ControlError),
}

impl From<ControlError> for LobbyError {
    fn from(e: ControlError) -> Self {
        Self::Control(e)
    }
}

impl From<Refusal> for LobbyError {
    fn from(r: Refusal) -> Self {
        Self::Refused(r)
    }
}

#[derive(Clone)]
struct Item {
    index: i64,
    name: String,
    kind: String,
    presentation: String,
}

#[derive(Default)]
struct State {
    running: bool,
    paused: &'static str,
    finished: bool,
    items: Vec<Item>,
    playlist: String,
    pos: usize,
    cue: i64,
    fired_at: f64,
    empty_since: Option<f64>,
    steps: u64,
    error: String,
    failures: u32,
    lost: bool,
}

/// What the announcements layer holds, as the follower last read it.
struct Seen {
    on: bool,
    uuid: String,
    cue: Option<i64>,
    cues: i64,
}

pub type Clock = Arc<dyn Fn() -> f64 + Send + Sync>;

pub struct Lobby {
    f: Arc<Follower>,
    ctl: Arc<Control>,
    clock: Clock,
    state: Mutex<State>,
    guard: tokio::sync::Mutex<()>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
}

fn monotonic() -> f64 {
    static START: std::sync::OnceLock<std::time::Instant> = std::sync::OnceLock::new();
    START.get_or_init(std::time::Instant::now).elapsed().as_secs_f64()
}

impl Lobby {
    pub fn new(f: Arc<Follower>, ctl: Arc<Control>) -> Arc<Self> {
        Self::with_clock(f, ctl, Arc::new(monotonic))
    }

    /// With a clock the caller moves by hand (a test).
    pub fn with_clock(f: Arc<Follower>, ctl: Arc<Control>, clock: Clock) -> Arc<Self> {
        Arc::new(Self { f, ctl, clock, state: Mutex::new(State::default()), guard: tokio::sync::Mutex::new(()), task: Mutex::new(None) })
    }

    fn st(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn now(&self) -> f64 {
        (self.clock)()
    }

    // -- lifecycle -----------------------------------------------------------------------------------------------------------
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

    /// Relay is shutting down: stop ticking. Nothing is written to ProPresenter, and nothing is cleared.
    pub fn cancel(&self) {
        self.st().running = false;
        if let Some(t) = self.task.lock().unwrap_or_else(PoisonError::into_inner).take() {
            t.abort();
        }
    }

    // -- settings ------------------------------------------------------------------------------------------------------------
    fn settings(&self) -> Value {
        let s = self.f.settings();
        if s["lobby"].is_object() {
            s["lobby"].clone()
        } else {
            settings::default_lobby()
        }
    }

    // -- reading what is up --------------------------------------------------------------------------------------------------
    /// The announcements layer as the follower last read it, or `None` before it has read the layers.
    fn observed(&self) -> Option<Seen> {
        let layers = self.f.data("status/layers");
        if !layers.is_object() || layers.get("announcements").is_none() {
            return None;
        }
        let ann = self.f.announcement();
        Some(Seen {
            on: settings::truthy(&layers["announcements"]),
            uuid: ann.as_ref().map(|a| a.uuid.clone()).unwrap_or_default(),
            cue: self.f.announcement_index(),
            cues: ann.map_or(0, |a| a.cues.len() as i64),
        })
    }

    fn matches(st: &State, seen: &Seen) -> bool {
        let Some(item) = st.items.get(st.pos) else { return false };
        if !seen.on {
            return false;
        }
        if item.kind == "presentation" {
            return seen.uuid == item.presentation && seen.cue == Some(st.cue);
        }
        seen.uuid.is_empty() // media: the layer is on and no presentation is up on it
    }

    /// Where in the playlist the slide that is up belongs, if it is one of the playlist's presentations: the first match at or after the current position,
    /// else the first match.
    fn find(st: &State, seen: &Seen) -> Option<usize> {
        if seen.uuid.is_empty() {
            return None;
        }
        let hits: Vec<usize> = st.items.iter().enumerate().filter(|(_, i)| i.kind == "presentation" && i.presentation == seen.uuid).map(|(n, _)| n).collect();
        hits.iter().find(|n| **n >= st.pos).or_else(|| hits.first()).copied()
    }

    // -- the operator's buttons ----------------------------------------------------------------------------------------------
    async fn load(&self, playlist: &str) -> Result<(), LobbyError> {
        let body = self.ctl.playlist_items(playlist).await.map_err(LobbyError::Control)?;
        let items: Vec<Item> = body["items"]
            .as_array()
            .into_iter()
            .flatten()
            .filter(|o| ROTATE_TYPES.contains(&text(o, "type").as_str()) && o["hidden"] != true && o["index"].is_i64())
            .map(|o| Item { index: o["index"].as_i64().unwrap_or(0), name: text(o, "name"), kind: text(o, "type"), presentation: text(o, "presentation") })
            .collect();
        if items.is_empty() {
            return Err(Refusal::Empty.into());
        }
        self.st().items = items;
        Ok(())
    }

    fn connected(&self) -> bool {
        self.f.is_up() && self.f.client().is_some()
    }

    /// Start (or restart) from the first item. Refused unless ProPresenter is up and the playlist is picked; a view-only ProPresenter refuses the first
    /// trigger, so nothing is started.
    pub async fn start_rotation(&self) -> Result<Value, LobbyError> {
        {
            let _one = self.guard.lock().await;
            let playlist = text(&self.settings(), "playlist");
            if playlist.is_empty() {
                return Err(Refusal::NoPlaylist.into());
            }
            if !self.connected() {
                return Err(Refusal::NotConnected.into());
            }
            self.st().running = false;
            self.load(&playlist).await?;
            self.st().playlist = playlist;
            self.put_up(0, self.now()).await?;
            let mut st = self.st();
            (st.running, st.paused, st.finished, st.error) = (true, "", false, String::new());
            (st.empty_since, st.failures, st.lost, st.steps) = (None, 0, false, 0);
            tracing::info!("Lobby rotation started ({} item(s), {:.1} s a slide)", st.items.len(), self.settings()["interval_s"].as_f64().unwrap_or(0.0));
        }
        Ok(self.state())
    }

    /// Stop stepping. With `clear`, also clear the announcements layer (a write: view-only refuses it).
    pub async fn stop_rotation(&self, clear: bool) -> Result<Value, LobbyError> {
        {
            let _one = self.guard.lock().await;
            let was = {
                let mut st = self.st();
                let was = st.running;
                (st.running, st.paused, st.finished, st.error) = (false, "", false, String::new());
                was
            };
            if clear {
                self.ctl.clear("announcements").await?;
            }
            if was {
                tracing::info!("Lobby rotation stopped{}", if clear { " and announcements cleared" } else { "" });
            }
        }
        Ok(self.state())
    }

    /// Carry on from the top after a pause, on the operator's say-so.
    pub async fn resume(&self) -> Result<Value, LobbyError> {
        if !self.st().running {
            return self.start_rotation().await;
        }
        {
            let _one = self.guard.lock().await;
            if !self.connected() {
                return Err(Refusal::NotConnected.into());
            }
            let playlist = self.st().playlist.clone();
            self.load(&playlist).await?;
            self.put_up(0, self.now()).await?;
            let mut st = self.st();
            (st.paused, st.empty_since, st.error) = ("", None, String::new());
        }
        Ok(self.state())
    }

    // -- one step ------------------------------------------------------------------------------------------------------------
    /// Trigger the playlist item at `pos` (its first slide) and note it.
    async fn put_up(&self, pos: usize, now: f64) -> Result<(), LobbyError> {
        let (playlist, index) = {
            let st = self.st();
            (st.playlist.clone(), st.items[pos].index)
        };
        self.ctl.trigger_playlist_item(&playlist, &json!(index)).await?;
        let mut st = self.st();
        (st.pos, st.cue, st.fired_at) = (pos, 0, now);
        Ok(())
    }

    async fn advance(&self, now: f64, seen: &Seen) -> Result<(), LobbyError> {
        let (is_presentation, cue, pos, count, playlist) = {
            let st = self.st();
            (st.items.get(st.pos).is_some_and(|i| i.kind == "presentation"), st.cue, st.pos, st.items.len(), st.playlist.clone())
        };
        if is_presentation && cue + 1 < seen.cues {
            self.ctl.trigger_announcement_cue(cue + 1).await?;
            let mut st = self.st();
            (st.cue, st.fired_at) = (cue + 1, now);
            st.steps += 1;
            return Ok(());
        }
        let mut next = pos + 1;
        if next >= count {
            if self.settings()["loop"] != Value::Bool(true) {
                let mut st = self.st();
                (st.running, st.finished) = (false, true); // the last slide stays up
                tracing::info!("Lobby rotation finished");
                return Ok(());
            }
            self.load(&playlist).await?; // the playlist may have been edited since
            next = 0;
        }
        self.put_up(next, now).await?;
        self.st().steps += 1;
        Ok(())
    }

    /// One look at the layer, and a step if one is due. `now` is the clock's, unless a test gives one.
    pub async fn tick(&self, now: Option<f64>) {
        let now = now.unwrap_or_else(|| self.now());
        if !self.st().running {
            return;
        }
        let _one = self.guard.lock().await;
        if !self.st().running {
            return;
        }
        let s = self.settings();
        if text(&s, "playlist") != self.st().playlist {
            // the operator picked another playlist: that ends this rotation
            self.st().running = false;
            tracing::info!("Lobby rotation stopped: another playlist was picked");
            return;
        }
        if !configured(&self.f.settings()) {
            // ProPresenter was switched off in admin: that ends it
            self.st().running = false;
            tracing::info!("Lobby rotation stopped: ProPresenter was switched off");
            return;
        }
        if !self.connected() {
            self.st().lost = true; // write nothing while ProPresenter is away
            return;
        }
        let Some(seen) = self.observed() else { return }; // connected, but the layers have not been read yet
        match self.tick_up(now, &seen, &s).await {
            Ok(()) => {}
            Err(LobbyError::Control(ControlError::Pro(e))) if e.kind == Kind::ViewOnly => {
                let mut st = self.st();
                (st.running, st.error) = (false, VIEW_ONLY.to_owned());
                tracing::warn!("Lobby rotation stopped: view-only mode is on");
            }
            Err(LobbyError::Control(_)) => {
                let mut st = self.st();
                st.failures += 1;
                st.error = FAILED.to_owned();
                if matches!(st.failures, 1 | 10) {
                    tracing::warn!("Lobby rotation step failed");
                }
            }
            Err(LobbyError::Refused(r)) => {
                let mut st = self.st();
                (st.running, st.error) = (false, r.message().to_owned());
            }
        }
    }

    async fn tick_up(&self, now: f64, seen: &Seen, s: &Value) -> Result<(), LobbyError> {
        let (lost, paused, playlist) = {
            let st = self.st();
            (st.lost, st.paused, st.playlist.clone())
        };
        if lost {
            // ProPresenter came back. If the layer is empty it was probably restarted: start the playlist over.
            self.st().lost = false;
            if !seen.on && paused.is_empty() {
                self.load(&playlist).await?;
                self.put_up(0, now).await?;
                let mut st = self.st();
                (st.failures, st.error) = (0, String::new());
                return Ok(());
            }
        }
        let paused = self.st().paused;
        if paused == "cleared" {
            return Ok(());
        }
        if paused == "operator" {
            let at = Self::find(&self.st(), seen);
            if let (true, Some(at)) = (seen.on, at) {
                // the operator is back in the playlist
                let mut st = self.st();
                (st.pos, st.cue, st.fired_at, st.paused, st.empty_since) = (at, seen.cue.unwrap_or(0), now, "", None);
            } else if !seen.on {
                let since = {
                    let mut st = self.st();
                    *st.empty_since.get_or_insert(now)
                };
                if now - since >= RESUME_S {
                    // the operator's content is gone: from the top
                    self.load(&playlist).await?;
                    self.put_up(0, now).await?;
                    let mut st = self.st();
                    (st.paused, st.empty_since, st.error) = ("", None, String::new());
                }
                return Ok(());
            } else {
                self.st().empty_since = None;
                return Ok(());
            }
        }
        let (fired_at, matches) = {
            let st = self.st();
            (st.fired_at, Self::matches(&st, seen))
        };
        if now - fired_at >= GRACE_S && !matches {
            if !seen.on {
                self.st().paused = "cleared";
                tracing::info!("Lobby rotation paused: announcements were cleared");
                return Ok(());
            }
            let at = Self::find(&self.st(), seen);
            let mut st = self.st();
            match at {
                None => {
                    (st.paused, st.empty_since) = ("operator", None);
                    tracing::info!("Lobby rotation paused: something else is on the announcements layer");
                    return Ok(());
                }
                Some(at) => (st.pos, st.cue, st.fired_at) = (at, seen.cue.unwrap_or(0), now), // the operator nudged it: carry on from there
            }
        }
        let fired_at = self.st().fired_at;
        if now - fired_at >= s["interval_s"].as_f64().unwrap_or(settings::LOBBY_INTERVAL_DEFAULT_S) {
            self.advance(now, seen).await?;
            let mut st = self.st();
            (st.failures, st.error) = (0, String::new());
        }
        Ok(())
    }

    // -- what the admin page shows -------------------------------------------------------------------------------------------
    pub fn state(&self) -> Value {
        let s = self.settings();
        let st = self.st();
        let item = if st.running || st.finished { st.items.get(st.pos) } else { None };
        let interval = s["interval_s"].as_f64().unwrap_or(settings::LOBBY_INTERVAL_DEFAULT_S);
        let next = (st.running && st.paused.is_empty()).then(|| ((interval - (self.now() - st.fired_at)).max(0.0) * 10.0).round() / 10.0);
        json!({
            "running": st.running, "paused": st.paused, "finished": st.finished, "waiting": st.running && !self.f.is_up(),
            "playlist": s["playlist"], "interval_s": s["interval_s"], "loop": s["loop"],
            "item": item.map_or("", |i| i.name.as_str()), "item_number": item.map(|_| st.pos + 1), "item_count": if item.is_some() { st.items.len() } else { 0 },
            "cue": item.map(|_| st.cue + 1), "next_in_s": next, "steps": st.steps, "error": st.error,
            "view_only": self.f.settings()["view_only"] == Value::Bool(true), "connected": self.f.is_up(),
        })
    }
}
