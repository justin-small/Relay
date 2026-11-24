use std::time::{Duration, Instant};

use relay_module_ndi::analysis::{parse_meta, source_type, Analyzer};
use relay_ndi::recv::{Audio, Video};
use serde_json::{json, Value};

fn video(w: u32, h: u32, fps: f64) -> Video {
    Video { width: w, height: h, fps, fourcc: "BGRA", data: vec![128; (w * h * 4) as usize], timecode: i64::MAX, timestamp: 0, metadata: String::new() }
}

fn audio(channels: usize, samples: usize, level: f32) -> Audio {
    Audio { rate: 48000, channels, samples, data: vec![level; channels * samples], peak_db: -6.0, levels: vec![(-6.0, -9.0); channels], timecode: i64::MAX, timestamp: 0 }
}

fn analyzer() -> (Analyzer, std::path::PathBuf) {
    let dir = std::env::temp_dir().join(format!("relay-ndi-analysis-{}-{:?}", std::process::id(), std::thread::current().id()));
    let _ = std::fs::remove_dir_all(&dir);
    (Analyzer::new(dir.clone()), dir)
}

/// Frames arrive at `fps`, stamped on the monotonic clock the analyzer uses (`now` back-dated), so the test does not wait.
fn feed_steady(a: &Analyzer, key: &str, n: usize, fps: f64) {
    let v = video(16, 8, fps);
    let start = Instant::now() - Duration::from_secs_f64(n as f64 / fps);
    for i in 0..n {
        a.on_video(key, key, false, &v, start + Duration::from_secs_f64(i as f64 / fps));
    }
}

#[test]
fn a_steady_source_is_healthy_and_an_unknown_one_is_not() {
    let (a, _) = analyzer();
    assert_eq!(a.health("nothing").0, "unknown");
    feed_steady(&a, "CAM", 120, 30.0);
    let (state, why) = a.health("CAM");
    assert_eq!((state, why.as_str()), ("ok", "Steady"));
}

#[test]
fn a_dropping_source_turns_bad() {
    let (a, _) = analyzer();
    feed_steady(&a, "CAM", 120, 30.0);
    a.on_perf("CAM", 5);
    assert_eq!(a.health("CAM"), ("bad", "Dropping frames".to_owned()));
}

#[test]
fn the_snapshot_has_the_shape_the_page_reads() {
    let (a, _) = analyzer();
    feed_steady(&a, "CAM", 60, 30.0);
    let rec = json!({"source": "Cam 1 NDI HX2", "machine": "STUDIO", "address": "10.0.0.5", "port": 5961, "groups": ["public"], "width": 16, "height": 8, "fps": 30});
    let s = a.snapshot("CAM", "CAM", &rec, true);
    assert_eq!(s["live"], true);
    assert_eq!(s["info"]["type"], "NDI|HX");
    assert_eq!(s["info"]["host"], "10.0.0.5");
    assert_eq!(s["info"]["format"]["width"], 16);
    assert!(s["timing"]["measured_fps"].as_f64().is_some_and(|f| (f - 30.0).abs() < 0.5));
    assert!(s["timing"]["interval_ms"]["avg"].is_number());
    assert_eq!(s["scte"]["total"], 0);
    assert!(s["picture"].is_null(), "no scopes before an analysis is open");
    let gone = a.snapshot("OTHER", "OTHER", &Value::Null, true);
    assert_eq!((gone["live"].clone(), gone["health"].clone()), (json!(false), json!("unknown")));
}

#[test]
fn scopes_are_computed_only_while_watched() {
    let (a, _) = analyzer();
    a.watch("CAM", true);
    feed_steady(&a, "CAM", 5, 30.0);
    let s = a.snapshot("CAM", "CAM", &Value::Null, true);
    assert!(s["picture"]["waveform"]["b64"].is_string(), "watched: scopes drawn");
    assert_eq!(s["picture"]["parade"].as_array().map(Vec::len), Some(3));
    assert!(a.snapshot("CAM", "CAM", &Value::Null, false)["picture"].is_null(), "scopes=false leaves them out");
    a.watch("CAM", false);
    assert!(a.snapshot("CAM", "CAM", &Value::Null, true)["picture"].is_null());
}

#[test]
fn audio_levels_age_out_and_loudness_runs_only_while_asked() {
    let (a, _) = analyzer();
    assert!(a.audio_level("CAM").is_none());
    a.on_audio("CAM", "CAM", false, &audio(2, 960, 0.25));
    assert_eq!(a.audio_level("CAM"), Some(-6.0));
    assert_eq!(a.audio_channels("CAM"), 2);
    let st = a.audio_state("CAM");
    assert_eq!(st["channels"].as_array().map(Vec::len), Some(2));
    assert_eq!(st["audio_only"], false);
    a.on_audio("CAM", "CAM", true, &audio(2, 960, 0.25));
    assert_eq!(a.audio_channels("LOWONLY"), 0, "a low-bandwidth receiver's sound is not the viewer's level");
}

#[test]
fn sound_without_a_picture_is_audio_only_after_a_while() {
    let (a, _) = analyzer();
    a.on_audio("MIC", "MIC", false, &audio(1, 480, 0.1));
    assert!(!a.audio_only("MIC"), "not at once");
}

#[test]
fn a_recording_writes_a_csv_and_stops() {
    let (a, dir) = analyzer();
    assert!(a.record("CAM", 5).is_none(), "nothing to record before the source is received");
    feed_steady(&a, "CAM", 10, 30.0);
    let rec = a.record("CAM", 5).expect("recording");
    let v = video(16, 8, 30.0);
    for i in 0..4 {
        a.on_video("CAM", "CAM", false, &v, Instant::now() + Duration::from_millis(i * 33));
    }
    assert!(rec.status()["rows"].as_u64().is_some_and(|r| r >= 3));
    a.stop_recording("CAM");
    assert!(!rec.active());
    let text = std::fs::read_to_string(&rec.path).expect("csv");
    assert!(text.starts_with("seconds,interval_ms,frame_bytes,width,height,flag\n"));
    assert!(text.lines().nth(1).is_some_and(|l| l.split(',').count() == 6));
    assert!(rec.path.starts_with(&dir) && rec.path.extension().is_some_and(|e| e == "csv"));
    assert!(a.last_recording("CAM").is_some());
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn what_a_sender_says_about_itself() {
    let m = parse_meta(r#"<ndi_product long_name="Cam" model_name="X1" version="2.0" serial="9"/><ndi_capabilities ntk_ptz="true" ntk_kvm="false"/>"#);
    assert_eq!(m["name"], "Cam");
    assert_eq!(m["model"], "X1");
    assert_eq!((m["ptz"].clone(), m["kvm"].clone(), m["recordable"].clone()), (json!(true), json!(false), json!(false)));
    assert!(parse_meta("<other/>").is_empty());
    assert!(parse_meta("<ndi_productx a=\"1\"/>").is_empty(), "a longer tag name is not the product tag");
    assert_eq!(source_type("NDI HX2", ""), "NDI|HX");
    assert_eq!(source_type("OBS", "HXLAB"), "NDI");
}

#[test]
fn dropping_a_receiver_forgets_its_numbers() {
    let (a, _) = analyzer();
    feed_steady(&a, "CAM", 10, 30.0);
    assert!(a.has("CAM"));
    a.drop_key("CAM");
    assert!(!a.has("CAM"));
    assert_eq!(a.health("CAM").0, "unknown");
}
