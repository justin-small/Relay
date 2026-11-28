//! Dante metering for hardware boxes (#585): ask a device to push its signal levels to us, and read what it sends (`metering.py`).
//!
//! A stage box sends no levels in its heartbeat (`heartbeat.rs`). Dante Controller gets them by telling each device, on the device's control-and-monitoring
//! port 8800, "send your meters to this address and port" (id 0x1200, opcode 0x3010, the device name, the controller's MAC, IP and port 8751), and
//! repeating that about every 3 seconds. The device then pushes meter packets to that port. Layouts follow chris-ritsen/network-audio-controller (public
//! domain). All big-endian. A packet that does not fit is "no reading" (`None`), never a made-up one.
//!
//! Two frame versions were seen from real gear on 2026-11-21 (a DT168 stage box sends version 3, another device version 2). Level byte, the "detailed"
//! scale: 0 is clipping, 1 to 253 is -((byte - 1) / 2) dBFS, 254 is muted, 255 is a framing marker (no reading).

use std::collections::HashMap;
use std::net::{IpAddr, SocketAddr};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use tokio::task::JoinHandle;

use crate::heartbeat::round_half_even;

pub const PROTOCOL_CMC: u16 = 0x1200;
pub const OPCODE: u16 = 0x3010;
pub const PORT: u16 = 8751;
pub const CONTROL_PORT: u16 = 8800;
pub const MARKER: &[u8; 8] = b"Audinate";
/// Between requests; the device stops pushing soon after the last one.
pub const RENEW: Duration = Duration::from_secs(3);
pub const MUTED: i32 = -127;
/// How long a device's last push still counts as its level.
pub const MAX_AGE: Duration = Duration::from_secs(3);

/// A detailed-scale level byte as dBFS (0 = clipping, 254 = muted as -127), or `None` for the framing marker.
pub fn level(byte: u8) -> Option<i32> {
    match byte {
        0 => Some(0),
        254 => Some(MUTED),
        255 => None,
        b => Some(-round_half_even(f64::from(b - 1) / 2.0)),
    }
}

fn frame(protocol: u16, message_id: u16, body: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(6 + body.len());
    out.extend_from_slice(&protocol.to_be_bytes());
    out.extend_from_slice(&u16::try_from(6 + body.len()).unwrap_or(u16::MAX).to_be_bytes());
    out.extend_from_slice(&message_id.to_be_bytes());
    out.extend_from_slice(body);
    out
}

fn words(out: &mut Vec<u8>, v: &[u16]) {
    for w in v {
        out.extend_from_slice(&w.to_be_bytes());
    }
}

/// The 8800 message that makes `device_name` push its meters to `ip:port` (all zeros there stops it).
pub fn request(device_name: &str, ip: &str, mac: &[u8], port: u16, message_id: u16) -> Vec<u8> {
    let mut name = device_name.as_bytes().to_vec();
    name.push(0);
    if name.len() % 2 == 1 {
        name.push(0);
    }
    let size = u16::try_from(name.len()).unwrap_or(u16::MAX);
    let dest: Vec<u8> = ip.parse::<std::net::Ipv4Addr>().map_or_else(|_| vec![0; 4], |a| a.octets().to_vec());
    let tail = size + 0x0C + 4;
    let mut body = Vec::new();
    words(&mut body, &[OPCODE, 0, 0]);
    let mut m = mac.iter().copied().take(6).collect::<Vec<u8>>();
    m.resize(6, 0);
    body.extend(m);
    words(&mut body, &[0, 4, size + 0x0A, 2, size + 0x0C, 0x000A]);
    body.extend(name);
    words(&mut body, &[1, 1, tail, 1, port, 1, 0]);
    body.extend(dest);
    words(&mut body, &[port]);
    body.extend_from_slice(&[0; 6]);
    words(&mut body, &[port, 0]);
    frame(PROTOCOL_CMC, message_id, &body)
}

/// The same message with the destination cleared: the device stops pushing.
pub fn stop_request(device_name: &str, mac: &[u8], port: u16) -> Vec<u8> {
    let mut pkt = request(device_name, "", mac, port, 0);
    let n = pkt.len();
    pkt[n - 16..].fill(0);
    pkt
}

/// A meter packet as a device pushes it (the fake device in tests builds its packets with this). Version 2 has one count byte each for transmit and
/// receive and a trailing zero; version 3 (the DT168 stage boxes) a pad byte and a u16 count each, and no trailing zero.
pub fn meter_frame(ident: &[u8], sequence: u16, tx: &[u8], rx: &[u8], version: u8) -> Vec<u8> {
    let mut id = ident.to_vec();
    id.resize(8, 0);
    let mut body = vec![0, 0];
    body.extend_from_slice(&id[..8]);
    body.extend_from_slice(MARKER);
    if version == 3 {
        body.extend_from_slice(&[3, 0]);
        words(&mut body, &[u16::try_from(tx.len()).unwrap_or(0), u16::try_from(rx.len()).unwrap_or(0)]);
        body.extend_from_slice(tx);
        body.extend_from_slice(rx);
    } else {
        body.extend_from_slice(&[2, u8::try_from(tx.len()).unwrap_or(0), u8::try_from(rx.len()).unwrap_or(0)]);
        body.extend_from_slice(tx);
        body.extend_from_slice(rx);
        body.push(0);
    }
    frame(0xFFFF, sequence, &body)
}

/// A pushed meter packet.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Meter {
    pub id: String,
    pub sequence: u16,
    pub tx: Vec<Option<i32>>,
    pub rx: Vec<Option<i32>>,
}

/// `None` when it is not a meter packet (a count that does not fit the packet's length is not one).
pub fn parse(data: &[u8]) -> Option<Meter> {
    if data.len() < 28 || data[..2] != [0xFF, 0xFF] || usize::from(u16::from_be_bytes([data[2], data[3]])) != data.len() || &data[16..24] != MARKER {
        return None;
    }
    let (tx_n, rx_n, at, tail) = match data[24] {
        2 => (usize::from(data[25]), usize::from(data[26]), 27, 1),
        3 if data.len() >= 30 => (usize::from(u16::from_be_bytes([data[26], data[27]])), usize::from(u16::from_be_bytes([data[28], data[29]])), 30, 0),
        _ => return None,
    };
    if at + tx_n + rx_n + tail != data.len() {
        return None;
    }
    let levels = &data[at..at + tx_n + rx_n];
    Some(Meter {
        id: data[8..16].iter().map(|b| format!("{b:02x}")).collect(),
        sequence: u16::from_be_bytes([data[4], data[5]]),
        tx: levels[..tx_n].iter().map(|b| level(*b)).collect(),
        rx: levels[tx_n..].iter().map(|b| level(*b)).collect(),
    })
}

struct Seen {
    meter: Meter,
    at: Instant,
}

#[derive(Default)]
struct Inner {
    latest: HashMap<String, Seen>,
    asked: HashMap<String, Instant>,
    devices: HashMap<String, (String, Vec<String>)>,
    mac: [u8; 6],
    sequence: u16,
}

/// Asks devices to push their meters here, and keeps the latest push of each sender by its IP address.
#[derive(Default)]
pub struct Meters {
    inner: Mutex<Inner>,
    sock: Mutex<Option<Arc<tokio::net::UdpSocket>>>, // allow-socket: the type of a socket Host::net() opened
    task: Mutex<Option<JoinHandle<()>>>,
}

impl Meters {
    pub fn new() -> Arc<Self> {
        Arc::new(Self::default())
    }

    fn inner(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Keep a received packet. `false` when it is not a meter packet Relay can read.
    pub fn feed(&self, data: &[u8], ip: &str, now: Instant) -> bool {
        let Some(meter) = parse(data) else { return false };
        self.inner().latest.insert(ip.to_owned(), Seen { meter, at: now });
        true
    }

    /// The transmit levels (dBFS, `None` for no reading) the device at one of `ips` pushed in the last few seconds.
    pub fn levels(&self, ips: &[String], now: Instant) -> Option<Vec<Option<i32>>> {
        let inner = self.inner();
        ips.iter().find_map(|ip| inner.latest.get(ip).filter(|g| now.saturating_duration_since(g.at) <= MAX_AGE).map(|g| g.meter.tx.clone()))
    }

    /// The port the devices are told to push to (the socket's own), or `None` before [`Meters::listen`].
    pub fn local(&self) -> Option<SocketAddr> {
        self.sock.lock().unwrap_or_else(PoisonError::into_inner).as_ref().and_then(|s| s.local_addr().ok())
    }

    /// Send the request to a device unless one went out in the last [`RENEW`]. `true` when sent. `control_port` is the device's 8800.
    pub async fn ask(&self, name: &str, ips: &[String], now: Instant, control_port: u16) -> bool {
        let Some(sock) = self.sock.lock().unwrap_or_else(PoisonError::into_inner).clone() else { return false };
        let Some(first) = ips.first() else { return false };
        let (mac, seq) = {
            let mut inner = self.inner();
            if inner.asked.get(first).is_some_and(|t| now.saturating_duration_since(*t) < RENEW) {
                return false;
            }
            inner.asked.insert(first.clone(), now);
            inner.devices.insert(first.clone(), (name.to_owned(), ips.to_vec()));
            inner.sequence = inner.sequence.wrapping_add(1);
            (inner.mac, inner.sequence)
        };
        let Ok(mine) = sock.local_addr() else { return false };
        let pkt = request(name, &mine.ip().to_string(), &mac, mine.port(), seq);
        let mut sent = false;
        for ip in ips {
            if let Ok(addr) = ip.parse::<IpAddr>() {
                sent |= sock.send_to(&pkt, SocketAddr::new(addr, control_port)).await.is_ok();
            }
        }
        sent
    }

    /// Tell every device that was asked to stop pushing.
    pub async fn release(&self, control_port: u16) {
        let Some(sock) = self.sock.lock().unwrap_or_else(PoisonError::into_inner).clone() else { return };
        let (devices, mac): (Vec<(String, Vec<String>)>, [u8; 6]) = {
            let inner = self.inner();
            (inner.devices.values().cloned().collect(), inner.mac)
        };
        let Ok(mine) = sock.local_addr() else { return };
        for (name, ips) in devices {
            let pkt = stop_request(&name, &mac, mine.port());
            for ip in ips {
                if let Ok(addr) = ip.parse::<IpAddr>() {
                    let _ = sock.send_to(&pkt, SocketAddr::new(addr, control_port)).await;
                }
            }
        }
    }

    /// Read pushed meters from `sock` (bound to the address the devices should push to) on a task until [`Meters::stop`].
    pub fn listen(self: &Arc<Self>, sock: tokio::net::UdpSocket, mac: [u8; 6]) { // allow-socket: the type of a socket Host::net() opened
        self.stop();
        let sock = Arc::new(sock);
        *self.sock.lock().unwrap_or_else(PoisonError::into_inner) = Some(Arc::clone(&sock));
        self.inner().mac = mac;
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

    /// Stop listening, drop the socket and forget what was heard.
    pub fn stop(&self) {
        if let Some(t) = self.task.lock().unwrap_or_else(PoisonError::into_inner).take() {
            t.abort();
        }
        *self.sock.lock().unwrap_or_else(PoisonError::into_inner) = None;
        let mut inner = self.inner();
        inner.latest.clear();
        inner.asked.clear();
        inner.devices.clear();
    }
}
