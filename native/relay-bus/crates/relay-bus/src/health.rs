//! Glitch counters, in the spirit of Splitwave's health.rs: the realtime code never logs or returns an error, it
//! bumps one of these, and `Engine::status()` reads them.

use std::sync::atomic::{AtomicU64, Ordering::Relaxed};

#[derive(Debug, Default)]
pub struct Glitches {
    /// A lane ran dry for an output (counted once per time it runs dry, not per block it stays dry).
    pub underruns: AtomicU64,
    /// An output skipped ahead because a lane ring held far more than its target (its reader had stalled).
    pub resyncs: AtomicU64,
    /// Lane frames thrown away because a consumer's ring was full.
    pub dropped_frames: AtomicU64,
    /// A retired object could not be handed back for dropping (the discard queue was full), so it was leaked.
    pub discard_overflow: AtomicU64,
    /// A clip or a sink arrived while the realtime side had no room for it, and was refused.
    pub rejected: AtomicU64,
    /// The engine could not send a command because the queue to the realtime side was full.
    pub command_busy: AtomicU64,
}

#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct GlitchCounters {
    pub underruns: u64,
    pub resyncs: u64,
    pub dropped_frames: u64,
    pub discard_overflow: u64,
    pub rejected: u64,
    pub command_busy: u64,
}

impl Glitches {
    pub fn snapshot(&self) -> GlitchCounters {
        GlitchCounters {
            underruns: self.underruns.load(Relaxed),
            resyncs: self.resyncs.load(Relaxed),
            dropped_frames: self.dropped_frames.load(Relaxed),
            discard_overflow: self.discard_overflow.load(Relaxed),
            rejected: self.rejected.load(Relaxed),
            command_busy: self.command_busy.load(Relaxed),
        }
    }
}
