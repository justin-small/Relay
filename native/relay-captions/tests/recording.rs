//! The recorder's run layout and the exports derived from it.

use std::io::Read;
use std::path::PathBuf;

use relay_captions::clock::{utc, Civil};
use relay_captions::exporting::{build_exports, pair_lines, split_streams, zip_bytes, Row, PAIR_TOLERANCE_S};
use relay_captions::recorder::{is_run_id, read_lines, Recorder};
use serde_json::{json, Value};

fn tmp(name: &str) -> PathBuf {
    let d = std::env::temp_dir().join(format!("relay-captions-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&d);
    std::fs::create_dir_all(&d).unwrap();
    d
}

fn at(ts: f64) -> Civil {
    utc(ts)
}

#[test]
fn utc_reads_a_known_moment() {
    // 2026-09-20 14:30:12 UTC
    let c = utc(1_789_914_612.0);
    assert_eq!(c.stamp(), "2026-09-20 14:30:12");
    assert_eq!(c.run_stamp(), "20260920-143012");
    assert_eq!(utc(0.0).stamp(), "1970-01-01 00:00:00");
    assert_eq!(utc(951_782_400.0).stamp(), "2000-02-29 00:00:00");
}

#[test]
fn run_ids_are_timestamps_and_nothing_else() {
    for ok in ["20260920-143012", "20260920-143012-2", "20260920-143012-17"] {
        assert!(is_run_id(ok), "{ok}");
    }
    for bad in ["", "../x", "20260920-143012-", "2026092-143012", "20260920-14301", "20260920-143012/x", "20260920-143012-a", "20260920_143012", "20260920-143012-2-3"] {
        assert!(!is_run_id(bad), "{bad}");
    }
}

fn recorder(dir: &std::path::Path) -> Recorder {
    fn now() -> f64 {
        1_789_914_612.0
    }
    let mut r = Recorder::new(now, at);
    r.configure(dir, 3);
    r
}

#[test]
fn a_run_is_a_directory_with_meta_and_lines() {
    let dir = tmp("run");
    let mut r = recorder(&dir);
    let id = r.start_run("ENGLISH", &["SPANISH".to_owned()]).unwrap();
    assert_eq!(id, "20260920-143012");
    assert!(r.active());
    r.append("transcription", "ENGLISH", 1, "Hello there.", 10.0004, 11.5);
    r.append("translation", "SPANISH", 2, "Hola.", 11.0, 12.0);
    let listed = r.list_run(&id).unwrap();
    assert_eq!(listed["lines"], 2, "the open run reports what is written so far");
    assert_eq!(listed["recording"], true);
    assert_eq!(listed["interrupted"], false);
    assert_eq!(r.finish_run().as_deref(), Some(id.as_str()));
    assert!(!r.active());
    let listed = r.list_run(&id).unwrap();
    assert_eq!((listed["lines"].clone(), listed["recording"].clone(), listed["ended"].is_null()), (json!(2), json!(false), false));
    let rows = read_lines(&dir.join(&id));
    assert_eq!(rows.len(), 2);
    assert_eq!(rows[0]["t0"], 10.0, "times are kept to the millisecond");
    assert_eq!(rows[0]["text"], "Hello there.");
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn two_starts_in_one_second_get_their_own_directories() {
    let dir = tmp("twice");
    let mut r = recorder(&dir);
    assert_eq!(r.start_run("ENGLISH", &[]).unwrap(), "20260920-143012");
    assert_eq!(r.start_run("ENGLISH", &[]).unwrap(), "20260920-143012-2");
    assert_eq!(r.start_run("ENGLISH", &[]).unwrap(), "20260920-143012-3");
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn a_killed_run_is_interrupted_and_a_torn_row_is_skipped() {
    let dir = tmp("killed");
    let run = dir.join("20260920-120000");
    std::fs::create_dir_all(&run).unwrap();
    std::fs::write(run.join("meta.json"), r#"{"run_id":"20260920-120000","started":5.0,"ended":null,"lines":0}"#).unwrap();
    std::fs::write(run.join("lines.jsonl"), "{\"stream\":\"transcription\",\"lang\":\"ENGLISH\",\"seq\":1,\"t0\":1,\"t1\":2,\"text\":\"one\"}\n{\"stream\":\"trans").unwrap();
    let r = recorder(&dir);
    let m = r.list_run("20260920-120000").unwrap();
    assert_eq!((m["interrupted"].clone(), m["lines"].clone()), (json!(true), json!(1)));
    std::fs::remove_dir_all(&dir).unwrap();
}

#[test]
fn the_oldest_runs_are_pruned_and_the_live_one_cannot_be_deleted() {
    let dir = tmp("prune");
    for (i, id) in ["20260101-000000", "20260102-000000", "20260103-000000", "20260104-000000"].iter().enumerate() {
        let d = dir.join(id);
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("meta.json"), json!({"run_id": id, "started": 100.0 + i as f64, "ended": 200.0}).to_string()).unwrap();
    }
    let mut r = recorder(&dir); // keeps 3
    let id = r.start_run("ENGLISH", &[]).unwrap();
    let ids: Vec<String> = r.list_runs().iter().map(|m| m["run_id"].as_str().unwrap().to_owned()).collect();
    assert_eq!(ids.len(), 3, "{ids:?}");
    assert_eq!(ids[0], id, "newest first");
    assert!(!ids.contains(&"20260101-000000".to_owned()));
    assert!(!r.delete_run(&id), "the run being written is refused");
    assert!(r.delete_run("20260104-000000"));
    assert!(!r.delete_run("../etc"));
    assert!(r.run_dir("20260104-000000").is_none());
    std::fs::remove_dir_all(&dir).unwrap();
}

fn row(stream: &str, lang: &str, seq: u64, t0: f64, t1: f64, text: &str) -> Row {
    Row::from_json(&json!({"stream": stream, "lang": lang, "seq": seq, "t0": t0, "t1": t1, "text": text}))
}

fn grades(source: &[Row], target: &[Row]) -> Vec<&'static str> {
    pair_lines(source, target, PAIR_TOLERANCE_S, at).iter().map(|p| p.confidence).collect()
}

#[test]
fn pairs_are_graded_by_how_the_times_line_up() {
    // Real times are far from zero; a zero start is read as missing, as in Python.
    let s = |seq, t0: f64, t1: f64, text: &str| row("transcription", "ENGLISH", seq, t0 + 100.0, t1 + 100.0, text);
    let t = |seq, t0: f64, t1: f64, text: &str| row("translation", "SPANISH", seq, t0 + 100.0, t1 + 100.0, text);
    // One to one.
    assert_eq!(grades(&[s(1, 0.0, 2.0, "a")], &[t(2, 0.5, 2.5, "A")]), ["exact"]);
    // Two source lines inside one translation.
    assert_eq!(grades(&[s(1, 0.0, 1.0, "a"), s(2, 1.1, 2.0, "b")], &[t(3, 0.0, 2.0, "AB")]), ["merged"]);
    // One source line answered twice.
    assert_eq!(grades(&[s(1, 0.0, 4.0, "a")], &[t(2, 0.5, 1.5, "A1"), t(3, 2.0, 3.0, "A2")]), ["split", "split"]);
    // A translation that lands just after its source, no overlap.
    assert_eq!(grades(&[s(1, 0.0, 2.0, "a"), s(2, 6.0, 8.0, "b")], &[t(3, 3.0, 4.0, "A"), t(4, 9.0, 10.0, "B")]), ["exact", "exact"]);
    // Nothing overlaps and the nearest is in the wider window: loose; beyond it, unpaired both ways.
    assert_eq!(grades(&[s(1, 0.0, 1.0, "a")], &[t(2, 4.0, 5.0, "A")]), ["loose"]);
    assert_eq!(grades(&[s(1, 0.0, 1.0, "a")], &[t(2, 50.0, 51.0, "A")]), ["unpaired", "unpaired"]);
}

fn lines() -> Vec<Value> {
    vec![
        json!({"stream": "transcription", "lang": "ENGLISH", "seq": 1, "t0": 1_789_914_612.0, "t1": 1_789_914_614.0, "text": "Good morning."}),
        json!({"stream": "translation", "lang": "SPANISH", "seq": 2, "t0": 1_789_914_613.0, "t1": 1_789_914_615.0, "text": "Buenos días."}),
        json!({"stream": "transcription", "lang": "ENGLISH", "seq": 3, "t0": 1_789_914_620.0, "t1": 1_789_914_622.0, "text": "Welcome."}),
    ]
}

#[test]
fn a_run_becomes_transcripts_pairs_and_a_finetune_file() {
    let rows: Vec<Row> = lines().iter().map(Row::from_json).collect();
    let (source, targets) = split_streams(&rows);
    assert_eq!((source.len(), targets["SPANISH"].len()), (2, 1));
    let meta = json!({"run_id": "20260920-143012", "started": 1_789_914_612.0, "ended": 1_789_914_700.0, "source_language": "ENGLISH"});
    let files = build_exports(&meta, &rows, at);
    let names: Vec<&str> = files.keys().map(String::as_str).collect();
    assert_eq!(names, ["finetune-SPANISH.jsonl", "manifest.txt", "pairs-SPANISH.jsonl", "transcript-ENGLISH.txt", "transcript-SPANISH.txt"]);
    let t = &files["transcript-ENGLISH.txt"];
    assert!(t.starts_with("# English transcript — Relay run 20260920-143012\n# started 2026-09-20 14:30:12   ended 2026-09-20 14:31:40   2 lines\n\n[14:30:12] Good morning.\n[14:30:20] Welcome.\n"), "{t}");
    let pairs: Vec<Value> = files["pairs-SPANISH.jsonl"].lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(pairs.len(), 2);
    assert_eq!((pairs[0]["confidence"].clone(), pairs[0]["source"].clone(), pairs[0]["target"].clone()), (json!("exact"), json!("Good morning."), json!("Buenos días.")));
    assert_eq!(pairs[1]["confidence"], "unpaired", "a source line with no translation is shown, not dropped");
    let ft: Vec<Value> = files["finetune-SPANISH.jsonl"].lines().map(|l| serde_json::from_str(l).unwrap()).collect();
    assert_eq!(ft.len(), 1, "only the exact pair");
    assert_eq!(ft[0]["messages"][1]["content"], "Good morning.");
    assert_eq!(ft[0]["messages"][2]["content"], "Buenos días.");
    assert!(ft[0]["messages"][0]["content"].as_str().unwrap().starts_with("Translate the user's English into Spanish."));
    assert!(files["manifest.txt"].contains("duration 0h 01m 28s"), "{}", files["manifest.txt"]);
    assert!(files["manifest.txt"].contains("Spanish (1 lines)"));
}

/// Read the zip back by hand: the end record, the central directory, then each entry inflated.
fn unzip(bytes: &[u8]) -> Vec<(String, Vec<u8>)> {
    let u16at = |i: usize| u16::from_le_bytes([bytes[i], bytes[i + 1]]) as usize;
    let u32at = |i: usize| u32::from_le_bytes([bytes[i], bytes[i + 1], bytes[i + 2], bytes[i + 3]]) as usize;
    let end = bytes.len() - 22;
    assert_eq!(u32at(end), 0x0605_4b50);
    let (count, mut at) = (u16at(end + 10), u32at(end + 16));
    let mut out = Vec::new();
    for _ in 0..count {
        assert_eq!(u32at(at), 0x0201_4b50);
        let (crc, packed, size, name_len, extra, comment, local) = (u32at(at + 16), u32at(at + 20), u32at(at + 24), u16at(at + 28), u16at(at + 30), u16at(at + 32), u32at(at + 42));
        let name = String::from_utf8(bytes[at + 46..at + 46 + name_len].to_vec()).unwrap();
        assert_eq!(u32at(local), 0x0403_4b50);
        let data = local + 30 + u16at(local + 26) + u16at(local + 28);
        let mut inflated = Vec::new();
        flate2::read::DeflateDecoder::new(&bytes[data..data + packed]).read_to_end(&mut inflated).unwrap();
        assert_eq!((inflated.len(), crc32fast::hash(&inflated) as usize), (size, crc));
        out.push((name, inflated));
        at += 46 + name_len + extra + comment;
    }
    out
}

#[test]
fn the_zip_holds_every_file_the_raw_lines_and_the_meta() {
    let rows: Vec<Row> = lines().iter().map(Row::from_json).collect();
    let meta = json!({"run_id": "20260920-143012", "started": 1_789_914_612.0, "ended": 1_789_914_700.0, "source_language": "ENGLISH"});
    let raw = "{\"a\":1}\n";
    let entries = unzip(&zip_bytes(&meta, &rows, raw, at, 1_789_914_700.0));
    let names: Vec<&str> = entries.iter().map(|(n, _)| n.as_str()).collect();
    assert_eq!(
        names,
        [
            "20260920-143012/finetune-SPANISH.jsonl",
            "20260920-143012/manifest.txt",
            "20260920-143012/pairs-SPANISH.jsonl",
            "20260920-143012/transcript-ENGLISH.txt",
            "20260920-143012/transcript-SPANISH.txt",
            "20260920-143012/lines.jsonl",
            "20260920-143012/meta.json",
        ]
    );
    assert_eq!(entries[5].1, raw.as_bytes());
    let m: Value = serde_json::from_slice(&entries[6].1).unwrap();
    assert_eq!(m["run_id"], "20260920-143012");
}

#[test]
fn the_relay_zone_reads_in_eastern_by_default_and_follows_daylight_saving() {
    use relay_captions::clock::{relay_zone, set_zone};
    set_zone("");
    // 2026-09-20 14:30:12 UTC is 10:30:12 in Eastern daylight time; 2026-01-15 17:00:00 UTC is 12:00 in Eastern standard time.
    assert_eq!(relay_zone(1_789_914_612.0).stamp(), "2026-09-20 10:30:12");
    assert_eq!(relay_zone(1_768_496_400.0).stamp(), "2026-01-15 12:00:00");
    set_zone("America/Phoenix");
    assert_eq!(relay_zone(1_789_914_612.0).stamp(), "2026-09-20 07:30:12", "Arizona keeps standard time all year");
    set_zone("America/Los_Angeles");
    assert_eq!(relay_zone(1_789_914_612.0).stamp(), "2026-09-20 07:30:12");
    set_zone("Europe/Paris");
    assert_eq!(relay_zone(1_789_914_612.0).stamp(), "2026-09-20 10:30:12", "a zone Relay does not offer is Eastern");
    set_zone("");
}
