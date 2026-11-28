//! The Dante control requests monitoring sends (#258): clock status, subscriptions, and the writes routing makes (`arc.py`).
//!
//! The subscription list (0x3000), set subscription (0x3010) and clear subscription (0x3014) follow the layouts two independent open projects use
//! against real devices (chris-ritsen/network-audio-controller, public domain, and dewiweb/aes67_visualizer, MIT). On 2026-11-20 the read side was
//! checked on a real Allen & Heath DT168 stage box (ARC version 2.7.41): channel count 0x1000, name 0x1002, info 0x1003, transmit lists 0x2000/0x2010 and
//! the receive list 0x3000 all answered. The write packets match Dante Controller's byte for byte (`tests/dante.rs`).
//!
//! Devices on ARC 2.8 or newer take the page format instead: opcode 0x3410 under protocol 0x2809, proven on 2026-11-21 against a real ARC 2.8.12 device.
//! UNVERIFIED: the clock (0x1200) layout, which neither open project uses. Every parser refuses what it cannot read (returns `None`) rather than guessing,
//! so a device that answers differently shows as "no reading" instead of a wrong one. Nothing else in the module knows a byte offset.
//!
//! Requests are an 8-byte header (protocol 0x27ff, length, sequence, command) then a payload that starts with a u16 zero (the slot a device fills with its
//! result in an answer). Answers carry the result there: 1 is success, 0x8112 is success with more pages. All big-endian.

use std::net::{IpAddr, SocketAddr};
use std::time::Duration;

use relay_core::{Net, RouteError, UdpOptions};
use serde_json::{json, Map, Value};

pub const CLASS_ID: &str = "dante";
pub const HEADER: usize = 10;
pub const PROTOCOL: u16 = 0x27FF;
/// What Dante Controller uses for writes to the older (2.7.x) devices.
pub const PROTOCOL_WRITE: u16 = 0x2729;
pub const REPLY_PROTOCOLS: [u16; 3] = [0x27FF, 0x2729, 0x2809];
pub const CMD_COUNTS: u16 = 0x1000;
pub const CMD_NAME: u16 = 0x1002;
pub const CMD_INFO: u16 = 0x1003;
pub const CMD_RATES: u16 = 0x1100;
pub const CMD_CLOCK: u16 = 0x1200;
pub const CMD_SUBSCRIPTIONS: u16 = 0x3000;
pub const CMD_SET_SUBSCRIPTION: u16 = 0x3010;
/// ARC 2.8+.
pub const CMD_SET_SUBSCRIPTION_PAGE: u16 = 0x3410;
pub const PROTOCOL_MODERN: u16 = 0x2809;
pub const MEDIA_AUDIO: u8 = 3;
pub const ARC_MODERN: (u32, u32) = (2, 8);
pub const MORE_PAGES: u16 = 0x8112;
/// Receive channels a device answers per page.
pub const PAGE: usize = 16;
pub const RX_RECORD: usize = 20;
/// A write's strings start here.
pub const STRINGS_AT: usize = 172;
pub const MAX_CHANNELS: usize = 512;
pub const SAMPLE_RATES: [u32; 6] = [44100, 48000, 88200, 96000, 176_400, 192_000];
/// In the 0x1100 table: points at the u32 sample rate.
pub const RATE_TAG: u16 = 0x8020;
pub const WAIT: Duration = Duration::from_millis(600);
pub const WRITE_WAIT: Duration = Duration::from_millis(1500);

fn be16(b: &[u8], at: usize) -> Option<u16> {
    b.get(at..at + 2).map(|x| u16::from_be_bytes([x[0], x[1]]))
}

fn put16(out: &mut Vec<u8>, v: u16) {
    out.extend_from_slice(&v.to_be_bytes());
}

fn length(n: usize) -> u16 {
    u16::try_from(n).unwrap_or(u16::MAX)
}

pub fn request(command: u16, sequence: u16, payload: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(HEADER + payload.len());
    for v in [PROTOCOL, length(HEADER + payload.len()), sequence, command, 0] {
        put16(&mut out, v);
    }
    out.extend_from_slice(payload);
    out
}

/// What a device answers (the fake device in tests builds its answers with this).
pub fn reply(command: u16, sequence: u16, payload: &[u8], result: u16, protocol: u16) -> Vec<u8> {
    let mut out = Vec::with_capacity(HEADER + payload.len());
    for v in [protocol, length(HEADER + payload.len()), sequence, command, result] {
        put16(&mut out, v);
    }
    out.extend_from_slice(payload);
    out
}

/// The payload of an answer to `command`/`sequence`, or `None` when it is not that answer.
pub fn body(data: &[u8], command: u16, sequence: u16) -> Option<&[u8]> {
    if data.len() < HEADER {
        return None;
    }
    let (proto, len, seq, cmd, result) = (be16(data, 0)?, be16(data, 2)?, be16(data, 4)?, be16(data, 6)?, be16(data, 8)?);
    if proto != PROTOCOL || cmd != command || seq != sequence || result != 1 || usize::from(len) != data.len() {
        return None;
    }
    Some(&data[HEADER..])
}

/// A device's clock: flags (bit 0 leader, bit 1 locked), follower errors, latency, interface errors.
pub fn parse_clock(payload: Option<&[u8]>) -> Option<Value> {
    let p = payload.filter(|p| p.len() == 10)?;
    let flags = p[0];
    let follower = be16(p, 2)?;
    let latency = u32::from_be_bytes([p[4], p[5], p[6], p[7]]);
    let iface = be16(p, 8)?;
    Some(json!({"leader": flags & 1 != 0, "locked": flags & 2 != 0, "follower_errors": follower, "latency_us": latency, "interface_errors": iface}))
}

/// Transmit and receive channel counts from 0x1000 (checked on a DT168: 16 and 8).
pub fn parse_counts(payload: Option<&[u8]>) -> Option<Value> {
    let p = payload.filter(|p| p.len() >= 6)?;
    let (tx, rx) = (usize::from(be16(p, 2)?), usize::from(be16(p, 4)?));
    (tx <= MAX_CHANNELS && rx <= MAX_CHANNELS).then(|| json!({"tx": tx, "rx": rx}))
}

/// The device's Dante name from 0x1002: a NUL-terminated string.
pub fn parse_name(payload: Option<&[u8]>) -> Option<String> {
    let p = payload.filter(|p| !p.is_empty())?;
    let end = p.iter().position(|b| *b == 0).unwrap_or(p.len());
    let name = String::from_utf8_lossy(&p[..end]).into_owned();
    (!name.is_empty()).then_some(name)
}

/// The string a pointer (an offset from the start of the whole packet) points at, or `""`.
pub fn text_at(payload: &[u8], pointer: u16) -> String {
    let pointer = usize::from(pointer);
    if pointer < HEADER || pointer - HEADER >= payload.len() {
        return String::new();
    }
    let at = pointer - HEADER;
    let end = payload[at..].iter().position(|b| *b == 0).map_or(payload.len(), |e| at + e);
    String::from_utf8_lossy(&payload[at..end]).into_owned()
}

/// Board, Dante name and hostname from 0x1003: string pointers at payload bytes 6, 12 and 14 (packet offsets, as in the receive list).
pub fn parse_info(payload: Option<&[u8]>) -> Option<Value> {
    let p = payload.filter(|p| p.len() >= 16)?;
    let (board, name, host) = (text_at(p, be16(p, 6)?), text_at(p, be16(p, 12)?), text_at(p, be16(p, 14)?));
    if board.is_empty() && name.is_empty() && host.is_empty() {
        return None;
    }
    Some(json!({"board": board, "name": name, "hostname": host}))
}

/// The sample rate from 0x1100: a table of (tag u16, pointer u16) pairs from payload byte 2, where tag 0x8020 points at a u32. Only the rates Dante runs
/// at are accepted, so a layout that differs shows "no reading".
pub fn parse_sample_rate(payload: Option<&[u8]>) -> Option<u32> {
    let p = payload?;
    let mut at = 2;
    while at + 4 <= p.len() {
        if be16(p, at)? == RATE_TAG {
            let pointer = usize::from(be16(p, at + 2)?);
            let where_ = pointer.checked_sub(HEADER)?;
            let raw = p.get(where_..where_ + 4)?;
            let rate = u32::from_be_bytes([raw[0], raw[1], raw[2], raw[3]]);
            return SAMPLE_RATES.contains(&rate).then_some(rate);
        }
        at += 4;
    }
    None
}

/// The payload that asks for the receive channels from `first` (0-based) on.
pub fn page_request(first: usize) -> Vec<u8> {
    let mut out = Vec::new();
    for v in [1, length(first + 1), 0] {
        put16(&mut out, v);
    }
    out
}

/// What a device answers to a page request: `(payload, result)`. `channels` is `(channel, source, status low byte)` with `<transmit channel>@<device>`
/// as the source (`""` when not subscribed). Used by the fake device in tests.
pub fn rx_page(channels: &[(u16, String, u16)], more: bool) -> (Vec<u8>, u16) {
    let base = HEADER + 2 + PAGE * RX_RECORD;
    let mut heap: Vec<u8> = Vec::new();
    let mut point = |text: &str| -> u16 {
        if text.is_empty() {
            return 0;
        }
        let at = base + heap.len();
        heap.extend_from_slice(text.as_bytes());
        heap.push(0);
        length(at)
    };
    let shown = &channels[..channels.len().min(PAGE)];
    let mut recs: Vec<u8> = Vec::new();
    for (ch, source, status) in shown {
        let (chan, dev) = source.rsplit_once('@').unwrap_or((source.as_str(), ""));
        let chan_at = point(chan);
        let dev_at = point(dev);
        for v in [*ch, 0, 0, chan_at, dev_at, 0, 0, *status] {
            put16(&mut recs, v);
        }
        recs.extend_from_slice(&[0; 4]);
    }
    recs.extend(std::iter::repeat_n(0u8, RX_RECORD * (PAGE - shown.len())));
    let mut out = vec![u8::try_from(PAGE).unwrap_or(16), u8::try_from(shown.len()).unwrap_or(0)];
    out.extend(recs);
    out.extend(heap);
    (out, if more { MORE_PAGES } else { 1 })
}

/// One receive channel as read from a page.
pub fn parse_rx_page(payload: Option<&[u8]>) -> Option<Vec<Value>> {
    let p = payload.filter(|p| p.len() >= 2)?;
    let count = usize::from(p[1]);
    if count > PAGE || p.len() < 2 + count * RX_RECORD {
        return None;
    }
    let mut out = Vec::with_capacity(count);
    for i in 0..count {
        let at = 2 + i * RX_RECORD;
        let (ch, tx_chan, tx_dev, status) = (be16(p, at)?, be16(p, at + 6)?, be16(p, at + 8)?, be16(p, at + 14)?);
        let (chan, dev) = (text_at(p, tx_chan), text_at(p, tx_dev));
        let low = status & 0xFF;
        out.push(json!({
            "channel": ch,
            "source": if chan.is_empty() { String::new() } else { format!("{chan}@{dev}") },
            "status": if low == 0 { "none" } else if low == 9 || low == 10 { "ok" } else { "unresolved" },
        }));
    }
    Some(out)
}

/// Split a write's source: `<transmit channel>@<device>`, where no `@` means a channel of the device itself (`.`). `None` when either half is empty.
fn halves(source: &str) -> Option<(Vec<u8>, Vec<u8>)> {
    let (chan, dev) = source.rsplit_once('@').unwrap_or((source, "."));
    if chan.is_empty() || dev.is_empty() {
        return None;
    }
    let mut c = chan.as_bytes().to_vec();
    c.push(0);
    let mut d = dev.as_bytes().to_vec();
    d.push(0);
    Some((c, d))
}

/// A request to subscribe receive `channel` (1-255) to `source` (`<transmit channel>@<device>`; no `@` means a channel of the device itself), byte for
/// byte what Dante Controller sends. An empty `source` clears the subscription. `None` when it cannot be written.
pub fn subscribe_request(channel: u32, source: &str, sequence: u16) -> Option<Vec<u8>> {
    let channel = u8::try_from(channel).ok().filter(|c| *c >= 1)?;
    let (chan_b, dev_b) = if source.is_empty() { (Vec::new(), Vec::new()) } else { halves(source)? };
    let (chan_at, dev_at) = if source.is_empty() { (0, 0) } else { (STRINGS_AT, STRINGS_AT + chan_b.len()) };
    let mut out = Vec::with_capacity(STRINGS_AT + chan_b.len() + dev_b.len());
    for v in [PROTOCOL_WRITE, length(STRINGS_AT + chan_b.len() + dev_b.len()), sequence, CMD_SET_SUBSCRIPTION, 0] {
        put16(&mut out, v);
    }
    out.extend_from_slice(&[0x08, 1, 0, channel]);
    put16(&mut out, length(chan_at));
    put16(&mut out, length(dev_at));
    out.resize(STRINGS_AT, 0);
    out.extend(chan_b);
    out.extend(dev_b);
    Some(out)
}

/// True when the device announced ARC 2.8 or newer (`"2.8.15"`), so it takes the 0x3410 page format. An unknown or unreadable version is not modern.
pub fn is_modern(arc_version: &str) -> bool {
    let mut parts = arc_version.split('.').take(2).map(|p| if !p.is_empty() && p.bytes().all(|b| b.is_ascii_digit()) { p.parse::<u32>().ok() } else { None });
    match (parts.next().flatten(), parts.next().flatten()) {
        (Some(a), Some(b)) => (a, b) >= ARC_MODERN,
        _ => false,
    }
}

/// The 0x3410 page a 2.8+ device takes to subscribe receive `channel` (1-255) to `source` (as [`subscribe_request`]), or to clear it when `source` is
/// empty. One record per page. `None` when it cannot be written.
pub fn subscribe_page_request(channel: u32, source: &str, sequence: u16) -> Option<Vec<u8>> {
    let channel = u8::try_from(channel).ok().filter(|c| *c >= 1)?;
    let (chan_b, dev_b) = if source.is_empty() { (Vec::new(), Vec::new()) } else { halves(source)? };
    let at = HEADER + 6 + 2 + 2 + 8; // where the strings start: after the one record
    let (chan_at, dev_at) = if source.is_empty() { (0, 0) } else { (at, at + chan_b.len()) };
    let mut out = Vec::new();
    for v in [PROTOCOL_MODERN, length(at + chan_b.len() + dev_b.len()), sequence, CMD_SET_SUBSCRIPTION_PAGE, 0] {
        put16(&mut out, v);
    }
    out.extend_from_slice(&[0; 6]);
    out.extend_from_slice(&[0x08, 0, 1, 1, 0, channel, 0, MEDIA_AUDIO]);
    put16(&mut out, length(chan_at));
    put16(&mut out, length(dev_at));
    out.extend(chan_b);
    out.extend(dev_b);
    Some(out)
}

/// A device could not be reached at all.
fn unreachable(_: std::io::Error) -> RouteError {
    RouteError::new("The Dante network could not be reached.")
}

/// Send `data` and return `(result, payload)` of the device's answer to `command`/`sequence`, or `None` when none comes. Errors only when the Dante
/// network port is not there.
pub async fn exchange(net: &Net, ip: &str, port: u16, data: &[u8], command: u16, sequence: u16, wait: Duration) -> Result<Option<(u16, Vec<u8>)>, RouteError> {
    let Ok(addr) = ip.parse::<IpAddr>() else { return Ok(None) };
    let sock = net.udp(CLASS_ID, &UdpOptions::default())?;
    sock.send_to(data, SocketAddr::new(addr, port)).await.map_err(unreachable)?;
    let end = tokio::time::Instant::now() + wait;
    let mut buf = vec![0u8; 4096];
    loop {
        let Ok(got) = tokio::time::timeout_at(end, sock.recv_from(&mut buf)).await else { return Ok(None) };
        let Ok((n, from)) = got else { continue };
        let got = &buf[..n];
        if from.ip() != addr || n < HEADER {
            continue;
        }
        let (proto, len, seq, cmd, result) = (be16(got, 0).unwrap_or(0), be16(got, 2).unwrap_or(0), be16(got, 4).unwrap_or(0), be16(got, 6).unwrap_or(0), be16(got, 8).unwrap_or(0));
        if REPLY_PROTOCOLS.contains(&proto) && cmd == command && seq == sequence && usize::from(len) == n {
            return Ok(Some((result, got[HEADER..].to_vec())));
        }
    }
}

/// Send one request and return the payload of its answer, or `None` when none comes (or it is a refusal).
pub async fn ask(net: &Net, ip: &str, port: u16, command: u16, sequence: u16, wait: Duration) -> Result<Option<Vec<u8>>, RouteError> {
    Ok(exchange(net, ip, port, &request(command, sequence, &[]), command, sequence, wait).await?.filter(|g| g.0 == 1).map(|g| g.1))
}

/// What a device says about itself: counts, name, board, hostname and sample rate. A request that gets no readable answer leaves its fields out.
pub async fn read_info(net: &Net, ip: &str, port: u16, sequence: u16, wait: Duration) -> Result<Map<String, Value>, RouteError> {
    let mut out = Map::new();
    let got = ask(net, ip, port, CMD_COUNTS, sequence, wait).await?;
    if let Some(Value::Object(m)) = parse_counts(got.as_deref()) {
        out.extend(m);
    }
    let got = ask(net, ip, port, CMD_INFO, sequence.wrapping_add(1), wait).await?;
    if let Some(Value::Object(m)) = parse_info(got.as_deref()) {
        out.extend(m);
    }
    let got = ask(net, ip, port, CMD_RATES, sequence.wrapping_add(2), wait).await?;
    if let Some(rate) = parse_sample_rate(got.as_deref()) {
        out.insert("sample_rate".to_owned(), json!(rate));
    }
    Ok(out)
}

/// Every receive channel of a device, a page at a time, or `None` when it does not answer or answers unreadably.
pub async fn read_subscriptions(net: &Net, ip: &str, port: u16, sequence: u16, wait: Duration) -> Result<Option<Vec<Value>>, RouteError> {
    let mut out: Vec<Value> = Vec::new();
    for page in 0..=(MAX_CHANNELS / PAGE) {
        let seq = sequence.wrapping_add(u16::try_from(page).unwrap_or(0));
        let data = request(CMD_SUBSCRIPTIONS, seq, &page_request(out.len()));
        let Some((result, payload)) = exchange(net, ip, port, &data, CMD_SUBSCRIPTIONS, seq, wait).await? else { return Ok(None) };
        if result != 1 && result != MORE_PAGES {
            return Ok(None);
        }
        let Some(rows) = parse_rx_page(Some(&payload)) else { return Ok(None) };
        let empty = rows.is_empty();
        out.extend(rows);
        if result != MORE_PAGES || empty {
            return Ok(Some(out));
        }
    }
    Ok(None)
}

/// How a write went.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Wrote {
    Ok,
    Refused(String),
    Silent(String),
}

/// Write one subscription (an empty `source` clears it). `modern` is a device on ARC 2.8 or newer, which takes the 0x3410 page. The caller reads the
/// device back to confirm.
pub async fn set_subscription(net: &Net, (ip, port): (&str, u16), channel: u32, source: &str, sequence: u16, wait: Duration, modern: bool) -> Result<Wrote, RouteError> {
    let data = if modern { subscribe_page_request(channel, source, sequence) } else { subscribe_request(channel, source, sequence) };
    let Some(data) = data else { return Ok(Wrote::Refused("Relay cannot write that channel or source.".to_owned())) };
    let command = if modern { CMD_SET_SUBSCRIPTION_PAGE } else { CMD_SET_SUBSCRIPTION };
    match exchange(net, ip, port, &data, command, sequence, wait).await? {
        None => Ok(Wrote::Silent("The device did not answer the change.".to_owned())),
        Some((result, _)) if result == 1 || result == MORE_PAGES => Ok(Wrote::Ok),
        Some(_) => Ok(Wrote::Refused("The device refused the change. On a network under Dante Domain Manager, changes are refused unless Relay is given its credentials, which Relay does not support yet.".to_owned())),
    }
}
