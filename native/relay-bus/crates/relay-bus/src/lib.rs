//! Relay audio bus core (issue 599 spike, phase 1).
//!
//! A pure-Rust engine with the same semantics as `app/audiobus.py`: 32 mono lanes at 48 kHz, inputs that feed lanes,
//! a crosspoint matrix, outputs that each follow their own clock, clips injected on top, meters, taps for
//! speech-to-text. There is no device code and no Python here; streams come from a `Backend`, and the only
//! backend in this phase is the deterministic `fake` one the tests drive on a virtual clock.
//!
//! The rule the design hangs on: realtime callbacks never allocate, free, lock or wait. Everything they need is
//! built by the engine and moved to them through SPSC queues, and everything they retire comes back through a
//! discard queue (see `rt.rs`).

pub mod analysis;
pub mod backend;
pub mod config;
pub mod engine;
pub mod error;
pub mod fake;
pub mod health;
pub mod meter;
pub mod status;
pub mod tap;
pub mod util;

mod input;
mod output;
mod resamp;
mod rt;

pub use analysis::probe::{ProbeHub, ProbeReading, ProbeSpec, ProbeStatus};
pub use backend::{Backend, DeviceFormat, InputCallback, OutputCallback, Stream};
pub use config::{db_to_gain, input_id, Cell, ChannelSel, Config, LaneSpec, OutputSpec, DEVICE_SOURCE};
pub use engine::Engine;
pub use error::Error;
pub use health::GlitchCounters;
pub use meter::MeterReading;
pub use status::{InputStatus, LaneStatus, OutputKind, OutputStatus, Status, StreamState, TapStatus};
pub use tap::{TapOptions, TapRead};
pub use util::{LANE_RATE, MAX_LANES};
