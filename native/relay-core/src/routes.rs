//! Routes as a module sees them (#544, #658): which source channel feeds which destination channel. A module that owns a destination
//! (the NDI router's "Relay - Lobby TV") registers what it can apply and read (`RouteProvider`), and asks for routes to be changed,
//! presets saved and taken, through [`Routes`]. Core owns the channels and routes tables (`relay-audio`), so the server hands the module
//! this trait (`Host::provide`, `"core.routes"`) and the module never touches them.
//!
//! Everything crosses as JSON and text, the shapes `app/channels.py` and `app/routing.py` use: a channel is `{"ref", "ends", ...}`, a
//! reference is `"channel:relay:<id>"`.

use std::sync::Arc;

use serde_json::Value;

use crate::future::BoxFuture;

/// A failure a module can only pass on: the database is down, or the change was refused with this fixed sentence.
#[derive(Debug, PartialEq, Eq)]
pub enum RouteError {
    Refused(&'static str),
    Unavailable,
}

pub type RouteResult<T> = Result<T, RouteError>;

/// What a source feeds, as a provider sees it: the source channel's ends (`{"provider", "key", ...}`) and the gain asked for.
#[derive(Clone, Debug)]
pub struct WantedSource {
    pub ends: Vec<Value>,
    pub gain_db: Option<f64>,
}

/// One source a provider says a destination takes now. `provider` `None` means the destination's own provider.
#[derive(Clone, Debug)]
pub struct ReadSource {
    pub provider: Option<String>,
    pub key: String,
    pub gain_db: Option<f64>,
}

/// What the module that owns a destination offers (`modules.provide("routes.<id>", {...})` in Python).
pub trait RouteProvider: Send + Sync + 'static {
    /// True when a destination takes one source (a video output), false when it mixes several.
    fn exclusive(&self) -> bool;
    /// True when the gear has a gain per route.
    fn gain(&self) -> bool;
    /// A permission that may route its destinations, besides `network.manage`.
    fn permission(&self) -> Option<String> {
        None
    }
    /// Point the destination `end` at `wanted`. Returns "" or a sentence saying why not.
    fn apply<'a>(&'a self, end: &'a Value, wanted: &'a [WantedSource]) -> BoxFuture<'a, String>;
    /// What the gear does now, or `None` when it cannot say.
    fn read<'a>(&'a self, end: &'a Value) -> BoxFuture<'a, Option<Vec<ReadSource>>>;
}

pub trait Routes: Send + Sync + 'static {
    /// The channel ref of the end (`provider`, `key`), made as a missing end when nothing has reported it yet.
    fn ensure<'a>(&'a self, provider: &'a str, key: &'a str, kind: &'a str, direction: &'a str, gear_name: &'a str) -> BoxFuture<'a, RouteResult<Option<String>>>;
    /// Every channel, oldest first, each `{"ref", "ends", "shown", ...}`.
    fn channels(&self) -> BoxFuture<'_, RouteResult<Vec<Value>>>;
    /// Every destination of `provider` that has a route, each `{"destination", "state", "sources", "wanted", ...}`.
    fn views<'a>(&'a self, provider: &'a str) -> BoxFuture<'a, RouteResult<Vec<Value>>>;
    /// Tell core the channels `provider` has now (`[{"key", "kind", "direction", "gear_name"}]`); ones it no longer reports go missing.
    fn report<'a>(&'a self, provider: &'a str, reported: Vec<Value>) -> BoxFuture<'a, ()>;
    /// `destination` takes `source` (channel refs). The sentences for what would not apply.
    fn set<'a>(&'a self, destination: &'a str, source: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>>;
    /// Take `destination` off everything.
    fn clear<'a>(&'a self, destination: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>>;
    /// Apply every route asked for on a destination of `provider` again.
    fn reapply<'a>(&'a self, provider: &'a str) -> BoxFuture<'a, RouteResult<Vec<String>>>;
    /// The presets saved with `scope`, each `{"id", "name", "routes": [{"destination", "source"}]}`.
    fn salvos<'a>(&'a self, scope: &'a str) -> BoxFuture<'a, RouteResult<Vec<Value>>>;
    /// Save what each of `destinations` takes now as a preset; with `sid` an existing one is replaced.
    fn save_salvo<'a>(&'a self, name: &'a str, destinations: Vec<String>, scope: &'a str, sid: Option<i64>) -> BoxFuture<'a, RouteResult<Value>>;
    fn delete_salvo(&self, sid: i64) -> BoxFuture<'_, RouteResult<()>>;
    /// Apply every route in a preset. The sentences for what would not apply.
    fn take_salvo(&self, sid: i64) -> BoxFuture<'_, RouteResult<Vec<String>>>;
    /// Let go of a channel that is missing (an output that was removed).
    fn forget<'a>(&'a self, channel: &'a str) -> BoxFuture<'a, RouteResult<()>>;
    /// Offer what this module can apply for its destinations under `provider`.
    fn register(&self, provider: &str, api: Arc<dyn RouteProvider>);
    fn withdraw(&self, provider: &str);
}

/// What the server provides under [`ROUTES`], boxed so a module can name the type.
pub struct RoutesCap(pub Arc<dyn Routes>);

/// The capability name (`Host::provide`, `Host::using`).
pub const ROUTES: &str = "core.routes";
