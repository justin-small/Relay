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
}

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
