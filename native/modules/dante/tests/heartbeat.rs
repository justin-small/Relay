//! Dante's heartbeat (`tests/test_dante_heartbeat.py` ported): the level scale, a packet read and what does not fit refused, records captured from real
//! gear, and the latest heartbeat of each sender kept for a few seconds.

mod fake;

use std::time::{Duration, Instant};

use fake::until;
use relay_module_dante::heartbeat::{self as hb, Heartbeats};
use tokio::net::UdpSocket;

const ID: [u8; 8] = [0x00, 0x1d, 0xc1, 0xff, 0xfe, 0x19, 0x86, 0x2a];

fn hex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

fn ips(s: &str) -> Vec<String> {
    vec![s.to_owned()]
}

#[test]
fn the_level_scale() {
    assert_eq!(hb::level(0), 0, "0 is clipping");
    assert_eq!(hb::level(2), -1);
    assert_eq!(hb::level(40), -20, "40 is -20 dBFS");
    assert_eq!(hb::level(252), -126, "252 is -126 dBFS");
    assert_eq!((hb::level(253), hb::level(255)), (-127, -127), "253 and up is silence");
    assert_eq!(hb::level(1), 0, "a half rounds to the even number, as Python's round does");
    assert_eq!(hb::level(3), -2);
}

#[test]
fn a_packet_is_read_and_what_does_not_fit_is_no_reading() {
    let got = hb::parse(&hb::packet(&ID, Some(&[2, 40, 255]), Some(&[9, 9]), Some(-120), 0, 0)).unwrap();
    assert_eq!(got.id, "001dc1fffe19862a", "identity");
    let p = got.presence.as_ref().unwrap();
    assert_eq!((p.tx.clone(), p.rx.clone()), (vec![2, 40, 255], vec![9, 9]), "transmit and receive levels");
    assert_eq!(got.offset_ppb, Some(-120), "clock error in parts per billion");
    assert_eq!(hb::parse(&hb::packet(&ID, None, None, Some(7), 0, 0)).unwrap().presence, None, "a packet with only a clock error has no levels");
    let good = hb::packet(&ID, Some(&[1, 2, 3]), None, None, 0, 0);
    let mut wrong = good.clone();
    wrong[..2].copy_from_slice(&[0, 0]);
    assert_eq!(hb::parse(&wrong), None, "a wrong protocol");
    assert_eq!(hb::parse(&good[..good.len() - 4]), None, "a wrong length");
    assert_eq!(hb::parse(&hb::packet(&[], Some(&[1]), None, None, 0, 0)), None, "an all-zero identity");
    assert_eq!(hb::parse(&hb::packet(&[0xff; 8], Some(&[1]), None, None, 0, 0)), None, "an all-ones identity");
    let mut runs_off = good[..good.len() - 4].to_vec();
    let len = u16::try_from(good.len() - 4).unwrap().to_be_bytes();
    runs_off[2..4].copy_from_slice(&len);
    assert_eq!(hb::parse(&runs_off), None, "a record that runs off the end");
    let mut disagrees = good.clone();
    disagrees[0x20 + 12..0x20 + 14].copy_from_slice(&9u16.to_be_bytes());
    assert_eq!(hb::parse(&disagrees).unwrap().presence, None, "a level count that disagrees with the record has no levels");
    assert_eq!(hb::parse(&[0xff, 0xfe]), None, "a short packet");
}

#[test]
fn records_captured_from_a_real_dante_virtual_soundcard_are_read() {
    // Real 0x8001 and 0x8002 records (2026-11-20); only the sender's identity is replaced.
    let clock = hex("003480010004002855310000fffff170000000ff0001570500000007000007fd0015253500000020000000000000000f00000000");
    let presence = hex("001c800200040010d7560000000200000002000000180000c1c1ffff");
    let mut packet = vec![0xFF, 0xFE];
    packet.extend_from_slice(&u16::try_from(32 + clock.len() + presence.len()).unwrap().to_be_bytes());
    packet.extend(hex("0f100000"));
    packet.extend(hex("001122fffe334455"));
    packet.extend(hex("417564696e6174650008000110000000"));
    packet.extend(&clock);
    packet.extend(&presence);
    let got = hb::parse(&packet).unwrap();
    assert_eq!(got.offset_ppb, Some(-3728), "the clock error is -3728 ppb");
    let p = got.presence.unwrap();
    assert_eq!((p.tx, p.rx), (vec![193, 193], vec![255, 255]), "two transmit and two receive channels");
    assert_eq!((hb::level(193), hb::level(255)), (-96, -127), "a transmit byte of 193 is about -96 dBFS, and 255 is silence");
}

#[test]
fn the_latest_heartbeat_of_each_sender_is_kept_for_a_few_seconds() {
    let beats = Heartbeats::new();
    let t0 = Instant::now();
    let at = |s: f64| t0 + Duration::from_secs_f64(s);
    assert!(beats.feed(&hb::packet(&ID, Some(&[2, 40]), None, Some(15), 0, 0), "10.0.0.5", t0), "a heartbeat is kept");
    assert!(!beats.feed(b"junk", "10.0.0.5", t0), "junk is not");
    assert_eq!(beats.levels(&ips("10.0.0.5"), at(1.0)), Some(vec![Some(-1), Some(-20)]), "levels by channel");
    assert_eq!(beats.offset_ppb(&ips("10.0.0.5"), at(1.0)), Some(15), "the clock error");
    assert_eq!(beats.levels(&ips("10.0.0.6"), at(1.0)), None, "another address has none");
    assert_eq!(beats.levels(&ips("10.0.0.5"), t0 + hb::MAX_AGE + Duration::from_secs(1)), None, "a stale heartbeat is no reading");
    beats.feed(&hb::packet(&ID, Some(&[2]), None, None, 2, 0), "10.0.0.7", t0);
    assert_eq!(beats.levels(&ips("10.0.0.7"), at(0.5)), Some(vec![None, None, Some(-1)]), "channels before the first are unknown");
}

#[tokio::test]
async fn a_device_multicasting_to_a_real_socket_is_heard() {
    let rx = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let to = rx.local_addr().unwrap();
    let live = Heartbeats::new();
    live.listen(rx);
    let tx = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    tx.send_to(b"not a heartbeat", to).await.unwrap();
    tx.send_to(&hb::packet(&ID, Some(&[4, 6]), None, None, 0, 0), to).await.unwrap();
    assert!(until(3.0, || live.levels(&ips("127.0.0.1"), Instant::now()).is_some()).await, "the heartbeat arrives");
    assert_eq!(live.levels(&ips("127.0.0.1"), Instant::now()), Some(vec![Some(-2), Some(-3)]), "read off the wire");
    live.stop();
    assert_eq!(live.levels(&ips("127.0.0.1"), Instant::now()), None, "stopping forgets it");
    assert_eq!(live.local(), None, "and it no longer listens");
}
