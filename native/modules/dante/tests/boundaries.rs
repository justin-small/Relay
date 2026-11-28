//! Edges of the parsers and builders, one test per rule a mutation run found untested: where a pointer may point, how short a page may be, what counts as a
//! version, and which halves of a label must be there.

use relay_module_dante::arc::{self, HEADER, PAGE, RX_RECORD};
use relay_module_dante::inventory::{inventory, split_channel, ARC, CHAN, CMC, DBC, VIA};
use relay_module_dante::mdns::Found;
use serde_json::json;

fn be(v: &[u8], at: usize) -> usize {
    usize::from(u16::from_be_bytes([v[at], v[at + 1]]))
}

#[test]
fn a_pointer_may_point_at_the_first_byte_after_the_header_and_no_earlier() {
    assert_eq!(arc::text_at(b"Hi\0", u16::try_from(HEADER).unwrap()), "Hi", "offset 10 is payload byte 0");
    assert_eq!(arc::text_at(b"Hi\0", u16::try_from(HEADER - 1).unwrap()), "", "inside the header is nothing");
    assert_eq!(arc::text_at(b"Hi\0", 0), "", "0 is no string");
    assert_eq!(arc::text_at(b"Hi\0", u16::try_from(HEADER + 3).unwrap()), "", "past the end is nothing");
    assert_eq!(arc::text_at(b"Hi", u16::try_from(HEADER).unwrap()), "Hi", "a string with no NUL runs to the end");
}

#[test]
fn info_needs_only_one_of_its_three_strings() {
    let build = |board: &[u8], name: &[u8], host: &[u8]| {
        let mut p = vec![0u8; 80];
        for (at, off, s) in [(6usize, 20usize, board), (12, 40, name), (14, 60, host)] {
            if !s.is_empty() {
                p[at..at + 2].copy_from_slice(&u16::try_from(HEADER + off).unwrap().to_be_bytes());
                p[off..off + s.len()].copy_from_slice(s);
            }
        }
        p
    };
    assert_eq!(arc::parse_info(Some(&build(b"B\0", b"", b""))), Some(json!({"board": "B", "name": "", "hostname": ""})), "a board alone is a reading");
    assert_eq!(arc::parse_info(Some(&build(b"", b"N\0", b""))), Some(json!({"board": "", "name": "N", "hostname": ""})), "a name alone is a reading");
    assert_eq!(arc::parse_info(Some(&build(b"", b"", b"H\0"))), Some(json!({"board": "", "name": "", "hostname": "H"})), "a hostname alone is a reading");
    assert_eq!(arc::parse_info(Some(&build(b"", b"", b""))), None, "no string is no reading");
    assert_eq!(arc::parse_info(Some(&[0u8; 15])), None, "too short");
}

#[test]
fn a_receive_page_is_exactly_as_long_as_its_count_says() {
    let page = |count: usize, len: usize| {
        let mut p = vec![0u8; len];
        p[0] = u8::try_from(PAGE).unwrap();
        p[1] = u8::try_from(count).unwrap();
        p
    };
    assert_eq!(arc::parse_rx_page(Some(&page(1, 2 + RX_RECORD))).map(|r| r.len()), Some(1), "one record, exactly its bytes");
    assert_eq!(arc::parse_rx_page(Some(&page(1, 2 + RX_RECORD - 1))), None, "one byte short");
    assert_eq!(arc::parse_rx_page(Some(&page(1, 2 + RX_RECORD + 40))).map(|r| r.len()), Some(1), "a string heap after the records is fine");
    assert_eq!(arc::parse_rx_page(Some(&page(PAGE, 2 + PAGE * RX_RECORD))).map(|r| r.len()), Some(PAGE), "a full page");
    assert_eq!(arc::parse_rx_page(Some(&page(PAGE, 2 + PAGE * RX_RECORD - 1))), None, "a full page one byte short");
    assert_eq!(arc::parse_rx_page(Some(&page(PAGE + 1, 2 + (PAGE + 1) * RX_RECORD))), None, "more records than a page holds");
    assert_eq!(arc::parse_rx_page(Some(&page(0, 2))).map(|r| r.len()), Some(0), "an empty page");
    assert_eq!(arc::parse_rx_page(Some(&[16])), None, "no count byte");
    assert_eq!(arc::parse_rx_page(None), None);
}

#[test]
fn only_plain_numbers_make_a_version() {
    for v in ["2.8", "2.8.12", "02.08", "3.0.1", "2.100"] {
        assert!(arc::is_modern(v), "{v} is modern");
    }
    for v in ["2.7", "1.99", "2.+9", "+2.8", "2.-1", "2. 8", "2..8", ".8", "2.", "2.8x", "a.b", "2,8"] {
        assert!(!arc::is_modern(v), "{v:?} is not");
    }
}

#[test]
fn the_page_write_puts_its_pointers_and_length_where_the_device_reads_them() {
    let p = arc::subscribe_page_request(2, "Mix@BOX", 7).unwrap();
    let at = HEADER + 6 + 2 + 2 + 8;
    assert_eq!(at, 28);
    assert_eq!((be(&p, 24), be(&p, 26)), (at, at + "Mix\0".len()), "the channel and device pointers");
    assert_eq!(be(&p, 2), p.len(), "the length field");
    assert_eq!(&p[at..], b"Mix\0BOX\0");
    assert_eq!((p[19], p[21], p[23]), (1, 2, arc::MEDIA_AUDIO), "one record, receive channel, audio");
    let clear = arc::subscribe_page_request(2, "", 7).unwrap();
    assert_eq!((clear.len(), be(&clear, 24), be(&clear, 26), be(&clear, 2)), (at, 0, 0, at), "a clear has no strings and no pointers");
    let own = arc::subscribe_page_request(2, "Mix", 7).unwrap();
    assert_eq!(&own[at..], b"Mix\0.\0", "no @ means a channel of the device itself");
    let old = arc::subscribe_request(2, "Mix@BOX", 7).unwrap();
    assert_eq!((be(&old, 14), be(&old, 16), be(&old, 2), old.len()), (172, 176, old.len(), 172 + 8), "the old write's pointers");
}

#[test]
fn a_label_splits_only_when_both_halves_are_there() {
    let whole = |l: &str| (l.to_owned(), String::new());
    assert_eq!(split_channel("A@B"), ("A".into(), "B".into()));
    assert_eq!(split_channel("@B"), (String::new(), "@B".into()), "no channel name");
    assert_eq!(split_channel("A@"), (String::new(), "A@".into()), "no device name");
    assert_eq!(split_channel("@"), (String::new(), "@".into()));
    assert_eq!(split_channel("A@B@C"), ("A@B".into(), "C".into()));
    assert_eq!(split_channel(""), (String::new(), String::new()));
    let _ = whole;
}

fn found(service: &str, label: &str, port: u16) -> Found {
    let mut f = Found::new(service, &format!("{label}.{service}"));
    f.port = Some(port);
    f.host = "box.local".into();
    f
}

#[test]
fn every_dante_announcement_gives_its_port_and_nothing_else_does() {
    let inv = inventory(&[found(ARC, "BOX", 4440), found(CMC, "BOX", 8800), found(DBC, "BOX", 8700), found(CHAN, "Mix@BOX", 4455), found(VIA, "BOX", 24440), found("_ndi._tcp.local", "BOX", 5961)]);
    assert_eq!(inv.len(), 1);
    assert_eq!(inv[0]["ports"], json!({"arc": 4440, "cmc": 8800, "dbc": 8700, "chan": 4455, "via": 24440}), "one port per announcement kind");
    assert_eq!(inv[0]["via"], json!(true), "a Via announcement marks Via");
    assert_eq!(inventory(&[found(DBC, "ONLY", 8700)])[0]["ports"], json!({"dbc": 8700}), "a device known only by its DBC announcement still exists");
    assert!(inventory(&[found(VIA, "ONLY-VIA", 24440)]).first().is_some_and(|d| d["via"] == json!(true)), "so does one known only as Via");
    assert!(inventory(&[found("_ndi._tcp.local", "CAM", 5961)]).is_empty(), "NDI is not a Dante device");
    assert!(inventory(&[found(CHAN, "Mix@", 4455)]).iter().all(|d| d["transmit"] == json!([])), "a channel label with no device is not a channel");
    assert_eq!(inventory(&[found(CHAN, "@BOX", 4455)])[0]["name"], json!("@BOX"), "nor with no channel name: the whole label is the device");
}
