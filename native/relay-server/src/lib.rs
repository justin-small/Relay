//! Relay's Rust server (#652, part of #650): the listeners, TLS and port rules of #653 on top of the skeleton.
//!
//! The server opens the plain-HTTP viewer port, the HTTPS panel port and each module's own port, serves the routes its modules
//! have, and forwards the rest to the running FastAPI app until the last route is ported (the strangler, #651). The program is
//! `main.rs`; everything it does is here so the tests can drive it.

pub mod audio;
pub use audio::{internal, lanescap, routescap};
mod web;
pub use web::{pages, statics, status, streams, ws};
mod bridge;
pub use bridge::{forward, front, pyapp};
mod net;
pub use net::{announce, listen, netinfo, netroute, netsock, serve, tls};
pub mod accounts;
pub mod app;
pub mod gate;
pub mod modulehost;
pub mod show;
