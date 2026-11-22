//! The `rtl_tcp` wire protocol (issue 372): a dongle on a Pi near the stage, read from the Relay host. Both ends are
//! here so a Pi can run `relay-rf serve` with no C tools, and the host also reads stock `rtl_tcp`.
use crate::IqSource;
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::Duration;

pub const CMD_FREQ: u8 = 1;
pub const CMD_RATE: u8 = 2;
pub const CMD_GAIN_MODE: u8 = 3;
pub const CMD_GAIN: u8 = 4;
pub const CMD_PPM: u8 = 5;

#[derive(Debug)]
pub struct Client {
    s: TcpStream,
    rate: u32,
    pub tuner: u32,
}

impl Client {
    pub fn connect(addr: &str, rate: u32) -> Result<Client, String> {
        let a = addr.parse().map_err(|e| format!("{e}"))?;
        let mut s = TcpStream::connect_timeout(&a, Duration::from_secs(3)).map_err(|e| e.to_string())?;
        s.set_read_timeout(Some(Duration::from_secs(3))).ok();
        let mut h = [0u8; 12];
        s.read_exact(&mut h).map_err(|e| format!("no rtl_tcp header: {e}"))?;
        if &h[..4] != b"RTL0" {
            return Err("not an rtl_tcp server".into());
        }
        let tuner = u32::from_be_bytes(h[4..8].try_into().unwrap());
        let mut c = Client { s, rate, tuner };
        c.cmd(CMD_RATE, rate)?;
        Ok(c)
    }
    pub fn cmd(&mut self, c: u8, p: u32) -> Result<(), String> {
        let mut m = [c, 0, 0, 0, 0];
        m[1..].copy_from_slice(&p.to_be_bytes());
        self.s.write_all(&m).map_err(|e| e.to_string())
    }
    pub fn set_ppm(&mut self, ppm: i32) -> Result<(), String> {
        self.cmd(CMD_PPM, ppm as u32)
    }
    pub fn set_gain_tenth_db(&mut self, g: i32) -> Result<(), String> {
        self.cmd(CMD_GAIN_MODE, 1)?;
        self.cmd(CMD_GAIN, g as u32)
    }
}

impl IqSource for Client {
    fn tune(&mut self, hz: u32) -> Result<(), String> {
        self.cmd(CMD_FREQ, hz)
    }
    fn read(&mut self, buf: &mut [u8]) -> Result<usize, String> {
        self.s.read_exact(buf).map_err(|e| e.to_string())?;
        Ok(buf.len())
    }
    fn sample_rate(&self) -> u32 {
        self.rate
    }
}

/// What a server needs beyond `IqSource`: the settings a client can ask for.
pub trait Tunable: IqSource {
    fn set_rate(&mut self, hz: u32) -> Result<(), String>;
    fn set_gain(&mut self, manual: bool, tenth_db: i32) -> Result<(), String>;
    fn set_ppm(&mut self, ppm: i32) -> Result<(), String>;
}

impl Tunable for crate::sdr::Sdr {
    fn set_rate(&mut self, hz: u32) -> Result<(), String> {
        crate::sdr::Sdr::set_rate(self, hz)
    }
    fn set_gain(&mut self, manual: bool, g: i32) -> Result<(), String> {
        if manual { self.set_gain_tenth_db(g) } else { self.set_auto_gain() }
    }
    fn set_ppm(&mut self, ppm: i32) -> Result<(), String> {
        crate::sdr::Sdr::set_ppm(self, ppm)
    }
}

/// Serve one client until it disconnects. `stop` ends it early.
pub fn serve_one<S: Tunable>(l: &TcpListener, src: &mut S, stop: &AtomicBool) -> Result<(), String> {
    l.set_nonblocking(true).ok();
    let (mut s, _) = loop {
        match l.accept() {
            Ok(x) => break x,
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                if stop.load(Ordering::Relaxed) {
                    return Ok(());
                }
                std::thread::sleep(Duration::from_millis(5)); // allow-sleep: poll an accept
            }
            Err(e) => return Err(e.to_string()),
        }
    };
    s.set_nonblocking(false).ok();
    let mut hdr = [0u8; 12];
    hdr[..4].copy_from_slice(b"RTL0");
    hdr[4..8].copy_from_slice(&5u32.to_be_bytes()); // R820T
    hdr[8..].copy_from_slice(&29u32.to_be_bytes());
    s.write_all(&hdr).map_err(|e| e.to_string())?;
    let (tx, rx) = mpsc::channel::<(u8, u32)>();
    let mut rd = s.try_clone().map_err(|e| e.to_string())?;
    std::thread::spawn(move || {
        let mut m = [0u8; 5];
        while rd.read_exact(&mut m).is_ok() {
            if tx.send((m[0], u32::from_be_bytes(m[1..].try_into().unwrap()))).is_err() {
                break;
            }
        }
    });
    let (mut manual, mut gain) = (false, 0i32);
    let mut buf = vec![0u8; 16384];
    while !stop.load(Ordering::Relaxed) {
        while let Ok((c, p)) = rx.try_recv() {
            match c {
                CMD_FREQ => src.tune(p)?,
                CMD_RATE => src.set_rate(p)?,
                CMD_GAIN_MODE => {
                    manual = p == 1;
                    src.set_gain(manual, gain)?
                }
                CMD_GAIN => {
                    gain = p as i32;
                    if manual {
                        src.set_gain(true, gain)?
                    }
                }
                CMD_PPM => src.set_ppm(p as i32)?,
                _ => {}
            }
        }
        let n = src.read(&mut buf)?;
        if s.write_all(&buf[..n]).is_err() {
            break;
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::dsp;
    use std::sync::atomic::{AtomicBool, Ordering};
    use std::sync::{Arc, Mutex};

    struct Synth {
        center: u32,
        rate: u32,
        ppm: Arc<Mutex<i32>>,
    }
    impl IqSource for Synth {
        fn tune(&mut self, hz: u32) -> Result<(), String> {
            self.center = hz;
            Ok(())
        }
        fn read(&mut self, buf: &mut [u8]) -> Result<usize, String> {
            let raw = dsp::synth(self.rate, &[(521_000_000.0 - self.center as f64, 0.5)], buf.len() / 2, 0.01);
            buf.copy_from_slice(&raw);
            Ok(buf.len())
        }
        fn sample_rate(&self) -> u32 {
            self.rate
        }
    }
    impl Tunable for Synth {
        fn set_rate(&mut self, hz: u32) -> Result<(), String> {
            self.rate = hz;
            Ok(())
        }
        fn set_gain(&mut self, _: bool, _: i32) -> Result<(), String> {
            Ok(())
        }
        fn set_ppm(&mut self, p: i32) -> Result<(), String> {
            *self.ppm.lock().unwrap() = p;
            Ok(())
        }
    }

    #[test]
    fn client_and_server_agree_over_loopback() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let stop = Arc::new(AtomicBool::new(false));
        let ppm = Arc::new(Mutex::new(0));
        let (st, pp) = (stop.clone(), ppm.clone());
        let h = std::thread::spawn(move || {
            let mut src = Synth { center: 0, rate: 0, ppm: pp };
            serve_one(&l, &mut src, &st)
        });
        let mut c = Client::connect(&addr, 2_400_000).unwrap();
        assert_eq!(c.tuner, 5);
        c.set_ppm(-7).unwrap();
        c.tune(520_000_000).unwrap();
        let mut raw = vec![0u8; 1 << 17];
        // blocks already in flight predate the retune; read until the carrier shows (as with real rtl_tcp)
        crate::tests_support::wait_until(|| {
            c.read(&mut raw).unwrap();
            let s = dsp::power_spectrum(&raw, 4096);
            dsp::level_at(&s, 2_400_000, 1_000_000.0, 5_000.0) > dsp::noise_floor(&s) + 30.0
        });
        crate::tests_support::wait_until(|| *ppm.lock().unwrap() == -7);
        stop.store(true, Ordering::Relaxed);
        drop(c);
        h.join().unwrap().unwrap();
    }

    #[test]
    fn rejects_a_server_that_is_not_rtl_tcp() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let addr = l.local_addr().unwrap().to_string();
        let h = std::thread::spawn(move || {
            let (mut s, _) = l.accept().unwrap();
            s.write_all(b"HTTP/1.1 200 OK").unwrap();
        });
        assert!(Client::connect(&addr, 2_400_000).unwrap_err().contains("not an rtl_tcp"));
        h.join().unwrap();
    }
}
