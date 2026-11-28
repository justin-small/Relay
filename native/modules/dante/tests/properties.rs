//! Properties that hold for any input: no parser panics on bytes it did not expect, a level is always in range, and what the module writes is what it can
//! read back.

use quickcheck::{quickcheck, TestResult};
use relay_module_dante::{arc, heartbeat, inventory, mdns, metering};

quickcheck! {
    fn no_parser_panics_on_any_bytes(data: Vec<u8>) -> bool {
        let _ = heartbeat::parse(&data);
        let _ = metering::parse(&data);
        let _ = mdns::parse(&data);
        let _ = arc::parse_rx_page(Some(&data));
        let _ = arc::parse_clock(Some(&data));
        let _ = arc::parse_counts(Some(&data));
        let _ = arc::parse_info(Some(&data));
        let _ = arc::parse_sample_rate(Some(&data));
        let _ = arc::parse_name(Some(&data));
        arc::body(&data, arc::CMD_CLOCK, 1).is_none() || data.len() >= arc::HEADER
    }

    fn levels_are_in_range(byte: u8) -> bool {
        (-127..=0).contains(&heartbeat::level(byte)) && metering::level(byte).is_none_or(|l| (-127..=0).contains(&l))
    }

    fn a_heartbeat_built_is_a_heartbeat_read(tx: Vec<u8>, rx: Vec<u8>, ppb: i32, ident: Vec<u8>) -> TestResult {
        if ident.iter().all(|b| *b == 0) || ident.iter().all(|b| *b == 0xFF) || ident.len() < 8 || tx.len() > 200 || rx.len() > 200 {
            return TestResult::discard();
        }
        let got = heartbeat::parse(&heartbeat::packet(&ident, Some(&tx), Some(&rx), Some(ppb), 0, 0));
        TestResult::from_bool(got.is_some_and(|b| b.offset_ppb == Some(ppb) && b.presence.is_some_and(|p| p.tx == tx && p.rx == rx)))
    }

    fn a_meter_frame_built_is_a_meter_frame_read(tx: Vec<u8>, rx: Vec<u8>, v3: bool) -> TestResult {
        if tx.len() > 100 || rx.len() > 100 {
            return TestResult::discard();
        }
        let got = metering::parse(&metering::meter_frame(&[1; 8], 7, &tx, &rx, if v3 { 3 } else { 2 }));
        TestResult::from_bool(got.is_some_and(|m| m.tx.len() == tx.len() && m.rx.len() == rx.len() && m.tx.iter().zip(&tx).all(|(l, b)| *l == metering::level(*b))))
    }

    fn a_page_built_is_a_page_read(sources: Vec<(String, bool)>) -> TestResult {
        let rows: Vec<(u16, String, u16)> = sources.iter().take(arc::PAGE).enumerate()
            .map(|(i, (s, on))| (u16::try_from(i + 1).unwrap_or(1), if *on && !s.is_empty() && !s.contains('\0') && !s.contains('@') { format!("{s}@DEV") } else { String::new() }, if *on { 10 } else { 0 }))
            .collect();
        let (payload, _) = arc::rx_page(&rows, false);
        let Some(read) = arc::parse_rx_page(Some(&payload)) else { return TestResult::failed() };
        TestResult::from_bool(read.len() == rows.len() && read.iter().zip(&rows).all(|(r, (c, s, _))| r["channel"] == *c && r["source"] == s.as_str()))
    }

    fn a_write_is_the_length_it_says(channel: u8, chan: String, dev: String) -> TestResult {
        if chan.is_empty() || dev.is_empty() || chan.contains('@') || chan.contains('\0') || dev.contains('\0') || channel == 0 {
            return TestResult::discard();
        }
        let source = format!("{chan}@{dev}");
        let (Some(old), Some(new)) = (arc::subscribe_request(u32::from(channel), &source, 1), arc::subscribe_page_request(u32::from(channel), &source, 1)) else { return TestResult::discard() };
        let said = |p: &[u8]| usize::from(u16::from_be_bytes([p[2], p[3]]));
        TestResult::from_bool(said(&old) == old.len() && said(&new) == new.len())
    }

    fn splitting_a_channel_label_keeps_every_character(label: String) -> bool {
        let (channel, device) = inventory::split_channel(&label);
        channel.is_empty() && device == label || format!("{channel}@{device}") == label
    }

    fn a_query_asks_what_it_says(names: Vec<String>) -> TestResult {
        let questions: Vec<(String, u16)> = names.iter().filter(|n| !n.is_empty() && n.len() < 40 && n.chars().all(|c| c.is_ascii_alphanumeric())).map(|n| (format!("{n}._udp.local"), mdns::TYPE_PTR)).take(8).collect();
        if questions.is_empty() {
            return TestResult::discard();
        }
        let q = mdns::build_query(&questions);
        TestResult::from_bool(u16::from_be_bytes([q[4], q[5]]) as usize == questions.len())
    }
}
