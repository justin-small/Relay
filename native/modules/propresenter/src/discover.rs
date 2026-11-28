//! Finding ProPresenters on the LAN: multicast DNS for `_proapiv1ws._tcp.local` (what ProPresenter 22 announces; its SRV port is the API port).
//! The question is asked from an ordinary port, so the answer comes straight back to it. Multicast does not cross routers, so a ProPresenter on
//! another subnet is typed in. Nothing is saved or contacted.

use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::time::{Duration, Instant};

use relay_core::{Net, UdpOptions};
use serde_json::{json, Value};

use crate::client::TRAFFIC;

pub const SERVICE: &str = "_proapiv1ws._tcp.local";
pub const MDNS_GROUP: Ipv4Addr = Ipv4Addr::new(224, 0, 0, 251);
pub const MDNS_PORT: u16 = 5353;

/// A DNS message that does not parse.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BadMessage;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Record {
    pub name: String,
    pub rtype: u16,
    pub rdata: Vec<u8>,
    /// The name the rdata holds when it is a PTR or SRV.
    pub target: String,
}

fn name_bytes(labels: &[&str]) -> Vec<u8> {
    let mut out = Vec::new();
    for l in labels.iter().filter(|l| !l.is_empty()) {
        out.push(u8::try_from(l.len()).unwrap_or(63));
        out.extend_from_slice(l.as_bytes());
    }
    out.push(0);
    out
}

fn read_name(data: &[u8], mut pos: usize) -> Result<(String, usize), BadMessage> {
    let (mut labels, mut end, mut jumped, mut hops) = (Vec::new(), pos, false, 0);
    loop {
        if pos >= data.len() || hops > 20 {
            return Err(BadMessage);
        }
        let n = data[pos];
        if n == 0 {
            pos += 1;
            break;
        }
        if n & 0xC0 == 0xC0 {
            let low = *data.get(pos + 1).ok_or(BadMessage)?;
            let ptr = (usize::from(n & 0x3F) << 8) | usize::from(low);
            if !jumped {
                end = pos + 2;
            }
            pos = ptr;
            jumped = true;
            hops += 1;
            continue;
        }
        let part = data.get(pos + 1..pos + 1 + usize::from(n)).ok_or(BadMessage)?;
        labels.push(String::from_utf8_lossy(part).into_owned());
        pos += 1 + usize::from(n);
    }
    Ok((labels.join("."), if jumped { end } else { pos }))
}

fn be16(data: &[u8], at: usize) -> Result<u16, BadMessage> {
    data.get(at..at + 2).map(|b| u16::from_be_bytes([b[0], b[1]])).ok_or(BadMessage)
}

/// Every resource record in a DNS message. `Err` on a message that does not parse.
pub fn parse_mdns(data: &[u8]) -> Result<Vec<Record>, BadMessage> {
    let (qd, an, ns, ar) = (be16(data, 4)?, be16(data, 6)?, be16(data, 8)?, be16(data, 10)?);
    let mut pos = 12;
    for _ in 0..qd {
        pos = read_name(data, pos)?.1 + 4;
    }
    let mut out = Vec::new();
    for _ in 0..(usize::from(an) + usize::from(ns) + usize::from(ar)) {
        let (name, next) = read_name(data, pos)?;
        pos = next;
        let rtype = be16(data, pos)?;
        let rdlen = usize::from(be16(data, pos + 8)?);
        pos += 10;
        let rdata = data.get(pos..pos + rdlen).ok_or(BadMessage)?.to_vec();
        let target = match rtype {
            12 => read_name(data, pos)?.0,
            33 if rdlen >= 7 => read_name(data, pos + 6)?.0,
            _ => String::new(),
        };
        out.push(Record { name, rtype, rdata, target });
        pos += rdlen;
    }
    Ok(out)
}

/// ProPresenters found in a set of records: name, host name, address, port.
pub fn collect(records: &[Record]) -> Vec<Value> {
    let inst: Vec<&str> = records.iter().filter(|r| r.rtype == 12 && r.name.eq_ignore_ascii_case(SERVICE)).map(|r| r.target.as_str()).collect();
    let srv = |full: &str| records.iter().find(|r| r.rtype == 33 && r.rdata.len() >= 7 && r.name.eq_ignore_ascii_case(full)).map(|r| (u16::from_be_bytes([r.rdata[4], r.rdata[5]]), r.target.clone()));
    let addr = |host: &str| records.iter().find(|r| r.rtype == 1 && r.rdata.len() == 4 && r.name.eq_ignore_ascii_case(host)).map(|r| Ipv4Addr::new(r.rdata[0], r.rdata[1], r.rdata[2], r.rdata[3]).to_string());
    let mut found = Vec::new();
    for full in inst {
        let Some((port, host)) = srv(full) else { continue };
        let host = host.trim_end_matches('.').to_owned();
        let name = full.get(..full.len().saturating_sub(SERVICE.len() + 1)).unwrap_or(full);
        found.push(json!({"name": name, "host": host, "address": addr(&host).unwrap_or_default(), "port": port}));
    }
    found
}

fn query(labels: &[&str], qtype: u16) -> Vec<u8> {
    let mut q = vec![0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0];
    q.extend(name_bytes(labels));
    q.extend(qtype.to_be_bytes());
    q.extend(1u16.to_be_bytes());
    q
}

/// Ask the LAN for ProPresenter over multicast DNS and return what answers within `seconds`.
pub async fn discover(net: &Net, seconds: f64) -> Vec<Value> {
    let sock = match net.udp(TRAFFIC, &UdpOptions::default()) {
        Ok(s) => s,
        Err(e) => {
            tracing::info!("mDNS search not possible: {e}");
            return Vec::new();
        }
    };
    let _ = sock.set_multicast_ttl_v4(255);
    let to = SocketAddr::new(IpAddr::V4(MDNS_GROUP), MDNS_PORT);
    let first = query(&SERVICE.split('.').collect::<Vec<_>>(), 12);
    let mut records: Vec<Record> = Vec::new();
    let mut asked: Vec<(u16, String)> = Vec::new();
    let deadline = Instant::now() + Duration::from_secs_f64(seconds);
    let mut sent: Option<Instant> = None;
    let mut buf = vec![0u8; 9000];
    while Instant::now() < deadline {
        if sent.is_none_or(|t| t.elapsed() > Duration::from_millis(800)) {
            let _ = sock.send_to(&first, to).await;
            sent = Some(Instant::now());
        }
        let Ok(got) = tokio::time::timeout(Duration::from_millis(250), sock.recv_from(&mut buf)).await else { continue };
        let Ok((n, _)) = got else { break };
        let Ok(more) = parse_mdns(&buf[..n]) else { continue };
        records.extend(more);
        // A responder that answered with less than all of it: ask for the rest by name.
        for r in records.clone() {
            let want = if r.rtype == 12 && r.name.eq_ignore_ascii_case(SERVICE) {
                Some(33)
            } else if r.rtype == 33 {
                Some(1)
            } else {
                None
            };
            if let Some(w) = want.filter(|_| !r.target.is_empty()) {
                if !asked.contains(&(w, r.target.clone())) {
                    asked.push((w, r.target.clone()));
                    let labels: Vec<&str> = r.target.trim_end_matches('.').split('.').collect();
                    let _ = sock.send_to(&query(&labels, w), to).await;
                }
            }
        }
    }
    let mut seen: Vec<(String, i64)> = Vec::new();
    let mut out = Vec::new();
    for f in collect(&records) {
        let key = (f["host"].as_str().unwrap_or("").to_lowercase(), f["port"].as_i64().unwrap_or(0));
        if !seen.contains(&key) {
            seen.push(key);
            out.push(f);
        }
    }
    out
}
