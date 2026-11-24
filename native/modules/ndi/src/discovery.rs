//! Finding NDI sources over multicast DNS (`discovery.py`, #294).
//!
//! NDI senders announce `_ndi._tcp` with an instance name `MACHINE (SOURCE)` (the source part can be blank or end in a comma), an SRV port
//! and TXT `groups=public discovery=5960`. A BirdDog converter also announces `_birddog._tcp` whose TXT `srnname` and `srcip` say which NDI
//! source it is set to; that is attached to the source at that address as `converter`. `parse_records` and `collect` are pure; `scan` sends
//! the questions through the module's traffic class, so it follows the network port chosen for NDI.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::net::{Ipv4Addr, SocketAddr};
use std::time::{Duration, Instant};

use relay_core::net::{Net, UdpOptions};

pub const TRAFFIC: &str = "ndi";
pub const MDNS_GROUP: Ipv4Addr = Ipv4Addr::new(224, 0, 0, 251);
pub const MDNS_PORT: u16 = 5353;
pub const NDI: &str = "_ndi._tcp.local";
pub const BIRDDOG: &str = "_birddog._tcp.local";
pub const PTR: u16 = 12;
pub const TXT: u16 = 16;
pub const SRV: u16 = 33;
pub const A: u16 = 1;
pub const MAX_FOUND: usize = 300;

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Record {
    pub name: String,
    pub rtype: u16,
    pub rdata: Vec<u8>,
    /// The name a PTR or SRV points at.
    pub target: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Converter {
    pub name: String,
    pub source: String,
    pub source_ip: String,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Found {
    pub name: String,
    pub machine: String,
    pub source: String,
    pub host: String,
    pub address: String,
    pub port: u16,
    pub groups: Vec<String>,
    pub converter: Option<Converter>,
}

fn name_bytes(name: &str) -> Vec<u8> {
    let mut out = Vec::new();
    for label in name.trim_end_matches('.').split('.').filter(|l| !l.is_empty()) {
        out.push(label.len().min(63) as u8);
        out.extend_from_slice(&label.as_bytes()[..label.len().min(63)]);
    }
    out.push(0);
    out
}

/// A DNS question for `name` of type `rtype`.
pub fn question(name: &str, rtype: u16, unicast: bool) -> Vec<u8> {
    let mut out = vec![0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0];
    out.extend(name_bytes(name));
    out.extend_from_slice(&rtype.to_be_bytes());
    out.extend_from_slice(&(if unicast { 0x8001u16 } else { 1 }).to_be_bytes());
    out
}

fn read_name(data: &[u8], mut pos: usize) -> Option<(String, usize)> {
    let (mut labels, mut end, mut jumped, mut hops) = (Vec::new(), pos, false, 0);
    loop {
        if pos >= data.len() || hops > 20 {
            return None;
        }
        let n = data[pos] as usize;
        if n == 0 {
            pos += 1;
            break;
        }
        if n & 0xC0 == 0xC0 {
            if pos + 1 >= data.len() {
                return None;
            }
            let ptr = ((n & 0x3F) << 8) | data[pos + 1] as usize;
            if !jumped {
                end = pos + 2;
            }
            pos = ptr;
            jumped = true;
            hops += 1;
            continue;
        }
        let part = data.get(pos + 1..pos + 1 + n)?;
        labels.push(String::from_utf8_lossy(part).into_owned());
        pos += 1 + n;
    }
    Some((labels.join("."), if jumped { end } else { pos }))
}

/// Every resource record in a DNS message, or `None` when the message is not well formed.
pub fn parse_records(data: &[u8]) -> Option<Vec<Record>> {
    if data.len() < 12 {
        return None;
    }
    let count = |i: usize| u16::from_be_bytes([data[i], data[i + 1]]) as usize;
    let (qd, total) = (count(4), count(6) + count(8) + count(10));
    let mut pos = 12;
    for _ in 0..qd {
        pos = read_name(data, pos)?.1 + 4;
    }
    let mut out = Vec::new();
    for _ in 0..total {
        let (name, p) = read_name(data, pos)?;
        let head = data.get(p..p + 10)?;
        let rtype = u16::from_be_bytes([head[0], head[1]]);
        let rdlen = u16::from_be_bytes([head[8], head[9]]) as usize;
        let start = p + 10;
        let rdata = data.get(start..start + rdlen)?.to_vec();
        let target = match rtype {
            PTR => read_name(data, start)?.0,
            SRV if rdlen >= 7 => read_name(data, start + 6)?.0,
            _ => String::new(),
        };
        out.push(Record { name, rtype, rdata, target });
        pos = start + rdlen;
    }
    Some(out)
}

pub fn parse_txt(rdata: &[u8]) -> BTreeMap<String, String> {
    let mut out = BTreeMap::new();
    let mut pos = 0;
    while pos < rdata.len() {
        let n = rdata[pos] as usize;
        let item = String::from_utf8_lossy(rdata.get(pos + 1..(pos + 1 + n).min(rdata.len())).unwrap_or(&[])).into_owned();
        pos += 1 + n;
        let (key, value) = item.split_once('=').unwrap_or((&item, ""));
        if !key.is_empty() {
            out.entry(key.to_lowercase()).or_insert_with(|| value.trim().chars().take(200).collect());
        }
    }
    out
}

/// `MACHINE (SOURCE)` -> (machine, source), read loosely: a blank source, a trailing comma and an instance with no brackets are all fine.
pub fn split_instance(instance: &str) -> (String, String) {
    let s = instance.trim();
    if let (Some(open), true) = (s.find('('), s.ends_with(')')) {
        let machine: String = s[..open].trim().chars().take(80).collect();
        let source: String = s[open + 1..s.len() - 1].trim().trim_matches(',').trim().chars().take(80).collect();
        return (machine, source);
    }
    (s.chars().take(80).collect(), String::new())
}

pub fn parse_groups(value: &str) -> Vec<String> {
    let g: Vec<String> = value.split([',', ';']).map(str::trim).filter(|g| !g.is_empty()).take(20).map(str::to_owned).collect();
    if g.is_empty() {
        vec!["public".into()]
    } else {
        g
    }
}

/// The NDI sources in a set of records, each with its machine, source name, address, port and groups, and (when a BirdDog converter
/// announced it) what that converter is set to.
pub fn collect(records: &[Record]) -> Vec<Found> {
    let lower = |s: &str| s.to_lowercase();
    let srv: HashMap<String, (u16, String)> =
        records.iter().filter(|r| r.rtype == SRV && r.rdata.len() >= 7).map(|r| (lower(&r.name), (u16::from_be_bytes([r.rdata[4], r.rdata[5]]), r.target.clone()))).collect();
    let txt: HashMap<String, BTreeMap<String, String>> = records.iter().filter(|r| r.rtype == TXT).map(|r| (lower(&r.name), parse_txt(&r.rdata))).collect();
    let addr: HashMap<String, String> = records
        .iter()
        .filter(|r| r.rtype == A && r.rdata.len() == 4)
        .map(|r| (lower(r.name.trim_end_matches('.')), Ipv4Addr::new(r.rdata[0], r.rdata[1], r.rdata[2], r.rdata[3]).to_string()))
        .collect();
    let mut birddog: HashMap<String, Converter> = HashMap::new();
    let mut found: Vec<(String, Found)> = Vec::new();
    for r in records {
        let service = lower(&r.name);
        if r.rtype != PTR || r.target.is_empty() || (service != NDI && service != BIRDDOG) {
            continue;
        }
        let full = &r.target;
        let suffix = format!(".{service}");
        let instance = if lower(full).ends_with(&suffix) { &full[..full.len() - suffix.len()] } else { full.as_str() };
        let (port, host) = srv.get(&lower(full)).cloned().unwrap_or((0, String::new()));
        let t = txt.get(&lower(full)).cloned().unwrap_or_default();
        let address = addr.get(&lower(host.trim_end_matches('.'))).cloned().unwrap_or_default();
        if service == BIRDDOG {
            let srn = t.get("srnname").cloned().unwrap_or_default();
            let srcip = t.get("srcip").cloned().unwrap_or_default();
            if !address.is_empty() && (!srn.is_empty() || !srcip.is_empty()) {
                birddog.insert(address, Converter { name: instance.chars().take(60).collect(), source: srn.chars().take(80).collect(), source_ip: srcip.chars().take(45).collect() });
            }
            continue;
        }
        if found.iter().any(|(k, _)| *k == lower(full)) || found.len() >= MAX_FOUND {
            continue;
        }
        let (machine, source) = split_instance(instance);
        found.push((
            lower(full),
            Found {
                name: instance.chars().take(160).collect(),
                machine,
                source,
                host: host.trim_end_matches('.').to_owned(),
                address,
                port,
                groups: parse_groups(t.get("groups").map_or("", String::as_str)),
                converter: None,
            },
        ));
    }
    let mut out: Vec<Found> = found.into_iter().map(|(_, mut f)| {
        f.converter = birddog.get(&f.address).cloned();
        f
    }).collect();
    out.sort_by_key(|f| (f.machine.to_lowercase(), f.source.to_lowercase()));
    out
}

/// Only the sources in a watched group (`public` is always watched); every source when no groups are set.
pub fn visible(sources: Vec<Found>, groups: &[String]) -> Vec<Found> {
    if groups.is_empty() {
        return sources;
    }
    let mut watch: Vec<String> = groups.iter().map(|g| g.to_lowercase()).collect();
    watch.push("public".into());
    sources.into_iter().filter(|s| s.groups.iter().any(|g| watch.contains(&g.to_lowercase()))).collect()
}

/// Ask the LAN (and each extra address) for NDI sources and return what answers within `seconds`.
pub async fn scan(net: &Net, seconds: f64, extra_ips: &[String]) -> Vec<Found> {
    let sock = match net.udp(TRAFFIC, &UdpOptions::default()) {
        Ok(s) => s,
        Err(_) => {
            tracing::info!("NDI mDNS search is not possible on the network port chosen for it");
            return Vec::new();
        }
    };
    let _ = sock.set_multicast_ttl_v4(255);
    let _ = sock.join_multicast_v4(MDNS_GROUP, Ipv4Addr::UNSPECIFIED);
    let mut targets: Vec<SocketAddr> = vec![SocketAddr::from((MDNS_GROUP, MDNS_PORT))];
    targets.extend(extra_ips.iter().filter_map(|ip| ip.parse::<std::net::IpAddr>().ok()).map(|ip| SocketAddr::new(ip, MDNS_PORT)));
    let first = [question(NDI, PTR, false), question(BIRDDOG, PTR, false)];
    let mut records: Vec<Record> = Vec::new();
    let mut asked: HashSet<(u16, String)> = HashSet::new();
    let deadline = Instant::now() + Duration::from_secs_f64(seconds);
    let mut sent: Option<Instant> = None;
    let mut buf = vec![0u8; 9000];
    while Instant::now() < deadline {
        if sent.is_none_or(|s| s.elapsed() > Duration::from_millis(900)) {
            for q in &first {
                for t in &targets {
                    let _ = sock.send_to(q, t).await;
                }
            }
            sent = Some(Instant::now());
        }
        let Ok(Ok((n, _))) = tokio::time::timeout(Duration::from_millis(200), sock.recv_from(&mut buf)).await else { continue };
        let Some(mut got) = parse_records(&buf[..n]) else { continue };
        let fresh: Vec<Record> = got.clone();
        records.append(&mut got);
        if records.len() > 4000 {
            records.drain(..records.len() - 4000);
        }
        for r in fresh {
            let wants: Vec<(u16, String)> = match r.rtype {
                PTR if !r.target.is_empty() && matches!(r.name.to_lowercase().as_str(), NDI | BIRDDOG) => vec![(SRV, r.target.clone()), (TXT, r.target.clone())],
                SRV if !r.target.is_empty() => vec![(A, r.target.clone())],
                _ => vec![],
            };
            for w in wants {
                if asked.insert(w.clone()) {
                    for t in &targets {
                        let _ = sock.send_to(&question(&w.1, w.0, false), t).await;
                    }
                }
            }
        }
    }
    collect(&records)
}
