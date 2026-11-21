//! The configuration the engine takes: the same shape as the output of Python's `audiobus.normalize()`
//! (lanes, outputs, matrix cells `{in, out, ch, gain_db}`), as plain Rust types. Phase 2 converts the Python dict.

use crate::error::Error;
use crate::util::{MAX_CHANNELS, MAX_LANES};
use std::collections::HashSet;

pub const MIN_LATENCY_MS: u32 = 20;
pub const MAX_LATENCY_MS: u32 = 500;
pub const DEFAULT_LATENCY_MS: u32 = 60;
pub const GAIN_MIN_DB: f32 = -90.0;
pub const GAIN_MAX_DB: f32 = 24.0;

/// The `source` / `output` name that means "a device the audio backend opens" (PortAudio in the Python bus, cpal in
/// the Rust one). Any other name is a module's network source or sender: the engine does not open anything for it;
/// Python feeds it with `Engine::push` and drains it with `Engine::pull`.
pub const DEVICE_SOURCE: &str = "portaudio";

/// Which channel of an input a lane takes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ChannelSel {
    /// One channel, counted from 0. Past the last channel it means the last channel.
    Index(usize),
    /// The average of every channel.
    Mix,
}

#[derive(Debug, Clone, PartialEq)]
pub struct LaneSpec {
    /// 1..=32
    pub lane: usize,
    pub name: String,
    pub source: String,
    pub device: Option<String>,
    pub channel: ChannelSel,
}

#[derive(Debug, Clone, PartialEq)]
pub struct OutputSpec {
    pub id: String,
    pub name: String,
    pub output: String,
    pub device: Option<String>,
    /// 1..=32
    pub channels: usize,
    /// 20..=500. The ring of each lane an output reads is held at about half of this; the rest is the output's
    /// own block and the device's buffer.
    pub latency_ms: u32,
}

/// One crosspoint: lane `lane` into channel `ch` (counted from 1) of output `out`, at `gain_db`.
#[derive(Debug, Clone, PartialEq)]
pub struct Cell {
    pub lane: usize,
    pub out: String,
    pub ch: usize,
    pub gain_db: f32,
}

#[derive(Debug, Clone, PartialEq, Default)]
pub struct Config {
    pub enabled: bool,
    pub lanes: Vec<LaneSpec>,
    pub outputs: Vec<OutputSpec>,
    pub matrix: Vec<Cell>,
}

/// dB to a linear gain; at or below -90 dB it is exactly zero (mute).
pub fn db_to_gain(db: f32) -> f32 {
    if db.is_nan() || db <= GAIN_MIN_DB {
        0.0
    } else {
        10f32.powf(db.min(GAIN_MAX_DB) / 20.0)
    }
}

/// The id an input goes by: `push(input_id, ...)` takes this. Matches the Python key `(source, device.lower())`.
pub fn input_id(source: &str, device: &Option<String>) -> String {
    format!("{}:{}", source.trim().to_lowercase(), device.as_deref().unwrap_or("").trim().to_lowercase())
}

impl LaneSpec {
    pub fn input_id(&self) -> String {
        input_id(&self.source, &self.device)
    }
    pub fn is_device(&self) -> bool {
        self.source == DEVICE_SOURCE
    }
}

impl OutputSpec {
    pub fn is_device(&self) -> bool {
        self.output == DEVICE_SOURCE
    }
}

impl Config {
    /// Refuse a configuration that `normalize()` would never produce. The engine does not clamp or drop quietly:
    /// that is Python's job, and a mismatch here means a bug on that side worth seeing.
    pub fn validate(&self) -> Result<(), Error> {
        let bad = |s: String| Err(Error::Config(s));
        let mut seen = HashSet::new();
        for l in &self.lanes {
            if !(1..=MAX_LANES).contains(&l.lane) {
                return bad(format!("lane {} is not in 1..={MAX_LANES}", l.lane));
            }
            if !seen.insert(l.lane) {
                return bad(format!("lane {} appears twice", l.lane));
            }
        }
        let mut ids = HashSet::new();
        for o in &self.outputs {
            if o.id.is_empty() || !ids.insert(o.id.as_str()) {
                return bad(format!("output id {:?} is empty or repeated", o.id));
            }
            if !(1..=MAX_CHANNELS).contains(&o.channels) {
                return bad(format!("output {} has {} channels", o.id, o.channels));
            }
            if !(MIN_LATENCY_MS..=MAX_LATENCY_MS).contains(&o.latency_ms) {
                return bad(format!("output {} latency {} ms is outside {MIN_LATENCY_MS}..={MAX_LATENCY_MS}", o.id, o.latency_ms));
            }
        }
        let mut cells = HashSet::new();
        for c in &self.matrix {
            let Some(o) = self.outputs.iter().find(|o| o.id == c.out) else {
                return bad(format!("matrix cell names unknown output {:?}", c.out));
            };
            if !(1..=MAX_LANES).contains(&c.lane) || !(1..=o.channels).contains(&c.ch) {
                return bad(format!("matrix cell lane {} -> {}:{} is out of range", c.lane, c.out, c.ch));
            }
            if !c.gain_db.is_finite() {
                return bad(format!("matrix cell lane {} -> {}:{} has a gain that is not a number", c.lane, c.out, c.ch));
            }
            if !cells.insert((c.lane, c.out.as_str(), c.ch)) {
                return bad(format!("matrix cell lane {} -> {}:{} appears twice", c.lane, c.out, c.ch));
            }
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn gain_floor_and_values() {
        assert_eq!(db_to_gain(-90.0), 0.0);
        assert_eq!(db_to_gain(-120.0), 0.0);
        assert!((db_to_gain(0.0) - 1.0).abs() < 1e-6);
        assert!((db_to_gain(-6.0206) - 0.5).abs() < 1e-4);
        assert!((db_to_gain(6.0206) - 2.0).abs() < 1e-3);
        assert!(db_to_gain(-89.9) > 0.0);
        assert!((db_to_gain(40.0) - db_to_gain(24.0)).abs() < 1e-6, "clamped at +24");
    }

    #[test]
    fn validation_catches_bad_shapes() {
        let mut cfg = Config { enabled: true, ..Default::default() };
        cfg.outputs.push(OutputSpec { id: "a".into(), name: "".into(), output: "portaudio".into(), device: None, channels: 2, latency_ms: 60 });
        cfg.matrix.push(Cell { lane: 1, out: "a".into(), ch: 2, gain_db: 0.0 });
        assert!(cfg.validate().is_ok());
        cfg.matrix.push(Cell { lane: 1, out: "a".into(), ch: 3, gain_db: 0.0 });
        assert!(cfg.validate().is_err(), "channel 3 of a 2 channel output");
        cfg.matrix.pop();
        cfg.outputs[0].latency_ms = 5;
        assert!(cfg.validate().is_err(), "latency below 20 ms");
    }

    #[test]
    fn input_ids_fold_case() {
        assert_eq!(input_id("AES67", &Some(" Stage ".into())), "aes67:stage");
        assert_eq!(input_id("portaudio", &None), "portaudio:");
    }
}
