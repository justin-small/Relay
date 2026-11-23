//! Relay's settings and secrets in Rust (#656, part of #650): `app/config.py` and `app/secretbox.py`.
//!
//! [`Settings`] loads, validates and saves config.json with the Python server's defaults and clamping, and [`SecretBox`] seals the
//! secrets in it with the same key store, so an install upgrades in place and can go back. See `docs/rust-server.md`.

pub mod private;
pub mod secretbox;
pub mod settings;

pub use secretbox::{SecretBox, Store, Unavailable};
pub use settings::Settings;
