//! Small shared pieces: an atomic f32 and the constants every part agrees on.

use std::sync::atomic::{AtomicU32, Ordering};

/// Every lane runs at this rate (mirrors `audio.LANE_RATE`).
pub const LANE_RATE: u32 = 48_000;
/// Lanes are numbered 1..=MAX_LANES (mirrors `audio.MAX_LANES`).
pub const MAX_LANES: usize = 32;
/// The most channels an output may have.
pub const MAX_CHANNELS: usize = 32;

/// An f32 that can be read and written from two threads. Stored as bits in an AtomicU32.
#[derive(Debug, Default)]
pub struct AtomicF32(AtomicU32);

impl AtomicF32 {
    pub fn new(v: f32) -> Self {
        Self(AtomicU32::new(v.to_bits()))
    }
    #[inline]
    pub fn get(&self) -> f32 {
        f32::from_bits(self.0.load(Ordering::Relaxed))
    }
    #[inline]
    pub fn set(&self, v: f32) {
        self.0.store(v.to_bits(), Ordering::Relaxed)
    }
    /// Lower the stored value to `v` if `v` is smaller. Only valid for values >= 0, where the bit pattern of an
    /// f32 sorts the same way as the number.
    #[inline]
    pub fn min_nonneg(&self, v: f32) {
        self.0.fetch_min(v.to_bits(), Ordering::Relaxed);
    }
    /// The same for a larger value.
    #[inline]
    pub fn max_nonneg(&self, v: f32) {
        self.0.fetch_max(v.to_bits(), Ordering::Relaxed);
    }
    /// Read the value and put `new` in its place.
    #[inline]
    pub fn swap(&self, new: f32) -> f32 {
        f32::from_bits(self.0.swap(new.to_bits(), Ordering::Relaxed))
    }
}

/// dBFS of a linear level, floored at -120.
pub fn dbfs(linear: f32) -> f32 {
    20.0 * linear.max(1e-6).log10()
}
