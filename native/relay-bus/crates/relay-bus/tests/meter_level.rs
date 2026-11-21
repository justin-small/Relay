//! An output meter reads the level of what was sent. The benchmark once saw output meters 1.6 to 2.6 dB low on a sum
//! of 31 sines; this pins down that the meter is right and the benchmark's averaging was not: the power average of
//! the meter's block readings matches the true level, while the plain average of those readings (which fluctuate a
//! lot on this signal) is low.

mod common;
use common::*;
use relay_bus::fake::{DeviceSpec, Signal};
use relay_bus::*;

const AMP: f32 = 0.03;
const LANES: usize = 31;
const OUT_BLOCK: usize = 480; // 10 ms at 48 kHz

fn lane_freq(n: usize) -> f64 {
    200.0 + 30.0 * n as f64 // lanes 2..=32, every frequency a multiple of 30 Hz, all in phase at t = 0
}

/// Channel c carries the sine of lane c + 2, the bench's synthetic device.
fn sines() -> Signal {
    Box::new(|n, f| {
        for (c, o) in f.iter_mut().enumerate() {
            *o = AMP * (2.0 * std::f64::consts::PI * lane_freq(c + 2) * n as f64 / 48_000.0).sin() as f32;
        }
    })
}

fn db(x: f32) -> f32 {
    20.0 * x.log10()
}

#[test]
fn output_meter_power_average_matches_the_level_sent() {
    let r = rig();
    r.fake.add_input("d", DeviceSpec::new(LANES, 48_000, 128), sines());
    r.fake.add_output("o", DeviceSpec::new(2, 48_000, OUT_BLOCK));
    r.fake.capture_output("o", true);
    let cfg = Config {
        enabled: true,
        lanes: (0..LANES).map(|c| device_lane(c + 2, "d", ChannelSel::Index(c))).collect(),
        outputs: vec![device_output("o", "o", 2, 60)],
        matrix: (0..LANES).map(|c| cell(c + 2, "o", 1, -40.0)).collect(),
    };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(1.0); // let the buffers fill

    // 31 sines of distinct frequencies add in power: each is AMP/sqrt(2) at -40 dB.
    let expected = (LANES as f32).sqrt() * 0.01 * AMP / 2f32.sqrt(); // -58.55 dBFS
    assert!(near(db(expected), -58.55, 0.01));

    r.fake.take_output("o");
    let (mut power, mut linear, blocks) = (0.0f32, 0.0f32, 300); // 3 s = 90 whole periods of the 33 ms pattern
    for _ in 0..blocks {
        r.fake.run_output_blocks("o", 1);
        let m = output(&r.eng.status(), "o").meters[0].rms;
        power += m * m;
        linear += m;
    }
    let power_avg = (power / blocks as f32).sqrt();
    let linear_avg = linear / blocks as f32;
    assert!(near(db(power_avg), db(expected), 0.1), "meter power average {} dBFS, expected {}", db(power_avg), db(expected));

    // The capture, measured over whole periods, agrees: the mix itself is at the expected level.
    let cap = r.fake.take_output("o");
    let ch = channel(&cap, 2, 0, 0, cap.len() / 2);
    assert!(near(db(rms(&ch)), db(expected), 0.1), "captured RMS {} dBFS", db(rms(&ch)));

    // The plain average of 10 ms block readings is what the benchmark took: it reads a couple of dB low here.
    let low = db(expected) - db(linear_avg);
    assert!(low > 1.0 && low < 4.0, "average of block RMS readings is {low} dB low");
}
