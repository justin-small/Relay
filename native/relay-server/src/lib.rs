//! Relay's Rust server (#652, part of #650): the listeners, TLS and port rules of #653 on top of the skeleton.
//!
//! The server opens the plain-HTTP viewer port, the HTTPS panel port and each module's own port, serves the routes its modules
//! have, and forwards the rest to the running FastAPI app until the last route is ported (the strangler, #651). The program is
//! `main.rs`; everything it does is here so the tests can drive it.

pub mod accounts;
pub mod announce;
pub mod app;
pub mod audio;
pub mod forward;
pub mod front;
pub mod gate;
pub mod internal;
pub mod lanescap;
pub mod listen;
pub mod modulehost;
pub mod netinfo;
pub mod netroute;
pub mod netsock;
pub mod pages;
pub mod pyapp;
pub mod routescap;
pub mod serve;
pub mod show;
pub mod statics;
pub mod status;
pub mod streams;
pub mod tls;
pub mod ws;
