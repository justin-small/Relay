//! The NDI analysis audio meter (issue 300): per-channel RMS and sample peak with hold, true peak (BS.1770-4),
//! clipping, DC, EBU R128 momentary, short-term and integrated loudness with loudness range, stereo correlation and a
//! spectrum of channel 1. A port of Python's `analysis.AudioMeter` (issue 606), step for step.

use std::collections::{BTreeMap, VecDeque};
use std::f64::consts::PI;

pub const LEVEL_FLOOR: f64 = -120.0;
const MAX_CHANNELS: usize = 8;
const TP_TAPS: usize = 24;

fn round_to(v: f64, places: i32) -> f64 {
    let m = 10f64.powi(places);
    (v * m).round_ties_even() / m
}

fn db(v: f64) -> f64 {
    if v > 1e-6 {
        round_to(20.0 * v.log10(), 1)
    } else {
        LEVEL_FLOOR
    }
}

fn lufs(power: f64) -> f64 {
    if power > 0.0 {
        -0.691 + 10.0 * power.log10()
    } else {
        LEVEL_FLOOR
    }
}

type Biquad = (f64, f64, f64, f64, f64);

/// K-weighting (BS.1770): a high shelf then a high-pass, as normalised biquads for the sample rate.
fn k_coeffs(fs: f64) -> (Biquad, Biquad) {
    let (f0, g, q) = (1681.974450955533, 3.999843853973347, 0.7071752369554196);
    let k = (PI * f0 / fs).tan();
    let vh = 10f64.powf(g / 20.0);
    let vb = vh.powf(0.4996667741545416);
    let a0 = 1.0 + k / q + k * k;
    let shelf = ((vh + vb * k / q + k * k) / a0, 2.0 * (k * k - vh) / a0, (vh - vb * k / q + k * k) / a0,
                 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0);
    let (f0, q) = (38.13547087602444, 0.5003270373238773);
    let k = (PI * f0 / fs).tan();
    let a0 = 1.0 + k / q + k * k;
    let high = (1.0, -2.0, 1.0, 2.0 * (k * k - 1.0) / a0, (1.0 - k / q + k * k) / a0);
    (shelf, high)
}

/// BS.1770-4 Annex 2: 4x oversampling with a 96-tap Blackman-windowed sinc, as 4 phases of 24 taps.
fn true_peak_taps() -> [[f64; TP_TAPS]; 4] {
    let taps = 96usize;
    let sinc = |x: f64| if x == 0.0 { 1.0 } else { (PI * x).sin() / (PI * x) };
    let h: Vec<f64> = (0..taps)
        .map(|i| {
            let n = i as f64 - (taps as f64 - 1.0) / 2.0;
            let b = 0.42 - 0.5 * (2.0 * PI * i as f64 / (taps as f64 - 1.0)).cos() + 0.08 * (4.0 * PI * i as f64 / (taps as f64 - 1.0)).cos();
            sinc(n / 4.0) * b
        })
        .collect();
    let mut out = [[0.0; TP_TAPS]; 4];
    for (p, row) in out.iter_mut().enumerate() {
        let phase: Vec<f64> = h.iter().skip(p).step_by(4).copied().collect();
        let sum: f64 = phase.iter().sum();
        for (o, v) in row.iter_mut().zip(phase) {
            *o = v / sum;
        }
    }
    out
}

/// Transposed direct form II, in place; `st` is the filter's two memory cells.
fn biquad(x: &mut [f64], c: Biquad, st: &mut [f64; 2]) {
    let (b0, b1, b2, a1, a2) = c;
    let [mut z1, mut z2] = *st;
    for v in x.iter_mut() {
        let o = b0 * *v + z1;
        z1 = b1 * *v - a1 * o + z2;
        z2 = b2 * *v - a2 * o;
        *v = o;
    }
    *st = [z1, z2];
}

/// In-place radix-2 complex FFT; `re.len()` is a power of two.
fn fft(re: &mut [f64], im: &mut [f64]) {
    let n = re.len();
    let mut j = 0;
    for i in 1..n {
        let mut bit = n >> 1;
        while j & bit != 0 {
            j ^= bit;
            bit >>= 1;
        }
        j ^= bit;
        if i < j {
            re.swap(i, j);
            im.swap(i, j);
        }
    }
    let mut len = 2;
    while len <= n {
        let ang = -2.0 * PI / len as f64;
        let (wr, wi) = (ang.cos(), ang.sin());
        for start in (0..n).step_by(len) {
            let (mut cr, mut ci) = (1.0, 0.0);
            for k in 0..len / 2 {
                let (a, b) = (start + k, start + k + len / 2);
                let (tr, ti) = (re[b] * cr - im[b] * ci, re[b] * ci + im[b] * cr);
                re[b] = re[a] - tr;
                im[b] = im[a] - ti;
                re[a] += tr;
                im[a] += ti;
                let nr = cr * wr - ci * wi;
                ci = cr * wi + ci * wr;
                cr = nr;
            }
        }
        len <<= 1;
    }
}

pub struct ChannelLevels {
    pub rms: f64,
    pub peak: f64,
    pub hold: f64,
    pub true_peak: f64,
    pub dc: f64,
    pub mute: bool,
}

pub struct Snapshot {
    pub rate: u32,
    pub channels: Vec<ChannelLevels>,
    pub clips: u64,
    pub clip_now: bool,
    pub momentary: Option<f64>,
    pub short: Option<f64>,
    pub integrated: Option<f64>,
    pub lra: Option<f64>,
    pub correlation: Option<f64>,
    pub spectrum: Vec<f64>,
    pub alarms: Vec<(&'static str, &'static str)>,
}

pub struct AudioMeter {
    taps: [[f64; TP_TAPS]; 4],
    hann: Vec<f64>,
    rate: u32,
    ch: usize,
    last: Option<f64>,
    rms: Vec<f64>,
    peak: Vec<f64>,
    dc: Vec<f64>,
    hold: Vec<f64>,
    hold_t: Vec<f64>,
    hold_upd: Vec<f64>,
    tpeak: Vec<f64>,
    clips: u64,
    clip_at: f64,
    silent_since: Option<f64>,
    corr: f64,
    spectrum: Vec<f64>,
    tail: Vec<Vec<f32>>,
    k: (Biquad, Biquad),
    st: Vec<([f64; 2], [f64; 2])>,
    buf: Vec<VecDeque<f64>>,
    weights: Vec<f64>,
    sub: VecDeque<f64>,
    blocks: BTreeMap<i64, (u64, f64)>,
    short_bins: BTreeMap<i64, u64>,
    momentary: Option<f64>,
    short: Option<f64>,
}

impl Default for AudioMeter {
    fn default() -> Self {
        Self::new()
    }
}

impl AudioMeter {
    pub fn new() -> AudioMeter {
        AudioMeter {
            taps: true_peak_taps(),
            hann: (0..1024).map(|k| 0.5 - 0.5 * (2.0 * PI * k as f64 / 1024.0).cos()).collect(),
            rate: 0,
            ch: 0,
            last: None,
            rms: vec![],
            peak: vec![],
            dc: vec![],
            hold: vec![],
            hold_t: vec![],
            hold_upd: vec![],
            tpeak: vec![],
            clips: 0,
            clip_at: 0.0,
            silent_since: None,
            corr: 0.0,
            spectrum: vec![],
            tail: vec![],
            k: k_coeffs(48000.0),
            st: vec![],
            buf: vec![],
            weights: vec![],
            sub: VecDeque::new(),
            blocks: BTreeMap::new(),
            short_bins: BTreeMap::new(),
            momentary: None,
            short: None,
        }
    }

    pub fn rate(&self) -> u32 {
        self.rate
    }

    pub fn channels(&self) -> usize {
        self.ch
    }

    /// Forget everything, as if no audio had been seen.
    pub fn reset(&mut self) {
        *self = AudioMeter::new();
    }

    fn setup(&mut self, ch: usize, rate: u32) {
        self.rate = rate;
        self.ch = ch;
        self.rms = vec![LEVEL_FLOOR; ch];
        self.peak = vec![LEVEL_FLOOR; ch];
        self.dc = vec![0.0; ch];
        self.hold = vec![LEVEL_FLOOR; ch];
        self.hold_t = vec![0.0; ch];
        self.hold_upd = vec![0.0; ch];
        self.tpeak = vec![LEVEL_FLOOR; ch];
        self.k = k_coeffs(rate as f64);
        self.st = vec![([0.0; 2], [0.0; 2]); ch];
        self.buf = vec![VecDeque::new(); ch];
        self.tail = vec![vec![0.0; TP_TAPS - 1]; ch];
        self.weights = match ch {
            6 => vec![1.0, 1.0, 1.0, 0.0, 1.41, 1.41],
            5 => vec![1.0, 1.0, 1.0, 1.41, 1.41],
            _ => vec![1.0; ch],
        };
    }

    /// Feed one chunk: `samples` is planar, `channels` rows of `n` samples; `now` is monotonic seconds.
    pub fn feed(&mut self, samples: &[f32], channels: usize, n: usize, rate: u32, now: f64) {
        if channels == 0 || n == 0 || rate == 0 || samples.len() < channels * n {
            return;
        }
        let ch = channels.min(MAX_CHANNELS);
        if ch != self.ch || rate != self.rate {
            self.setup(ch, rate);
        }
        let row = |i: usize| &samples[i * n..(i + 1) * n];
        let win_n = ((rate as f64 * 0.05) as usize).max(1).min(n);
        for i in 0..ch {
            let x = row(i);
            let a_max = x.iter().fold(0f32, |m, v| m.max(v.abs())) as f64;
            let pk = db(a_max);
            self.peak[i] = pk;
            let win = &x[n - win_n..];
            let ms = win.iter().map(|v| (*v as f64) * (*v as f64)).sum::<f64>() / win_n as f64;
            self.rms[i] = db(ms.sqrt());
            self.dc[i] = round_to(x.iter().map(|v| *v as f64).sum::<f64>() / n as f64, 4);
            if pk >= self.hold[i] {
                (self.hold[i], self.hold_t[i], self.hold_upd[i]) = (pk, now, now);
            } else if now - self.hold_t[i] > 1.5 {
                // held 1.5 s, then falls at 22 dB a second
                let fall = 22.0 * (now - self.hold_upd[i].max(self.hold_t[i] + 1.5));
                self.hold[i] = pk.max(self.hold[i] - fall);
                self.hold_upd[i] = now;
            }
            let clip = x.iter().filter(|v| v.abs() >= 1.0).count() as u64;
            if clip > 0 {
                self.clips += clip;
                self.clip_at = now;
            }
        }
        for i in 0..ch {
            let mut ext: Vec<f64> = self.tail[i].iter().map(|v| *v as f64).collect();
            ext.extend(row(i).iter().map(|v| *v as f64));
            let mut top = 0f64;
            if ext.len() >= TP_TAPS {
                for k in 0..=ext.len() - TP_TAPS {
                    for ph in &self.taps {
                        // numpy.convolve reverses the kernel
                        let v: f64 = (0..TP_TAPS).map(|j| ext[k + j] * ph[TP_TAPS - 1 - j]).sum();
                        top = top.max(v.abs());
                    }
                }
            }
            self.tpeak[i] = self.tpeak[i].max(db(top));
            self.tail[i] = ext[ext.len() - (TP_TAPS - 1)..].iter().map(|v| *v as f32).collect();
        }
        let loudest = self.rms.iter().cloned().fold(f64::MIN, f64::max);
        if self.silent_since.is_none() && loudest < -60.0 {
            self.silent_since = Some(now);
        } else if loudest >= -60.0 {
            self.silent_since = None;
        }
        if ch >= 2 {
            let (l, r) = (row(0), row(1));
            let (mut ll, mut rr, mut lr) = (0f64, 0f64, 0f64);
            for (a, b) in l.iter().zip(r) {
                let (a, b) = (*a as f64, *b as f64);
                ll += a * a;
                rr += b * b;
                lr += a * b;
            }
            let den = (ll * rr).sqrt();
            let c = if den > 0.0 { lr / den } else { 0.0 };
            self.corr = (0.8 * self.corr + 0.2 * c).clamp(-1.0, 1.0);
        }
        if n >= 1024 {
            let seg = &row(0)[n - 1024..];
            let mut re: Vec<f64> = seg.iter().zip(&self.hann).map(|(s, w)| (*s * (*w as f32)) as f64).collect();
            let mut im = vec![0.0; 1024];
            fft(&mut re, &mut im);
            self.spectrum = (1..513)
                .map(|k| {
                    let m = (re[k] * re[k] + im[k] * im[k]).sqrt() / 512.0;
                    round_to((20.0 * (m + 1e-9).log10()).max(-100.0), 1)
                })
                .collect();
        }
        self.loudness(samples, n, ch);
        self.last = Some(now);
    }

    fn loudness(&mut self, samples: &[f32], n: usize, ch: usize) {
        let block = ((self.rate / 10) as usize).max(1);
        let (shelf, high) = self.k;
        for i in 0..ch {
            if self.weights[i] == 0.0 {
                continue;
            }
            let mut y: Vec<f64> = samples[i * n..(i + 1) * n].iter().map(|v| *v as f64).collect();
            biquad(&mut y, shelf, &mut self.st[i].0);
            biquad(&mut y, high, &mut self.st[i].1);
            self.buf[i].extend(y.iter().map(|v| v * v));
        }
        while (0..ch).filter(|&i| self.weights[i] != 0.0).all(|i| self.buf[i].len() >= block) {
            let mut tot = 0.0;
            for i in 0..ch {
                if self.weights[i] == 0.0 {
                    continue;
                }
                tot += self.weights[i] * (self.buf[i].iter().take(block).sum::<f64>() / block as f64);
                self.buf[i].drain(..block);
            }
            if self.sub.len() == 30 {
                self.sub.pop_front();
            }
            self.sub.push_back(tot);
            if self.sub.len() >= 4 {
                let m = self.sub.iter().rev().take(4).sum::<f64>() / 4.0;
                self.momentary = Some(lufs(m));
                let lk = lufs(m);
                if lk > -70.0 {
                    let rec = self.blocks.entry((lk * 10.0).round_ties_even() as i64).or_insert((0, 0.0));
                    rec.0 += 1;
                    rec.1 += m;
                }
            }
            if self.sub.len() >= 30 {
                let s = self.sub.iter().sum::<f64>() / 30.0;
                self.short = Some(lufs(s));
                if lufs(s) > -70.0 {
                    *self.short_bins.entry((lufs(s) * 10.0).round_ties_even() as i64).or_insert(0) += 1;
                }
            }
        }
    }

    fn integrated(&self) -> Option<f64> {
        if self.blocks.is_empty() {
            return None;
        }
        let n: u64 = self.blocks.values().map(|(c, _)| c).sum();
        let rel = lufs(self.blocks.values().map(|(_, p)| p).sum::<f64>() / n as f64) - 10.0;
        let (mut cnt, mut pow) = (0u64, 0f64);
        for (b, (c, p)) in &self.blocks {
            if *b as f64 / 10.0 > rel {
                cnt += c;
                pow += p;
            }
        }
        (cnt > 0).then(|| round_to(lufs(pow / cnt as f64), 1))
    }

    fn lra(&self) -> Option<f64> {
        if self.short_bins.is_empty() {
            return None;
        }
        let n: u64 = self.short_bins.values().sum();
        let mean = self.short_bins.iter().map(|(b, c)| 10f64.powf((*b as f64 / 10.0 + 0.691) / 10.0) * *c as f64).sum::<f64>() / n as f64;
        let rel = lufs(mean) - 20.0;
        let vals: Vec<(f64, u64)> = self.short_bins.iter().map(|(b, c)| (*b as f64 / 10.0, *c)).filter(|(v, _)| *v > rel).collect();
        let total: u64 = vals.iter().map(|(_, c)| c).sum();
        if total < 2 {
            return None;
        }
        let pct = |q: f64| {
            let mut acc = 0u64;
            for (v, c) in &vals {
                acc += c;
                if acc as f64 >= q * total as f64 {
                    return *v;
                }
            }
            vals[vals.len() - 1].0
        };
        Some(round_to(pct(0.95) - pct(0.10), 1))
    }

    pub fn snapshot(&self, now: f64) -> Option<Snapshot> {
        let last = self.last?;
        if now - last > 2.0 || self.ch == 0 {
            return None;
        }
        let channels = (0..self.ch)
            .map(|i| ChannelLevels { rms: self.rms[i], peak: self.peak[i], hold: self.hold[i], true_peak: self.tpeak[i], dc: self.dc[i],
                                     mute: self.peak[i] <= LEVEL_FLOOR })
            .collect();
        let silent = self.silent_since.is_some_and(|s| now - s >= 2.0);
        let corr = (self.ch >= 2).then(|| round_to(self.corr, 2));
        let mut alarms = Vec::new();
        if silent {
            alarms.push(("SILENCE", "warn"));
        }
        if self.clips > 0 {
            alarms.push(("CLIP", "warn"));
        }
        if corr.is_some_and(|c| c < -0.5) {
            alarms.push(("PHASE", "warn"));
        }
        Some(Snapshot {
            rate: self.rate,
            channels,
            clips: self.clips,
            clip_now: now - self.clip_at < 2.5,
            momentary: self.momentary.map(|v| round_to(v, 1)),
            short: self.short.map(|v| round_to(v, 1)),
            integrated: self.integrated(),
            lra: self.lra(),
            correlation: corr,
            spectrum: self.spectrum.clone(),
            alarms,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tone(freq: f64, amp: f64, rate: u32, secs: f64, ch: usize) -> Vec<f32> {
        let n = (rate as f64 * secs) as usize;
        let one: Vec<f32> = (0..n).map(|i| (amp * (2.0 * PI * freq * i as f64 / rate as f64).sin()) as f32).collect();
        (0..ch).flat_map(|_| one.clone()).collect()
    }

    #[test]
    fn a_minus_23_dbfs_tone_reads_about_minus_23_lufs() {
        let mut m = AudioMeter::new();
        let amp = 10f64.powf(-23.0 / 20.0) * 1.0;
        for k in 0..40 {
            let x = tone(1000.0, amp, 48000, 0.1, 2);
            m.feed(&x, 2, 4800, 48000, k as f64 * 0.1);
        }
        let s = m.snapshot(4.0).unwrap();
        assert!((s.integrated.unwrap() - -23.0).abs() < 0.5, "{:?}", s.integrated);
        assert!((s.channels[0].peak - -23.0).abs() < 0.2);
        assert!(s.correlation.unwrap() > 0.9);
    }

    #[test]
    fn silence_alarms_after_two_seconds_and_stale_audio_has_no_snapshot() {
        let mut m = AudioMeter::new();
        for k in 0..30 {
            m.feed(&vec![0.0; 2 * 4800], 2, 4800, 48000, k as f64 * 0.1);
        }
        assert!(m.snapshot(2.95).unwrap().alarms.contains(&("SILENCE", "warn")));
        assert!(m.snapshot(10.0).is_none());
    }

    #[test]
    fn the_fft_finds_a_tone() {
        let mut re: Vec<f64> = (0..1024).map(|i| (2.0 * PI * 64.0 * i as f64 / 1024.0).sin()).collect();
        let mut im = vec![0.0; 1024];
        fft(&mut re, &mut im);
        let bin = (0..512).max_by(|a, b| (re[*a].hypot(im[*a])).total_cmp(&re[*b].hypot(im[*b]))).unwrap();
        assert_eq!(bin, 64);
    }
}
