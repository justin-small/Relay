//! Metering and loudness maths that reads the bus (issue 604).
//!
//! Everything here is pure: samples go in, numbers come out, and no clock or device is touched, so the tests feed
//! known signals and read exact values. `probe.rs` is the only part that touches the engine: it opens a tap per
//! lane a meter reads and pumps the samples through these analysers on its own thread, so a meter sees every sample
//! whatever Python is doing.
//!
//! * `spl`: the sound-level meter: A and C weighting, Fast and Slow, an A-weighted Leq, 31 third-octave bands.
//! * `loudness`: the stream-mix meter: BS.1770 momentary, short-term and integrated loudness, loudness range, true
//!   peak, left/right correlation, octave bands, and one row a second.
//! * `filters` and `gating` are ported from PLVS (see `THIRD-PARTY-NOTICES.md`).

#[allow(dead_code)]
mod filters;
#[allow(dead_code)]
mod gating;
pub mod loudness;
pub mod probe;
pub mod spl;
