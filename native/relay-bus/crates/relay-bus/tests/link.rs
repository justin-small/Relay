//! A link carries a lane of one engine into an input of another, without Python (#605).

mod common;
use common::*;
use relay_bus::fake::{signal, DeviceSpec, FakeBackend};
use relay_bus::*;
use std::sync::Arc;
use std::time::{Duration, Instant};

const INTAKE: &str = "feed:main";

/// A source engine with a steady lane on a fake clock, and a destination engine with an external input patched to a
/// pull output.
fn pair() -> (Arc<FakeBackend>, Arc<Engine>, Arc<Engine>) {
    let fake = FakeBackend::new();
    fake.add_input("mic", DeviceSpec::new(1, 48_000, 480), signal::dc(0.25));
    let src = Arc::new(Engine::new(fake.clone()));
    src.apply(&Config { enabled: true, lanes: vec![device_lane(1, "mic", ChannelSel::Index(0))], ..Default::default() }).unwrap();
    let dst = Arc::new(Engine::new(FakeBackend::new()));
    dst.apply(&Config {
        enabled: true,
        lanes: vec![lane(1, "feed", "main", ChannelSel::Index(0))],
        outputs: vec![OutputSpec { id: "mix".into(), name: "mix".into(), output: "pull".into(), device: None, channels: 1, latency_ms: 40 }],
        matrix: vec![cell(1, "mix", 1, 0.0)],
    })
    .unwrap();
    (fake, src, dst)
}

fn wait_for(what: &str, mut ok: impl FnMut() -> bool) {
    let until = Instant::now() + Duration::from_secs(5);
    while !ok() {
        assert!(Instant::now() < until, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn audio_on_a_source_lane_comes_out_of_the_destination_mix() {
    let (fake, src, dst) = pair();
    let link = Link::start(src, 1, dst.clone(), INTAKE).unwrap();
    // The fake input runs on a virtual clock: feed it in 20 ms steps while the link thread carries it.
    let mut heard = Vec::new();
    let mut t = 0.0;
    wait_for("the level to come out of the mix", || {
        t += 0.02;
        fake.run_until(t);
        std::thread::sleep(Duration::from_millis(3));
        heard.extend(dst.pull("mix", 960).unwrap());
        heard.len() > 48_000 && heard.iter().rev().take(480).all(|&x| (x - 0.25).abs() < 0.01)
    });
    assert!(link.status().carried > 0);
    assert_eq!(link.status().dropped, 0);
}

#[test]
fn a_destination_with_no_such_input_refuses_and_the_link_counts_it() {
    let (fake, src, dst) = pair();
    let link = Link::start(src, 1, dst, "nothing:here").unwrap();
    let mut t = 0.0;
    wait_for("refused frames", || {
        t += 0.02;
        fake.run_until(t);
        std::thread::sleep(Duration::from_millis(3));
        link.status().refused > 0
    });
    assert_eq!(link.status().carried, 0);
}

#[test]
fn stopping_a_link_releases_its_tap() {
    let (_fake, src, dst) = pair();
    let mut link = Link::start(src.clone(), 1, dst, INTAKE).unwrap();
    assert_eq!(src.status().taps.len(), 1);
    link.stop();
    assert_eq!(src.status().taps.len(), 0);
}

#[test]
fn a_link_from_a_lane_number_that_does_not_exist_is_refused() {
    let (_fake, src, dst) = pair();
    assert!(Link::start(src, 99, dst, INTAKE).is_err());
}
