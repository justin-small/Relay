//! Relay's database, accounts and sessions in Rust (#655, part of #650). The Rust form of `app/db.py`, `app/users.py`,
//! `app/audit.py` and the sign-in rules of `app/main.py`, on the same `relay.db`.
//!
//! * [`Auth`] opens the file and holds the pool; its methods are the account calls (users, sessions, trusted devices, API
//!   tokens, the audit log). Each returns [`Error::Refused`] with a [`Refusal`] whose fixed sentence is the only text that may
//!   reach a browser.
//! * [`perms`] is the catalogue of permissions and the five roles; [`lockout`] counts failed sign-ins per address.
//! * Every `AssertSqlSafe` is a statement built from constants (the column lists, `order by`, `where` fragments written in this
//!   crate) or a table name read from `sqlite_master`; a value from a request is only ever a bound `?`.
//! * [`hash`] verifies the scrypt hashes Python wrote and writes argon2id.

mod accounts;
mod audit;
mod db;
mod devices;
mod error;
mod prefs;
pub mod hash;
pub mod lockout;
pub mod perms;
mod sessions;
mod tokens;

pub use accounts::{NewUser, UserChanges, UserRow};
pub use audit::{Actor, Entry, Record, Search, SearchResult, ACTION_KEEP_DAYS, KEEP_DAYS, MAX_ROWS};
pub use db::{later, now, Auth, AUTH_VERSIONS};
pub use devices::{Device, TrustedDevice};
pub use error::{Error, Refusal, Result};
pub use hash::Cost;
pub use perms::{Permission, User};
pub use sessions::{Session, PASSWORD_SESSION_SECONDS, PIN_ATTEMPTS, PIN_SESSION_SECONDS};
pub use tokens::{TokenRow, TOKENS_PER_USER, TOKEN_DEFAULT_DAYS, TOKEN_EXPIRY_DAYS, TOKEN_MIN_ROLE, TOKEN_PREFIX};
