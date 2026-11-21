//! The crosspoint matrix: sums, gains, mute floor, channel pick versus mix, rate conversion.

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec};
use relay_bus::*;

#[test]
fn matrix_sums_and_gains_are_right() {
    let r = rig();
    // mic: channel 0 carries 0.2, channel 1 carries 0.3.
    r.fake.add_input("mic", DeviceSpec::new(2, 48_000, 128), signal::dc_per_channel(vec![0.2, 0.3]));
    r.fake.add_output("o", DeviceSpec::new(8, 48_000, 256));
    r.fake.capture_output("o", true);
    let cfg = Config {
        enabled: true,
        lanes: vec![
            device_lane(1, "mic", ChannelSel::Index(0)),
            device_lane(2, "mic", ChannelSel::Index(1)),
            device_lane(3, "mic", ChannelSel::Mix),
        ],
        outputs: vec![device_output("o", "o", 3, 60)],
        matrix: vec![
            cell(1, "o", 1, 0.0),
            cell(2, "o", 1, -6.0206),  // x0.5
            cell(3, "o", 2, 0.0),
            cell(1, "o", 2, -90.0),    // the mute floor: contributes nothing
            cell(1, "o", 3, 6.0206),   // x2
        ],
    };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(3.0);
    let cap = r.fake.take_output("o");
    let n = cap.len() / 3;
    let tail = |c| channel(&cap, 3, c, n - 24_000, n);
    // The resampler sits at about 1:1; allow a little for its ripple.
    assert!(near(mean(&tail(0)), 0.2 + 0.3 * 0.5, 2e-3), "0.2 + 0.3 at -6 dB, got {}", mean(&tail(0)));
    assert!(near(mean(&tail(1)), 0.25, 2e-3), "mix of 0.2 and 0.3 and a muted cell, got {}", mean(&tail(1)));
    assert!(near(mean(&tail(2)), 0.4, 2e-3), "0.2 at +6 dB, got {}", mean(&tail(2)));

    let st = r.eng.status();
    assert_eq!(st.glitches.underruns, 0);
    assert_eq!(st.glitches.resyncs, 0);
    // Lane meters read the picked channel; output meters read what was sent.
    assert!(near(st.lanes[0].meter.rms, 0.2, 1e-4));
    assert!(near(st.lanes[2].meter.rms, 0.25, 1e-4));
    assert!(near(output(&st, "o").meters[2].peak, 0.4, 5e-3));
    assert_eq!(st.lanes[0].subscribers, 1);
    assert_eq!(st.lanes[1].subscribers, 1);
}

#[test]
fn one_device_is_opened_once_for_many_lanes() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(4, 48_000, 128), signal::silence());
    let cfg = Config {
        enabled: true,
        lanes: (1..=4).map(|n| device_lane(n, "mic", ChannelSel::Index(n - 1))).collect(),
        ..Default::default()
    };
    r.eng.apply(&cfg).unwrap();
    let st = r.eng.status();
    assert_eq!(st.inputs.len(), 1);
    assert_eq!(st.inputs[0].lanes, vec![1, 2, 3, 4]);
    assert_eq!(st.inputs[0].state, StreamState::Open);
    assert_eq!(st.inputs[0].channels, Some(4));
}

#[test]
fn a_channel_past_the_end_means_the_last_channel() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(2, 48_000, 128), signal::dc_per_channel(vec![0.1, 0.7]));
    let cfg = Config { enabled: true, lanes: vec![device_lane(1, "mic", ChannelSel::Index(9))], ..Default::default() };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(0.2);
    assert!(near(r.eng.status().lanes[0].meter.rms, 0.7, 1e-4));
}

#[test]
fn input_and_output_at_44k1_keep_pitch_and_level() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 44_100, 441), signal::sine(1000.0, 44_100, 0.5));
    r.fake.add_output("o", DeviceSpec::new(2, 44_100, 441));
    r.fake.capture_output("o", true);
    let cfg = Config {
        enabled: true,
        lanes: vec![device_lane(1, "mic", ChannelSel::Mix)],
        outputs: vec![device_output("o", "o", 2, 60)],
        matrix: vec![cell(1, "o", 1, 0.0), cell(1, "o", 2, 0.0)],
    };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(4.0);
    let cap = r.fake.take_output("o");
    let n = cap.len() / 2;
    let tail = channel(&cap, 2, 0, n - 44_100, n);
    assert!(near(rms(&tail), 0.5 / 2f32.sqrt(), 0.01), "level kept through two conversions, got {}", rms(&tail));
    let crossings = tail.windows(2).filter(|w| w[0] <= 0.0 && w[1] > 0.0).count();
    assert!((998..=1002).contains(&crossings), "1 kHz stays 1 kHz, counted {crossings}");
    // The lane itself sits at 48 kHz: the lane meter sees the same level.
    let st = r.eng.status();
    assert!(near(st.lanes[0].meter.rms, 0.5 / 2f32.sqrt(), 0.01));
    assert_eq!(st.glitches.underruns, 0);
}

#[test]
fn meters_hold_peaks_decay_and_flag_clipping() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::dc(1.0));
    let cfg = Config { enabled: true, lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))], ..Default::default() };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(0.5);
    let m = r.eng.status().lanes[0].meter;
    assert!(m.clipping && m.clipped_samples > 0 && near(m.peak, 1.0, 1e-6));
    // Silence: the peak decays at 20 dB/s (0.1 after one second) and the clip light stays on for 3 s.
    r.fake.set_signal("mic", signal::silence());
    r.fake.advance(1.0);
    let m = r.eng.status().lanes[0].meter;
    assert!(m.clipping, "still held after 1 s");
    assert!(m.peak > 0.07 && m.peak < 0.14, "peak decayed to about 0.1, got {}", m.peak);
    assert_eq!(m.rms, 0.0);
    r.fake.advance(3.0);
    assert!(!r.eng.status().lanes[0].meter.clipping, "clip light off after the hold");
}

#[test]
fn last_input_age_follows_the_clock() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::dc(0.1));
    let cfg = Config { enabled: true, lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))], ..Default::default() };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(1.0);
    let age = r.eng.status().lanes[0].last_input_age_s.unwrap();
    assert!(age < 0.01, "audio is arriving, age {age}");
    assert!(r.eng.status().lanes[1].last_input_age_s.is_none(), "an unpatched lane has none");
}
