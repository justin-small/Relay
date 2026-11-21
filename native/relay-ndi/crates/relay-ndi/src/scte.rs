//! SCTE-104 ad-insertion cues from NDI (issue 560). NDI carries SDI ancillary data as XML in a video frame's metadata
//! (`<vancData version="1.0"><vancPacket did="65" sdid="7" line="10">base64</vancPacket></vancData>`, NDI's proposed
//! metadata messages); a SCTE-104 packet is DID 0x41, SDID 0x07, and its payload is a SMPTE ST 2010 message.

const DID: u32 = 0x41;
const SDID: u32 = 0x07;
/// A pre-roll over this many milliseconds, or a break over an hour, is a unit misread.
pub const PREROLL_WARN_MS: u32 = 60_000;
pub const BREAK_WARN_TENTHS: u32 = 36_000;

#[derive(Clone, Debug, PartialEq)]
pub struct Packet {
    pub did: u32,
    pub sdid: u32,
    pub line: u32,
    pub data: Vec<u8>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Cue {
    pub line: u32,
    /// "start", "end" or "cancel".
    pub action: &'static str,
    pub immediate: bool,
    pub event_id: u32,
    pub unique_program_id: u16,
    pub pre_roll_ms: u32,
    /// In tenths of a second, as SCTE-104 sends it.
    pub break_tenths: u32,
    pub auto_return: bool,
    /// "none", "utc", "vitc" or "gpi".
    pub time_type: &'static str,
    pub time: String,
    pub warnings: Vec<&'static str>,
}

fn b64(c: u8) -> Option<u8> {
    match c {
        b'A'..=b'Z' => Some(c - b'A'),
        b'a'..=b'z' => Some(c - b'a' + 26),
        b'0'..=b'9' => Some(c - b'0' + 52),
        b'+' => Some(62),
        b'/' => Some(63),
        _ => None,
    }
}

pub fn base64_decode(s: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(s.len() * 3 / 4);
    let (mut acc, mut bits) = (0u32, 0);
    for c in s.bytes().filter(|c| !c.is_ascii_whitespace() && *c != b'=') {
        acc = (acc << 6) | b64(c)? as u32;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Some(out)
}

fn attr(tag: &str, name: &str) -> Option<u32> {
    let key = format!("{name}=\"");
    let at = tag.find(&key)? + key.len();
    let end = tag[at..].find('"')?;
    tag[at..at + end].trim().parse().ok()
}

/// Every `<vancPacket>` in a video frame's metadata XML.
pub fn packets(xml: &str) -> Vec<Packet> {
    let mut out = Vec::new();
    let mut rest = xml;
    while let Some(i) = rest.find("<vancPacket") {
        rest = &rest[i..];
        let Some(close) = rest.find('>') else { break };
        let tag = &rest[..close];
        let (body, next) = if tag.ends_with('/') {
            ("", close + 1)
        } else {
            let end = rest[close + 1..].find("</vancPacket>").map(|e| close + 1 + e).unwrap_or(rest.len());
            (&rest[close + 1..end], (end + 13).min(rest.len()))
        };
        if let (Some(did), Some(sdid)) = (attr(tag, "did"), attr(tag, "sdid")) {
            if let Some(data) = base64_decode(body) {
                out.push(Packet { did, sdid, line: attr(tag, "line").unwrap_or(0), data });
            }
        }
        rest = &rest[next..];
    }
    out
}

fn be(d: &[u8], at: usize, n: usize) -> Option<u32> {
    let s = d.get(at..at + n)?;
    Some(s.iter().fold(0u32, |a, &b| (a << 8) | b as u32))
}

fn hms(h: u32, m: u32, s: u32, f: u32) -> String {
    format!("{h:02}:{m:02}:{s:02}:{f:02}")
}

/// The splice cues in one SCTE-104 packet's payload (descriptor byte, then a SMPTE ST 2010 message). Only the
/// multiple-operation message is read; a single-operation message carries no splice_request here.
pub fn decode_104(p: &Packet) -> Vec<Cue> {
    let d = &p.data;
    let mut cues = Vec::new();
    // Byte 0 is the payload descriptor; the message follows: opID(2) size(2) then, for a multiple operation message,
    // protocol(1) AS_index(1) message_number(1) DPI_PID_index(2) SCTE35_protocol(1) timestamp num_ops(1) ops.
    if p.did != DID || p.sdid != SDID || be(d, 1, 2) != Some(0xFFFF) {
        return cues;
    }
    let mut at = 1 + 2 + 2 + 1 + 1 + 1 + 2 + 1;
    let (time_type, time) = match be(d, at, 1) {
        Some(0) => ("none", String::new()),
        Some(1) => ("utc", format!("{} s {} us", be(d, at + 1, 4).unwrap_or(0), be(d, at + 5, 2).unwrap_or(0))),
        Some(2) => ("vitc", hms(be(d, at + 1, 1).unwrap_or(0), be(d, at + 2, 1).unwrap_or(0), be(d, at + 3, 1).unwrap_or(0), be(d, at + 4, 1).unwrap_or(0))),
        Some(3) => ("gpi", format!("input {}", be(d, at + 1, 1).unwrap_or(0))),
        _ => return cues,
    };
    at += match be(d, at, 1) {
        Some(1) => 7,
        Some(2) => 5,
        Some(3) => 3,
        _ => 1,
    };
    let Some(ops) = be(d, at, 1) else { return cues };
    at += 1;
    for _ in 0..ops {
        let (Some(op), Some(len)) = (be(d, at, 2), be(d, at + 2, 2)) else { break };
        let body = at + 4;
        at = body + len as usize;
        if op != 0x0101 || len < 14 || d.len() < body + 14 {
            continue;
        }
        let (action, immediate) = match d[body] {
            1 => ("start", false),
            2 => ("start", true),
            3 => ("end", false),
            4 => ("end", true),
            5 => ("cancel", false),
            _ => continue,
        };
        let pre = be(d, body + 7, 2).unwrap_or(0);
        let brk = be(d, body + 9, 2).unwrap_or(0);
        let mut warnings = Vec::new();
        if pre > PREROLL_WARN_MS {
            warnings.push("pre-roll over 60 s");
        }
        if brk > BREAK_WARN_TENTHS {
            warnings.push("break over an hour");
        }
        cues.push(Cue {
            line: p.line,
            action,
            immediate,
            event_id: be(d, body + 1, 4).unwrap_or(0),
            unique_program_id: be(d, body + 5, 2).unwrap_or(0) as u16,
            pre_roll_ms: pre,
            break_tenths: brk,
            auto_return: d[body + 13] != 0,
            time_type,
            time: time.clone(),
            warnings,
        });
    }
    cues
}

/// Every SCTE-104 cue in a frame's metadata XML.
pub fn cues(xml: &str) -> Vec<Cue> {
    packets(xml).iter().flat_map(decode_104).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The SCTE-104 example NDI's own documentation shows.
    const EXAMPLE: &str = r#"<vancData version="1.0">
      <vancPacket did="97" sdid="1" line="9">lmlZT38BWHL0/ICA</vancPacket>
      <vancPacket did="65" sdid="7" line="10">CP//AB4AAQEAAgAAAQEBAA4BAAAAAwAAAAAAAAAAAA==</vancPacket></vancData>"#;

    #[test]
    fn base64_round_trips_known_text() {
        assert_eq!(base64_decode("aGVsbG8=").unwrap(), b"hello");
        assert_eq!(base64_decode("aGVsbG8h").unwrap(), b"hello!");
        assert!(base64_decode("a*b").is_none());
    }

    #[test]
    fn packets_are_found_with_their_ids_and_lines() {
        let p = packets(EXAMPLE);
        assert_eq!(p.len(), 2);
        assert_eq!((p[0].did, p[0].sdid, p[0].line), (97, 1, 9));
        assert_eq!((p[1].did, p[1].sdid, p[1].line, p[1].data.len()), (65, 7, 10, 31));
    }

    #[test]
    fn the_documented_example_is_a_splice_start_with_event_3() {
        let c = cues(EXAMPLE);
        assert_eq!(c.len(), 1, "the caption packet is not a cue");
        assert_eq!((c[0].action, c[0].immediate, c[0].event_id, c[0].pre_roll_ms, c[0].break_tenths), ("start", false, 3, 0, 0));
        assert_eq!((c[0].time_type, c[0].line), ("none", 10));
    }

    fn message(op_data: &[u8], time: &[u8]) -> Packet {
        let mut m = vec![0x08, 0xFF, 0xFF, 0, 0, 0, 1, 1, 0, 2, 0];
        m.extend_from_slice(time);
        m.push(1); // one operation
        m.extend_from_slice(&[0x01, 0x01, 0, op_data.len() as u8]);
        m.extend_from_slice(op_data);
        let size = (m.len() - 1) as u16;
        m[3] = (size >> 8) as u8;
        m[4] = size as u8;
        Packet { did: 65, sdid: 7, line: 12, data: m }
    }

    #[test]
    fn pre_roll_break_auto_return_and_time_are_read() {
        // splice_start_immediate, event 0x01020304, program 7, pre-roll 8000 ms, break 300 (30 s), auto return
        let op = [2, 1, 2, 3, 4, 0, 7, 0x1F, 0x40, 0x01, 0x2C, 0, 0, 1];
        let c = decode_104(&message(&op, &[2, 10, 30, 5, 12]));
        assert_eq!(c.len(), 1);
        assert_eq!((c[0].action, c[0].immediate, c[0].event_id, c[0].unique_program_id), ("start", true, 0x01020304, 7));
        assert_eq!((c[0].pre_roll_ms, c[0].break_tenths, c[0].auto_return), (8000, 300, true));
        assert_eq!((c[0].time_type, c[0].time.as_str()), ("vitc", "10:30:05:12"));
        assert!(c[0].warnings.is_empty());
    }

    #[test]
    fn a_unit_misread_is_warned() {
        // pre-roll 61 000 ms and a break of 40 000 tenths of a second (over an hour)
        let op = [3, 0, 0, 0, 9, 0, 0, 0xEE, 0x48, 0x9C, 0x40, 0, 0, 0];
        let c = decode_104(&message(&op, &[0]));
        assert_eq!((c[0].action, c[0].pre_roll_ms, c[0].break_tenths), ("end", 61_000, 40_000));
        assert_eq!(c[0].warnings, vec!["pre-roll over 60 s", "break over an hour"]);
    }

    #[test]
    fn junk_gives_nothing() {
        assert!(cues("").is_empty());
        assert!(cues("<vancPacket did=\"65\" sdid=\"7\">!!</vancPacket>").is_empty());
        assert!(decode_104(&Packet { did: 65, sdid: 7, line: 1, data: vec![8, 0xFF, 0xFF, 0, 1] }).is_empty());
        assert!(decode_104(&Packet { did: 97, sdid: 1, line: 1, data: vec![] }).is_empty());
    }
}
