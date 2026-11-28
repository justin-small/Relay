//! Dante's heartbeat: what every Dante device announces about itself on multicast 224.0.0.233, port 8708 (`heartbeat.py`).
//!
//! Each device sends a packet about once a second with no request from anyone: a 0x20-byte header (protocol 0xfffe, total length, the device's 8-byte
//! identity at offset 8) followed by records. Relay reads two of them:
//!   0x8002 signal presence: a level byte for every transmit channel, then every receive channel.
//!   0x8001 clock frequency offset: the device's clock error against its leader, in parts per billion (a signed u32).
//! Layouts follow chris-ritsen/network-audio-controller (public domain) and avstreamlens (MIT). Checked on 2026-11-20 against 25 packets from 10 real senders
//! on the church's two Dante networks. A packet that does not fit those layouts is "no reading" (`None`), never a made-up one. All big-endian.
//!
//! What real devices send: each sends a heartbeat about once a second, but only software devices and some others carry 0x8002 signal presence (2 of 10
//! senders did; the four hardware boxes did not). Their levels need the metering requests (`metering.rs`). 0x8001 comes from every sender.
//!
//! Level byte, the "signal presence" scale: 0 is clipping, 1 to 252 is -(byte / 2) dBFS, 253 and up is silence or mute.

use std::collections::HashMap;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use tokio::task::JoinHandle;

pub const PROTOCOL: u16 = 0xFFFE;
pub const HEADER: usize = 0x20;
pub const GROUP: [u8; 4] = [224, 0, 0, 233];
pub const PORT: u16 = 8708;
pub const SIGNAL_PRESENCE: u16 = 0x8002;
pub const CLOCK_OFFSET: u16 = 0x8001;
/// A signal presence record before its level bytes.
pub const FIXED: usize = 0x18;
pub const SILENCE: i32 = -127;
/// How long a device's last heartbeat still counts as its level.
pub const MAX_AGE: Duration = Duration::from_secs(3);

/// A level byte as dBFS: 0 (clipping) is 0, then -(byte / 2) rounded, and silence is -127.
pub fn level(byte: u8) -> i32 {
    if byte == 0 {
        0
    } else if byte >= 253 {
        SILENCE
    } else {
        // Python's round() takes halves to the even number: -(byte / 2).
        -round_half_even(f64::from(byte) / 2.0)
    }
}

/// Python's `round()` on a half-integer: to the nearest even number.
pub fn round_half_even(x: f64) -> i32 {
    let f = x.floor();
    let d = x - f;
    let base = f as i32;
    if (d - 0.5).abs() < f64::EPSILON {
        if base % 2 == 0 { base } else { base + 1 }
    } else if d > 0.5 {
        base + 1
    } else {
        base
    }
}

fn be16(b: &[u8], at: usize) -> Option<u16> {
    b.get(at..at + 2).map(|x| u16::from_be_bytes([x[0], x[1]]))
}

/// `(record type, record bytes)` of a heartbeat packet, or `None` when it is not one.
pub fn records(data: &[u8]) -> Option<Vec<(u16, &[u8])>> {
    if data.len() < HEADER || be16(data, 0)? != PROTOCOL || usize::from(be16(data, 2)?) != data.len() {
        return None;
    }
    let mut out = Vec::new();
    let mut at = HEADER;
    while at < data.len() {
        if at + 4 > data.len() {
            return None;
        }
        let (size, kind) = (usize::from(be16(data, at)?), be16(data, at + 2)?);
        if size < 4 || size % 4 != 0 || at + size > data.len() {
            return None;
        }
        out.push((kind, &data[at..at + size]));
        at += size;
    }
    Some(out)
}

/// The signal presence of a device: levels by transmit channel (from `tx_first`) and receive channel (from `rx_first`).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Presence {
    pub tx_first: usize,
    pub tx: Vec<u8>,
    pub rx_first: usize,
    pub rx: Vec<u8>,
}

fn presence(rec: &[u8]) -> Option<Presence> {
    if rec.len() < FIXED {
        return None;
    }
    let f: Vec<usize> = (0..12).map(|i| usize::from(be16(rec, i * 2).unwrap_or(0))).collect();
    let (ext, plen, zero1, tx_n, tx_first, rx_n, rx_first, start, zero2) = (f[2], f[3], f[5], f[6], f[7], f[8], f[9], f[10], f[11]);
    if ext != 4 || plen % 4 != 0 || 8 + ext + plen != rec.len() || zero1 != 0 || zero2 != 0 {
        return None;
    }
    let (tx_end, rx_end) = (start + tx_n, start + tx_n + rx_n);
    if start < FIXED || rx_end > rec.len() || (rx_end + 3) & !3 != rec.len() {
        return None;
    }
    Some(Presence { tx_first, tx: rec[start..tx_end].to_vec(), rx_first, rx: rec[tx_end..rx_end].to_vec() })
}

/// A heartbeat Relay could read.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Beat {
    /// The sender's 8-byte identity in hex.
    pub id: String,
    pub presence: Option<Presence>,
    pub offset_ppb: Option<i32>,
}

pub fn parse(data: &[u8]) -> Option<Beat> {
    let recs = records(data);
    let ident = data.get(8..16)?;
    let recs = recs?;
    if ident.iter().all(|b| *b == 0) || ident.iter().all(|b| *b == 0xFF) {
        return None;
    }
    let mut out = Beat { id: ident.iter().map(|b| format!("{b:02x}")).collect(), presence: None, offset_ppb: None };
    for (kind, rec) in recs {
        if kind == SIGNAL_PRESENCE {
            out.presence = presence(rec);
        } else if kind == CLOCK_OFFSET && rec.len() >= 16 && be16(rec, 4).is_some_and(|e| e >= 2) {
            let at = 8 + usize::from(be16(rec, 4)?);
            if let Some(b) = rec.get(at..at + 4) {
                out.offset_ppb = Some(i32::from_be_bytes([b[0], b[1], b[2], b[3]]));
            }
        }
    }
    Some(out)
}

/// A heartbeat packet as a device sends it (the fake device in tests builds its packets with this).
pub fn packet(ident: &[u8], tx: Option<&[u8]>, rx: Option<&[u8]>, offset_ppb: Option<i32>, tx_first: u16, rx_first: u16) -> Vec<u8> {
    let mut body: Vec<u8> = Vec::new();
    if tx.is_some() || rx.is_some() {
        let (tx, rx) = (tx.unwrap_or(&[]), rx.unwrap_or(&[]));
        let raw: Vec<u8> = tx.iter().chain(rx.iter()).copied().collect();
        let size = (FIXED + raw.len() + 3) & !3;
        let len = |n: usize| u16::try_from(n).unwrap_or(u16::MAX);
        for v in [len(size), SIGNAL_PRESENCE, 4, len(size - 12), 1, 0, len(tx.len()), tx_first, len(rx.len()), rx_first, len(FIXED), 0] {
            body.extend_from_slice(&v.to_be_bytes());
        }
        body.extend(&raw);
        body.extend(std::iter::repeat_n(0u8, size - FIXED - raw.len()));
    }
    if let Some(ppb) = offset_ppb {
        for v in [16u16, CLOCK_OFFSET, 4, 4, 1, 0] {
            body.extend_from_slice(&v.to_be_bytes());
        }
        body.extend_from_slice(&ppb.to_be_bytes());
    }
    let mut out = Vec::new();
    out.extend_from_slice(&PROTOCOL.to_be_bytes());
    out.extend_from_slice(&u16::try_from(HEADER + body.len()).unwrap_or(u16::MAX).to_be_bytes());
    out.extend_from_slice(&[0; 4]);
    let mut id = ident.to_vec();
    id.resize(8, 0);
    out.extend_from_slice(&id[..8]);
    out.extend_from_slice(&[0; 16]);
    out.extend(body);
    out
}

struct Seen {
    beat: Beat,
    at: Instant,
}

/// The latest heartbeat of each sender, by its IP address, kept as packets arrive.
#[derive(Default)]
pub struct Heartbeats {
    latest: Mutex<HashMap<String, Seen>>,
    task: Mutex<Option<JoinHandle<()>>>,
    addr: Mutex<Option<std::net::SocketAddr>>,
}

impl Heartbeats {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    fn map(&self) -> std::sync::MutexGuard<'_, HashMap<String, Seen>> {
        self.latest.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Keep a received packet. `false` when it is not a heartbeat Relay can read.
    pub fn feed(&self, data: &[u8], ip: &str, now: Instant) -> bool {
        let Some(beat) = parse(data) else { return false };
        self.map().insert(ip.to_owned(), Seen { beat, at: now });
        true
    }

    /// The transmit levels (dBFS, indexed from channel 0, `None` where the device sent none) of the device that sent from one of `ips` in the last few
    /// seconds, or `None`.
    pub fn levels(&self, ips: &[String], now: Instant) -> Option<Vec<Option<i32>>> {
        let map = self.map();
        for ip in ips {
            if let Some(got) = map.get(ip).filter(|g| now.saturating_duration_since(g.at) <= MAX_AGE) {
                if let Some(p) = &got.beat.presence {
                    let mut out: Vec<Option<i32>> = vec![None; p.tx_first];
                    out.extend(p.tx.iter().map(|b| Some(level(*b))));
                    return Some(out);
                }
            }
        }
        None
    }

    /// A device sent a heartbeat from one of `ips` in the last few seconds: alive without asking it.
    pub fn seen(&self, ips: &[String], now: Instant) -> bool {
        let map = self.map();
        ips.iter().any(|ip| map.get(ip).is_some_and(|g| now.saturating_duration_since(g.at) <= MAX_AGE))
    }

    pub fn offset_ppb(&self, ips: &[String], now: Instant) -> Option<i32> {
        let map = self.map();
        ips.iter().find_map(|ip| map.get(ip).filter(|g| now.saturating_duration_since(g.at) <= MAX_AGE).and_then(|g| g.beat.offset_ppb))
    }

    /// Read heartbeats from `sock` on a task until [`Heartbeats::stop`].
    pub fn listen(self: &Arc<Self>, sock: tokio::net::UdpSocket) { // allow-socket: the type of a socket Host::net() opened
        self.stop();
        *self.addr.lock().unwrap_or_else(PoisonError::into_inner) = sock.local_addr().ok();
        let me = Arc::clone(self);
        let handle = tokio::spawn(async move {
            let mut buf = vec![0u8; 2048];
            loop {
                let Ok((n, from)) = sock.recv_from(&mut buf).await else { continue };
                me.feed(&buf[..n], &from.ip().to_string(), Instant::now());
            }
        });
        *self.task.lock().unwrap_or_else(PoisonError::into_inner) = Some(handle);
    }

    /// Where heartbeats are heard, or `None` when not listening.
    pub fn local(&self) -> Option<std::net::SocketAddr> {
        *self.addr.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Stop listening and forget what was heard. Ending the task is immediate: nothing here waits for a thread to notice.
    pub fn stop(&self) {
        if let Some(t) = self.task.lock().unwrap_or_else(PoisonError::into_inner).take() {
            t.abort();
        }
        *self.addr.lock().unwrap_or_else(PoisonError::into_inner) = None;
        self.map().clear();
    }
}
