//! Multicast DNS for the Dante services (`app/mdns.py`, the part Dante uses): ask for `_netaudio-*` announcements and fold the answers into one record per
//! service instance. Every question goes out through the `dante` traffic class's network port (`Host::net()`); nothing is saved or contacted beyond the
//! multicast group.

use std::collections::BTreeMap;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::time::Duration;

use relay_core::{Net, UdpOptions};

use crate::arc::CLASS_ID;

pub const GROUP: SocketAddr = SocketAddr::new(IpAddr::V4(Ipv4Addr::new(224, 0, 0, 251)), 5353);
pub const TYPE_A: u16 = 1;
pub const TYPE_PTR: u16 = 12;
pub const TYPE_TXT: u16 = 16;
pub const TYPE_SRV: u16 = 33;
pub const TYPE_AAAA: u16 = 28;
const MAX_NAME: usize = 253;

/// What a record carries.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Value {
    /// A PTR's target.
    Name(String),
    /// An SRV's port and target.
    Service(u16, String),
    /// An A or AAAA record's address.
    Address(String),
    Txt(BTreeMap<String, String>),
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Record {
    pub name: String,
    pub rtype: u16,
    pub value: Value,
}

/// One announced service instance, with whatever the replies told about its host.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    /// `_netaudio-arc._udp.local`
    pub service: String,
    /// `STAGE-PC._netaudio-arc._udp.local`
    pub instance: String,
    /// `stage-pc.local`
    pub host: String,
    pub port: Option<u16>,
    pub ips: Vec<String>,
    pub txt: BTreeMap<String, String>,
}

impl Found {
    pub fn new(service: &str, instance: &str) -> Self {
        Self { service: service.to_owned(), instance: instance.to_owned(), host: String::new(), port: None, ips: Vec::new(), txt: BTreeMap::new() }
    }

    /// The instance's own name without the service on the end.
    pub fn label(&self) -> &str {
        self.instance.strip_suffix(&format!(".{}", self.service)).unwrap_or(&self.instance)
    }
}

/// A DNS name at `pos` (labels, or a compression pointer back into the packet) and the position after it. `None` on anything malformed; pointer loops
/// are refused.
fn name_at(raw: &[u8], mut pos: usize) -> Option<(String, usize)> {
    let mut labels: Vec<String> = Vec::new();
    let mut end: Option<usize> = None;
    let mut jumps = 0;
    loop {
        let n = usize::from(*raw.get(pos)?);
        if n == 0 {
            pos += 1;
            break;
        }
        if n & 0xC0 == 0xC0 {
            let low = usize::from(*raw.get(pos + 1)?);
            end.get_or_insert(pos + 2);
            pos = ((n & 0x3F) << 8) | low;
            jumps += 1;
            if jumps > 20 {
                return None;
            }
            continue;
        }
        if n & 0xC0 != 0 {
            return None;
        }
        let label = raw.get(pos + 1..pos + 1 + n)?;
        labels.push(String::from_utf8_lossy(label).into_owned());
        pos += 1 + n;
        if labels.iter().map(|l| l.len() + 1).sum::<usize>() > MAX_NAME {
            return None;
        }
    }
    Some((labels.join("."), end.unwrap_or(pos)))
}

fn txt(data: &[u8]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let mut i = 0;
    while i < data.len() {
        let n = usize::from(data[i]);
        let chunk = &data[(i + 1).min(data.len())..(i + 1 + n).min(data.len())];
        i += 1 + n;
        if chunk.is_empty() {
            continue;
        }
        let text = String::from_utf8_lossy(chunk).into_owned();
        let (key, val) = text.split_once('=').unwrap_or((&text, ""));
        if !key.is_empty() && out.len() < 24 {
            out.insert(key.to_lowercase().chars().take(40).collect(), val.chars().take(120).collect());
        }
    }
    out
}

/// Every answer, authority and additional record in one mDNS packet. A packet that cannot be read gives nothing.
pub fn parse(raw: &[u8]) -> Vec<Record> {
    parse_inner(raw).unwrap_or_default()
}

fn parse_inner(raw: &[u8]) -> Option<Vec<Record>> {
    if raw.len() < 12 {
        return None;
    }
    let count = |at: usize| usize::from(u16::from_be_bytes([raw[at], raw[at + 1]]));
    let (qd, an, ns, ar) = (count(4), count(6), count(8), count(10));
    let mut pos = 12;
    for _ in 0..qd {
        pos = name_at(raw, pos)?.1 + 4;
    }
    let mut out = Vec::new();
    for _ in 0..(an + ns + ar).min(200) {
        let (name, next) = name_at(raw, pos)?;
        pos = next;
        let head = raw.get(pos..pos + 10)?;
        let rtype = u16::from_be_bytes([head[0], head[1]]);
        let rdlen = usize::from(u16::from_be_bytes([head[8], head[9]]));
        pos += 10;
        let data = raw.get(pos..pos + rdlen)?;
        let value = match rtype {
            TYPE_PTR => Some(Value::Name(name_at(raw, pos)?.0)),
            TYPE_SRV if rdlen >= 6 => Some(Value::Service(u16::from_be_bytes([data[4], data[5]]), name_at(raw, pos + 6)?.0)),
            TYPE_A if rdlen == 4 => Some(Value::Address(Ipv4Addr::new(data[0], data[1], data[2], data[3]).to_string())),
            TYPE_AAAA if rdlen == 16 => {
                let mut b = [0u8; 16];
                b.copy_from_slice(data);
                Some(Value::Address(std::net::Ipv6Addr::from(b).to_string()))
            }
            TYPE_TXT => Some(Value::Txt(txt(data))),
            _ => None,
        };
        pos += rdlen;
        if let Some(value) = value {
            let name = if rtype == TYPE_A || rtype == TYPE_AAAA { name.to_lowercase() } else { name };
            out.push(Record { name, rtype, value });
        }
    }
    Some(out)
}

/// A query packet for (name, type) pairs. The unicast-response bit is set, so answers come straight back.
pub fn build_query(questions: &[(String, u16)]) -> Vec<u8> {
    let mut out = Vec::new();
    for v in [0u16, 0, u16::try_from(questions.len()).unwrap_or(0), 0, 0, 0] {
        out.extend_from_slice(&v.to_be_bytes());
    }
    for (name, rtype) in questions {
        for label in name.trim_end_matches('.').split('.') {
            let b: Vec<u8> = label.bytes().take(63).collect();
            out.push(u8::try_from(b.len()).unwrap_or(63));
            out.extend(b);
        }
        out.push(0);
        out.extend_from_slice(&rtype.to_be_bytes());
        out.extend_from_slice(&0x8001u16.to_be_bytes());
    }
    out
}

/// Fold the records of the packets into `found` (instance to [`Found`]): PTRs add instances; SRV, TXT and address records fill them in, in whatever order
/// they arrive and across packets.
pub fn collect(packets: &[Vec<u8>], found: &mut BTreeMap<String, Found>, services: &[&str]) {
    let recs: Vec<Record> = packets.iter().flat_map(|p| parse(p)).collect();
    for r in &recs {
        if let (TYPE_PTR, Value::Name(target)) = (r.rtype, &r.value) {
            let service = r.name.to_lowercase();
            if services.contains(&service.as_str()) {
                found.entry(target.clone()).or_insert_with(|| Found::new(&service, target));
            }
        }
    }
    let mut hosts: BTreeMap<String, Vec<String>> = BTreeMap::new();
    for r in &recs {
        if let (TYPE_A | TYPE_AAAA, Value::Address(a)) = (r.rtype, &r.value) {
            let list = hosts.entry(r.name.to_lowercase()).or_default();
            if !list.contains(a) {
                list.push(a.clone());
            }
        }
    }
    for r in &recs {
        let Some(f) = found.get_mut(&r.name) else { continue };
        match (&r.value, r.rtype) {
            (Value::Service(port, target), TYPE_SRV) => {
                f.port = Some(*port);
                f.host = target.to_lowercase();
            }
            (Value::Txt(t), TYPE_TXT) => f.txt.extend(t.clone()),
            _ => {}
        }
    }
    for f in found.values_mut() {
        for ip in hosts.get(&f.host).into_iter().flatten() {
            if !f.ips.contains(ip) {
                f.ips.push(ip.clone());
            }
        }
    }
}

/// How to ask: where the questions go, and how long each round listens.
#[derive(Clone, Debug)]
pub struct Ask {
    pub group: SocketAddr,
    pub seconds: f64,
}

impl Default for Ask {
    fn default() -> Self {
        Self { group: GROUP, seconds: 2.0 }
    }
}

async fn listen(socks: &[tokio::net::UdpSocket], seconds: f64, into: &mut Vec<Vec<u8>>) { // allow-socket: the type of the sockets Host::net() opened
    let end = tokio::time::Instant::now() + Duration::from_secs_f64(seconds);
    if socks.is_empty() {
        return;
    }
    let mut bufs: Vec<Vec<u8>> = socks.iter().map(|_| vec![0u8; 9000]).collect();
    while tokio::time::Instant::now() < end {
        // One pass over the sockets, each given a short slice of the time left.
        for (s, buf) in socks.iter().zip(bufs.iter_mut()) {
            let slice = (end.saturating_duration_since(tokio::time::Instant::now())).min(Duration::from_millis(60));
            if let Ok(Ok((n, _))) = tokio::time::timeout(slice, s.recv_from(buf)).await {
                into.push(buf[..n].to_vec()); // the sender's address is deliberately ignored
            }
        }
    }
}

async fn send(socks: &[tokio::net::UdpSocket], group: SocketAddr, questions: Vec<(String, u16)>) { // allow-socket: the type of the sockets Host::net() opened
    let packets: Vec<Vec<u8>> = questions.chunks(8).map(build_query).collect();
    for s in socks {
        for p in &packets {
            let _ = s.send_to(p, group).await;
        }
    }
}

/// Ask on every given local address (none: through the class's own port) and return what answered. An address that will not send is skipped.
pub async fn scan(net: &Net, from: &[Ipv4Addr], services: &[&str], ask: &Ask) -> Vec<Found> {
    let mut socks = Vec::new();
    if from.is_empty() {
        if let Ok(s) = net.udp(CLASS_ID, &UdpOptions::default()) {
            let _ = s.set_multicast_ttl_v4(255);
            socks.push(s);
        }
    }
    for ip in from {
        if let Ok(s) = net.udp(CLASS_ID, &UdpOptions { host: Some(IpAddr::V4(*ip)), ..UdpOptions::default() }) {
            let _ = s.set_multicast_ttl_v4(255);
            socks.push(s);
        }
    }
    let mut found: BTreeMap<String, Found> = BTreeMap::new();
    let mut packets: Vec<Vec<u8>> = Vec::new();
    send(&socks, ask.group, services.iter().map(|s| ((*s).to_owned(), TYPE_PTR)).collect()).await;
    listen(&socks, ask.seconds, &mut packets).await;
    collect(&packets, &mut found, services);
    // Second round: an instance with no host or port asks for its SRV and TXT; a host with no address, its A.
    let mut questions: Vec<(String, u16)> = Vec::new();
    for f in found.values().filter(|f| f.host.is_empty() || f.port.is_none()) {
        questions.push((f.instance.clone(), TYPE_SRV));
        questions.push((f.instance.clone(), TYPE_TXT));
    }
    let more = !questions.is_empty();
    send(&socks, ask.group, questions).await;
    listen(&socks, if more { ask.seconds / 2.0 } else { 0.0 }, &mut packets).await;
    collect(&packets, &mut found, services);
    let questions: Vec<(String, u16)> = found.values().filter(|f| !f.host.is_empty() && f.ips.is_empty()).map(|f| (f.host.clone(), TYPE_A)).collect();
    let more = !questions.is_empty();
    send(&socks, ask.group, questions).await;
    listen(&socks, if more { ask.seconds / 2.0 } else { 0.0 }, &mut packets).await;
    collect(&packets, &mut found, services);
    let mut out: Vec<Found> = found.into_values().collect();
    out.sort_by_key(|f| f.instance.to_lowercase());
    out
}
