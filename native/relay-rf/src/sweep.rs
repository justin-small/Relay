//! Band sweep (issue 366): hop the tuner across a band using the middle of each capture, and stitch one spectrum.
use crate::{dsp, IqSource};

pub struct Plan {
    pub centers: Vec<u32>,
    pub usable_hz: f64,
}

/// Centres that cover start..stop. Only the middle `usable` fraction of each capture is kept: the edges roll off.
pub fn plan(start_hz: u32, stop_hz: u32, rate: u32, usable: f64) -> Plan {
    let usable_hz = rate as f64 * usable;
    let mut centers = vec![];
    let mut c = start_hz as f64 + usable_hz / 2.0;
    loop {
        centers.push(c as u32);
        if c + usable_hz / 2.0 >= stop_hz as f64 {
            break;
        }
        c += usable_hz;
    }
    Plan { centers, usable_hz }
}

pub struct Sweep {
    /// (frequency Hz, dBFS) points, rising.
    pub points: Vec<(f64, f32)>,
    pub seconds: f64,
}

pub fn run<S: IqSource>(src: &mut S, start: u32, stop: u32, bins: usize, frames: usize) -> Result<Sweep, String> {
    let rate = src.sample_rate();
    let p = plan(start, stop, rate, 0.75);
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
        let (lo, hi) = (((1.0 - 0.75) / 2.0 * bins as f64) as usize, ((1.0 + 0.75) / 2.0 * bins as f64) as usize);
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
    fn plan_covers_the_band_with_few_hops() {
        let p = plan(470_000_000, 608_000_000, 2_400_000, 0.75);
        assert_eq!(p.centers.len(), 77); // 138 MHz / 1.8 MHz
        assert!(*p.centers.first().unwrap() as f64 - p.usable_hz / 2.0 <= 470_000_000.0);
        assert!(*p.centers.last().unwrap() as f64 + p.usable_hz / 2.0 >= 608_000_000.0);
        for w in p.centers.windows(2) {
            assert_eq!(w[1] - w[0], 1_800_000);
        }
    }

    struct Fake {
        center: u32,
        tone_hz: f64,
    }
    impl IqSource for Fake {
        fn tune(&mut self, hz: u32) -> Result<(), String> {
            self.center = hz;
            Ok(())
        }
        fn read(&mut self, buf: &mut [u8]) -> Result<usize, String> {
            let off = self.tone_hz - self.center as f64;
            let raw = dsp::synth(2_400_000, &[(off, 0.5)], buf.len() / 2, 0.01); // synth drops out-of-band tones
            buf.copy_from_slice(&raw);
            Ok(buf.len())
        }
        fn sample_rate(&self) -> u32 {
            2_400_000
        }
    }

    #[test]
    fn stitched_sweep_finds_a_carrier_in_the_right_place() {
        let mut f = Fake { center: 0, tone_hz: 521_300_000.0 };
        let s = run(&mut f, 500_000_000, 540_000_000, 2048, 8).unwrap();
        let top = s.points.iter().max_by(|a, b| a.1.total_cmp(&b.1)).unwrap();
        assert!((top.0 - 521_300_000.0).abs() < 2_500.0, "peak at {}", top.0);
        assert!(s.points.windows(2).all(|w| w[1].0 > w[0].0), "points must rise");
    }
}
