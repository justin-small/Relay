use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use relay_core::future::BoxFuture;
use relay_module_ndi::multiview::Clock;
use relay_module_ndi::outputs::{Frames, Maker, Sink};
use relay_module_ndi::testpatterns::{base_picture, check_image, size_of, test_ndi_name, Burn, Deps, Tests, MAX_IMAGE};
use serde_json::{json, Value};

fn png(w: u32, h: u32, rgba: [u8; 4]) -> Vec<u8> {
    let mut out = Vec::new();
    let mut enc = png::Encoder::new(&mut out, w, h);
    enc.set_color(png::ColorType::Rgba);
    enc.set_depth(png::BitDepth::Eight);
    let mut wr = enc.write_header().expect("header");
    wr.write_image_data(&rgba.iter().copied().cycle().take((w * h * 4) as usize).collect::<Vec<u8>>()).expect("data");
    drop(wr);
    out
}

fn px(f: &[u8], w: usize, x: usize, y: usize) -> [u8; 4] {
    let i = (y * w + x) * 4;
    [f[i], f[i + 1], f[i + 2], f[i + 3]]
}

#[test]
fn only_a_small_readable_png_is_taken() {
    assert_eq!(check_image(&png(8, 8, [1, 2, 3, 255])), "");
    assert_eq!(check_image(b"GIF89a....."), "That is not a PNG picture.");
    let mut big = png(8, 8, [0; 4]);
    big.resize(MAX_IMAGE + 1, 0);
    assert_eq!(check_image(&big), "That picture is over 5 MB.");
    let mut cut = png(64, 64, [9, 9, 9, 255]);
    cut.truncate(cut.len() / 2);
    assert_eq!(check_image(&cut), "That PNG could not be read.");
    assert_eq!(check_image(b"\x89PNG\r\n\x1a\nnot really"), "That PNG could not be read.");
}

#[test]
fn your_own_picture_is_fitted_centred_and_transparent_around_it() {
    let dir = std::env::temp_dir().join(format!("relay-ndi-testpic-{}", std::process::id()));
    std::fs::create_dir_all(&dir).expect("dir");
    let path = dir.join("a.png");
    std::fs::write(&path, png(100, 50, [10, 20, 30, 255])).expect("write");
    let t = json!({"pattern": "image"});
    let f = base_picture(&t, 200, 200, &path);
    assert_eq!(f.len(), 200 * 200 * 4);
    assert_eq!(px(&f, 200, 100, 100), [30, 20, 10, 255], "the picture, in BGRA");
    assert_eq!(px(&f, 200, 100, 10)[3], 0, "above it the canvas is clear");
    assert_eq!(px(&f, 200, 100, 190)[3], 0, "and below it");
    assert_eq!(px(&f, 200, 2, 100)[3], 255, "it fills the width");
    let none = base_picture(&t, 640, 360, &dir.join("missing.png"));
    assert_eq!(px(&none, 640, 2, 2), [24, 24, 24, 255], "until a PNG is uploaded it says so on a dark ground");
    assert!(none.chunks(4).any(|p| p == [255, 255, 255, 255]), "in white text");
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn patterns_come_from_the_engine() {
    let bars = base_picture(&json!({"pattern": "bars"}), 320, 180, std::path::Path::new("none.png"));
    assert_eq!(bars.len(), 320 * 180 * 4);
    assert_eq!(size_of("3840x2160"), (3840, 2160));
    assert_eq!(size_of("nope"), (1280, 720));
    assert_eq!(test_ndi_name(&json!({"name": "Wall  A"})), "Relay - Test Wall A");
}

#[test]
fn the_burned_in_text_is_redrawn_only_when_it_changes() {
    let mut b = Burn::new(&json!({"name": "Bars"}), 1280, 720);
    let (x, y, w, h, bgra) = {
        let p = b.patch("10:00:00", 0, 30, "");
        (p.0, p.1, p.2, p.3, p.4.to_vec())
    };
    assert_eq!(y, (720.0 * 0.62) as i64, "on the lower third");
    assert_eq!(x, (1280 - w as i64) / 2, "centred");
    assert_eq!(bgra.len(), w * h * 4);
    assert_eq!(&bgra[..4], &[0, 0, 0, 200], "a dark plate");
    assert!(bgra.chunks(4).any(|p| p == [255, 255, 255, 255]), "with white text");
    let again = b.patch("10:00:00", 5, 30, "").4.to_vec();
    assert_eq!(again, bgra, "the same second draws nothing new");
    let later = b.patch("10:00:01", 30, 30, "").4.to_vec();
    assert_ne!(later, bgra, "a new second is drawn again");
    let with_channel = b.patch("10:00:01", 31, 30, "LEFT");
    assert_ne!(with_channel.2 * with_channel.3, 0);
    assert_ne!(with_channel.4.to_vec(), later, "the side that is sounding is written on it");
}

#[derive(Default)]
struct Log {
    sent: AtomicUsize,
    with_sound: AtomicUsize,
    sizes: Mutex<Vec<(usize, usize)>>,
    names: Mutex<Vec<String>>,
    refused: AtomicBool,
    closed: AtomicUsize,
}

struct S(Arc<Log>, usize);

impl Sink for S {
    fn send(&self, bgra: &[u8]) -> Result<(), String> {
        self.send_av(bgra, None)
    }
    fn send_av(&self, bgra: &[u8], audio: Option<(&[f32], usize, usize)>) -> Result<(), String> {
        assert_eq!(bgra.len(), self.1);
        if let Some((data, ch, n)) = audio {
            assert_eq!((ch, data.len()), (2, 2 * n), "planar stereo, one frame's worth");
            self.0.with_sound.fetch_add(1, Ordering::SeqCst);
        }
        self.0.sent.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    fn close(&self) {
        self.0.closed.fetch_add(1, Ordering::SeqCst);
    }
}

struct M(Arc<Log>);

impl Maker for M {
    fn frames<'a>(&'a self, _: &'a str, _: usize, _: usize, _: bool) -> BoxFuture<'a, Result<Box<dyn Frames>, String>> {
        Box::pin(async { Err("no pages here".to_owned()) })
    }
    fn sink(&self, name: &str, w: usize, h: usize, _fps: f64) -> Option<Arc<dyn Sink>> {
        if self.0.refused.load(Ordering::SeqCst) {
            return None;
        }
        self.0.names.lock().expect("lock").push(name.to_owned());
        self.0.sizes.lock().expect("lock").push((w, h));
        Some(Arc::new(S(Arc::clone(&self.0), w * h * 4)))
    }
    fn available(&self) -> String {
        String::new()
    }
}

fn tests(settings: Value, log: &Arc<Log>) -> (Tests, Arc<Mutex<Value>>) {
    let s = Arc::new(Mutex::new(settings));
    let t = Tests::new(Deps {
        maker: Arc::new(M(Arc::clone(log))),
        settings: Arc::new({
            let s = Arc::clone(&s);
            move || s.lock().expect("lock").clone()
        }),
        folder: Arc::new(|| std::env::temp_dir().join("relay-ndi-tests-folder")),
        clock: Arc::new(|| Clock { secs_of_day: 10 * 3600 }),
        notify: Arc::new(|| {}),
    });
    (t, s)
}

fn one(id: &str, name: &str, audio: &str, size: &str) -> Value {
    json!({"id": id, "name": name, "pattern": "bars", "burn": true, "audio": audio, "level": -18, "size": size, "fps": 60})
}

fn wait(what: &str, mut f: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(5);
    while !f() {
        assert!(Instant::now() < end, "timed out waiting for {what}");
        std::thread::sleep(Duration::from_millis(5)); // allow-sleep: polls a counter a thread bumps
    }
}

fn state(t: &Tests, id: &str) -> String {
    t.status().iter().find(|r| r["id"] == id).and_then(|r| r["state"].as_str().map(str::to_owned)).unwrap_or_default()
}

#[test]
fn a_test_source_sends_pictures_with_sound_as_an_ndi_source() {
    let log = Arc::new(Log::default());
    let (t, _) = tests(json!({"tests": [one("aaaa1111", "Wall", "tone", "640x360"), one("bbbb2222", "Quiet", "", "640x360")], "tests_on": ["aaaa1111", "bbbb2222"]}), &log);
    t.sync();
    wait("frames from both", || log.sent.load(Ordering::SeqCst) >= 20);
    let mut names = log.names.lock().expect("lock").clone();
    names.sort();
    assert_eq!(names, ["Relay - Test Quiet".to_owned(), "Relay - Test Wall".to_owned()]);
    assert!(log.sizes.lock().expect("lock").iter().all(|s| *s == (640, 360)));
    assert!(log.with_sound.load(Ordering::SeqCst) > 0, "the toned source carries sound");
    assert!(log.with_sound.load(Ordering::SeqCst) < log.sent.load(Ordering::SeqCst), "the silent one does not");
    assert_eq!(state(&t, "aaaa1111"), "on");
    assert_eq!(t.names(), ["Relay - Test Quiet".to_owned(), "Relay - Test Wall".to_owned()]);
    let row = t.status().into_iter().find(|r| r["id"] == "aaaa1111").expect("row");
    assert_eq!((row["on"].clone(), row["ndi"].clone(), row["has_image"].clone()), (json!(true), json!("Relay - Test Wall"), json!(false)));
    t.close();
    assert_eq!(log.closed.load(Ordering::SeqCst), 2, "closing lets go of every output");
    assert!(t.names().is_empty());
}

#[test]
fn changing_a_test_restarts_it_and_switching_it_off_stops_it() {
    let log = Arc::new(Log::default());
    let (t, s) = tests(json!({"tests": [one("aaaa1111", "Wall", "", "640x360")], "tests_on": ["aaaa1111"]}), &log);
    t.sync();
    wait("it to send", || log.sent.load(Ordering::SeqCst) >= 3);
    t.sync();
    assert_eq!(log.names.lock().expect("lock").len(), 1, "nothing changed, so it was not restarted");
    *s.lock().expect("lock") = json!({"tests": [one("aaaa1111", "Wall", "", "1280x720")], "tests_on": ["aaaa1111"]});
    t.sync();
    wait("the bigger one", || log.sizes.lock().expect("lock").contains(&(1280, 720)));
    assert_eq!(log.closed.load(Ordering::SeqCst), 1, "the old one was closed");
    *s.lock().expect("lock") = json!({"tests": [one("aaaa1111", "Wall", "", "1280x720")], "tests_on": []});
    t.sync();
    assert_eq!(state(&t, "aaaa1111"), "off");
    assert_eq!(log.closed.load(Ordering::SeqCst), 2);
    t.close();
}

#[test]
fn a_name_already_in_use_says_so() {
    let log = Arc::new(Log::default());
    log.refused.store(true, Ordering::SeqCst);
    let (t, _) = tests(json!({"tests": [one("aaaa1111", "Wall", "", "640x360")], "tests_on": ["aaaa1111"]}), &log);
    t.sync();
    wait("the error", || state(&t, "aaaa1111") == "error");
    let row = t.status().into_iter().find(|r| r["id"] == "aaaa1111").expect("row");
    assert_eq!(row["error"], "NDI would not open this source (is the name already in use?).");
    log.refused.store(false, Ordering::SeqCst);
    t.sync();
    wait("it to come back", || state(&t, "aaaa1111") == "on");
    t.close();
}

#[test]
fn a_pictures_file_goes_with_its_test() {
    let log = Arc::new(Log::default());
    let (t, _) = tests(json!({"tests": []}), &log);
    let path = t.image_path("cccc3333");
    std::fs::create_dir_all(path.parent().expect("parent")).expect("dir");
    std::fs::write(&path, png(4, 4, [1, 1, 1, 255])).expect("write");
    assert!(path.is_file());
    t.forget("cccc3333");
    assert!(!path.is_file());
    t.forget("cccc3333");
}
