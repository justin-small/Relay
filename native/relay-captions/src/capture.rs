//! The room's audio on its way to the transcription sessions (`app/audio.py` `AudioCapture`, the part after the source): the meter,
//! the speaking indicator, silence gating, resampling to 24 kHz and the fan-out to every consumer.
//!
//! Where the blocks come from (a lane tap on the audio bus, a sound card) is not here: the server reads them and calls
//! [`Pump::push`]. What it gets back is the 24 kHz, 16-bit, mono frames to hand to [`Consumers::dispatch`].
//!
//! The transcription model hallucinates confident text, often in other languages, when fed a room's noise floor, so by default only
//! audio around detected speech is sent. [`PREROLL_FRAMES`] of lead-in are replayed on onset so a word start is not clipped.

use std::collections::VecDeque;
use std::f64::consts::PI;
use std::sync::{Arc, Mutex, MutexGuard};

use serde_json::{json, Value};

use crate::session::{AudioFeed, AudioSender};

pub const TARGET_RATE: u32 = 24000;
/// ~1920 bytes per send at 24 kHz mono PCM16.
pub const CHUNK_MS: usize = 40;
/// Frames of speech kept ahead of a detected onset: 8 x 40 ms = 320 ms.
pub const PREROLL_FRAMES: usize = 8;
pub const SPEAKING_RMS: f64 = 0.012;
pub const SPEAKING_HOLD_S: f64 = 0.6;
/// A sample at or above this is clipped.
pub const CLIP_LEVEL: f32 = 0.999;
/// How long a clip stays latched on the panel, so a transient is seen at its 1 Hz poll.
pub const CLIP_HOLD_S: f64 = 3.0;
/// The peak marker's fall, dB per second.
pub const PEAK_DECAY_DB_PER_S: f64 = 20.0;
/// The floor of the meter scale: below this the reading is "-inf" for the operator.
pub const DBFS_FLOOR: f64 = -60.0;
/// Frames a consumer may fall behind before its oldest is evicted.
pub const CONSUMER_QUEUE: usize = 100;
/// A stream that delivers nothing for this long has lost its device.
pub const LOST_AFTER_S: f64 = 2.0;
/// Fixed text for the admin page while the input is gone; it names no device.
pub const INPUT_LOST_SENTENCE: &str = "The audio input stopped. Check the interface is plugged in; Relay reconnects on its own when it comes back.";

/// Linear amplitude (0..1) as dBFS, floored rather than -inf.
pub fn dbfs(linear: f64) -> f64 {
    if linear <= 0.0 || !linear.is_finite() {
        return DBFS_FLOOR;
    }
    (20.0 * linear.min(1.0).log10()).max(DBFS_FLOOR)
}

fn round_to(x: f64, places: i32) -> f64 {
    let k = 10f64.powi(places);
    (x * k).round() / k
}

/// A streaming windowed-sinc resampler for mono audio, any ratio. Speech going to a transcription model asks for no more than
/// this: the pass band is 45% of the lower rate, with a Blackman window 32 input samples either side.
pub struct Resampler {
    /// Same rate in and out: nothing to do.
    same: bool,
    ratio: f64,
    cutoff: f64,
    half: usize,
    hist: Vec<f32>,
    /// The absolute input index of `hist[0]`.
    base: i64,
    /// The absolute input position of the next output sample.
    next: f64,
}

impl Resampler {
    pub fn new(from: u32, to: u32) -> Self {
        let ratio = f64::from(from) / f64::from(to);
        // Low-pass at 45% of the lower of the two rates, in units of the input rate.
        let cutoff = (0.45 * f64::from(from.min(to)) / f64::from(from)).min(0.5) * 2.0;
        Self { same: from == to, ratio, cutoff, half: 32, hist: Vec::new(), base: 0, next: 0.0 }
    }

    pub fn process(&mut self, input: &[f32]) -> Vec<f32> {
        if self.same {
            return input.to_vec();
        }
        self.hist.extend_from_slice(input);
        let end = self.base + self.hist.len() as i64;
        let mut out = Vec::with_capacity((input.len() as f64 / self.ratio) as usize + 1);
        let half = self.half as i64;
        while (self.next.floor() as i64) + half < end {
            let centre = self.next.floor() as i64;
            let mut acc = 0.0f64;
            for idx in centre - half + 1..=centre + half {
                let rel = idx - self.base;
                if rel < 0 {
                    continue;
                }
                let d = idx as f64 - self.next;
                let x = f64::from(self.hist[rel as usize]);
                let arg = PI * d * self.cutoff;
                let sinc = if arg.abs() < 1e-12 { 1.0 } else { arg.sin() / arg };
                let w = d / self.half as f64;
                let window = if w.abs() >= 1.0 { 0.0 } else { 0.42 + 0.5 * (PI * w).cos() + 0.08 * (2.0 * PI * w).cos() };
                acc += x * sinc * window * self.cutoff;
            }
            out.push(acc as f32);
            self.next += self.ratio;
        }
        // Keep what the next output still reaches back to.
        let keep_from = (self.next.floor() as i64 - half).max(self.base);
        let drop = (keep_from - self.base) as usize;
        if drop > 0 {
            self.hist.drain(..drop.min(self.hist.len()));
            self.base = keep_from;
        }
        out
    }
}

/// What the meter and the pump keep between blocks.
pub struct Pump {
    gate: bool,
    resampler: Resampler,
    chunk_samples: usize,
    pending: Vec<f32>,
    preroll: VecDeque<Vec<u8>>,
    pub level: f64,
    pub speaking: bool,
    pub peak: f64,
    peak_hold: f64,
    peak_ts: f64,
    pub clipped_samples: u64,
    clip_until: f64,
    last_voice_ts: f64,
    last_audio_ts: Option<f64>,
}

impl Pump {
    /// `native_rate` is the rate of the blocks pushed; `gate` holds back audio until speech is heard.
    pub fn new(native_rate: u32, gate: bool) -> Self {
        Self {
            gate,
            resampler: Resampler::new(native_rate, TARGET_RATE),
            chunk_samples: TARGET_RATE as usize * CHUNK_MS / 1000,
            pending: Vec::new(),
            preroll: VecDeque::new(),
            level: 0.0,
            speaking: false,
            peak: 0.0,
            peak_hold: 0.0,
            peak_ts: 0.0,
            clipped_samples: 0,
            clip_until: 0.0,
            last_voice_ts: f64::NEG_INFINITY,
            last_audio_ts: None,
        }
    }

    /// A new input at another rate (a device that came back): start the resampler over for it.
    pub fn rebuild(&mut self, native_rate: u32) {
        self.resampler = Resampler::new(native_rate, TARGET_RATE);
        self.pending.clear();
    }

    /// One mono float block at `now` (seconds). Returns the PCM frames to send, in order.
    pub fn push(&mut self, block: &[f32], now: f64) -> Vec<Vec<u8>> {
        let rms = if block.is_empty() { 0.0 } else { (block.iter().map(|s| f64::from(*s) * f64::from(*s)).sum::<f64>() / block.len() as f64).sqrt() };
        self.level = rms;
        if rms >= SPEAKING_RMS {
            self.last_voice_ts = now;
        }
        self.speaking = now - self.last_voice_ts < SPEAKING_HOLD_S;
        self.meter(block, now);
        let out = self.resampler.process(block);
        if out.is_empty() {
            return Vec::new();
        }
        self.pending.extend(out);
        let mut frames = Vec::new();
        while self.pending.len() >= self.chunk_samples {
            let frame: Vec<f32> = self.pending.drain(..self.chunk_samples).collect();
            let pcm: Vec<u8> = frame.iter().flat_map(|s| ((s.clamp(-1.0, 1.0) * 32767.0) as i16).to_le_bytes()).collect();
            self.last_audio_ts = Some(now);
            if !self.gate || self.speaking {
                frames.extend(self.preroll.drain(..));
                frames.push(pcm);
            } else {
                if self.preroll.len() == PREROLL_FRAMES {
                    self.preroll.pop_front();
                }
                self.preroll.push_back(pcm);
            }
        }
        frames
    }

    /// Peak, peak-hold and clip detection for one input block. Measured on the input, not the resampled frame: clipping is an
    /// interface or console problem, and the resampler can ring a fraction of a dB past a hot input.
    fn meter(&mut self, block: &[f32], now: f64) {
        if block.is_empty() {
            return;
        }
        let peak = block.iter().fold(0.0f32, |m, s| m.max(s.abs()));
        let clipped = block.iter().filter(|s| s.abs() >= CLIP_LEVEL).count() as u64;
        if clipped > 0 {
            self.clipped_samples += clipped;
            self.clip_until = now + CLIP_HOLD_S;
        }
        self.peak = f64::from(peak);
        let elapsed = if self.peak_ts != 0.0 { now - self.peak_ts } else { 0.0 };
        self.peak_ts = now;
        let mut decayed = self.peak_hold;
        if elapsed > 0.0 {
            decayed *= 10f64.powf(-(PEAK_DECAY_DB_PER_S * elapsed) / 20.0);
        }
        self.peak_hold = self.peak.max(decayed);
    }

    pub fn clipping(&self, now: f64) -> bool {
        now < self.clip_until
    }

    /// The meter's slice of the status (the capture's `status()` fields that come from the audio itself).
    pub fn meter_status(&self, now: f64) -> Value {
        json!({
            "level": round_to(self.level, 5),
            "peak": round_to(self.peak, 5),
            "peak_hold": round_to(self.peak_hold, 5),
            "rms_dbfs": round_to(dbfs(self.level), 1),
            "peak_dbfs": round_to(dbfs(self.peak_hold), 1),
            "clipping": self.clipping(now),
            "clipped_samples": self.clipped_samples,
            "speaking": self.speaking,
            "last_audio_ts": self.last_audio_ts,
        })
    }
}

/// Every transcription session's audio queue. A stalled session must not back-pressure the others: its oldest frame is evicted to
/// make room, and what was lost is counted.
#[derive(Default)]
pub struct Consumers {
    inner: Mutex<(u64, Vec<(u64, AudioSender)>)>,
}

fn lock<T>(m: &Mutex<T>) -> MutexGuard<'_, T> {
    m.lock().unwrap_or_else(|e| e.into_inner())
}

impl Consumers {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    /// A new queue of [`CONSUMER_QUEUE`] frames. The id removes it.
    pub fn add(&self) -> (u64, AudioFeed) {
        let (tx, feed) = AudioFeed::channel(CONSUMER_QUEUE);
        let mut g = lock(&self.inner);
        g.0 += 1;
        let id = g.0;
        g.1.push((id, tx));
        (id, feed)
    }

    pub fn remove(&self, id: u64) {
        lock(&self.inner).1.retain(|(i, _)| *i != id);
    }

    pub fn len(&self) -> usize {
        lock(&self.inner).1.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// Hand one frame to every consumer.
    pub fn dispatch(&self, pcm: &[u8]) {
        let mut g = lock(&self.inner);
        g.1.retain(|(_, tx)| !tx.is_closed());
        for (_, tx) in &g.1 {
            tx.push(pcm.to_vec());
        }
    }
}
