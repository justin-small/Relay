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
}
