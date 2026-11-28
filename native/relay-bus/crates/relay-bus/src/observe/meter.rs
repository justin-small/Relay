//! Level, decaying peak hold and clip indicator for one stream of samples.
//!
//! The realtime side owns a `Meter` and publishes into a `MeterShared` with atomics; `status()` reads those.
//! Time here is audio time (samples fed / rate), not the wall clock, so the realtime code never asks for the time
//! and the tests can run it on a virtual clock. Constants mirror app/audio.py.

use crate::util::AtomicF32;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering::Relaxed};
use std::sync::Arc;

pub const CLIP_LEVEL: f32 = 0.999;
pub const CLIP_HOLD_S: f32 = 3.0;
pub const PEAK_DECAY_DB_PER_S: f32 = 20.0;

/// What the realtime side publishes.
#[derive(Debug, Default)]
pub struct MeterShared {
    pub rms: AtomicF32,
    /// Peak with a decaying hold.
    pub peak: AtomicF32,
    pub clipping: AtomicBool,
    pub clipped_samples: AtomicU64,
    /// Total frames fed; the engine turns a change in this into "last input age".
    pub frames: AtomicU64,
}

impl MeterShared {
    pub fn reset(&self) {
        self.rms.set(0.0);
        self.peak.set(0.0);
        self.clipping.store(false, Relaxed);
    }

    pub fn reading(&self) -> MeterReading {
        MeterReading {
            rms: self.rms.get(),
            peak: self.peak.get(),
            clipping: self.clipping.load(Relaxed),
            clipped_samples: self.clipped_samples.load(Relaxed),
        }
    }
}

/// A copy of a meter for `status()`. Levels are linear; use `util::dbfs` for dBFS.
#[derive(Debug, Clone, Copy, Default, PartialEq)]
pub struct MeterReading {
    pub rms: f32,
    pub peak: f32,
    pub clipping: bool,
    pub clipped_samples: u64,
}

pub(crate) struct Meter {
    hold: f32,
    clip_left_s: f32,
    shared: Arc<MeterShared>,
}

impl Meter {
    pub fn new(shared: Arc<MeterShared>) -> Self {
        Self { hold: 0.0, clip_left_s: 0.0, shared }
    }

    /// Feed `frames` samples read from `data` starting at `offset`, `stride` apart (stride = channel count for
    /// one channel of an interleaved block), at `rate` Hz.
    pub fn feed(&mut self, data: &[f32], offset: usize, stride: usize, frames: usize, rate: f32) {
        if frames == 0 {
            return;
        }
        let (mut sumsq, mut peak, mut clipped) = (0.0f32, 0.0f32, 0u64);
        for &x in data[offset..].iter().step_by(stride).take(frames) {
            let a = x.abs();
            sumsq += x * x;
            peak = peak.max(a);
            clipped += (a >= CLIP_LEVEL) as u64;
        }
        let dt = frames as f32 / rate;
        // 20 dB per second is a factor of 10^(-dt).
        self.hold = peak.max(self.hold * 10f32.powf(-PEAK_DECAY_DB_PER_S * dt / 20.0));
        if clipped > 0 {
            self.clip_left_s = CLIP_HOLD_S;
            self.shared.clipped_samples.fetch_add(clipped, Relaxed);
        } else {
            self.clip_left_s = (self.clip_left_s - dt).max(0.0);
        }
        self.shared.rms.set((sumsq / frames as f32).sqrt());
        self.shared.peak.set(self.hold);
        self.shared.clipping.store(self.clip_left_s > 0.0, Relaxed);
        self.shared.frames.fetch_add(frames as u64, Relaxed);
    }
}
