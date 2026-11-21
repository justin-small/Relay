//! Each output follows its own clock: an hour of audio against clocks that are fast and slow.

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec};
use relay_bus::*;

fn drift_rig() -> Rig {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::sine(440.0, 48_000, 0.3));
    r.fake.add_output("fast", DeviceSpec::new(2, 48_000, 256).with_error_ppm(3000.0));
    r.fake.add_output("slow", DeviceSpec::new(2, 48_000, 256).with_error_ppm(-3000.0));
    r.fake.add_output("exact", DeviceSpec::new(2, 48_000, 256));
    r.eng
        .apply(&Config {
            enabled: true,
            lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))],
            outputs: vec![
                device_output("fast", "fast", 2, 60),
                device_output("slow", "slow", 2, 60),
                device_output("exact", "exact", 2, 60),
            ],
            matrix: ["fast", "slow", "exact"].iter().flat_map(|o| [cell(1, o, 1, 0.0), cell(1, o, 2, 0.0)]).collect(),
        })
        .unwrap();
    r
}

#[test]
fn an_hour_against_fast_and_slow_output_clocks() {
    let r = drift_rig();
    let settle = 60.0;
    r.fake.run_until(settle);
    let base = r.eng.status();
    for id in ["fast", "slow", "exact"] {
        eprintln!("t=60 {id}: ratio {:.5} fill {:.2} ms (target {:.1})", output(&base, id).drift, output(&base, id).queued_ms, output(&base, id).target_ms);
    }
    let mut worst = [0.0f32; 3];
    for minute in 2..=60 {
        r.fake.run_until(minute as f64 * 60.0);
        let st = r.eng.status();
        for (i, id) in ["fast", "slow", "exact"].iter().enumerate() {
            let o = output(&st, id);
            worst[i] = worst[i].max((o.fill_min_ms - o.target_ms).abs()).max((o.fill_max_ms - o.target_ms).abs());
        }
    }
    let st = r.eng.status();
    for (i, id) in ["fast", "slow", "exact"].iter().enumerate() {
        let o = output(&st, id);
        eprintln!("t=3600 {id}: ratio {:.5} fill {:.2} ms, worst instantaneous deviation {:.2} ms, underruns {} resyncs {}", o.drift, o.queued_ms, worst[i], o.underruns, o.resyncs);
        assert_eq!(o.underruns, 0, "{id}: no underruns");
        assert_eq!(o.resyncs, 0, "{id}: no resyncs");
        assert!(worst[i] <= 5.0, "{id}: fill stayed within 5 ms of its target, worst {}", worst[i]);
        assert!((o.queued_ms - o.target_ms).abs() < 1.0, "{id}: smoothed fill sits on the target");
    }
    assert!(near(output(&st, "fast").drift, 1.003, 5e-5), "fast clock: the learned drift converged on the true offset, got {}", output(&st, "fast").drift);
    assert!(near(output(&st, "slow").drift, 0.997, 5e-5), "slow clock: got {}", output(&st, "slow").drift);
    assert!(near(output(&st, "exact").drift, 1.0, 5e-5), "exact clock: got {}", output(&st, "exact").drift);
    assert_eq!(st.glitches.underruns + st.glitches.resyncs + st.glitches.dropped_frames, 0);
}

#[test]
fn the_input_clock_counts_too_and_each_lane_has_its_own_loop() {
    // Two input devices on two different clocks, both into one output on an exact clock. One ratio could not
    // serve both lanes; each lane's own loop settles on its own offset.
    let r = rig();
    r.fake.add_input("a", DeviceSpec::new(1, 48_000, 128).with_error_ppm(1000.0), signal::sine(440.0, 48_000, 0.2));
    r.fake.add_input("b", DeviceSpec::new(1, 48_000, 128).with_error_ppm(-700.0), signal::sine(660.0, 48_000, 0.2));
    r.fake.add_output("o", DeviceSpec::new(1, 48_000, 256));
    r.eng
        .apply(&Config {
            enabled: true,
            lanes: vec![device_lane(1, "a", ChannelSel::Index(0)), device_lane(2, "b", ChannelSel::Index(0))],
            outputs: vec![device_output("o", "o", 1, 60)],
            matrix: vec![cell(1, "o", 1, 0.0), cell(2, "o", 1, -6.0)],
        })
        .unwrap();
    r.fake.run_until(90.0);
    let settled = r.eng.status();
    r.fake.run_until(600.0);
    let st = r.eng.status();
    let o = output(&st, "o");
    assert_eq!(o.underruns, output(&settled, "o").underruns, "no underruns after settling");
    assert_eq!(o.resyncs, 0);
    assert_eq!(st.glitches.dropped_frames, 0);
    // The reported drift is the mean over the two lanes: (1 - 0.001 + 1 + 0.0007) / 2.
    assert!(near(o.drift, 1.0 - 0.00015, 5e-5), "mean drift of two lanes with opposite errors, got {}", o.drift);
    assert!((o.queued_ms - o.target_ms).abs() < 1.5, "fill {} vs target {}", o.queued_ms, o.target_ms);
}

#[test]
fn an_input_clock_that_is_fast_makes_the_output_play_it_faster() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128).with_error_ppm(1000.0), signal::sine(440.0, 48_000, 0.2));
    r.fake.add_output("o", DeviceSpec::new(1, 48_000, 256));
    r.eng
        .apply(&Config {
            enabled: true,
            lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))],
            outputs: vec![device_output("o", "o", 1, 60)],
            matrix: vec![cell(1, "o", 1, 0.0)],
        })
        .unwrap();
    r.fake.run_until(120.0);
    let st = r.eng.status();
    let o = output(&st, "o");
    // The lane arrives 0.1% fast, so the output consumes at 0.999 of nominal ratio.
    assert!(near(o.drift, 0.999001, 5e-5), "got {}", o.drift);
    assert!(near(o.ratio, o.drift, 1.5e-3), "the instantaneous ratio stays close to the learned one");
    assert_eq!(o.resyncs + o.underruns, 0);
}
