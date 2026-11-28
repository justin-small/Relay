//! Dante metering for hardware boxes (`tests/test_dante_metering.py` ported): the level scale, the request byte for byte as a real stage box accepted it,
//! packets captured from real devices, and a fake box that pushes meters after one request.

use std::time::{Duration, Instant};

use relay_module_dante::metering::{self as m, Meters};
use tokio::net::UdpSocket;

const MAC: [u8; 6] = [0x00, 0x15, 0x5d, 0x0a, 0x8b, 0x1d];

fn hex(s: &str) -> Vec<u8> {
    (0..s.len()).step_by(2).map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap()).collect()
}

#[test]
fn the_level_scale() {
    assert_eq!(m::level(0), Some(0), "0 is clipping");
    assert_eq!(m::level(1), Some(0), "1 is 0 dBFS");
    assert_eq!(m::level(0x9C), Some(-78), "0x9C (156) is about -77 dBFS");
    assert_eq!(m::level(254), Some(m::MUTED), "254 is muted");
    assert_eq!(m::level(255), None, "255 is a framing marker, no reading");
}

#[test]
fn the_request_matches_the_one_a_real_stage_box_accepted() {
    // Sent on 2026-11-21 from a computer on the church's Dante network to a real DT168 (port 8800): the box answered with its own copy (result 1) and
    // pushed meters at once. Only the device name and addresses are changed here.
    let real = hex("1200004c000130100000000000155d0a8b1d00000004001a0002001c000a5374616765426f782d312d546f7000000001000100200001222f00010000c0a8fdfd222f000000000000222f0000");
    assert_eq!(m::request("StageBox-1-Top", "192.168.253.253", &MAC, 8751, 1), real, "StageBox-1-Top to 192.168.253.253:8751");
    assert_eq!(usize::from(u16::from_be_bytes([real[2], real[3]])), real.len(), "the length field is the packet length");
    let off = m::stop_request("StageBox-1-Top", &MAC, 8751);
    assert_eq!((off.len(), off[off.len() - 16..].to_vec()), (real.len(), vec![0u8; 16]), "stop is the same size with the destination cleared");
}

#[test]
fn packets_captured_from_real_devices_are_read() {
    // Real pushes (2026-11-21). A DT168 stage box: version 3, 16 transmit and 8 receive levels (identity replaced).
    let boxed = hex("FFFF003641D800000004C408450A0000417564696E6174650300001000089CB4CCB0AEAFBAC09995C8B9C7C8D2C7FEFEFEFEFEFEFEFE");
    let got = m::parse(&boxed).unwrap();
    assert_eq!((got.sequence, got.id.as_str()), (0x41D8, "0004c408450a0000"), "sequence and identity");
    assert_eq!(got.tx.len(), 16, "16 transmit levels");
    assert_eq!(got.tx[0], Some(-78), "the first transmit channel");
    assert_eq!(got.rx, vec![Some(m::MUTED); 8], "unused receive channels are muted");
    // Another device: version 2, 64 transmit and 64 receive levels, 156 bytes (identity replaced).
    let v2 = hex("FFFF009CB02900000102030405060000417564696E6174650240408E8CFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEFEA1B3C4C0ADAC9B97B7C1CEA0CCFED9C6D0FEFEFEFEFEFEFEFEFEFED1FEFED2C6C7C6CBCDC7C7A7FEA1A0D9C6C7FEFEFED9B2C1D1C7C9D7C9D6FEFEA7FEFEA6C600");
    let got = m::parse(&v2).unwrap();
    assert_eq!((v2.len(), got.tx.len(), got.rx.len()), (156, 64, 64), "version 2: 64 and 64");
    assert_eq!(got.rx[0], Some(-80), "version 2: the first receive level");
}

#[test]
fn what_does_not_fit_is_no_reading() {
    let boxed = hex("FFFF003641D800000004C408450A0000417564696E6174650300001000089CB4CCB0AEAFBAC09995C8B9C7C8D2C7FEFEFEFEFEFEFEFE");
    let with = |at: usize, bytes: &[u8]| {
        let mut b = boxed.clone();
        b[at..at + bytes.len()].copy_from_slice(bytes);
        b
    };
    assert_eq!(m::parse(&with(16, b"Audinatf")), None, "a wrong marker");
    assert_eq!(m::parse(&boxed[..boxed.len() - 1]), None, "a wrong length");
    assert_eq!(m::parse(&with(24, &[9])), None, "an unknown version");
    assert_eq!(m::parse(&with(26, &[1, 0])), None, "a count that runs off the end");
    assert_eq!(m::parse(&[0xff, 0xff]), None, "a short packet");
    assert_eq!(m::parse(&m::meter_frame(&[1; 8], 7, &[1, 3, 255], &[254], 3)).unwrap().tx, vec![Some(0), Some(-1), None], "built and read back");
}

#[tokio::test]
async fn a_device_pushes_meters_after_one_request_and_they_are_kept_by_address() {
    let boxed = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let mine = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let meters = Meters::new();
    meters.listen(mine, MAC);
    let mine = meters.local().unwrap();
    let port = boxed.local_addr().unwrap().port(); // the fake box listens where the request goes
    let ips = |s: &str| vec![s.to_owned()];
    let t0 = Instant::now();
    assert!(meters.ask("Test-Box", &ips("127.0.0.1"), t0, port).await, "the first request goes out");
    assert!(!meters.ask("Test-Box", &ips("127.0.0.1"), t0 + Duration::from_secs(1), port).await, "not again within the renewal time");
    let mut buf = vec![0u8; 2048];
    let (n, _) = boxed.recv_from(&mut buf).await.unwrap();
    let data = &buf[..n];
    assert_eq!((&data[..2], &data[6..8], data.windows(8).any(|w| w == b"Test-Box")), (&[0x12, 0x00][..], &[0x30, 0x10][..], true), "the box got a request for it");
    let mut ours = vec![127, 0, 0, 1];
    ours.extend_from_slice(&mine.port().to_be_bytes());
    assert!(data.windows(6).any(|w| w == ours.as_slice()), "our address and port are in it");
    boxed.send_to(&m::meter_frame(&[5; 8], 1, &[1, 3, 255], &[2], 3), mine).await.unwrap();
    let mut seen = None;
    for _ in 0..150 {
        seen = meters.levels(&ips("127.0.0.1"), Instant::now());
        if seen.is_some() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert_eq!(seen, Some(vec![Some(0), Some(-1), None]), "levels");
    assert_eq!(meters.levels(&ips("10.9.9.9"), Instant::now()), None, "another address has none");
    let t1 = Instant::now();
    meters.feed(&m::meter_frame(&[5; 8], 2, &[1], &[], 2), "10.0.0.7", t1);
    assert_eq!(meters.levels(&ips("10.0.0.7"), t1 + m::MAX_AGE + Duration::from_secs(1)), None, "a push that has gone quiet is no reading");
    assert!(meters.ask("Test-Box", &ips("127.0.0.1"), t0 + Duration::from_secs(4), port).await, "it asks again after the renewal time");
    boxed.recv_from(&mut buf).await.unwrap();
    meters.release(port).await;
    let (n, _) = boxed.recv_from(&mut buf).await.unwrap();
    assert_eq!(buf[n - 16..n].to_vec(), vec![0u8; 16], "release tells the box to stop");
    meters.stop();
    assert!(meters.local().is_none());
}
