//! The stream-mix meter: BS.1770 / EBU R128 loudness of a stereo pair of lanes, plus what a mix report needs.
//!
//! K-weighted mean squares are taken per 100 ms block. Momentary loudness is the last 400 ms, short-term the last
//! 3 s, integrated is gated exactly (absolute -70 LUFS, then 10 LU under the ungated level, over 75 %-overlapped
//! 400 ms blocks), and the loudness range is the EBU one. True peak is the 4x oversampled peak. Every second the meter
//! also writes one `Row`: the loudness values, the sample and true peak of that second, the left/right correlation
//! and ten octave bands of the mono mix. A row says whether the mix carried sound (above `silence_dbfs`).

use super::filters::{init_true_peak_filters, KWeightStereo};
use super::gating::{IntegratedAccumulator, LraAccumulator};
use super::spl::{hann, RATE};
use rustfft::{num_complex::Complex, Fft, FftPlanner};
use std::collections::VecDeque;
use std::sync::Arc;

const BLOCK: usize = 4800; // 100 ms
const SHORT_BLOCKS: usize = 30;
const MOMENTARY_BLOCKS: usize = 4;
const SECOND: usize = 48_000;
const FRAME: usize = 4096;
const FRAME_HOP: usize = 2048;
pub const OCTAVES: [f64; 10] = [31.5, 63.0, 125.0, 250.0, 500.0, 1000.0, 2000.0, 4000.0, 8000.0, 16000.0];
const MAX_ROWS: usize = 3600;

pub fn dbfs(v: f64) -> f64 {
    if v > 1e-9 {
        20.0 * v.log10()
    } else {
        -120.0
    }
}

fn lufs_opt(ms: f64) -> Option<f64> {
    (ms > 1e-12).then(|| -0.691 + 10.0 * ms.log10())
}

fn finite(v: f64) -> Option<f64> {
    v.is_finite().then_some(v)
}

/// What a viewer sees now. LUFS values are `None` until there is sound to measure.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct LoudnessReading {
    pub momentary: Option<f64>,
    pub short_term: Option<f64>,
    pub integrated: Option<f64>,
    pub range_lu: f64,
    /// Highest 4x oversampled peak since the last reset, in dBTP.
    pub true_peak_dbtp: f64,
    /// Highest sample peak since the last reset, in dBFS.
    pub sample_peak_dbfs: f64,
    /// Left/right correlation of the last full second; `None` when either side is silent.
    pub correlation: Option<f64>,
    /// Ten octave levels (31.5 Hz to 16 kHz) of the last full second, dBFS.
    pub octaves: [f64; 10],
    pub seconds: u64,
}

/// One second of a stereo mix.
#[derive(Debug, Clone, PartialEq)]
pub struct Row {
    pub momentary: Option<f64>,
    pub short_term: Option<f64>,
    pub integrated: Option<f64>,
    pub range_lu: f64,
    pub sample_peak_dbfs: f64,
    pub true_peak_dbtp: f64,
    pub correlation: Option<f64>,
    pub octaves: [f64; 10],
    /// The mix carried sound this second (above the silence level), so the row is worth saving.
    pub sound: bool,
}

struct OctaveBank {
    fft: Arc<dyn Fft<f64>>,
    window: Vec<f64>,
    window_ms: f64,
    bins: Vec<(usize, usize)>,
    buf: Vec<f64>,
    power: Vec<f64>,
    frames: usize,
}

impl OctaveBank {
    fn new() -> Self {
        let window = hann(FRAME);
        let res = RATE / FRAME as f64;
        let bins = OCTAVES
            .iter()
            .map(|&fc| {
                let a = ((fc / 2f64.sqrt() / res).ceil() as usize).max(1);
                let b = ((fc * 2f64.sqrt() / res).ceil() as usize).max(2);
                (a, b)
            })
            .collect();
        Self {
            fft: FftPlanner::new().plan_fft_forward(FRAME),
            window_ms: window.iter().map(|x| x * x).sum::<f64>() / FRAME as f64,
            window,
            bins,
            buf: Vec::new(),
            power: vec![0.0; FRAME / 2 + 1],
            frames: 0,
        }
    }

    fn feed(&mut self, x: f64) {
        self.buf.push(x);
        if self.buf.len() >= FRAME {
            let mut spec: Vec<Complex<f64>> =
                self.buf[..FRAME].iter().zip(&self.window).map(|(a, w)| Complex::new(a * w, 0.0)).collect();
            self.fft.process(&mut spec);
            let last = FRAME / 2;
            for k in 0..=last {
                self.power[k] += spec[k].norm_sqr() * if k == 0 || k == last { 1.0 } else { 2.0 };
            }
            self.frames += 1;
            self.buf.drain(..FRAME_HOP);
        }
    }

    /// The ten octave levels of the frames since the last call, dBFS.
    fn take(&mut self) -> [f64; 10] {
        let mut out = [-120.0; 10];
        if self.frames > 0 {
            let scale = 1.0 / (self.frames as f64 * (FRAME * FRAME) as f64 * self.window_ms);
            for (o, &(a, b)) in out.iter_mut().zip(&self.bins) {
                let sum: f64 = (a..b.min(self.power.len())).map(|k| self.power[k] * scale).sum();
                *o = dbfs(sum.sqrt());
            }
        }
        self.power.iter_mut().for_each(|p| *p = 0.0);
        self.frames = 0;
        out
    }
}

pub struct LoudnessMeter {
    silence_dbfs: f64,
    kf: KWeightStereo,
    block_sq: [f64; 2],
    block_n: usize,
    blocks: VecDeque<[f64; 2]>,
    integrated: IntegratedAccumulator,
    range: LraAccumulator,
    tp_t: usize,
    tp_p: usize,
    tp_ph: Vec<Vec<f64>>,
    tp_h: [Vec<f64>; 2],
    tp_wp: usize,
    total_peak: f64,
    total_tp: f64,
    sec_peak: f64,
    sec_tp: f64,
    sec_n: usize,
    sums: [f64; 5], // sum l, sum r, sum ll, sum rr, sum lr
    sec_sq: [f64; 3], // sum l^2, r^2, mono^2 (for the silence test)
    octaves: OctaveBank,
    reading: LoudnessReading,
    rows: VecDeque<Row>,
    dropped_rows: u64,
}

impl LoudnessMeter {
    pub fn new(silence_dbfs: f64) -> Self {
        let (tp_t, tp_p, tp_ph) = init_true_peak_filters();
        Self {
            silence_dbfs,
            kf: KWeightStereo::new(RATE),
            block_sq: [0.0; 2],
            block_n: 0,
            blocks: VecDeque::with_capacity(SHORT_BLOCKS + 1),
            integrated: IntegratedAccumulator::new(),
            range: LraAccumulator::new(),
            tp_t,
            tp_p,
            tp_ph,
            tp_h: [vec![0.0; tp_t], vec![0.0; tp_t]],
            tp_wp: 0,
            total_peak: 0.0,
            total_tp: 0.0,
            sec_peak: 0.0,
            sec_tp: 0.0,
            sec_n: 0,
            sums: [0.0; 5],
            sec_sq: [0.0; 3],
            octaves: OctaveBank::new(),
            reading: LoudnessReading { true_peak_dbtp: -120.0, sample_peak_dbfs: -120.0, octaves: [-120.0; 10], ..Default::default() },
            rows: VecDeque::new(),
            dropped_rows: 0,
        }
    }

    pub fn set_silence_dbfs(&mut self, v: f64) {
        self.silence_dbfs = v;
    }

    /// Forget everything measured so far (a new programme).
    pub fn reset(&mut self) {
        *self = Self::new(self.silence_dbfs);
    }

    pub fn reading(&self) -> &LoudnessReading {
        &self.reading
    }

    /// Rows written since the last call. A row that would have made the list longer than an hour is dropped.
    pub fn take_rows(&mut self) -> Vec<Row> {
        self.rows.drain(..).collect()
    }

    pub fn dropped_rows(&self) -> u64 {
        self.dropped_rows
    }

    /// The 4x oversampled peak around the newest sample of one channel.
    fn true_peak(&mut self, ch: usize, x: f64) -> f64 {
        let t = self.tp_t;
        let wp = self.tp_wp;
        self.tp_h[ch][wp] = x;
        let mut mx = x.abs();
        for ph in &self.tp_ph[1..self.tp_p] {
            let mut y = 0.0;
            for (tt, coeff) in ph.iter().enumerate() {
                let idx = if tt <= wp { wp - tt } else { wp + t - tt };
                y += coeff * self.tp_h[ch][idx];
            }
            mx = mx.max(y.abs());
        }
        mx
    }

    /// Feed matching stretches of the left and right lanes. Both must have the same length.
    pub fn feed(&mut self, left: &[f32], right: &[f32]) {
        debug_assert_eq!(left.len(), right.len());
        for (&l, &r) in left.iter().zip(right) {
            let (l, r) = (l as f64, r as f64);
            let (kl, kr) = self.kf.tick_lr(l, r);
            self.block_sq[0] += kl * kl;
            self.block_sq[1] += kr * kr;
            let tp = self.true_peak(0, l).max(self.true_peak(1, r));
            self.tp_wp = if self.tp_wp + 1 == self.tp_t { 0 } else { self.tp_wp + 1 };
            let pk = l.abs().max(r.abs());
            self.sec_peak = self.sec_peak.max(pk);
            self.sec_tp = self.sec_tp.max(tp);
            self.sums[0] += l;
            self.sums[1] += r;
            self.sums[2] += l * l;
            self.sums[3] += r * r;
            self.sums[4] += l * r;
            let mono = 0.5 * (l + r);
            self.sec_sq[0] += l * l;
            self.sec_sq[1] += r * r;
            self.sec_sq[2] += mono * mono;
            self.octaves.feed(mono);
            self.block_n += 1;
            self.sec_n += 1;
            if self.block_n == BLOCK {
                self.end_block();
            }
            if self.sec_n == SECOND {
                self.end_second();
            }
        }
    }

    fn mean_of_last(&self, n: usize) -> f64 {
        let take = n.min(self.blocks.len());
        if take == 0 {
            return 0.0;
        }
        let sum: f64 = self.blocks.iter().rev().take(take).map(|b| b[0] + b[1]).sum();
        sum / take as f64
    }

    fn end_block(&mut self) {
        let block = [self.block_sq[0] / BLOCK as f64, self.block_sq[1] / BLOCK as f64];
        self.block_sq = [0.0; 2];
        self.block_n = 0;
        self.blocks.push_back(block);
        if self.blocks.len() > SHORT_BLOCKS {
            self.blocks.pop_front();
        }
        self.integrated.push(block);
        let short = lufs_opt(self.mean_of_last(SHORT_BLOCKS));
        if self.blocks.len() == SHORT_BLOCKS {
            if let Some(s) = short {
                self.range.push(s);
            }
        }
        self.reading.momentary = lufs_opt(self.mean_of_last(MOMENTARY_BLOCKS));
        self.reading.short_term = short;
        self.reading.integrated = finite(self.integrated.value());
        self.reading.range_lu = self.range.value();
    }

    fn end_second(&mut self) {
        let n = self.sec_n as f64;
        let [sl, sr, sll, srr, slr] = self.sums;
        let (var_l, var_r) = (sll - sl * sl / n, srr - sr * sr / n);
        let den = (var_l * var_r).sqrt();
        let correlation = (den > 1e-12).then(|| ((slr - sl * sr / n) / den * 100.0).round() / 100.0);
        let rms = |sq: f64| (sq / n).sqrt();
        let sound = dbfs(rms(self.sec_sq[2])) > self.silence_dbfs
            || dbfs(rms(self.sec_sq[0]).max(rms(self.sec_sq[1]))) > self.silence_dbfs;
        let octaves = self.octaves.take();
        let sample_peak = dbfs(self.sec_peak);
        let true_peak = dbfs(self.sec_tp);
        self.total_peak = self.total_peak.max(self.sec_peak);
        self.total_tp = self.total_tp.max(self.sec_tp);
        self.reading.sample_peak_dbfs = dbfs(self.total_peak);
        self.reading.true_peak_dbtp = dbfs(self.total_tp);
        self.reading.correlation = correlation;
        self.reading.octaves = octaves;
        self.reading.seconds += 1;
        let row = Row {
            momentary: self.reading.momentary,
            short_term: self.reading.short_term,
            integrated: self.reading.integrated,
            range_lu: self.reading.range_lu,
            sample_peak_dbfs: sample_peak,
            true_peak_dbtp: true_peak,
            correlation,
            octaves,
            sound,
        };
        if self.rows.len() >= MAX_ROWS {
            self.rows.pop_front();
            self.dropped_rows += 1;
        }
        self.rows.push_back(row);
        self.sums = [0.0; 5];
        self.sec_sq = [0.0; 3];
        self.sec_peak = 0.0;
        self.sec_tp = 0.0;
        self.sec_n = 0;
    }
}
