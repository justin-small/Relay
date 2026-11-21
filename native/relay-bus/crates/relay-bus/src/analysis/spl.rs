//! The sound-level meter: a calibrated SPL reading and a third-octave analyser from one mono 48 kHz stream.
//!
//! Every hop (2048 samples, about 43 ms) it takes a Hann-windowed 4096-sample FFT for the weighted level (a
//! rectangular window leaks a low tone into bins the weighting treats differently) and a Hann-windowed 8192-sample
//! FFT for the bands. The level is A- and C-weighted, each with Fast (125 ms) and Slow (1 s) exponential time
//! weighting, plus an A-weighted Leq over the last minute (energy averaged, never dB averaged). The bands are the 31
//! base-10 third-octave bands from 20 Hz to 20 kHz, Z-weighted. The weighting curves are the analytic IEC 61672 ones,
//! applied per FFT bin.
//!
//! `calibration_db` is the sound level that a digital RMS of 0 dBFS stands for.

use rustfft::{num_complex::Complex, Fft, FftPlanner};
use std::collections::VecDeque;
use std::sync::Arc;

pub const RATE: f64 = 48_000.0;
pub const HOP: usize = 2048;
pub const WINDOW: usize = 8192;
pub const LEVEL_WINDOW: usize = 4096;
pub const FAST_S: f64 = 0.125;
pub const SLOW_S: f64 = 1.0;
pub const BAND_S: f64 = 0.5;
pub const LEQ_S: f64 = 60.0;
pub const BAND_N: usize = 31;
/// Nothing reads below this; a silent lane shows the floor, not minus infinity.
pub const FLOOR_DB: f64 = 0.0;

pub fn a_weight_db(f: f64) -> f64 {
    let f2 = f * f;
    let ra = (12194.0f64.powi(2) * f2 * f2)
        / ((f2 + 20.6f64.powi(2)) * ((f2 + 107.7f64.powi(2)) * (f2 + 737.9f64.powi(2))).sqrt() * (f2 + 12194.0f64.powi(2)));
    20.0 * ra.max(1e-30).log10() + 2.0
}

pub fn c_weight_db(f: f64) -> f64 {
    let f2 = f * f;
    let rc = (12194.0f64.powi(2) * f2) / ((f2 + 20.6f64.powi(2)) * (f2 + 12194.0f64.powi(2)));
    20.0 * rc.max(1e-30).log10() + 0.06
}

/// The squared A and C gain of each rfft bin of an n-sample FFT, with the DC bin silenced.
fn bin_weights(n: usize) -> (Vec<f64>, Vec<f64>) {
    let bins = n / 2 + 1;
    let mut a = Vec::with_capacity(bins);
    let mut c = Vec::with_capacity(bins);
    for k in 0..bins {
        let f = if k == 0 { 1.0 } else { k as f64 * RATE / n as f64 };
        a.push(10f64.powf(a_weight_db(f) / 10.0));
        c.push(10f64.powf(c_weight_db(f) / 10.0));
    }
    a[0] = 0.0;
    c[0] = 0.0;
    (a, c)
}

/// Centre frequency of third-octave band `n` (0..31): 19.9 Hz ... 19953 Hz.
pub fn band_centre(n: usize) -> f64 {
    1000.0 * 10f64.powf(0.1 * (n as f64 - 17.0))
}

/// For each band, the first and last-plus-one rfft bin of the WINDOW FFT. A band that holds no bin takes the nearest.
fn band_edges() -> Vec<(usize, usize)> {
    let res = RATE / WINDOW as f64;
    (0..BAND_N)
        .map(|n| {
            let fc = band_centre(n);
            let (lo, hi) = (fc * 10f64.powf(-0.05), fc * 10f64.powf(0.05));
            let (mut a, mut b) = ((lo / res).ceil() as usize, (hi / res).ceil() as usize);
            if b <= a {
                a = (fc / res).round() as usize;
                b = a + 1;
            }
            (a.max(1), b.min(WINDOW / 2 + 1))
        })
        .collect()
}

pub fn hann(n: usize) -> Vec<f64> {
    (0..n).map(|i| 0.5 - 0.5 * (2.0 * std::f64::consts::PI * i as f64 / (n - 1) as f64).cos()).collect()
}

/// dB SPL of a mean square (1.0 = 0 dBFS RMS).
pub fn level_db(ms: f64, calibration_db: f64) -> f64 {
    if ms > 1e-14 {
        calibration_db + 10.0 * ms.log10()
    } else {
        FLOOR_DB
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct SplReading {
    pub a_fast: f64,
    pub a_slow: f64,
    pub c_fast: f64,
    pub c_slow: f64,
    pub leq_a: f64,
    pub bands: [f64; BAND_N],
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CalibrateError {
    /// The reference is not a level between 30 and 140 dB.
    Reference,
    /// The calibrator is too quiet to read.
    TooQuiet,
}

pub struct SplAnalyzer {
    pub calibration_db: f64,
    wa: Vec<f64>,
    wc: Vec<f64>,
    edges: Vec<(usize, usize)>,
    hann_l: Vec<f64>,
    hann_l_ms: f64,
    hann_w: Vec<f64>,
    hann_w_ms: f64,
    fft_l: Arc<dyn Fft<f64>>,
    fft_w: Arc<dyn Fft<f64>>,
    pending: Vec<f64>,
    tail: Vec<f64>,
    ms: [f64; 4],
    bands: [f64; BAND_N],
    leq: VecDeque<f64>,
    leq_sum: f64,
    hops: usize,
}

impl SplAnalyzer {
    pub fn new(calibration_db: f64) -> Self {
        let (wa, wc) = bin_weights(LEVEL_WINDOW);
        let mut planner = FftPlanner::new();
        let (hann_l, hann_w) = (hann(LEVEL_WINDOW), hann(WINDOW));
        let mean_sq = |w: &[f64]| w.iter().map(|x| x * x).sum::<f64>() / w.len() as f64;
        Self {
            calibration_db,
            wa,
            wc,
            edges: band_edges(),
            hann_l_ms: mean_sq(&hann_l),
            hann_w_ms: mean_sq(&hann_w),
            hann_l,
            hann_w,
            fft_l: planner.plan_fft_forward(LEVEL_WINDOW),
            fft_w: planner.plan_fft_forward(WINDOW),
            pending: Vec::new(),
            tail: vec![0.0; WINDOW],
            ms: [0.0; 4],
            bands: [0.0; BAND_N],
            leq: VecDeque::new(),
            leq_sum: 0.0,
            hops: 0,
        }
    }

    pub fn reset(&mut self) {
        self.pending.clear();
        self.tail.iter_mut().for_each(|x| *x = 0.0);
        self.ms = [0.0; 4];
        self.bands = [0.0; BAND_N];
        self.leq.clear();
        self.leq_sum = 0.0;
        self.hops = 0;
    }

    pub fn feed(&mut self, block: &[f32]) {
        self.pending.extend(block.iter().map(|&x| x as f64));
        let mut used = 0;
        while self.pending.len() - used >= HOP {
            let hop: Vec<f64> = self.pending[used..used + HOP].to_vec();
            used += HOP;
            self.step(&hop);
        }
        self.pending.drain(..used);
    }

    fn spectrum(fft: &Arc<dyn Fft<f64>>, x: &[f64], window: &[f64]) -> Vec<Complex<f64>> {
        let mut buf: Vec<Complex<f64>> = x.iter().zip(window).map(|(a, w)| Complex::new(a * w, 0.0)).collect();
        fft.process(&mut buf);
        buf.truncate(x.len() / 2 + 1);
        buf
    }

    /// The weighted mean square of an n-sample windowed block (Parseval over the one-sided spectrum), `window_ms`
    /// being the mean square of the window itself.
    fn weighted_ms(spec: &[Complex<f64>], n: usize, w2: &[f64], window_ms: f64) -> f64 {
        let last = spec.len() - 1;
        let sum: f64 = spec
            .iter()
            .enumerate()
            .map(|(k, c)| c.norm_sqr() * if k == 0 || k == last { 1.0 } else { 2.0 } * w2[k])
            .sum();
        sum / ((n * n) as f64 * window_ms)
    }

    fn step(&mut self, hop: &[f64]) {
        let dt = HOP as f64 / RATE;
        self.tail.copy_within(HOP.., 0);
        self.tail[WINDOW - HOP..].copy_from_slice(hop);
        let spec = Self::spectrum(&self.fft_l, &self.tail[WINDOW - LEVEL_WINDOW..], &self.hann_l);
        let ms_a = Self::weighted_ms(&spec, LEVEL_WINDOW, &self.wa, self.hann_l_ms);
        let ms_c = Self::weighted_ms(&spec, LEVEL_WINDOW, &self.wc, self.hann_l_ms);
        for (i, (ms, tau)) in [(ms_a, FAST_S), (ms_a, SLOW_S), (ms_c, FAST_S), (ms_c, SLOW_S)].into_iter().enumerate() {
            let k = 1.0 - (-dt / tau).exp();
            self.ms[i] += (ms - self.ms[i]) * k;
        }
        self.leq.push_back(ms_a);
        self.leq_sum += ms_a;
        while self.leq.len() as f64 * dt > LEQ_S {
            self.leq_sum -= self.leq.pop_front().unwrap_or(0.0);
        }
        if self.hops >= WINDOW / HOP - 1 {
            let spec = Self::spectrum(&self.fft_w, &self.tail, &self.hann_w);
            let scale = 2.0 / ((WINDOW * WINDOW) as f64 * self.hann_w_ms);
            let k = 1.0 - (-dt / BAND_S).exp();
            for (b, &(lo, hi)) in self.edges.iter().enumerate() {
                let ms: f64 = (lo..hi).map(|i| spec[i].norm_sqr() * scale).sum();
                self.bands[b] += (ms - self.bands[b]) * k;
            }
        }
        self.hops += 1;
    }

    pub fn reading(&self) -> SplReading {
        let cal = self.calibration_db;
        let leq = if self.leq.is_empty() { 0.0 } else { self.leq_sum / self.leq.len() as f64 };
        let mut bands = [FLOOR_DB; BAND_N];
        for (o, m) in bands.iter_mut().zip(self.bands) {
            *o = level_db(m, cal);
        }
        SplReading {
            a_fast: level_db(self.ms[0], cal),
            a_slow: level_db(self.ms[1], cal),
            c_fast: level_db(self.ms[2], cal),
            c_slow: level_db(self.ms[3], cal),
            leq_a: level_db(leq.max(0.0), cal),
            bands,
        }
    }

    /// Set the offset so the current A-weighted Slow reading (a 1 kHz calibrator reads the same either way) is
    /// `reference_db`; returns the new calibration. Needs a signal.
    pub fn calibrate(&mut self, reference_db: f64) -> Result<f64, CalibrateError> {
        if !(30.0..=140.0).contains(&reference_db) {
            return Err(CalibrateError::Reference);
        }
        let ms = self.ms[1];
        if ms <= 1e-12 {
            return Err(CalibrateError::TooQuiet);
        }
        let cal = (reference_db - 10.0 * ms.log10()).clamp(60.0, 160.0);
        self.calibration_db = (cal * 10.0).round() / 10.0;
        Ok(self.calibration_db)
    }
}
