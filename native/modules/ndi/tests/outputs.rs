use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use relay_core::module::BoxFuture;
use relay_module_ndi::outputs::{builtin_defs, is_page, page_url, size_of, Deps, Frames, Maker, Outputs, Sink};
use relay_module_ndi::render::Bgra;
use serde_json::{json, Value};

#[test]
fn the_built_in_pages_follow_the_configuration() {
    let cfg = |k: &str| -> Option<Value> {
        match k {
            "source_language" => Some(json!("ENGLISH")),
            "overlays" => Some(json!([{"id": "lt", "name": "Lower third"}, {"name": "no id"}])),
            "boards" => Some(json!([{"id": "b1", "name": "Lobby", "public": true}, {"id": "b2", "name": "Private"}])),
            _ => None,
        }
    };
    let defs = builtin_defs(&cfg);
    let ids: Vec<&str> = defs.iter().filter_map(|d| d["id"].as_str()).collect();
    assert_eq!(ids[0], "screen:source");
    assert!(ids.contains(&"screen:SPANISH") && !ids.contains(&"screen:ENGLISH"), "the source language is not offered as a target");
    assert!(ids.contains(&"overlay:default") && ids.contains(&"overlay:lt") && !ids.iter().any(|i| i.contains("no id")));
    assert!(ids.contains(&"board:b1") && !ids.contains(&"board:b2"), "a board that needs a sign-in is left out");
    let by = |id: &str| defs.iter().find(|d| d["id"] == id).cloned().expect("def");
    assert_eq!((by("overlay:lt")["alpha"].clone(), by("overlay:lt")["url"].clone(), by("overlay:lt")["name"].clone()), (json!(true), json!("/overlay/lt"), json!("Overlay (Lower third)")));
    assert_eq!(by("screen:SPANISH")["name"], "Screen (Spanish)");
    assert_eq!(by("board:b1")["alpha"], false);
}

#[test]
fn only_pages_relay_serves_can_be_drawn() {
    assert!(is_page("/overlay"));
    assert!(!is_page("//evil.example/x"));
    assert!(!is_page("https://example.com/"));
    assert!(is_page("http://127.0.0.1:8090/signage/lobby"));
    assert!(!is_page("http://127.0.0.1/x"), "a local address needs its port");
    assert!(!is_page("http://127.0.0.1:123456/x"));
    assert!(!is_page("http://127.0.0.1.evil.example:80/x"));
    assert_eq!(page_url("http://127.0.0.1:80", "/overlay"), "http://127.0.0.1:80/overlay");
    assert_eq!(page_url("http://127.0.0.1:80", "http://127.0.0.1:8090/signage/x"), "http://127.0.0.1:8090/signage/x");
    assert_eq!(size_of("1920x1080"), (1920, 1080));
    assert_eq!(size_of("nonsense"), (1280, 720));
}

#[derive(Default)]
struct Log {
    sent: AtomicUsize,
    opened: Mutex<Vec<(String, usize, usize, f64)>>,
    urls: Mutex<Vec<(String, bool)>>,
    closed: AtomicUsize,
    sink_refused: AtomicBool,
    no_chrome: AtomicBool,
}

struct FakeFrames(usize, usize, Arc<Log>);

impl Frames for FakeFrames {
    fn frame<'a>(&'a self, _wait: Duration) -> BoxFuture<'a, Result<Bgra, String>> {
        Box::pin(async move { Ok(Bgra { width: self.0, height: self.1, data: vec![7; self.0 * self.1 * 4] }) })
    }
    fn close(self: Box<Self>) -> BoxFuture<'static, ()> {
        Box::pin(async move {
            self.2.closed.fetch_add(1, Ordering::SeqCst);
        })
    }
}

struct FakeSink(Arc<Log>, usize);

impl Sink for FakeSink {
    fn send(&self, bgra: &[u8]) -> Result<(), String> {
        assert_eq!(bgra.len(), self.1, "a frame is width x height x 4 bytes");
        self.0.sent.fetch_add(1, Ordering::SeqCst);
        Ok(())
    }
    fn close(&self) {}
}

struct FakeMaker(Arc<Log>);

impl Maker for FakeMaker {
    fn frames<'a>(&'a self, url: &'a str, w: usize, h: usize, alpha: bool) -> BoxFuture<'a, Result<Box<dyn Frames>, String>> {
        Box::pin(async move {
            self.0.urls.lock().expect("lock").push((url.to_owned(), alpha));
            Ok(Box::new(FakeFrames(w, h, Arc::clone(&self.0))) as Box<dyn Frames>)
        })
    }
    fn sink(&self, name: &str, w: usize, h: usize, fps: f64) -> Option<Arc<dyn Sink>> {
        if self.0.sink_refused.load(Ordering::SeqCst) {
            return None;
        }
        self.0.opened.lock().expect("lock").push((name.to_owned(), w, h, fps));
        Some(Arc::new(FakeSink(Arc::clone(&self.0), w * h * 4)))
    }
    fn available(&self) -> String {
        if self.0.no_chrome.load(Ordering::SeqCst) { "Page outputs need Chrome or Chromium.".to_owned() } else { String::new() }
    }
}

fn outputs(settings: Value, log: &Arc<Log>) -> (Outputs, Arc<Mutex<Value>>) {
    let s = Arc::new(Mutex::new(settings));
    let o = Outputs::new(Deps {
        maker: Arc::new(FakeMaker(Arc::clone(log))),
        settings: Arc::new({
            let s = Arc::clone(&s);
            move || s.lock().expect("lock").clone()
        }),
        config: Arc::new(|k| (k == "boards").then(|| json!([{"id": "b", "name": "Lobby", "public": true}]))),
        providers: Arc::new(|| vec![json!({"id": "signage:lobby", "name": "Lobby TV", "group": "Signage", "url": "http://127.0.0.1:8090/signage/lobby"}), json!({"id": "bad", "url": "https://x"}), json!({"id": "board:b", "name": "dup", "url": "/x"})]),
        base: Arc::new(|| "http://127.0.0.1:80".to_owned()),
        notify: Arc::new(|| {}),
    });
    (o, s)
}

async fn until(what: &str, mut f: impl FnMut() -> bool) {
    let end = Instant::now() + Duration::from_secs(5);
    while !f() {
        assert!(Instant::now() < end, "timed out waiting for {what}");
        tokio::time::sleep(Duration::from_millis(10)).await; // allow-sleep: polls a counter a task bumps
    }
}

fn state(o: &Outputs, id: &str) -> String {
    o.status().iter().find(|d| d["id"] == id).and_then(|d| d["state"].as_str().map(str::to_owned)).unwrap_or_default()
}

#[test]
fn other_modules_pages_are_listed_once_and_only_when_drawable() {
    let log = Arc::new(Log::default());
    let (o, _) = outputs(json!({}), &log);
    let ids: Vec<String> = o.defs().iter().filter_map(|d| d["id"].as_str().map(str::to_owned)).collect();
    assert!(ids.contains(&"signage:lobby".to_owned()));
    assert!(!ids.contains(&"bad".to_owned()), "a page outside Relay is refused");
    assert_eq!(ids.iter().filter(|i| *i == "board:b").count(), 1, "a provider cannot take a built-in id");
}

#[tokio::test(flavor = "multi_thread")]
async fn an_output_that_is_on_sends_frames_as_an_ndi_source() {
    let log = Arc::new(Log::default());
    let (o, _) = outputs(json!({"out_on": ["overlay:default"], "out_size": "1280x720", "out_fps": 30}), &log);
    o.sync().await;
    until("frames to be sent", || log.sent.load(Ordering::SeqCst) >= 3).await;
    assert_eq!(log.opened.lock().expect("lock").as_slice(), [("Relay - Overlay".to_owned(), 1280, 720, 30.0)]);
    assert_eq!(log.urls.lock().expect("lock").as_slice(), [("http://127.0.0.1:80/overlay".to_owned(), true)], "the overlay is drawn with alpha");
    assert_eq!(state(&o, "overlay:default"), "on");
    assert_eq!(o.names(), vec!["Relay - Overlay".to_owned()]);
    assert_eq!(state(&o, "screen:source"), "off");
    o.close().await;
    assert_eq!(log.closed.load(Ordering::SeqCst), 1, "closing lets go of the browser");
    assert!(o.names().is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn changing_the_size_or_switching_off_restarts_or_stops() {
    let log = Arc::new(Log::default());
    let (o, s) = outputs(json!({"out_on": ["screen:source", "signage:lobby"], "out_size": "1280x720", "out_fps": 30}), &log);
    o.sync().await;
    until("both to be on", || state(&o, "screen:source") == "on" && state(&o, "signage:lobby") == "on").await;
    assert_eq!(log.urls.lock().expect("lock").iter().filter(|u| u.0.contains("8090")).count(), 1, "a signage address is used as it is");
    o.sync().await;
    assert_eq!(log.opened.lock().expect("lock").len(), 2, "nothing changed, so nothing restarted");
    *s.lock().expect("lock") = json!({"out_on": ["screen:source"], "out_size": "1920x1080", "out_fps": 30});
    o.sync().await;
    until("the bigger output", || log.opened.lock().expect("lock").iter().any(|x| x.1 == 1920)).await;
    assert_eq!(state(&o, "signage:lobby"), "off", "an output switched off stops");
    until("the old output to close", || log.closed.load(Ordering::SeqCst) >= 2).await;
    o.close().await;
}

#[tokio::test(flavor = "multi_thread")]
async fn an_output_that_cannot_start_says_why() {
    let log = Arc::new(Log::default());
    let (o, _) = outputs(json!({"out_on": ["overlay:default"], "out_fps": 30}), &log);
    log.no_chrome.store(true, Ordering::SeqCst);
    o.sync().await;
    until("the error", || state(&o, "overlay:default") == "error").await;
    let row = o.status().into_iter().find(|d| d["id"] == "overlay:default").expect("row");
    assert_eq!(row["error"], "Page outputs need Chrome or Chromium.");
    log.no_chrome.store(false, Ordering::SeqCst);
    log.sink_refused.store(true, Ordering::SeqCst);
    o.sync().await;
    until("the sink error", || o.status().iter().any(|d| d["id"] == "overlay:default" && d["error"].as_str().is_some_and(|e| e.contains("already in use")))).await;
    assert_eq!(log.closed.load(Ordering::SeqCst), 1, "the browser is let go when NDI will not open the output");
    o.close().await;
}

/// Real Chromium drawing the page, a fake NDI sink counting what arrives: the whole output path but the NDI library.
struct ChromeMaker(Arc<Log>);

impl Maker for ChromeMaker {
    fn frames<'a>(&'a self, _url: &'a str, w: usize, h: usize, alpha: bool) -> BoxFuture<'a, Result<Box<dyn Frames>, String>> {
        Box::pin(async move {
            let html = "<html><body style='margin:0;background:rgb(10,20,200)'></body></html>";
            let enc: String = html.bytes().map(|b| format!("%{b:02X}")).collect();
            let r = relay_module_ndi::render::Renderer::open(&format!("data:text/html,{enc}"), w, h, alpha).await?;
            Ok(Box::new(r) as Box<dyn Frames>)
        })
    }
    fn sink(&self, name: &str, w: usize, h: usize, fps: f64) -> Option<Arc<dyn Sink>> {
        FakeMaker(Arc::clone(&self.0)).sink(name, w, h, fps)
    }
    fn available(&self) -> String {
        relay_module_ndi::render::available().to_owned()
    }
}

#[tokio::test(flavor = "multi_thread")]
async fn a_real_browser_page_reaches_the_sink_at_the_asked_size() {
    if !relay_module_ndi::render::available().is_empty() {
        eprintln!("skipped: no Chrome or Chromium installed");
        return;
    }
    let log = Arc::new(Log::default());
    let s = Arc::new(Mutex::new(json!({"out_on": ["screen:source"], "out_size": "1280x720", "out_fps": 30})));
    let o = Outputs::new(Deps {
        maker: Arc::new(ChromeMaker(Arc::clone(&log))),
        settings: Arc::new({
            let s = Arc::clone(&s);
            move || s.lock().expect("lock").clone()
        }),
        config: Arc::new(|_| None),
        providers: Arc::new(Vec::new),
        base: Arc::new(|| "http://127.0.0.1:80".to_owned()),
        notify: Arc::new(|| {}),
    });
    o.sync().await;
    let end = Instant::now() + Duration::from_secs(20);
    while log.sent.load(Ordering::SeqCst) < 30 {
        assert!(Instant::now() < end, "only {} frames reached the sink; state {}", log.sent.load(Ordering::SeqCst), state(&o, "screen:source"));
        tokio::time::sleep(Duration::from_millis(50)).await; // allow-sleep: polls a counter a task bumps
    }
    assert_eq!(state(&o, "screen:source"), "on");
    o.close().await;
}
