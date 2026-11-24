//! Screens as a module sees them (#527, #660): `app/screens.py`'s `by_key`, `adopt`, `move_key`, `forget_key`, `rename` and `keys`. A
//! module (signage) links each of its displays to the one core screen of the same name; core owns the tables, so the server hands the
//! module this trait (`Host::provide`, `"core.screens"`) and the module never touches them.

use serde_json::Value;

use crate::module::BoxFuture;

/// A failure a module can only pass on: the database is down, or the change was refused with this fixed sentence.
#[derive(Debug, PartialEq, Eq)]
pub enum ScreenError {
    Refused(&'static str),
    Unavailable,
}

pub type ScreenResult<T> = Result<T, ScreenError>;

pub trait ScreenDirectory: Send + Sync + 'static {
    /// The screen known by `key` (`signage:lobby`), as `{"id", "name", ...}`.
    fn by_key<'a>(&'a self, key: &'a str) -> BoxFuture<'a, ScreenResult<Option<Value>>>;
    /// The screen an older id stands for, made the first time it is seen; `heard = false` only records the key.
    fn adopt<'a>(&'a self, key: &'a str, name: &'a str, heard: bool) -> BoxFuture<'a, ScreenResult<Value>>;
    fn move_key<'a>(&'a self, old: &'a str, new: &'a str) -> BoxFuture<'a, ScreenResult<()>>;
    fn forget_key<'a>(&'a self, key: &'a str) -> BoxFuture<'a, ScreenResult<()>>;
    /// Name a screen (a panel too, with `panel_ok`); a name another screen has merges the two.
    fn rename<'a>(&'a self, id: i64, name: &'a str, panel_ok: bool) -> BoxFuture<'a, ScreenResult<i64>>;
    /// The keys a screen is known by.
    fn keys<'a>(&'a self, id: i64) -> BoxFuture<'a, ScreenResult<Vec<String>>>;
    /// A screen by id, as the table holds it plus its `ref` and `place_ref`.
    fn get<'a>(&'a self, id: i64) -> BoxFuture<'a, ScreenResult<Option<Value>>>;
    /// The screen was heard from just now, at this address.
    fn touch<'a>(&'a self, id: i64, address: &'a str) -> BoxFuture<'a, ScreenResult<()>>;
}

/// What the server provides under [`SCREENS`]: the directory, boxed so a module can name the type.
pub struct ScreensCap(pub std::sync::Arc<dyn ScreenDirectory>);

/// Capability names the server provides to modules (`Host::provide`, `Host::using`).
pub const SCREENS: &str = "core.screens";
/// `Arc<sqlx::SqlitePool>`: relay.db, opened by the server.
pub const DB: &str = "core.db";
/// [`AlertsCap`]: the alerts flashing now.
pub const ALERTS: &str = "core.alerts";
/// [`AppDir`]: the folder holding `static/`, `templates/` and `modules/<id>/`.
pub const APP_DIR: &str = "core.app_dir";
/// [`LinksCap`]: the addresses this machine is reached at.
pub const LINKS: &str = "core.links";

pub struct AppDir(pub std::path::PathBuf);

/// The addresses a traffic class is reached at (`netroute.link_addresses`): given the class, the IPs a TV or phone should use.
pub struct LinksCap(pub Box<LinkFn>);

pub type LinkFn = dyn Fn(&str) -> Vec<String> + Send + Sync;

/// The alerts flashing now, as `[{"id", "to", "from"}]` (`app/modules/signage/takeover.py` `_active_alerts`).
pub struct AlertsCap(pub Box<dyn Fn() -> Vec<Value> + Send + Sync>);

/// What the screen shows now: `{"kind": "board"|"signage"|"", "ref", "why"}` (`app/screens.py` `showing`). `schedule_open(ref)` says
/// whether a named schedule is open. While Signage is off a signage choice falls back to the board the screen last showed.
pub fn showing(screen: &Value, schedule_open: impl Fn(&str) -> bool, signage_on: bool) -> Value {
    let s = |k: &str| screen[k].as_str().unwrap_or("").to_owned();
    let (mut kind, mut ref_, mut why) = (s("normal_kind"), s("normal_ref"), "all the time");
    if !s("during_kind").is_empty() && !s("during_schedule").is_empty() && schedule_open(&s("during_schedule")) {
        (kind, ref_, why) = (s("during_kind"), s("during_ref"), "while its schedule is open");
    }
    if !s("hold_kind").is_empty() {
        (kind, ref_, why) = (s("hold_kind"), s("hold_ref"), "held by hand");
    }
    if kind == "signage" && !signage_on {
        let mut board = s("last_board");
        if board.is_empty() && s("during_kind") == "board" {
            board = s("during_ref");
        }
        if s("normal_kind") == "board" && board.is_empty() {
            board = s("normal_ref");
        }
        return if board.is_empty() { serde_json::json!({"kind": "", "ref": "", "why": ""}) } else { serde_json::json!({"kind": "board", "ref": board, "why": "Signage is off"}) };
    }
    serde_json::json!({"kind": kind, "ref": ref_, "why": if kind.is_empty() { "" } else { why }})
}

/// Where signage playlist items come from besides uploads (`app/modules/signage/sources.py`, #205): a module that adds items provides one
/// as the capability `signage.source.<kind>` ([`SIGNAGE_SOURCE`] plus the kind) and withdraws it when it stops, so signage never names it.
pub const SIGNAGE_SOURCE: &str = "signage.source.";

pub type ResolveFn = dyn Fn(&str) -> Option<Value> + Send + Sync;
pub type DescribeFn = dyn Fn(&str) -> Value + Send + Sync;
pub type ChoicesFn = dyn Fn() -> Vec<Value> + Send + Sync;

pub struct SignageSource {
    pub label: String,
    /// What the player shows for `ref`: `{"type": "image"|"video"|"page", "url", ...}`, or `None` to skip the item.
    pub resolve: Box<ResolveFn>,
    /// How the admin page lists `ref`: `{"name", "thumb", "missing"?}`.
    pub describe: Box<DescribeFn>,
    /// What can be added from it: `[{"ref", "name", "thumb"}]`.
    pub choices: Box<ChoicesFn>,
}
