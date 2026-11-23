//! Relay's Rust server core (#652, part of #650). The design is in `docs/rust-server.md`.
//!
//! This crate is core: it knows no integration. A product Relay talks to (ProPresenter, a console, a mic) is a module,
//! its own crate under `native/modules/<id>/` that depends on this one and is listed in `relay-server`'s registry.
//! `tests/test_rust_modules.py` fails the build when a core crate depends on a module crate, or a module on another one.

pub mod config;
pub mod error;
pub mod hub;
pub mod languages;
pub mod module;
pub mod paths;
pub mod registry;
pub mod shutdown;

pub use config::Config;
pub use error::{AppError, ErrorCode};
pub use module::{Health, HealthState, Host, Listener, Manifest, Module};
pub use registry::{ActiveListener, Registry};
pub use shutdown::Shutdown;
