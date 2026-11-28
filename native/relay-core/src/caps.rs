//! Capabilities a module offers or asks for by name (#666): the typed halves of Python's `modules.provide` / `modules.using` for what
//! Planning Center, signage, the panels and the directory hand each other. Core defines the shapes so no module crate names another.
//!
//! * [`PLAN`] ([`PlanCap`]): today's followed plan, provided by Planning Center while it is on.
//! * [`PANELS`] ([`PanelDirectory`]): what a panel's press needs of the panel registry, provided by the server while it owns the panels.
//! * [`SIGNAGE_CONTENT`] ([`SignageContent`]): signage's media library and playlists, provided by signage while it is on.
//! * [`DIRECTORY`] ([`Directory`]) and [`CHANNELS`] ([`ChannelCatalog`]): teams, positions and audio channels by ref. Neither has a Rust
//!   implementation yet, so a module that asks gets `None` and keeps what was typed, as Python does while the directory is empty.

use std::sync::Arc;

use axum::body::Body;
use serde_json::Value;

use crate::future::BoxFuture;

// ------------------------------------------------------------------------------------------------------------------ events
/// Announced (`Host::announce`) when the followed plans change: the directory reads the teams again, crew and the ProPresenter link follow.
pub const PLAN_CHANGED: &str = "plan.changed";
/// Announced with a provider id when a team or position was added, renamed or went missing.
pub const DIRECTORY_CHANGED: &str = "directory.changed";
/// Announced when the service feeds a panel can pick change (a service type was followed or dropped): the panels' status is pushed again.
pub const OFFERED_CHANGED: &str = "plan.offered_changed";
/// Announced when an alert goes on or off the stage display, with `{"id": "<alert id>", "text": "<text>" | null}` as JSON: ProPresenter shows
/// the text on its stage screens and takes it down when the alert ends (#145).
pub const ALERT_STAGE: &str = "alerts.stage";
/// Announced when a panel's own timer settings change: ProPresenter's timers feed sends the panel its new thresholds at once.
pub const PANEL_TIMERS_CHANGED: &str = "panels.timers_changed";
/// Announced when what the plan feeds say of ProPresenter changed (which plan item it has up): the Service feed is sent again.
pub const FEED_REFRESH: &str = "plan.feed_refresh";
/// Announced with the presentation's name when a new presentation comes up in ProPresenter (REAPER's song markers, #413).
pub const PRESENTATION_UP: &str = "presentation.up";

// ---------------------------------------------------------------------------------------------------------------- the plan
/// `Arc<PlanCap>`: what other modules and core ask of the plan Planning Center follows (`plan.*` and `directory.pco*` in Python).
pub const PLAN: &str = "plan";

/// Today's followed plan, as the rest of Relay reads it. Each field is a function of the moment it is called.
pub struct PlanCap {
    /// The service types a panel may be set to show or control (`plan.service_types`).
    pub service_types: Box<dyn Fn() -> Vec<String> + Send + Sync>,
    /// The service feeds a panel can pick, `[{"service_type", "label"}]`, empty until connected (`plan.feed`.offered).
    pub offered: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
    /// "Sunday Morning 9:00 AM", or `""` when no plan is followed (`plan.label`).
    pub label: Box<dyn Fn() -> String + Send + Sync>,
    /// The next service start after now, epoch seconds (`plan.starts_at`).
    pub starts_at: Box<dyn Fn() -> Option<f64> + Send + Sync>,
    /// The service starts in `[from, to)`, epoch seconds each (`schedules.plan_times`).
    pub times: Box<dyn Fn(i64, i64) -> Vec<i64> + Send + Sync>,
    /// Everyone scheduled on the followed plans, once each: `[{"id", "name"}]` (`directory.pco`).
    pub people: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
    /// The teams of the followed service types, `[{"id", "name", "positions": [{"id", "name"}]}]` (`directory.pco.teams`).
    pub teams: Box<dyn Fn() -> Vec<Value> + Send + Sync>,
}

// -------------------------------------------------------------------------------------------------------------- the panels
/// `Arc<PanelsCap>`: the caption panels' registry, as far as a module that moves LIVE from a panel needs it.
pub const PANELS: &str = "core.panels";

pub struct PanelsCap(pub Arc<dyn PanelDirectory>);

pub trait PanelDirectory: Send + Sync + 'static {
    /// Whether `id` is a panel id the registry could hold, and one it has heard of.
    fn known(&self, id: &str) -> bool;
    /// The key the panel must send to press, or `None` while the operator has not let it control.
    fn control_key(&self, id: &str) -> Option<String>;
    /// The panel's Service settings: `{"mode", "service_type", ...}`.
    fn service(&self, id: &str) -> Value;
    /// The name the operator gave the panel.
    fn display_name(&self, id: &str) -> String;
    /// The panel's own timer settings (`amber_s`, `red_s`, `show`), or `None` while it follows the global ones.
    fn timers(&self, _id: &str) -> Option<Value> {
        None
    }
}

// ---------------------------------------------------------------------------------------------------------- ProPresenter's offers
/// `Arc<ProOffers>`: what a panel's menu may offer from ProPresenter, provided by the ProPresenter module while it is on (`propresenter.panel_offers`).
pub const PRO_OFFERS: &str = "propresenter.offers";

pub struct ProOffers {
    /// The slide's lyrics, while the operator has them on for panels and ProPresenter is set up.
    pub lyrics: Box<dyn Fn() -> bool + Send + Sync>,
    /// The timers, while ProPresenter is set up.
    pub timers: Box<dyn Fn() -> bool + Send + Sync>,
}

/// `Arc<TagAction>` under `tag.action.<name>`: what a ProPresenter slide-note tag does in Relay, provided by whoever can do it (core for capture,
/// panels and alerts; a module for its own: `light`, `obs`, `reaper`). It takes the tag's value and says in a sentence what it did.
pub const TAG_ACTION: &str = "tag.action.";

pub type TagActionFn = dyn Fn(Option<String>) -> BoxFuture<'static, Result<String, String>> + Send + Sync;

pub struct TagAction(pub Box<TagActionFn>);

// ------------------------------------------------------------------------------------------------------- signage's content
/// `Arc<ContentCap>`: signage's library, as far as a module that adds files and playlists needs it (`signage.content`).
pub const SIGNAGE_CONTENT: &str = "signage.content";

pub struct ContentCap(pub Arc<dyn SignageContent>);

/// A change signage refused, in a fixed sentence for the log; the admin page never shows it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentError(pub String);

pub type ContentResult<T> = Result<T, ContentError>;

pub trait SignageContent: Send + Sync + 'static {
    /// The most bytes of an image or a video signage keeps.
    fn limit(&self, image: bool) -> u64;
    /// Every media row, as signage lists it (`id`, `name`, `sha256`, ...).
    fn media_list(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>>;
    /// Keep an upload arriving as a body. `Ok(None)` is a file signage does not play or one that is too large; `Ok(Some((sha256, ext, size)))` is kept.
    fn receive(&self, body: Body) -> BoxFuture<'_, ContentResult<Option<(String, String, u64)>>>;
    fn media_add<'a>(&'a self, name: &'a str, sha: &'a str, ext: &'a str, size: u64) -> BoxFuture<'a, ContentResult<Value>>;
    /// Refused (an error) while a playlist still uses the file.
    fn media_delete<'a>(&'a self, id: &'a str) -> BoxFuture<'a, ContentResult<()>>;
    fn playlists(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>>;
    fn playlist_create<'a>(&'a self, name: &'a str) -> BoxFuture<'a, ContentResult<Value>>;
    fn playlist_set_items<'a>(&'a self, id: i64, items: &'a Value) -> BoxFuture<'a, ContentResult<()>>;
    /// Keep a small picture for a media row (its thumbnail in the admin page). A library without thumbnails keeps nothing.
    fn media_thumb<'a>(&'a self, _id: &'a str, _data: &'a [u8]) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async { Ok(()) })
    }
}

// ----------------------------------------------------------------------------------------------------- signage's layers and control
/// `signage.layer.<name>` (`name` is `tag`): what a module puts on a screen above the schedule, as `Arc<SignageLayer>`. Called with the screen's record
/// and the time (epoch seconds); `Some({"playlist_id", "why"})` claims the screen.
pub const SIGNAGE_LAYER: &str = "signage.layer.";

pub type LayerFn = dyn Fn(&Value, i64) -> Option<Value> + Send + Sync;

pub struct SignageLayer(pub Box<LayerFn>);

/// `Arc<ControlCap>`: what a module that drives signage screens (ProPresenter's slide-note tags) needs of signage, provided while signage is on.
pub const SIGNAGE_CONTROL: &str = "signage.control";

pub struct ControlCap(pub Arc<dyn SignageControl>);

pub trait SignageControl: Send + Sync + 'static {
    fn screens(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>>;
    fn groups(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>>;
    fn playlists(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>>;
    fn media_list(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>>;
    /// Send one picture to a screen, a group (`target` is `screen`, `group` or `all`) to play once. `Err` carries signage's own sentence.
    fn push_set<'a>(&'a self, target: &'a str, target_id: i64, media_id: &'a str) -> BoxFuture<'a, ContentResult<()>>;
    /// Put the live captions or the service view (`captions`, `service`) on a screen until cleared.
    fn live_view_set<'a>(&'a self, target: &'a str, target_id: i64, view: &'a str) -> BoxFuture<'a, ContentResult<()>>;
    /// End what was put on a screen; `Ok(false)` when nothing was.
    fn takeover_clear<'a>(&'a self, target: &'a str, target_id: i64) -> BoxFuture<'a, ContentResult<bool>>;
    /// End every push and live view; how many ended.
    fn takeover_clear_all(&self) -> BoxFuture<'_, ContentResult<u64>>;
    /// Tell the players something changed.
    fn bump(&self);
}

// ------------------------------------------------------------------------------------------------------------- directory
/// `Arc<DirectoryCap>`: teams and positions by ref (`app/directory.py`).
pub const DIRECTORY: &str = "core.directory";

pub struct DirectoryCap(pub Arc<dyn Directory>);

/// The provider every ref here names: Planning Center.
pub const PCO: &str = "pco";

/// A team or position the provider lists.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DirItem {
    pub reference: String,
    pub name: String,
    /// A position's team ref; `""` for a team.
    pub parent: String,
    /// A position's team name; `""` for a team.
    pub team: String,
    pub missing: bool,
}

/// How a saved ref and name are shown (`directory.describe`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Described {
    pub name: String,
    pub missing: bool,
    pub unmatched: bool,
}

pub trait Directory: Send + Sync + 'static {
    /// Every position the provider lists or listed, not missing or missing alike.
    fn positions(&self) -> Vec<DirItem>;
    fn describe(&self, reference: &str, saved: &str) -> Described;
    /// Whether this team or position ref is one the provider lists or listed (`directory.item` is not `None`).
    fn knows(&self, reference: &str) -> bool;
    /// Bring what the methods above read up to date. A caller awaits this once before it reads, so the methods stay plain functions.
    fn refresh(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// `"team:pco:12"`.
pub fn team_ref(team_id: &str) -> String {
    format!("team:{PCO}:{team_id}")
}

/// `"position:pco:12/34"`: position 34 of team 12.
pub fn position_ref(team_id: &str, position_id: &str) -> String {
    format!("position:{PCO}:{team_id}/{position_id}")
}

/// `("position", "pco", "12/34")` from `"position:pco:12/34"`; `None` for anything that is not a team or position ref.
pub fn parse_item(text: &str) -> Option<(&str, &str, &str)> {
    let (kind, rest) = text.split_once(':')?;
    let (provider, ident) = rest.split_once(':')?;
    (matches!(kind, "team" | "position") && !provider.is_empty() && !ident.is_empty()).then_some((kind, provider, ident))
}

/// The team ref a team or position ref belongs to (a team's own ref is its own team).
pub fn team_of(text: &str) -> Option<String> {
    let (kind, provider, ident) = parse_item(text)?;
    Some(if kind == "team" { text.to_owned() } else { format!("team:{provider}:{}", ident.split('/').next().unwrap_or("")) })
}

// -------------------------------------------------------------------------------------------------------------- channels
/// `Arc<ChannelsCap>`: the audio channels core holds (`app/channels.py`).
pub const CHANNELS: &str = "core.channels";

pub struct ChannelsCap(pub Arc<dyn ChannelCatalog>);

/// One channel as the rundown reads it.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Channel {
    pub reference: String,
    /// The name shown for it.
    pub shown: String,
    pub number: Option<String>,
    pub missing: bool,
    /// `"source"` or `"destination"`.
    pub direction: String,
}

pub trait ChannelCatalog: Send + Sync + 'static {
    /// The channel a ref names, whether present or missing, or `None` when it names none.
    fn by_ref(&self, reference: &str) -> Option<Channel>;
    /// Every audio channel core holds.
    fn audio(&self) -> Vec<Channel>;
    /// Bring what the methods above read up to date; awaited once before a caller reads.
    fn refresh(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// The id in `"channel:relay:5"`, or `None` for anything else.
pub fn channel_id(text: &str) -> Option<i64> {
    let parts: Vec<&str> = text.split(':').collect();
    if parts.len() != 3 || parts[0] != "channel" || parts[1] != "relay" || parts[2].is_empty() || !parts[2].bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    parts[2].parse().ok()
}

// ------------------------------------------------------------------------------------------------------------ feed extras
/// `Arc<FeedExtra>`: extra fields for a service type's Service feed message, a function of the service type id (`propresenter.feed_extra`):
/// which plan item ProPresenter has up. A module that is off provides none.
pub const FEED_EXTRA: &str = "propresenter.feed_extra";

pub type FeedExtraFn = dyn Fn(&str) -> Option<Value> + Send + Sync;

pub struct FeedExtra(pub Box<FeedExtraFn>);

// ---------------------------------------------------------------------------------------------------------- the link to the plan
/// `Arc<PlanLink>`: the plan Planning Center follows, as far as ProPresenter's link needs it (`plan.follower`, `plan.control` and `plan.api` in Python):
/// the followed service types with their items and LIVE state, and the means to move LIVE the way a panel's Next and Previous do. Provided by Planning
/// Center while it is on.
pub const PLAN_LINK: &str = "plan.link";

/// One followed service type.
#[derive(Debug, Clone, Default)]
pub struct LinkTrack {
    pub service_type: String,
    /// The name Planning Center gives the service type, `""` until read.
    pub name: String,
    /// `{"id", "title", "dates"}` of the plan followed, or `None`.
    pub plan: Option<Value>,
    /// The plan's day as `YYYY-MM-DD`.
    pub day: String,
    /// The plan's items in order: `[{"id", "title", "type"}]`.
    pub items: Vec<Value>,
    /// Planning Center's LIVE answer: `{"current": {"item"}, "controller": {"id", "name"}}`.
    pub live: Option<Value>,
}

/// How a try at moving LIVE ended: a fixed code, the steps pressed, and the name of whoever holds control when it was someone else.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct DriveResult {
    pub result: String,
    pub steps: u32,
    pub who: String,
}

pub type DriveFn = dyn Fn(String, Vec<String>, Vec<String>, bool) -> BoxFuture<'static, DriveResult> + Send + Sync;

pub struct PlanLink {
    /// A token is saved: Planning Center is usable.
    pub connected: Box<dyn Fn() -> bool + Send + Sync>,
    pub tracks: Box<dyn Fn() -> Vec<LinkTrack> + Send + Sync>,
    /// Move LIVE of service type `.0` to one of the item ids `.2` (`.1` is the plan's item ids in order); `.3` allows going back.
    pub drive: Box<DriveFn>,
}
