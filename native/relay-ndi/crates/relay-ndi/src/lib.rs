//! Relay's NDI engine (issue 606). Everything that talks to the NDI runtime lives here and runs without Python:
//! the finder, one receiver thread per source, senders and routers. The picture scopes, test patterns and meters
//! that work on what they carry are in their own modules.

pub mod audio;
pub mod ffi;
pub mod find;
pub mod health;
pub mod patterns;
pub mod pcm;
pub mod picture;
pub mod recv;
pub mod route;
pub mod scopes;
pub mod send;
pub mod sink;

pub use ffi::{api, set_search_dirs};
