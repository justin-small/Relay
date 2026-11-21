//! Live reconfiguration, supervision, and the external (pushed) inputs and pulled outputs.

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec};
use relay_bus::*;

fn two_lane_rig() -> Rig {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(2, 48_000, 128), signal::dc_per_channel(vec![0.2, 0.3]));
    // The output clock is 0.1% fast, so the drift loop has real work to keep.
    r.fake.add_output("o", DeviceSpec::new(2, 48_000, 256).with_error_ppm(1000.0));
    r.fake.capture_output("o", true);
    r
}

fn cfg_with(matrix: Vec<Cell>) -> Config {
    Config {
        enabled: true,
        lanes: vec![device_lane(1, "mic", ChannelSel::Index(0)), device_lane(2, "mic", ChannelSel::Index(1))],
        outputs: vec![device_output("o", "o", 2, 60)],
        matrix,
    }
}

#[test]
fn applying_a_new_matrix_mid_stream_does_not_glitch_or_reset_drift() {
    let r = two_lane_rig();
    r.eng.apply(&cfg_with(vec![cell(1, "o", 1, 0.0)])).unwrap();
    r.fake.run_until(40.0);
    let before = r.eng.status();
    let o0 = output(&before, "o").clone();
    assert!(near(o0.drift, 1.001, 5e-5), "settled on the 0.1% offset, got {}", o0.drift);
    r.fake.take_output("o");

    // Change a gain, add a second lane to channel 2 (a new ring), and mix two lanes into channel 1.
    let next = vec![cell(1, "o", 1, -6.0206), cell(2, "o", 1, 0.0), cell(2, "o", 2, 0.0)];
    r.eng.apply(&cfg_with(next)).unwrap();
    r.fake.run_until(45.0);
    let cap = r.fake.take_output("o");
    let after = r.eng.status();
    let o1 = output(&after, "o");
    assert_eq!(o1.underruns, o0.underruns, "no underrun");
    assert_eq!(o1.resyncs, o0.resyncs, "no resync");
    assert_eq!(after.glitches.dropped_frames, 0);
    assert_eq!(after.state_of_input("mic"), StreamState::Open, "nothing was reopened");
    assert!(near(o1.drift, o0.drift, 1e-4), "drift state kept: {} then {}", o0.drift, o1.drift);

    // The audio: channel 1 goes from 0.2 to 0.2*0.5 + 0.3 = 0.4, channel 2 from 0 to 0.3, with no jump bigger
    // than a ramp step (the lane that is added starts silent while its ring fills, then plays).
    let n = cap.len() / 2;
    let c1 = channel(&cap, 2, 0, 0, n);
    let c2 = channel(&cap, 2, 1, 0, n);
    assert!(near(mean(&c1[n - 4800..]), 0.4, 3e-3) && near(mean(&c2[n - 4800..]), 0.3, 3e-3));
    let worst = |x: &[f32]| x.windows(2).map(|w| (w[1] - w[0]).abs()).fold(0.0f32, f32::max);
    // The new lane comes in when its ring has filled to its target (30 ms of silence first), and its gain ramps.
    assert!(worst(&c1) < 0.01, "channel 1 steps by {} at most", worst(&c1));
    assert!(worst(&c2) < 0.01, "channel 2 steps by {} at most", worst(&c2));
}

#[test]
fn removing_a_cell_ramps_down_and_then_lets_the_ring_go() {
    let r = two_lane_rig();
    r.eng.apply(&cfg_with(vec![cell(1, "o", 1, 0.0), cell(2, "o", 2, 0.0)])).unwrap();
    r.fake.run_until(10.0);
    assert_eq!(r.eng.status().lanes[1].subscribers, 1);
    r.fake.take_output("o");
    r.eng.apply(&cfg_with(vec![cell(1, "o", 1, 0.0)])).unwrap();
    r.fake.run_until(10.5);
    let cap = r.fake.take_output("o");
    let n = cap.len() / 2;
    let c2 = channel(&cap, 2, 1, 0, n);
    let worst = c2.windows(2).map(|w| (w[1] - w[0]).abs()).fold(0.0f32, f32::max);
    assert!(worst < 0.01, "channel 2 fades instead of being cut, biggest step {worst}");
    assert!(c2[n - 100..].iter().all(|&x| x == 0.0));
    // After the grace period a supervise pass lets the lane's ring go.
    r.eng.supervise();
    assert_eq!(r.eng.status().lanes[1].subscribers, 0);
    let st = r.eng.status();
    assert_eq!(st.glitches.underruns + st.glitches.resyncs, 0);
    // Using the lane again later starts cleanly (no stale audio, no resync).
    r.eng.apply(&cfg_with(vec![cell(1, "o", 1, 0.0), cell(2, "o", 2, 0.0)])).unwrap();
    r.fake.run_until(12.0);
    let st = r.eng.status();
    assert_eq!(st.glitches.underruns + st.glitches.resyncs, 0);
    assert!(near(st.lanes[1].meter.rms, 0.3, 1e-4));
}

#[test]
fn adding_and_removing_lanes_and_outputs_goes_through_the_queues() {
    // Everything in one run so that the realtime callbacks (guarded by assert_no_alloc) see every kind of command.
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(2, 44_100, 441), signal::sine(500.0, 44_100, 0.3));
    r.fake.add_output("a", DeviceSpec::new(4, 48_000, 256));
    r.fake.add_output("b", DeviceSpec::new(2, 44_100, 441).with_error_ppm(-500.0));
    let lanes = |n: usize| (1..=n).map(|l| device_lane(l, "mic", if l % 2 == 0 { ChannelSel::Mix } else { ChannelSel::Index(l % 2) })).collect::<Vec<_>>();
    let mut cfg = Config { enabled: true, lanes: lanes(2), outputs: vec![device_output("a", "a", 4, 60)], matrix: vec![cell(1, "a", 1, 0.0)] };
    r.eng.apply(&cfg).unwrap();
    let tap = r.eng.open_tap(1, TapOptions { rate: Some(24_000), capacity_ms: 1000 }).unwrap();
    r.fake.run_until(2.0);
    for step in 1..=6 {
        match step {
            1 => {
                cfg.lanes = lanes(6);
                cfg.outputs.push(device_output("b", "b", 2, 100));
                cfg.matrix = (1..=6).map(|l| cell(l, "a", (l - 1) % 4 + 1, -3.0)).chain([cell(2, "b", 1, 0.0), cell(3, "b", 2, 0.0)]).collect();
            }
            2 => cfg.matrix.retain(|c| c.lane != 2 && c.lane != 5),
            3 => cfg.lanes.retain(|l| l.lane != 4),
            4 => cfg.outputs.retain(|o| o.id != "a"),
            5 => {
                cfg.matrix.retain(|c| c.out != "a");
                cfg.lanes[0].channel = ChannelSel::Index(1);
            }
            _ => cfg.outputs[0].latency_ms = 200, // reopens b
        }
        cfg.matrix.retain(|c| cfg.outputs.iter().any(|o| o.id == c.out));
        r.eng.apply(&cfg).unwrap();
        r.eng.inject("b", &[0], &vec![0.1; 2000], 22_050).ok();
        r.fake.advance(1.0);
        r.eng.supervise();
        r.eng.read_tap(tap, 100_000, std::time::Duration::ZERO).unwrap();
    }
    let st = r.eng.status();
    assert_eq!(st.glitches.discard_overflow, 0);
    assert_eq!(st.glitches.command_busy, 0);
    r.eng.close_tap(tap).unwrap();
    r.eng.apply(&Config::default()).unwrap();
    assert!(!r.eng.status().running);
}

#[test]
fn a_failed_output_goes_to_waiting_and_comes_back_through_supervise() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::dc(0.2));
    r.fake.add_output("o", DeviceSpec::new(1, 48_000, 256));
    r.fake.capture_output("o", true);
    let cfg = Config {
        enabled: true,
        lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))],
        outputs: vec![device_output("o", "o", 1, 60)],
        matrix: vec![cell(1, "o", 1, 0.0)],
    };
    r.eng.apply(&cfg).unwrap();
    r.fake.run_until(2.0);
    assert_eq!(output(&r.eng.status(), "o").state, StreamState::Open);
    assert!(near(mean(&r.fake.take_output("o")[48_000..]), 0.2, 3e-3));

    // The device is unplugged.
    r.fake.set_unplugged("o", true);
    r.eng.supervise();
    let st = r.eng.status();
    assert_eq!(output(&st, "o").state, StreamState::Waiting);
    assert!(output(&st, "o").error.as_deref().unwrap().contains("unplugged"));
    assert_eq!(st.lanes[0].subscribers, 0, "the lane let go of the dead output");
    assert!(near(st.lanes[0].meter.rms, 0.2, 1e-4), "the input carries on regardless");

    // Retries back off: nothing before the first delay, a failed try after it, a longer wait after that.
    r.fake.advance(0.3);
    r.eng.supervise();
    assert_eq!(output(&r.eng.status(), "o").state, StreamState::Waiting);
    r.fake.advance(0.3);
    r.eng.supervise(); // tries (0.6 s since the failure), fails: device still unplugged
    let err = output(&r.eng.status(), "o").error.clone().unwrap();
    assert!(err.contains("not connected"), "the backend's own words: {err}");
    r.fake.set_unplugged("o", false);
    r.fake.advance(0.5);
    r.eng.supervise(); // 0.5 s after the failed try: the second delay is 1 s, so still waiting
    assert_eq!(output(&r.eng.status(), "o").state, StreamState::Waiting);
    r.fake.advance(0.6);
    r.eng.supervise();
    let st = r.eng.status();
    assert_eq!(output(&st, "o").state, StreamState::Open, "reopened");
    assert!(output(&st, "o").error.is_none());
    assert_eq!(st.lanes[0].subscribers, 1, "and rewired to the lane");

    r.fake.take_output("o");
    r.fake.advance(2.0);
    let cap = r.fake.take_output("o");
    assert!(near(mean(&cap[cap.len() - 48_000..]), 0.2, 3e-3), "audio flows again");
}

#[test]
fn a_failed_input_is_reopened_and_its_lanes_rewired() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::dc(0.2));
    r.fake.add_output("o", DeviceSpec::new(1, 48_000, 256));
    r.fake.capture_output("o", true);
    r.eng
        .apply(&Config {
            enabled: true,
            lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))],
            outputs: vec![device_output("o", "o", 1, 60)],
            matrix: vec![cell(1, "o", 1, 0.0)],
        })
        .unwrap();
    r.fake.run_until(1.0);
    r.fake.fail_streams("mic", "driver fault");
    r.fake.advance(0.3); // the output runs dry before the next supervise pass notices: one underrun, not one per block
    r.eng.supervise();
    let st = r.eng.status();
    assert_eq!(st.inputs[0].state, StreamState::Waiting);
    assert_eq!(st.inputs[0].error.as_deref(), Some("driver fault"));
    assert_eq!(st.lanes[0].state, Some(StreamState::Waiting));
    r.fake.advance(0.6); // past the first retry delay (0.5 s)
    let dry = r.eng.status().glitches.underruns;
    assert_eq!(dry, 1);
    r.eng.supervise(); // a driver fault is fixed by reopening
    assert_eq!(r.eng.status().inputs[0].state, StreamState::Open);
    r.fake.take_output("o");
    r.fake.advance(2.0);
    let st = r.eng.status();
    assert!(near(st.lanes[0].meter.rms, 0.2, 1e-4));
    assert_eq!(st.lanes[0].subscribers, 1);
    let cap = r.fake.take_output("o");
    assert!(near(mean(&cap[cap.len() - 48_000..]), 0.2, 3e-3), "audio flows again through the output");
    assert_eq!(st.glitches.underruns, dry, "and it came back cleanly");
}

#[test]
fn an_input_that_goes_silent_for_two_seconds_is_treated_as_lost() {
    let r = rig();
    r.fake.add_input("mic", DeviceSpec::new(1, 48_000, 128), signal::dc(0.2));
    r.eng.apply(&Config { enabled: true, lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))], ..Default::default() }).unwrap();
    r.fake.run_until(1.0);
    r.eng.supervise();
    assert_eq!(r.eng.status().inputs[0].state, StreamState::Open);
    // The stream stays "open" but stops delivering (a device that hangs rather than reporting an error).
    r.fake.stall_input("mic", true);
    r.fake.advance(1.0);
    r.eng.supervise();
    assert_eq!(r.eng.status().inputs[0].state, StreamState::Open, "one second is not enough");
    r.fake.advance(1.5);
    r.eng.supervise();
    let st = r.eng.status();
    assert_eq!(st.inputs[0].state, StreamState::Waiting);
    assert!(st.inputs[0].error.as_deref().unwrap().contains("stopped"));
}

#[test]
fn a_missing_device_waits_and_is_found_later() {
    let r = rig();
    r.eng.apply(&Config { enabled: true, lanes: vec![device_lane(1, "usb", ChannelSel::Mix)], ..Default::default() }).unwrap();
    assert_eq!(r.eng.status().inputs[0].state, StreamState::Waiting);
    r.fake.add_input("usb", DeviceSpec::new(2, 48_000, 128), signal::dc(0.4));
    r.fake.advance(0.6);
    r.eng.supervise();
    assert_eq!(r.eng.status().inputs[0].state, StreamState::Open);
    r.fake.advance(0.5);
    assert!(near(r.eng.status().lanes[0].meter.rms, 0.4, 1e-4));
}

// -- external inputs and pull outputs -------------------------------------------------------------------------
#[test]
fn pushed_audio_reaches_a_device_output_through_a_lane() {
    let r = rig();
    r.fake.add_output("o", DeviceSpec::new(1, 48_000, 256));
    r.fake.capture_output("o", true);
    let lane = lane(1, "aes67", "stage", ChannelSel::Index(1));
    let id = input_id("aes67", &lane.device);
    r.eng
        .apply(&Config {
            enabled: true,
            lanes: vec![lane],
            outputs: vec![device_output("o", "o", 1, 60)],
            matrix: vec![cell(1, "o", 1, 0.0)],
        })
        .unwrap();
    assert_eq!(r.eng.status().inputs[0].state, StreamState::Waiting, "nothing has arrived yet");
    // A stereo network source at 44.1 kHz: 10 ms per push, channel 1 carries 0.35, channel 0 carries -0.5.
    let block: Vec<f32> = (0..441).flat_map(|_| [-0.5, 0.35]).collect();
    for step in 1..=300 {
        r.eng.push(&id, &block, 2, 44_100).unwrap();
        r.fake.run_until(step as f64 * 0.01);
    }
    let st = r.eng.status();
    assert_eq!(st.inputs[0].state, StreamState::Open);
    assert_eq!(st.glitches.underruns + st.glitches.resyncs, 0);
    let cap = r.fake.take_output("o");
    assert!(near(mean(&cap[cap.len() - 24_000..]), 0.35, 4e-3));
    assert_eq!(r.eng.push("aes67:nowhere", &block, 2, 44_100), Err(Error::UnknownInput("aes67:nowhere".into())));
}

#[test]
fn a_pull_output_is_paced_by_the_callers_clock() {
    let r = rig();
    let lane = lane(1, "ndi", "cam", ChannelSel::Index(0));
    let id = input_id("ndi", &lane.device);
    let out = OutputSpec { id: "send".into(), name: "".into(), output: "aes67".into(), device: None, channels: 2, latency_ms: 60 };
    r.eng
        .apply(&Config { enabled: true, lanes: vec![lane], outputs: vec![out], matrix: vec![cell(1, "send", 1, 0.0), cell(1, "send", 2, -6.0206)] })
        .unwrap();
    assert_eq!(r.eng.status().outputs[0].kind, OutputKind::Pull);
    // The source pushes 481 frames per 10 ms (0.2% fast); the sender pulls 480 per 10 ms.
    let block = vec![0.4f32; 481];
    let mut last = Vec::new();
    for step in 0..6000 {
        r.eng.push(&id, &block, 1, 48_000).unwrap();
        last = r.eng.pull("send", 480).unwrap();
        if step % 1000 == 999 {
            r.eng.supervise();
        }
    }
    let st = r.eng.status();
    let o = &st.outputs[0];
    assert_eq!(last.len(), 960, "interleaved, two channels");
    assert!(near(last[900], 0.4, 3e-3) && near(last[901], 0.2, 3e-3), "channel 2 is 6 dB down: {} {}", last[900], last[901]);
    // It settles on the source's 0.2% excess within the minute; a few early underruns/resyncs are not allowed
    // either, because the loop starts from a primed ring.
    assert!(near(o.drift, 1.0 / 1.0020833, 1e-4), "the sender's pacing is 0.208% slower than the source's: {}", o.drift);
    assert_eq!(o.resyncs, 0);
    assert_eq!(o.underruns, 0);
    assert!(r.eng.pull("nope", 10).is_err());
    assert!(r.eng.pull("send", 0).unwrap().is_empty());
}

trait StatusExt {
    fn state_of_input(&self, device: &str) -> StreamState;
}
impl StatusExt for Status {
    fn state_of_input(&self, device: &str) -> StreamState {
        self.inputs.iter().find(|i| i.device.as_deref() == Some(device)).expect("input").state
    }
}
