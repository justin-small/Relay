//! Tests against a real RTL-SDR. They skip (and say so) when no dongle is plugged in, so CI stays green.
//! One dongle means one test at a time: a single #[test] runs the steps in order.
use relay_rf::{dsp, imd, rtltcp, sdr, sweep, IqSource};
use std::sync::atomic::{AtomicBool, Ordering};

const BAND: (u32, u32) = (470_000_000, 608_000_000);

fn grab<S: IqSource>(s: &mut S, hz: u32) -> Vec<f32> {
    s.tune(hz).unwrap();
    let mut raw = vec![0u8; 2 * 4096 * 128];
    s.read(&mut raw).unwrap(); // discard: still the old frequency
    s.read(&mut raw).unwrap();
    dsp::power_spectrum(&raw, 4096)
}

#[test]
fn real_dongle() {
    let found = sdr::enumerate().unwrap();
    if found.is_empty() {
        eprintln!("SKIP: no RTL-SDR plugged in");
        return;
    }
    let d = &found[0];
    println!("device: {:04x}:{:04x} {} {}", d.vid, d.pid, d.manufacturer, d.product);
    assert_eq!(d.vid, 0x0bda);

    let mut s = sdr::Sdr::open(0).unwrap();

    // #373 gain steps exist and a manual gain can be set and put back
    let gains = s.gains().unwrap();
    println!("gains (tenth dB): {gains:?}");
    assert!(gains.len() > 10);
    s.set_gain_tenth_db(*gains.last().unwrap()).unwrap();
    s.set_auto_gain().unwrap();

    // #362 throughput: a second of samples arrives at the sample rate with no gap
    let mut raw = vec![0u8; 2 * 2_400_000];
    let t = std::time::Instant::now();
    let mut got = 0;
    while got < raw.len() {
        got += s.read(&mut raw[got..]).unwrap();
    }
    let secs = t.elapsed().as_secs_f64();
    println!("1 s of IQ took {secs:.3} s");
    assert!(secs > 0.9 && secs < 1.3, "rate off: {secs}");

    // #362 a quiet UHF channel reads a stable floor; max gain reads higher than min gain (gain really applies)
    s.set_gain_tenth_db(gains[0]).unwrap();
    let lo = dsp::noise_floor(&grab(&mut s, 540_000_000));
    s.set_gain_tenth_db(*gains.last().unwrap()).unwrap();
    let hi = dsp::noise_floor(&grab(&mut s, 540_000_000));
    println!("floor at min gain {lo:.1}, max gain {hi:.1} dBFS");
    assert!(hi > lo + 5.0);
    s.set_auto_gain().unwrap();

    // #362 repeat readings of the same channel agree (stable enough to alert on)
    let r: Vec<f32> = (0..5).map(|_| dsp::noise_floor(&grab(&mut s, 540_000_000))).collect();
    let (mn, mx) = r.iter().fold((f32::MAX, f32::MIN), |(a, b), v| (a.min(*v), b.max(*v)));
    println!("floor spread over 5 reads: {:.2} dB", mx - mn);
    assert!(mx - mn < 3.0);

    // #373 ppm correction accepted
    s.set_ppm(5).unwrap();
    s.set_ppm(0).unwrap();

    // #366 sweep the UHF mic band
    let sw = sweep::run(&mut s, BAND.0, BAND.1, 2048, 8).unwrap();
    println!("sweep {} points in {:.2} s", sw.points.len(), sw.seconds);
    assert!(sw.points.len() > 5000);
    assert!(sw.points.windows(2).all(|w| w[1].0 > w[0].0));
    assert!(sw.points.first().unwrap().0 <= 471_000_000.0 && sw.points.last().unwrap().0 >= 607_000_000.0);
    assert!(sw.seconds < 20.0);
    let floor = {
        let mut v: Vec<f32> = sw.points.iter().map(|p| p.1).collect();
        v.sort_by(|a, b| a.total_cmp(b));
        v[v.len() / 2]
    };
    let peak = sw.points.iter().max_by(|a, b| a.1.total_cmp(&b.1)).unwrap();
    println!("band floor {floor:.1} dBFS, strongest {:.1} dBFS at {:.3} MHz", peak.1, peak.0 / 1e6);

    // #368 clean channels from this very sweep
    let occupied = |f: f64| {
        sw.points.iter().filter(|p| (p.0 - f).abs() < 100e3).any(|p| p.1 > floor + 10.0)
    };
    let ch = imd::pick(BAND.0 as f64, BAND.1 as f64, 25e3, 8, 400e3, 100e3, occupied);
    println!("clean channels: {:?}", ch.iter().map(|f| f / 1e6).collect::<Vec<_>>());
    assert!(ch.len() >= 4);

    // #369 baseline: a second sweep matches the first apart from interferers that come and go
    let sw2 = sweep::run(&mut s, BAND.0, BAND.1, 2048, 8).unwrap();
    let diffs: Vec<f32> = sw.points.iter().zip(&sw2.points).map(|(a, b)| (a.1 - b.1).abs()).collect();
    let mean = diffs.iter().sum::<f32>() / diffs.len() as f32;
    println!("sweep to sweep mean difference {mean:.2} dB");
    assert!(mean < 3.0);

    // #372 serve the real dongle over rtl_tcp and sweep it through the client
    let l = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let addr = l.local_addr().unwrap().to_string();
    let stop = std::sync::Arc::new(AtomicBool::new(false));
    let st = stop.clone();
    let h = std::thread::spawn(move || {
        rtltcp::serve_one(&l, &mut s, &st).unwrap();
        s
    });
    let mut c = rtltcp::Client::connect(&addr, 2_400_000).unwrap();
    let remote = sweep::run(&mut c, BAND.0, 500_000_000, 2048, 8).unwrap();
    let local_part: Vec<_> = sw.points.iter().filter(|p| p.0 < 500_000_000.0).collect();
    let n = remote.points.len().min(local_part.len());
    let rmean = remote.points[..n].iter().zip(&local_part[..n]).map(|(a, b)| (a.1 - b.1).abs()).sum::<f32>() / n as f32;
    println!("remote sweep ({} pts) vs local: mean difference {rmean:.2} dB, took {:.2} s", n, remote.seconds);
    assert!(rmean < 4.0);
    stop.store(true, Ordering::Relaxed);
    drop(c);
    let s = h.join().unwrap();

    // #361 close and reopen (what a hot-plug recovery does)
    drop(s);
    let mut again = sdr::Sdr::open(0).expect("reopen after close");
    let f = dsp::noise_floor(&grab(&mut again, 540_000_000));
    println!("reopened, floor {f:.1} dBFS");
}
