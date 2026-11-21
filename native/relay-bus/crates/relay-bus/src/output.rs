//! The output side: what runs inside an output stream's callback (or, for a "pull" output, on the caller's thread
//! when Python asks for frames).
//!
//! Each output follows its own clock. For every lane it reads there is a `LaneRx`: that lane's ring plus a
//! mono cubic (Catmull-Rom) interpolator running at about 1:1 whose ratio a small PI loop adjusts to hold the
//! ring at a target fill. (The ratio never leaves 1 +- 0.5%, so a four-point interpolator is enough; a sinc
//! resampler here cost more than everything else in the heavy case together. A real rate conversion, a device
//! that is not 48 kHz, is still sinc and is done once per input device and once per output, not per lane.) If
//! the device's clock is fast the ring drains and the loop stretches the audio by a fraction of a percent; if
//! slow, it squeezes it. The lanes are then mixed through the matrix at 48 kHz, converted to the device's rate
//! if it is not 48 kHz, and handed to the device. Clips from `inject` are added at the very end, on the device
//! block being filled, so they do not wait behind the latency buffer.
//!
//! The loop works per lane, not per output, because two lanes fed by two different input devices have two
//! different clock errors against this output; one ratio could only fix one of them.

use crate::health::Glitches;
use crate::meter::{Meter, MeterShared};
use crate::resamp::FixedIn;
use crate::rt::{Discard, Discards};
use crate::util::{AtomicF32, LANE_RATE, MAX_LANES};
use rtrb::{Consumer, RingBuffer};
use std::sync::atomic::{AtomicU64, Ordering::Relaxed};
use std::sync::Arc;

/// Frames (at 48 kHz) an output mixes at a time: 5.3 ms. The device may ask for any block size; leftovers wait in
/// `carry`.
pub(crate) const CHUNK: usize = 256;
/// Each lane ring holds this many frames (16384 = 341 ms). It has to hold the resync threshold (`high`, below)
/// with room to spare and nothing more: every ring page is touched as the write position wraps, so a 2 s ring
/// per (lane, output) pair was 49 MB of resident memory in the 32-lane, four-output case.
pub(crate) const RING_FRAMES: usize = 16384;
/// The drift loop never moves the ratio more than this from 1 (0.5%), as in the Python bus.
pub const MAX_TRIM: f32 = 0.005;
const MAX_CLIPS: usize = 16;

// The drift loop. The fill error is measured in seconds of audio; the ratio trim is dimensionless:
//     trim = -(KP * error + integral),   integral += KI * error * dt
// With the ring modelled as an integrator, this is a second-order loop with natural frequency sqrt(KI) = 0.35 rad/s
// and damping KP / (2 sqrt(KI)) = 1: about 15 s to settle on a 0.3% offset, with a peak fill error of about 5 ms.
// (Real clocks differ by tens of ppm, which settles with a peak error far below a millisecond.) The fill is
// smoothed first (FILL_TAU_S) because it is sampled at block boundaries and has a sawtooth of a block or two, which
// must not reach the ratio.
const KP: f32 = 0.7;
const KI: f32 = 0.12;
const FILL_TAU_S: f32 = 0.5;

/// Where a lane's ring is held: half of the output's latency, but not so little that normal block sizes starve it.
pub(crate) fn target_frames(latency_ms: u32) -> usize {
    (latency_ms as usize * LANE_RATE as usize / 1000 / 2).max(3 * CHUNK)
}

/// Shared with the engine so `status()` can read what the realtime side measured, and so the numbers survive an
/// output being re-opened.
#[derive(Debug)]
pub struct OutputShared {
    pub underruns: AtomicU64,
    pub resyncs: AtomicU64,
    pub frames_out: AtomicU64,
    /// Mean smoothed ring fill over the lanes being read, in frames (0 when none).
    pub fill: AtomicF32,
    /// Smallest and largest instantaneous fill since `status()` last looked.
    pub fill_min: AtomicF32,
    pub fill_max: AtomicF32,
    /// Mean resample ratio relative to nominal right now (1.003 = playing 0.3% slower than the lane delivers it).
    /// It includes the loop's proportional term, so it wobbles a little around the settled value.
    pub ratio: AtomicF32,
    /// The settled part of it: what the loop's integrator has learned the clock offset to be. This is the
    /// number to read as "this output's clock is 0.3% fast" (1.003).
    pub drift: AtomicF32,
    pub dropped: Arc<AtomicU64>,
    pub meters: Vec<Arc<MeterShared>>,
}

impl OutputShared {
    pub fn new(channels: usize) -> Self {
        Self {
            underruns: AtomicU64::new(0),
            resyncs: AtomicU64::new(0),
            frames_out: AtomicU64::new(0),
            fill: AtomicF32::new(0.0),
            fill_min: AtomicF32::new(f32::MAX),
            fill_max: AtomicF32::new(0.0),
            ratio: AtomicF32::new(1.0),
            drift: AtomicF32::new(1.0),
            dropped: Arc::new(AtomicU64::new(0)),
            meters: (0..channels).map(|_| Arc::new(MeterShared::default())).collect(),
        }
    }
}

#[derive(PartialEq, Eq, Clone, Copy)]
enum Phase {
    /// Waiting for the ring to reach its target before reading (at start, and after running dry).
    Priming,
    Running,
}

/// One lane's ring at one output, with its drift loop.
pub(crate) struct LaneRx {
    rx: Consumer<f32>,
    /// Four samples of history, then the block's new input. See `pull`.
    inbuf: Vec<f32>,
    /// Read position in `inbuf`, in [1, 2) between blocks.
    pos: f64,
    phase: Phase,
    target: usize,
    high: usize,
    ema: f32,
    alpha: f32,
    integral: f32,
    rel: f32,
    /// Just left priming: the first block runs the resampler over real input to fill its filter history and is
    /// not played (its start is the filter's warm-up transient). The block after it fades in.
    warm_up: bool,
    fade_in: bool,
}

pub(crate) enum Pulled {
    /// Nothing to add this block: the ring is priming or ran dry.
    Silent,
    Audio,
}

impl LaneRx {
    /// `seed_drift` is the output's learned drift so far (see `OutputShared::drift`): a lane added to a running
    /// output starts near where the others settled instead of at 1.0.
    pub fn new(rx: Consumer<f32>, target: usize, seed_drift: f32) -> Self {
        let inbuf = vec![0.0; 4 + CHUNK + CHUNK / 32 + 8];
        let dt = CHUNK as f32 / LANE_RATE as f32;
        let integral = (1.0 - seed_drift).clamp(-MAX_TRIM, MAX_TRIM);
        Self {
            rx,
            inbuf,
            pos: 1.0,
            phase: Phase::Priming,
            target,
            // Past this the reader must have stalled; skip ahead rather than play old audio late.
            high: target + (2 * target).max(LANE_RATE as usize / 10),
            ema: target as f32,
            alpha: 1.0 - (-dt / FILL_TAU_S).exp(),
            integral,
            rel: 1.0 - integral,
            warm_up: false,
            fade_in: false,
        }
    }

    /// The offset the integrator has learned, as a ratio.
    fn drift(&self) -> f32 {
        1.0 - self.integral
    }

    pub fn running(&self) -> bool {
        self.phase == Phase::Running
    }

    fn skip(&mut self, n: usize) {
        if n > 0 {
            if let Ok(chunk) = self.rx.read_chunk(n) {
                chunk.commit_all();
            }
        }
    }

    /// Throw away what is queued and wait for the target again before the next read.
    pub fn park(&mut self) {
        let n = self.rx.slots();
        self.skip(n);
        self.phase = Phase::Priming;
    }

    /// Produce CHUNK frames of this lane into `out` (48 kHz, paced by the caller's clock).
    pub fn pull(&mut self, out: &mut [f32], g: &Glitches, sh: &OutputShared) -> Pulled {
        let mut slots = self.rx.slots();
        if self.phase == Phase::Priming {
            if slots < self.target {
                return Pulled::Silent;
            }
            self.skip(slots - self.target);
            slots = self.target;
            self.ema = self.target as f32;
            self.phase = Phase::Running;
            self.inbuf[..4].fill(0.0);
            self.pos = 1.0;
            self.warm_up = true;
        }
        if slots > self.high {
            // The reader stalled and the ring filled up behind it. Jump back to the target.
            self.skip(slots - self.target);
            slots = self.target;
            self.ema = self.target as f32;
            sh.resyncs.fetch_add(1, Relaxed);
            g.resyncs.fetch_add(1, Relaxed);
        }
        sh.fill_min.min_nonneg(slots as f32);
        sh.fill_max.max_nonneg(slots as f32);

        // The loop.
        self.ema += self.alpha * (slots as f32 - self.ema);
        let err_s = (self.ema - self.target as f32) / LANE_RATE as f32;
        let dt = CHUNK as f32 / LANE_RATE as f32;
        self.integral = (self.integral + KI * err_s * dt).clamp(-MAX_TRIM, MAX_TRIM);
        let trim = (-(KP * err_s + self.integral)).clamp(-MAX_TRIM, MAX_TRIM);
        self.rel = 1.0 + trim;

        // Cubic interpolation at a read position that advances 1/rel input frames per output frame. `inbuf` holds
        // four frames of history and then this block's new input; `pos` is where the first output sits (in [1, 2)),
        // so it needs `pos + CHUNK/rel` rounded down, minus one, new frames, and keeps the last four as history.
        let step = 1.0 / self.rel as f64;
        let end = self.pos + CHUNK as f64 * step;
        let need = end as usize - 1;
        if slots < need {
            sh.underruns.fetch_add(1, Relaxed);
            g.underruns.fetch_add(1, Relaxed);
            self.phase = Phase::Priming;
            return Pulled::Silent;
        }
        if let Ok(chunk) = self.rx.read_chunk(need) {
            let (a, b) = chunk.as_slices();
            self.inbuf[4..4 + a.len()].copy_from_slice(a);
            self.inbuf[4 + a.len()..4 + a.len() + b.len()].copy_from_slice(b);
            chunk.commit_all();
        } else {
            return Pulled::Silent;
        }
        let w = &self.inbuf[..];
        for (i, o) in out[..CHUNK].iter_mut().enumerate() {
            let p = self.pos + i as f64 * step;
            let k = p as usize;
            let t = (p - k as f64) as f32;
            let (y0, y1, y2, y3) = (w[k - 1], w[k], w[k + 1], w[k + 2]);
            let a = 1.5 * (y1 - y2) + 0.5 * (y3 - y0);
            let b = y0 - 2.5 * y1 + 2.0 * y2 - 0.5 * y3;
            let c = 0.5 * (y2 - y0);
            *o = ((a * t + b) * t + c) * t + y1;
        }
        self.inbuf.copy_within(need..need + 4, 0);
        self.pos = end - need as f64;
        if std::mem::take(&mut self.warm_up) {
            // The first block ran over zero history: it is not played, and the next one fades in.
            self.fade_in = true;
            return Pulled::Silent;
        }
        if std::mem::take(&mut self.fade_in) {
            for (i, s) in out[..CHUNK].iter_mut().enumerate() {
                *s *= i as f32 / CHUNK as f32;
            }
        }
        Pulled::Audio
    }
}

/// A clip being played over an output: mono at the device's rate, added to the channels in `mask`.
pub(crate) struct Clip {
    pub samples: Box<[f32]>,
    pub mask: u32,
    pub pos: usize,
}

pub(crate) enum OutCmd {
    /// Target gains, lanes x channels, lane-major. Copied into the output's own array.
    SetMatrix(Box<[f32]>),
    SetLane(usize, Option<Box<LaneRx>>),
    AddClip(Box<Clip>),
    StopClips,
}

pub(crate) struct OutputCore {
    ch: usize,
    dev_rate: u32,
    lanes: Vec<Option<Box<LaneRx>>>,
    /// Gain now and gain wanted, per (lane, channel). A change is ramped across the next block.
    cur: Vec<f32>,
    tgt: Vec<f32>,
    /// Boxed for the same reason as the taps in input.rs: the box arrives in a command and leaves on the discard queue.
    #[allow(clippy::vec_box)]
    clips: Vec<Box<Clip>>,
    meters: Vec<Meter>,
    /// The block being mixed, channel-major (`ch` runs of CHUNK frames) so each (lane, channel) cell is one
    /// contiguous multiply-add the compiler can vectorise; interleaved once at the end.
    mix: Vec<f32>,
    inter: Vec<f32>,
    lane_buf: Vec<f32>,
    conv: Option<FixedIn>,
    carry: Vec<f32>,
    carry_pos: usize,
    carry_len: usize,
    cmd: Consumer<OutCmd>,
    discards: Discards,
    shared: Arc<OutputShared>,
    glitches: Arc<Glitches>,
}

impl OutputCore {
    pub fn new(
        ch: usize,
        dev_rate: u32,
        cmd: Consumer<OutCmd>,
        discards: Discards,
        shared: Arc<OutputShared>,
        glitches: Arc<Glitches>,
    ) -> Self {
        let conv = (dev_rate != LANE_RATE).then(|| FixedIn::new(LANE_RATE, dev_rate, ch, CHUNK));
        let carry_frames = conv.as_ref().map_or(CHUNK, |c| c.out_frames_max());
        let meters = shared.meters.iter().map(|m| Meter::new(m.clone())).collect();
        Self {
            ch,
            dev_rate,
            lanes: (0..MAX_LANES).map(|_| None).collect(),
            cur: vec![0.0; MAX_LANES * ch],
            tgt: vec![0.0; MAX_LANES * ch],
            clips: Vec::with_capacity(MAX_CLIPS),
            meters,
            mix: vec![0.0; CHUNK * ch],
            inter: vec![0.0; CHUNK * ch],
            lane_buf: vec![0.0; CHUNK],
            conv,
            carry: vec![0.0; carry_frames * ch],
            carry_pos: 0,
            carry_len: 0,
            cmd,
            discards,
            shared,
            glitches,
        }
    }

    fn drain(&mut self) {
        while let Ok(cmd) = self.cmd.pop() {
            match cmd {
                OutCmd::SetMatrix(m) => {
                    if m.len() == self.tgt.len() {
                        self.tgt.copy_from_slice(&m);
                    }
                    self.discards.send(Discard::Matrix(m));
                }
                OutCmd::SetLane(lane, rx) => {
                    if lane < MAX_LANES {
                        if let Some(old) = std::mem::replace(&mut self.lanes[lane], rx) {
                            self.discards.send(Discard::LaneRx(old));
                        }
                    } else if let Some(rx) = rx {
                        self.discards.send(Discard::LaneRx(rx));
                    }
                }
                OutCmd::AddClip(c) => {
                    if self.clips.len() < MAX_CLIPS {
                        self.clips.push(c);
                    } else {
                        self.glitches.rejected.fetch_add(1, Relaxed);
                        self.discards.send(Discard::Clip(c));
                    }
                }
                OutCmd::StopClips => {
                    while let Some(c) = self.clips.pop() {
                        self.discards.send(Discard::Clip(c));
                    }
                }
            }
        }
    }

    /// Fill `out` (interleaved, `ch` channels, at the device's rate). Realtime-safe.
    pub fn render(&mut self, out: &mut [f32]) {
        self.drain();
        let ch = self.ch;
        let usable = out.len() - out.len() % ch;
        let frames = usable / ch;
        let mut done = 0;
        while done < usable {
            if self.carry_pos >= self.carry_len && !self.produce() {
                out[done..usable].fill(0.0);
                break;
            }
            let take = (self.carry_len - self.carry_pos).min(usable - done);
            out[done..done + take].copy_from_slice(&self.carry[self.carry_pos..self.carry_pos + take]);
            self.carry_pos += take;
            done += take;
        }
        out[usable..].fill(0.0);

        // Clips go straight onto this block.
        let mut i = 0;
        while i < self.clips.len() {
            let clip = &mut self.clips[i];
            let take = (clip.samples.len() - clip.pos).min(frames);
            for (f, &s) in clip.samples[clip.pos..clip.pos + take].iter().enumerate() {
                let frame = &mut out[f * ch..(f + 1) * ch];
                let mut m = clip.mask;
                while m != 0 {
                    let c = m.trailing_zeros() as usize;
                    m &= m - 1;
                    if c < ch {
                        frame[c] += s;
                    }
                }
            }
            clip.pos += take;
            if clip.pos >= clip.samples.len() {
                let done_clip = self.clips.swap_remove(i);
                self.discards.send(Discard::Clip(done_clip));
            } else {
                i += 1;
            }
        }

        // Meter before clipping so an overload shows, then clamp what goes to the device.
        for (c, m) in self.meters.iter_mut().enumerate() {
            m.feed(out, c, ch, frames, self.dev_rate as f32);
        }
        for s in out[..usable].iter_mut() {
            // Not `clamp`: that passes a NaN through to the device. This turns one into -1.0, which is loud enough to notice.
            #[allow(clippy::manual_clamp)]
            {
                *s = s.max(-1.0).min(1.0);
            }
        }
        self.shared.frames_out.fetch_add(frames as u64, Relaxed);
    }

    /// Mix the next CHUNK frames into `carry`. Returns false if it could not (only if the converter yields nothing).
    fn produce(&mut self) -> bool {
        let ch = self.ch;
        self.mix.fill(0.0);
        let mut fill_sum = 0.0f32;
        let mut rel_sum = 0.0f32;
        let mut drift_sum = 0.0f32;
        let mut running = 0u32;
        for lane in 0..MAX_LANES {
            let base = lane * ch;
            let gains = base..base + ch;
            if self.cur[gains.clone()].iter().all(|&g| g == 0.0) && self.tgt[gains.clone()].iter().all(|&g| g == 0.0) {
                // Not routed (any more). Keep its ring empty so that if the matrix uses it again it starts clean.
                if let Some(rx) = self.lanes[lane].as_deref_mut() {
                    rx.park();
                }
                continue;
            }
            let Some(rx) = self.lanes[lane].as_deref_mut() else { continue };
            let pulled = rx.pull(&mut self.lane_buf, &self.glitches, &self.shared);
            if rx.running() {
                fill_sum += rx.ema;
                rel_sum += rx.rel;
                drift_sum += rx.drift();
                running += 1;
            }
            if let Pulled::Silent = pulled {
                continue;
            }
            for c in 0..ch {
                let (g0, g1) = (self.cur[base + c], self.tgt[base + c]);
                let dst = &mut self.mix[c * CHUNK..(c + 1) * CHUNK];
                if g0 == g1 {
                    if g0 != 0.0 {
                        for (d, &s) in dst.iter_mut().zip(&self.lane_buf) {
                            *d += s * g0;
                        }
                    }
                } else {
                    // A gain change ramps over the block instead of stepping.
                    let step = (g1 - g0) / CHUNK as f32;
                    for (i, (d, &s)) in dst.iter_mut().zip(&self.lane_buf).enumerate() {
                        *d += s * (g0 + step * i as f32);
                    }
                }
            }
        }
        self.cur.copy_from_slice(&self.tgt);
        if running > 0 {
            let n = running as f32;
            self.shared.fill.set(fill_sum / n);
            self.shared.ratio.set(rel_sum / n);
            self.shared.drift.set(drift_sum / n);
        } else {
            self.shared.fill.set(0.0);
        }

        self.carry_pos = 0;
        self.carry_len = 0;
        // Channel-major to interleaved.
        for (c, plane) in self.mix.as_chunks::<CHUNK>().0.iter().enumerate() {
            for (frame, &x) in self.inter.chunks_exact_mut(ch).zip(plane) {
                frame[c] = x;
            }
        }
        match self.conv.as_mut() {
            None => {
                self.carry[..CHUNK * ch].copy_from_slice(&self.inter);
                self.carry_len = CHUNK * ch;
            }
            Some(conv) => {
                let carry = &mut self.carry;
                let len = &mut self.carry_len;
                conv.feed(&self.inter, |o| {
                    carry[..o.len()].copy_from_slice(o);
                    *len = o.len();
                });
            }
        }
        self.carry_len > 0
    }
}

/// A fresh ring for one (lane, consumer) pair.
pub(crate) fn new_ring(frames: usize) -> (rtrb::Producer<f32>, Consumer<f32>) {
    RingBuffer::new(frames)
}
