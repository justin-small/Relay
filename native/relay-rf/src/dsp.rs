//! Spectrum maths on 8-bit IQ (issue 362). Power is dBFS: a full-scale complex tone reads 0 dB.
use rustfft::{num_complex::Complex, FftPlanner};

pub fn iq_to_complex(raw: &[u8]) -> Vec<Complex<f32>> {
    raw.as_chunks::<2>()
        .0
        .iter()
        .map(|p| Complex::new((p[0] as f32 - 127.5) / 127.5, (p[1] as f32 - 127.5) / 127.5))
        .collect()
}

/// Averaged (Welch) power spectrum, `n` bins, Hann window, DC in bin 0 and negative frequencies in the upper half.
pub fn power_spectrum(raw: &[u8], n: usize) -> Vec<f32> {
    let iq = iq_to_complex(raw);
    let fft = FftPlanner::<f32>::new().plan_fft_forward(n);
    let win: Vec<f32> = (0..n).map(|i| 0.5 - 0.5 * (2.0 * std::f32::consts::PI * i as f32 / n as f32).cos()).collect();
    let gain: f32 = win.iter().sum(); // coherent gain: a full-scale tone then reads 0 dBFS
    let mut acc = vec![0f32; n];
    let mut frames = 0;
    for blk in iq.chunks_exact(n) {
        let mut c: Vec<Complex<f32>> = blk.iter().zip(&win).map(|(s, w)| s * *w).collect();
        fft.process(&mut c);
        for (a, v) in acc.iter_mut().zip(&c) {
            *a += v.norm_sqr();
        }
        frames += 1;
    }
    let f = (frames.max(1) as f32) * gain * gain;
    acc.iter().map(|a| 10.0 * (a / f + 1e-12).log10()).collect()
}

/// Bin index for a frequency offset from the centre (Hz, may be negative).
pub fn bin_for(offset_hz: f64, rate: u32, n: usize) -> usize {
    let b = (offset_hz / rate as f64 * n as f64).round() as i64;
    b.rem_euclid(n as i64) as usize
}

/// Peak level within `width_hz` around `offset_hz`: what a mic carrier reads (issue 362/364).
pub fn level_at(spec: &[f32], rate: u32, offset_hz: f64, width_hz: f64) -> f32 {
    let n = spec.len();
    let half = ((width_hz / 2.0) / rate as f64 * n as f64).ceil() as i64;
    let c = bin_for(offset_hz, rate, n) as i64;
    (-half..=half).map(|d| spec[(c + d).rem_euclid(n as i64) as usize]).fold(f32::MIN, f32::max)
}

/// Median bin level: a robust noise floor even with carriers present.
pub fn noise_floor(spec: &[f32]) -> f32 {
    let mut v = spec.to_vec();
    v.sort_by(|a, b| a.total_cmp(b));
    v[v.len() / 2]
}

/// Rearrange so frequency rises left to right (negative half first).
pub fn fft_shift(spec: &[f32]) -> Vec<f32> {
    let h = spec.len() / 2;
    spec[h..].iter().chain(&spec[..h]).copied().collect()
}

#[cfg(test)]
pub fn synth(rate: u32, tones: &[(f64, f32)], n: usize, noise: f32) -> Vec<u8> {
    let mut seed = 12345u32;
    let mut rnd = move || {
        seed = seed.wrapping_mul(1664525).wrapping_add(1013904223);
        (seed >> 8) as f32 / (1 << 24) as f32 - 0.5
    };
    let mut out = Vec::with_capacity(n * 2);
    for i in 0..n {
        let t = i as f64 / rate as f64;
        let (mut re, mut im) = (rnd() * noise, rnd() * noise);
        for (f, a) in tones {
            if f.abs() > rate as f64 / 2.0 {
                continue; // out of band: a real tuner filters it, the synth must not alias it in
            }
            let ph = 2.0 * std::f64::consts::PI * f * t;
            re += a * ph.cos() as f32;
            im += a * ph.sin() as f32;
        }
        out.push((re * 127.5 + 127.5).clamp(0.0, 255.0) as u8);
        out.push((im * 127.5 + 127.5).clamp(0.0, 255.0) as u8);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    const R: u32 = 2_400_000;

    #[test]
    fn tone_lands_in_the_right_bin_at_the_right_level() {
        let raw = synth(R, &[(300_000.0, 0.5)], 1 << 18, 0.0);
        let s = power_spectrum(&raw, 4096);
        let l = level_at(&s, R, 300_000.0, 5_000.0);
        // a 0.5 amplitude tone is -6 dBFS; Hann scalloping and 8-bit rounding allow a dB or so
        assert!((l + 6.0).abs() < 1.5, "level {l}");
        assert!(level_at(&s, R, -300_000.0, 5_000.0) < l - 40.0);
    }

    #[test]
    fn negative_offsets_and_floor() {
        let raw = synth(R, &[(-700_000.0, 0.25)], 1 << 18, 0.02);
        let s = power_spectrum(&raw, 4096);
        assert!(level_at(&s, R, -700_000.0, 5_000.0) > noise_floor(&s) + 30.0);
        assert!(level_at(&s, R, 700_000.0, 5_000.0) < noise_floor(&s) + 6.0);
    }

    #[test]
    fn two_mics_read_independently() {
        let raw = synth(R, &[(-500_000.0, 0.4), (200_000.0, 0.1)], 1 << 18, 0.0);
        let s = power_spectrum(&raw, 4096);
        let d = level_at(&s, R, -500_000.0, 5_000.0) - level_at(&s, R, 200_000.0, 5_000.0);
        assert!((d - 12.04).abs() < 1.5, "delta {d}");
    }
}
