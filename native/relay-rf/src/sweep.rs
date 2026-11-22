//! Band sweep (issue 366): hop the tuner across a band using the middle of each capture, and stitch one spectrum.
use crate::{dsp, IqSource};

/// The tuner puts a DC spike at its centre, so each hop keeps only the stretch from 5% to 37.5% of the sample rate
/// ABOVE its centre (the far edges roll off). The next hop's centre sits one stretch higher, so the stretches meet
/// with no overlap, no gap, and no hop's spike inside what it keeps.
const SKIP: f64 = 0.05;
const KEEP_TO: f64 = 0.375;

pub struct Plan {
    pub centers: Vec<u32>,
    pub step_hz: f64,
}

pub fn plan(start_hz: u32, stop_hz: u32, rate: u32) -> Plan {
    let step_hz = rate as f64 * (KEEP_TO - SKIP);
    let mut centers = vec![];
    let mut c = start_hz as f64 - rate as f64 * SKIP;
    while c + rate as f64 * SKIP < stop_hz as f64 {
        centers.push(c as u32);
        c += step_hz;
    }
    Plan { centers, step_hz }
}

pub struct Sweep {
    /// (frequency Hz, dBFS) points, rising.
    pub points: Vec<(f64, f32)>,
    pub seconds: f64,
}

pub fn run<S: IqSource>(src: &mut S, start: u32, stop: u32, bins: usize, frames: usize) -> Result<Sweep, String> {
    let rate = src.sample_rate();
    let p = plan(start, stop, rate);
    let t0 = std::time::Instant::now();
    let mut points = vec![];
    let mut raw = vec![0u8; bins * frames * 2];
    for &c in &p.centers {
        src.tune(c)?;
        let mut got = 0;
        // the first reads after a retune still hold the old frequency; read and discard one block
        let mut junk = vec![0u8; 16384];
        src.read(&mut junk)?;
        while got < raw.len() {
            let n = src.read(&mut raw[got..])?;
            if n == 0 {
                return Err("short read".into());
            }
            got += n;
        }
        let spec = dsp::fft_shift(&dsp::power_spectrum(&raw, bins));
        let (lo, hi) = (((0.5 + SKIP) * bins as f64) as usize, ((0.5 + KEEP_TO) * bins as f64) as usize);
        for (i, v) in spec.iter().enumerate().take(hi).skip(lo) {
            points.push((c as f64 + (i as f64 - bins as f64 / 2.0) * rate as f64 / bins as f64, *v));
        }
    }
    Ok(Sweep { points, seconds: t0.elapsed().as_secs_f64() })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn plan_covers_the_band_and_no_hop_keeps_its_own_centre() {
        let p = plan(470_000_000, 608_000_000, 2_400_000);
        assert_eq!(p.centers.len(), 177); // 138 MHz / 780 kHz
        for w in p.centers.windows(2) {
            assert_eq!(w[1] - w[0], 780_000);
        }
        let first_kept = p.centers[0] as f64 + 2_400_000.0 * SKIP;
        let last_kept = *p.centers.last().unwrap() as f64 + 2_400_000.0 * KEEP_TO;
        assert!(first_kept <= 470_000_000.0 + 1.0 && last_kept >= 608_000_000.0);
    }

    #[test]
    fn a_carrier_at_a_hop_centre_is_still_seen_and_no_spike_is_invented() {
        // Fake puts a DC spike (a tone at offset 0) in every capture, like the real tuner.
        let mut f = Fake { center: 0, tone_hz: 521_300_000.0, dc: true };
        let s = run(&mut f, 500_000_000, 540_000_000, 2048, 8).unwrap();
        let top = s.points.iter().max_by(|a, b| a.1.total_cmp(&b.1)).unwrap();
        assert!((top.0 - 521_300_000.0).abs() < 2_500.0, "peak at {}", top.0);
        let floor = crate::dsp::noise_floor(&s.points.iter().map(|p| p.1).collect::<Vec<_>>());
        let strong = s.points.iter().filter(|p| p.1 > floor + 20.0).count();
        assert!(strong < 40, "spikes leaked into the sweep: {strong} strong points");
    }

    struct Fake {
        center: u32,
        tone_hz: f64,
        dc: bool,
    }
    impl IqSource for Fake {
        fn tune(&mut self, hz: u32) -> Result<(), String> {
            self.center = hz;
            Ok(())
        }
        fn read(&mut self, buf: &mut [u8]) -> Result<usize, String> {
            let off = self.tone_hz - self.center as f64;
            let mut tones = vec![(off, 0.5)];
            if self.dc {
                tones.push((0.0, 0.3));
            }
            let raw = dsp::synth(2_400_000, &tones, buf.len() / 2, 0.01); // synth drops out-of-band tones
            buf.copy_from_slice(&raw);
            Ok(buf.len())
        }
        fn sample_rate(&self) -> u32 {
            2_400_000
        }
    }
}
