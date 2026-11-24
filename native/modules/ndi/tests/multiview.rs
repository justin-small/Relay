use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use relay_module_ndi::analysis::Analyzer;
use relay_module_ndi::font;
use relay_module_ndi::multiview::{cells, countdown_text, fit, layout, seconds_until, Canvas, Clock, Env, Multiviews};
use relay_module_ndi::pool::{Changes, Lease, Pool};
use relay_module_ndi::settings::clean_settings;
use relay_module_ndi::viewer::Limits;
use relay_module_ndi::watch::{Ctx, Watch};
use relay_ndi::picture::Rgb;
use relay_ndi::watch::Kind;
use serde_json::{json, Value};

#[test]
fn layouts_have_the_boxes_python_drew() {
    assert_eq!(cells("2x2").len(), 4);
    assert_eq!(cells("3x3").len(), 9);
    assert_eq!(cells("4x4").len(), 16);
    let one_five = cells("1+5");
    assert_eq!((one_five.len(), one_five[0]), (6, (0, 0, 2, 2)), "one big box and five small, the big one first");
    assert_eq!(cells("1+7").len(), 8);
    assert!(cells("5x5").is_empty() && layout("5x5").is_none());
    let mut seen = std::collections::HashSet::new();
    for (c, r, w, h) in cells("1+7") {
        for y in r..r + h {
            for x in c..c + w {
                assert!(seen.insert((x, y)), "no two boxes cover the same cell");
            }
        }
    }
    assert_eq!(seen.len(), 16, "every cell is covered");
}

#[test]
fn countdowns_and_the_clock_read_like_python() {
    assert_eq!(countdown_text(0.0), "Starting");
    assert_eq!(countdown_text(3725.9), "1:02:05");
    assert_eq!(seconds_until("10:30", 10 * 3600 + 29 * 60), 60);
    assert_eq!(seconds_until("10:30", 10 * 3600 + 30 * 60), 86400, "already reads that time: tomorrow");
    assert_eq!(Clock { secs_of_day: 0 }.text(), "12:00:00 AM");
    assert_eq!(Clock { secs_of_day: 13 * 3600 + 5 * 60 + 9 }.text(), "1:05:09 PM");
    assert_eq!(Clock { secs_of_day: 12 * 3600 }.text(), "12:00:00 PM");
}

#[test]
fn text_is_measured_and_drawn() {
    assert_eq!(font::width("", 3), 0);
    assert_eq!(font::width("ab", 1), 11);
    assert_eq!(font::width("ab", 2), 22);
    let mut lit = 0;
    font::draw("A", 1, |x, y| {
        assert!(x < 5 && y < 8);
        lit += 1;
    });
    assert!(lit > 8, "a letter has pixels");
    let mut c = Canvas::new(60, 20);
    c.text("Hi 12", 2, 2, 1, [255, 255, 255]);
    assert!(c.rgb.chunks(3).any(|p| p == [255, 255, 255]));
    let mut unknown = 0;
    font::draw("\u{e9}", 1, |_, _| unknown += 1);
    let mut q = 0;
    font::draw("?", 1, |_, _| q += 1);
    assert_eq!(unknown, q, "anything outside ASCII is drawn as a question mark");
}

#[test]
fn a_picture_is_scaled_down_to_fit_and_never_up() {
    let wide = Rgb { width: 200, height: 100, data: vec![9; 200 * 100 * 3] };
    let got = fit(wide, 100, 100);
    assert_eq!((got.width, got.height, got.data.len()), (100, 50, 100 * 50 * 3));
    let tall = fit(Rgb { width: 100, height: 200, data: vec![9; 100 * 200 * 3] }, 100, 100);
    assert_eq!((tall.width, tall.height), (50, 100));
    let small = fit(Rgb { width: 10, height: 10, data: vec![1; 300] }, 100, 100);
    assert_eq!((small.width, small.height), (10, 10));
}

fn env(settings: Value) -> Env {
    let changes = Arc::new(Changes::default());
    let analysis = Arc::new(Analyzer::new(std::env::temp_dir().join("relay-ndi-mv-test")));
    let pool = Pool::with_opener(changes, Some(Arc::clone(&analysis)), Arc::new(|_, _| Err("no such source".to_owned())));
    let s = clean_settings(&settings);
    let limits = Limits::from_settings(&s);
    Env { pool, analysis, limits: Arc::new(move || limits), settings: Arc::new(move || s.clone()), clock: Arc::new(|| Clock { secs_of_day: 9 * 3600 }) }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_multiview_is_composed_once_and_every_browser_gets_the_same_jpeg() {
    let mvs = Multiviews::new(env(json!({"view_fps": 30, "view_width": 640, "multiviews": [
        {"id": "wall", "name": "Wall", "layout": "2x2", "boxes": [
            {"kind": "source", "source": "CAM (1)", "label": "Camera", "meter": true}, {"kind": "clock"}, {"kind": "timer", "at": "10:30"}, {"kind": "empty"}]}]})));
    assert!(mvs.open("nope").is_none(), "no such multiview");
    let a = mvs.open("wall").expect("open");
    let b = mvs.open("wall").expect("join");
    assert!(Arc::ptr_eq(&a, &b), "one compositor however many browsers");
    assert_eq!(mvs.count().get("wall"), Some(&2));
    let (seq, jpeg) = a.feed.next(0, Duration::from_secs(5)).await.expect("a composed picture");
    assert!(seq >= 1 && jpeg.starts_with(&[0xFF, 0xD8]));
    mvs.close(&a);
    assert_eq!(mvs.count().get("wall"), Some(&1), "one browser is still watching");
    mvs.close(&b);
    assert!(mvs.count().is_empty(), "the last one leaving closes it");
    mvs.close_all();
}

#[test]
fn a_composed_picture_shows_no_signal_where_nothing_arrives() {
    let mvs = Multiviews::new(env(json!({"multiviews": [{"id": "m", "name": "M", "layout": "2x2", "boxes": [{"kind": "source", "source": "X"}]}]})));
    let c = mvs.open("m").expect("open");
    let e = env(json!({}));
    let picture = c.compose(&e);
    assert_eq!((picture.w, picture.h), (1280, 720));
    assert!(picture.rgb.chunks(3).any(|p| p == [235, 235, 235]), "the words No signal are drawn in the text colour");
    mvs.close_all();
}

struct Fake {
    cfg: Mutex<Value>,
    problem: Mutex<Vec<(Kind, String)>>,
    in_service: Mutex<bool>,
    changed: AtomicUsize,
}

impl Ctx for Fake {
    fn settings(&self) -> Value {
        self.cfg.lock().map(|c| c.clone()).unwrap_or_default()
    }
    fn auto(&self) -> Vec<String> {
        vec!["AUTO".to_owned()]
    }
    fn problems(&self, _: &str, _: bool) -> Vec<(Kind, String)> {
        self.problem.lock().map(|p| p.clone()).unwrap_or_default()
    }
    fn acquire(&self, _: &str) -> Option<Lease> {
        None
    }
    fn in_service(&self, _: &str) -> bool {
        self.in_service.lock().map(|b| *b).unwrap_or(false)
    }
    fn changed(&self) {
        self.changed.fetch_add(1, Ordering::SeqCst);
    }
}

fn fake(cfg: Value) -> Fake {
    Fake { cfg: Mutex::new(cfg), problem: Mutex::default(), in_service: Mutex::new(true), changed: AtomicUsize::new(0) }
}

#[test]
fn the_watch_raises_a_problem_only_after_the_hold_and_clears_it_later() {
    let w = Watch::new();
    let f = fake(json!({"on": true, "auto": false, "sources": ["CAM"], "hold": 10, "schedule": "", "sound": true}));
    *f.problem.lock().expect("lock") = vec![(Kind::Offline, "CAM is offline".to_owned())];
    w.tick(100.0, &f);
    w.tick(105.0, &f);
    assert!(w.active().is_empty(), "a blip raises nothing");
    w.tick(111.0, &f);
    assert_eq!(w.active().first().map(|a| a["text"].clone()), Some(json!("CAM is offline")));
    assert_eq!(f.changed.load(Ordering::SeqCst), 1, "the page is told once, when it is raised");
    f.problem.lock().expect("lock").clear();
    w.tick(112.0, &f);
    assert_eq!(w.active().len(), 1, "gone for a moment is not cleared");
    w.tick(118.0, &f);
    assert!(w.active().is_empty());
    assert_eq!(f.changed.load(Ordering::SeqCst), 2);
}

#[test]
fn the_watch_follows_its_switch_the_schedule_and_the_auto_list() {
    let w = Watch::new();
    let f = fake(json!({"on": false, "auto": true, "sources": ["A"], "hold": 3, "schedule": "schedule:relay:x", "sound": false}));
    *f.problem.lock().expect("lock") = vec![(Kind::Frames, "bad".to_owned())];
    w.tick(0.0, &f);
    w.tick(10.0, &f);
    assert!(w.active().is_empty(), "switched off watches nothing");
    *f.cfg.lock().expect("lock") = json!({"on": true, "auto": true, "sources": ["A"], "hold": 3, "schedule": "schedule:relay:x", "sound": false});
    *f.in_service.lock().expect("lock") = false;
    w.tick(20.0, &f);
    w.tick(30.0, &f);
    assert!(w.active().is_empty(), "outside its schedule it watches nothing");
    *f.in_service.lock().expect("lock") = true;
    w.tick(40.0, &f);
    w.tick(50.0, &f);
    let sources: Vec<Value> = w.active().iter().map(|a| a["source"].clone()).collect();
    assert_eq!(sources, vec![json!("A"), json!("AUTO")], "saved sources and the ones the booth relies on");
    let snap = w.snapshot(&f.settings(), &f.auto());
    assert_eq!((snap["in_service"].clone(), snap["relied"].clone()), (json!(true), json!(["A", "AUTO"])));
    *f.cfg.lock().expect("lock") = json!({"on": false, "auto": true, "sources": [], "hold": 3, "schedule": "", "sound": false});
    w.tick(60.0, &f);
    assert!(w.active().is_empty(), "turning it off clears what was raised");
    assert!(!w.stop());
}
