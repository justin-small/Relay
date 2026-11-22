//! `relay-rf enumerate | level <MHz> | sweep <startMHz> <stopMHz> | serve <addr>`
use relay_rf::{dsp, rtltcp, sdr, sweep, IqSource};

fn main() -> Result<(), String> {
    let a: Vec<String> = std::env::args().collect();
    let mhz = |i: usize| a.get(i).and_then(|s| s.parse::<f64>().ok()).map(|m| (m * 1e6) as u32);
    match a.get(1).map(String::as_str) {
        Some("enumerate") => {
            for d in sdr::enumerate()? {
                println!("{}: {:04x}:{:04x} {} / {} serial {}", d.index, d.vid, d.pid, d.manufacturer, d.product, d.serial);
            }
        }
        Some("level") => {
            let mut s = sdr::Sdr::open(0)?;
            let c = mhz(2).unwrap_or(540_000_000);
            s.tune(c)?;
            let mut raw = vec![0u8; 2 * 4096 * 64];
            s.read(&mut raw)?;
            s.read(&mut raw)?;
            let sp = dsp::power_spectrum(&raw, 4096);
            println!("floor {:.1} dBFS, max {:.1} dBFS", dsp::noise_floor(&sp), sp.iter().cloned().fold(f32::MIN, f32::max));
        }
        Some("sweep") => {
            let mut s = sdr::Sdr::open(0)?;
            let sw = sweep::run(&mut s, mhz(2).ok_or("start MHz")?, mhz(3).ok_or("stop MHz")?, 2048, 8)?;
            println!("{} points in {:.2}s", sw.points.len(), sw.seconds);
            for (f, v) in sw.points.iter().step_by((sw.points.len() / 40).max(1)) {
                println!("{:9.3} MHz {:6.1} {}", f / 1e6, v, "#".repeat(((v + 80.0).max(0.0) / 2.0) as usize));
            }
        }
        Some("serve") => {
            let l = std::net::TcpListener::bind(a.get(2).map(String::as_str).unwrap_or("0.0.0.0:1234")).map_err(|e| e.to_string())?;
            let mut s = sdr::Sdr::open(0)?;
            let stop = std::sync::atomic::AtomicBool::new(false);
            loop {
                rtltcp::serve_one(&l, &mut s, &stop)?;
            }
        }
        _ => println!("usage: relay-rf enumerate | level <MHz> | sweep <startMHz> <stopMHz> | serve <addr>"),
    }
    Ok(())
}
