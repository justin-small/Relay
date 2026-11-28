//! Relay's Rust server core (#652, part of #650). The design is in `docs/rust-server.md`.
//!
//! This crate is core: it knows no integration. A product Relay talks to (ProPresenter, a console, a mic) is a module,
//! its own crate under `native/modules/<id>/` that depends on this one and is listed in `relay-server`'s registry.
//! `tests/test_rust_modules.py` fails the build when a core crate depends on a module crate, or a module on another one.

pub mod access;
pub mod gate;
pub mod schedules;
pub mod timewin;
pub mod lanes;
pub mod routes;
pub mod screens;
pub mod config;
pub mod error;
pub mod future;
pub mod hub;
pub mod languages;
pub mod module;
pub mod net;
pub mod paths;
pub mod registry;
pub mod shutdown;
pub mod store;

pub use access::{Authorizer, Permission};
pub use config::Config;
pub use store::{PlainStore, SettingsStore};
pub use error::{AppError, ErrorCode};
pub use module::{AdminPage, Health, HealthState, Host, Listener, Manifest, Module, ViewerPort};
pub use net::{Net, RouteError, Router, UdpOptions};
pub use registry::{ActiveListener, AdminView, Registry, NAV_SECTIONS};
pub use shutdown::Shutdown;
