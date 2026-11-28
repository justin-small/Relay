//! What `tests/test_signage_web.py` checks of the Python module: link parsing, what the player is told, the framing and embedding checks,
//! the admin routes and the sources signage plays from, with signage's own side stood in for by what it asks of a source. Nothing touches a
//! network: the fetch is replaced, and the real fetcher is shown to refuse private addresses and to read an answer off an open stream.

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{Method, Request, StatusCode};
use axum::response::Response;
use bytes::Bytes;
use http_body_util::BodyExt;
use relay_core::future::BoxFuture;
use relay_core::screens::{SignageSource, SIGNAGE_SOURCE};
use relay_core::{Authorizer, Host, Module, SettingsStore, Shutdown};
use relay_module_signage_web::check::{self, exchange, Fetch, FetchError, Reply, Web};
use relay_module_signage_web::links::{self, clean_link, clean_stored, frame_verdict, parse_youtube, resolve_web, resolve_youtube};
use relay_module_signage_web::SignageWeb;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tower::ServiceExt;

const V: &str = "dQw4w9WgXcQ";
const L: &str = "PLabcdefghijk123";

fn h(pairs: &[(&str, &str)]) -> Vec<(String, String)> {
    pairs.iter().map(|(k, v)| ((*k).to_owned(), (*v).to_owned())).collect()
}

fn yt(url: &str) -> Option<(String, String)> {
    parse_youtube(url).map(|y| (y.video, y.list))
}

fn pair(v: &str, l: &str) -> Option<(String, String)> {
    Some((v.to_owned(), l.to_owned()))
}

#[test]
fn youtube_addresses_name_a_video_or_a_playlist() {
    for url in [format!("https://www.youtube.com/watch?v={V}"), format!("https://youtu.be/{V}?t=4"), format!("https://www.youtube.com/embed/{V}"), format!("https://www.youtube.com/shorts/{V}"), format!("https://m.youtube.com/watch?v={V}&feature=share")] {
        assert_eq!(yt(&url), pair(V, ""), "{url}");
    }
    assert_eq!(yt(&format!("https://www.youtube.com/playlist?list={L}")), pair("", L));
    assert_eq!(yt(&format!("https://www.youtube.com/watch?v={V}&list={L}")), pair(V, L));
    assert_eq!(yt(&format!("https://www.youtube.com/watch?v={V}&list=RDdQw4w9WgXcQ")), pair(V, ""), "a mix is not a playlist");
    assert_eq!(yt("https://www.youtube.com/"), None);
    assert_eq!(yt(&format!("https://evil.example/watch?v={V}")), None);
    assert_eq!(yt(&format!("https://youtube.com.evil.example/watch?v={V}")), None);
}

#[test]
fn links_are_cleaned_and_bad_ones_refused() {
    let none = HashSet::new();
    assert_eq!(clean_link(&json!({"url": "example.org/menu"}), &none).unwrap()["url"], "https://example.org/menu");
    assert_eq!(clean_link(&json!({"url": "example.org:8080/x"}), &none).unwrap()["url"], "https://example.org:8080/x");
    for bad in ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.org/a", "", "https://u:p@example.org/", "https://example.org/a b", "https://www.youtube.com/"] {
        assert!(clean_link(&json!({"url": bad}), &none).is_err(), "{bad}");
    }
    assert_eq!(clean_link(&json!("x"), &none).unwrap_err().0, "A link must be an object.");
    assert_eq!(clean_link(&json!({"url": "https://u:p@example.org/"}), &none).unwrap_err().0, "A web address with a user name or password in it can't be used.");
    assert_eq!(clean_link(&json!({"url": "https://www.youtube.com/"}), &none).unwrap_err().0, "That YouTube address names no video or playlist.");
    assert!(clean_link(&json!({"url": format!("https://example.org/{}", "a".repeat(500))}), &none).is_err());
    let a = clean_link(&json!({"url": format!("https://youtu.be/{V}"), "name": "  Welcome   loop "}), &none).unwrap();
    assert_eq!((a["kind"].as_str(), a["name"].as_str()), (Some("youtube"), Some("Welcome loop")));
    let mut a_with_id = a.clone();
    a_with_id["id"] = json!("aaaaaaaa");
    let stored = clean_stored(&json!({"links": [a_with_id.clone(), a_with_id, {"url": "javascript:x"}, "x"]}));
    let ids: Vec<&str> = stored["links"].as_array().unwrap().iter().map(|l| l["id"].as_str().unwrap()).collect();
    assert_eq!(ids, ["aaaaaaaa"], "stored links are cleaned and deduplicated");
    assert_eq!(stored["links"][0]["name"], "Welcome loop");
    let nameless = clean_stored(&json!({"links": [{"url": "https://example.org/", "id": "bbbbbbbb", "check": {"state": "weird", "note": "n"}}]}));
    assert_eq!(nameless["links"][0]["name"], "https://example.org/", "no name falls back to the address");
    assert_eq!(nameless["links"][0]["check"], json!({"state": "", "note": "n", "at": ""}));
    let many: Vec<Value> = (0..300).map(|i| json!({"url": format!("https://example.org/{i}")})).collect();
    assert_eq!(clean_stored(&json!({"links": many}))["links"].as_array().unwrap().len(), 200);
}

#[test]
fn the_player_is_told_what_to_show() {
    let r = resolve_web("https://example.org/menu?x=1");
    assert_eq!((r["type"].as_str(), r["url"].as_str(), r["probe"].as_str()), (Some("page"), Some("https://example.org/menu?x=1"), Some("https://example.org/")));
    assert_eq!(resolve_web("https://example.org:8443/a")["probe"], "https://example.org:8443/");
    let r = resolve_youtube(&format!("https://youtu.be/{V}"));
    assert!(r["url"].as_str().unwrap().starts_with(&format!("https://www.youtube-nocookie.com/embed/{V}?")));
    assert!(r["url"].as_str().unwrap().contains("mute=1") && !r["url_sound"].as_str().unwrap().contains("mute"));
    assert_eq!(r["post_message"]["event"], "listening");
    assert!(r["end_when"].is_array());
    assert_eq!(r["probe"], "https://www.youtube-nocookie.com/favicon.ico");
    let r = resolve_youtube(&format!("https://www.youtube.com/playlist?list={L}"));
    let url = r["url"].as_str().unwrap();
    assert!(url.contains("/embed/videoseries?") && url.contains(&format!("list={L}")) && url.contains("loop=1"));
    assert!(r.get("end_when").is_none(), "a playlist runs for the item's seconds");
    let r = resolve_youtube(&format!("https://www.youtube.com/watch?v={V}&list={L}"));
    assert!(r.get("end_when").is_none() && r["url"].as_str().unwrap().contains(&format!("/embed/{V}?")));
}

#[test]
fn framing_headers_decide_whether_a_page_can_be_shown() {
    assert_eq!(frame_verdict(&[]).0, "ok");
    assert_eq!(frame_verdict(&h(&[("X-Frame-Options", "SAMEORIGIN")])).0, "blocked");
    assert_eq!(frame_verdict(&h(&[("x-frame-options", "  ")])).0, "ok");
    assert_eq!(frame_verdict(&h(&[("Content-Security-Policy", "default-src *; frame-ancestors 'none'")])).0, "blocked");
    assert_eq!(frame_verdict(&h(&[("Content-Security-Policy", "frame-ancestors 'self'")])).0, "blocked");
    assert_eq!(frame_verdict(&h(&[("Content-Security-Policy", "Frame-Ancestors *")])).0, "ok");
    assert_eq!(frame_verdict(&h(&[("Content-Security-Policy", "frame-ancestors https://a.org")])).0, "unsure");
    assert_eq!(frame_verdict(&h(&[("Content-Security-Policy", "frame-ancestors")])).0, "blocked");
}

/// Answers by address without its query; "oembed" for any oEmbed call.
#[derive(Default)]
struct Answers(Mutex<HashMap<String, Result<Reply, FetchError>>>);

impl Answers {
    fn set(&self, url: &str, a: Result<Reply, FetchError>) {
        self.0.lock().unwrap().insert(url.to_owned(), a);
    }
}

impl Fetch for Answers {
    fn get<'a>(&'a self, url: &'a str) -> BoxFuture<'a, Result<Reply, FetchError>> {
        Box::pin(async move {
            let key = if url.contains("oembed") { "oembed" } else { url.split('?').next().unwrap_or("") };
            self.0.lock().unwrap().get(key).cloned().unwrap_or(Err(FetchError::Unreachable))
        })
    }
}

fn reply(status: u16, headers: &[(&str, &str)], body: &str) -> Result<Reply, FetchError> {
    Ok(Reply { status, headers: h(headers), body: Bytes::from(body.to_owned()) })
}

fn web_link(url: &str) -> Value {
    clean_link(&json!({"url": url}), &HashSet::new()).unwrap()
}

async fn state_of(a: &Answers, link: &Value) -> &'static str {
    check::check(a, link).await.state
}

#[tokio::test]
async fn a_site_is_checked_for_framing() {
    let a = Answers::default();
    let link = web_link("https://example.org/menu");
    a.set("https://example.org/menu", reply(200, &[("X-Frame-Options", "DENY")], ""));
    assert_eq!(state_of(&a, &link).await, "blocked");
    a.set("https://example.org/menu", reply(200, &[], ""));
    assert_eq!(state_of(&a, &link).await, "ok");
    a.set("https://example.org/menu", Err(FetchError::Unreachable));
    assert_eq!(state_of(&a, &link).await, "offline");
    a.set("https://example.org/menu", Err(FetchError::Private));
    assert_eq!(state_of(&a, &link).await, "unsure");
    a.set("https://example.org/menu", reply(503, &[], ""));
    let v = check::check(&a, &link).await;
    assert_eq!((v.state, v.note.as_str()), ("ok", "The site answered with an error (503), but allows framing."));
    a.set("https://example.org/menu", reply(404, &[("x-frame-options", "DENY")], ""));
    assert_eq!(state_of(&a, &link).await, "blocked", "an error page that refuses framing is still blocked");
}

#[tokio::test]
async fn youtube_is_checked_through_oembed() {
    let a = Answers::default();
    let link = web_link(&format!("https://youtu.be/{V}"));
    a.set("oembed", reply(200, &[], &json!({"title": "Welcome"}).to_string()));
    let v = check::check(&a, &link).await;
    assert_eq!((v.state, v.title.as_str()), ("ok", "Welcome"));
    for (code, state) in [(401, "blocked"), (403, "blocked"), (404, "gone"), (400, "gone"), (500, "unsure")] {
        a.set("oembed", reply(code, &[], ""));
        assert_eq!(check::check(&a, &link).await.state, state, "{code}");
    }
    a.set("oembed", Err(FetchError::Unreachable));
    assert_eq!(check::check(&a, &link).await.state, "offline");
    a.set("oembed", reply(200, &[], "not json"));
    assert_eq!(check::check(&a, &link).await.state, "offline");
}

#[tokio::test]
async fn the_real_fetcher_refuses_private_addresses() {
    let web = Web::new(relay_core::Net::unrouted());
    for url in ["http://127.0.0.1:9/", "http://192.168.1.20/", "http://169.254.169.254/latest/", "http://[::1]/", "ftp://example.org/", "file:///etc/passwd"] {
        assert_eq!(web.get(url).await.unwrap_err(), FetchError::Private, "{url}");
    }
    assert_eq!(check::check_web(&web, "http://127.0.0.1:9/").await.state, "unsure");
}

#[tokio::test]
async fn an_answer_is_read_off_an_open_stream() {
    let (client, mut server) = tokio::io::duplex(64 * 1024);
    let serving = tokio::spawn(async move {
        let mut seen = vec![0u8; 4096];
        let n = server.read(&mut seen).await.unwrap();
        server.write_all(b"HTTP/1.1 200 OK\r\nX-Frame-Options: DENY\r\nContent-Length: 5\r\nConnection: close\r\n\r\nhello").await.unwrap();
        String::from_utf8_lossy(&seen[..n]).into_owned()
    });
    let got = exchange(client, "example.org", "/menu?x=1").await.unwrap();
    assert_eq!((got.status, got.header("x-frame-options"), &got.body[..]), (200, Some("DENY"), &b"hello"[..]));
    let sent = serving.await.unwrap();
    assert!(sent.starts_with("GET /menu?x=1 HTTP/1.1\r\n") && sent.to_lowercase().contains("host: example.org\r\n") && sent.contains("user-agent: Relay-signage-check"), "{sent}");
}

struct Settings(Mutex<HashMap<String, Value>>);

impl SettingsStore for Settings {
    fn read(&self, key: &str) -> Option<Value> {
        self.0.lock().unwrap().get(key).cloned()
    }
    fn save(&self, key: &str, changes: &Value) -> Result<(), relay_core::AppError> {
        let mut m = self.0.lock().unwrap();
        let mut cur = m.get(key).cloned().unwrap_or_else(|| json!({}));
        for (k, v) in changes.as_object().into_iter().flatten() {
            cur[k] = v.clone();
        }
        m.insert(key.to_owned(), cur);
        Ok(())
    }
    fn add_secret(&self, _key: &str, _field: &str) {}
}

/// Who may do what, and the audit entries kept.
#[derive(Default)]
struct Gate {
    refuse: Mutex<Vec<&'static str>>,
    audit: Mutex<Vec<(String, String, Option<Value>)>>,
}

impl Authorizer for Gate {
    fn require<'a>(&'a self, _r: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async move {
            if self.refuse.lock().unwrap().contains(&permission) {
                let mut r = Response::new(Body::empty());
                *r.status_mut() = StatusCode::FORBIDDEN;
                return Err(r);
            }
            Ok(())
        })
    }
    fn note<'a>(&'a self, _r: &'a Parts, action: &'a str, _s: &'a str, target: &'a str, detail: Option<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move { self.audit.lock().unwrap().push((action.to_owned(), target.to_owned(), detail)) })
    }
}

struct Rig {
    module: SignageWeb,
    host: Host,
    answers: Arc<Answers>,
    gate: Arc<Gate>,
    router: axum::Router,
}

struct Shared(Arc<Answers>);

impl Fetch for Shared {
    fn get<'a>(&'a self, url: &'a str) -> BoxFuture<'a, Result<Reply, FetchError>> {
        self.0.get(url)
    }
}

fn rig() -> Rig {
    let answers = Arc::new(Answers::default());
    let settings = Arc::new(Settings(Mutex::new(HashMap::new())));
    let host = Host::new(Shutdown::new()).with_store(settings);
    let gate = Arc::new(Gate::default());
    host.set_authorizer(gate.clone());
    let module = SignageWeb::with_fetcher(Arc::new(Shared(answers.clone())));
    module.attach(&host);
    let router = module.routes();
    Rig { module, host, answers, gate, router }
}

async fn send(r: &Rig, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value) {
    let req = Request::builder().method(method).uri(uri).header("content-type", "application/json").body(body.map_or_else(Body::empty, |b| Body::from(b.to_string()))).unwrap();
    let res = r.router.clone().oneshot(req).await.unwrap();
    let status = res.status();
    let bytes = res.into_body().collect().await.unwrap().to_bytes();
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
}

#[tokio::test]
async fn the_admin_routes_add_change_check_and_remove_links() {
    let r = rig();
    r.answers.set("https://example.org/menu", reply(200, &[], ""));
    r.answers.set("oembed", reply(200, &[], &json!({"title": "Welcome video"}).to_string()));
    let (st, web) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": "https://example.org/menu", "name": ""}))).await;
    assert_eq!((st, web["kind"].as_str(), web["check"]["state"].as_str()), (StatusCode::OK, Some("web"), Some("ok")));
    assert_eq!(web["name"], "https://example.org/menu", "an address is the name when none is typed");
    assert!(web["check"]["at"].as_str().unwrap().ends_with('Z'));
    let web_id = web["id"].as_str().unwrap().to_owned();
    let (st, video) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": format!("https://youtu.be/{V}")}))).await;
    assert_eq!((st, video["name"].as_str()), (StatusCode::OK, Some("Welcome video")), "a video takes YouTube's title");
    let yt_id = video["id"].as_str().unwrap().to_owned();
    let (st, bad) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": "javascript:alert(1)"}))).await;
    assert_eq!((st, bad["error"].as_str()), (StatusCode::BAD_REQUEST, Some("Enter a web address starting with http:// or https://.")));
    let (st, _) = send(&r, Method::POST, "/api/admin/signage-web/links", None).await;
    assert_eq!(st, StatusCode::UNPROCESSABLE_ENTITY);
    let (_, list) = send(&r, Method::GET, "/api/admin/signage-web", None).await;
    let kinds: Vec<&str> = list["links"].as_array().unwrap().iter().map(|l| l["kind"].as_str().unwrap()).collect();
    assert_eq!(kinds, ["web", "youtube"]);

    let (_, renamed) = send(&r, Method::PUT, &format!("/api/admin/signage-web/links/{web_id}"), Some(json!({"name": "Menu"}))).await;
    assert_eq!((renamed["name"].as_str(), renamed["check"]["state"].as_str()), (Some("Menu"), Some("ok")), "a rename does not check again");
    r.answers.set("https://example.org/menu", reply(200, &[("X-Frame-Options", "DENY")], ""));
    let (_, same) = send(&r, Method::PUT, &format!("/api/admin/signage-web/links/{web_id}"), Some(json!({"name": "Menu 2"}))).await;
    assert_eq!(same["check"]["state"], "ok");
    let (_, moved) = send(&r, Method::PUT, &format!("/api/admin/signage-web/links/{web_id}"), Some(json!({"url": "https://example.org/menu"}))).await;
    assert_eq!(moved["check"]["state"], "ok", "the same address is not checked again");
    r.answers.set("https://example.org/lunch", reply(200, &[("X-Frame-Options", "DENY")], ""));
    let (_, moved) = send(&r, Method::PUT, &format!("/api/admin/signage-web/links/{web_id}"), Some(json!({"url": "https://example.org/lunch"}))).await;
    assert_eq!((moved["url"].as_str(), moved["check"]["state"].as_str()), (Some("https://example.org/lunch"), Some("blocked")), "a new address is checked");
    let (_, again) = send(&r, Method::POST, &format!("/api/admin/signage-web/links/{web_id}/check"), None).await;
    assert_eq!(again["check"]["state"], "blocked");
    let (st, _) = send(&r, Method::POST, "/api/admin/signage-web/links/nope/check", None).await;
    assert_eq!(st, StatusCode::NOT_FOUND);
    let (st, _) = send(&r, Method::PUT, "/api/admin/signage-web/links/nope", Some(json!({}))).await;
    assert_eq!(st, StatusCode::NOT_FOUND);
    let (st, _) = send(&r, Method::PUT, &format!("/api/admin/signage-web/links/{web_id}"), Some(json!({"url": "ftp://x"}))).await;
    assert_eq!(st, StatusCode::BAD_REQUEST);

    let (st, gone) = send(&r, Method::DELETE, &format!("/api/admin/signage-web/links/{yt_id}"), None).await;
    assert_eq!((st, gone), (StatusCode::OK, json!({"ok": true})));
    let (_, list) = send(&r, Method::GET, "/api/admin/signage-web", None).await;
    assert_eq!(list["links"].as_array().unwrap().len(), 1);
    let actions: Vec<String> = r.gate.audit.lock().unwrap().iter().map(|a| a.0.clone()).collect();
    assert_eq!(actions, ["signage_web.add", "signage_web.add", "signage_web.edit", "signage_web.edit", "signage_web.edit", "signage_web.edit", "signage_web.check", "signage_web.delete"]);
    assert_eq!(r.gate.audit.lock().unwrap()[0].2, Some(json!({"kind": "web"})));
    assert_eq!(r.host.settings("signage_web").unwrap()["links"].as_array().unwrap().len(), 1, "the links are saved in the module's settings");
}

#[tokio::test]
async fn the_list_is_limited() {
    let r = rig();
    let rows: Vec<Value> = (0..200).map(|i| json!({"url": format!("https://example.org/{i}")})).collect();
    r.host.save_settings("signage_web", &json!({"links": clean_stored(&json!({"links": rows}))["links"]})).unwrap();
    let (st, e) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": "https://example.org/more"}))).await;
    assert_eq!((st, e["error"].as_str()), (StatusCode::BAD_REQUEST, Some("At most 200 links can be saved.")));
}

#[tokio::test]
async fn each_route_asks_for_its_permission() {
    let r = rig();
    *r.gate.refuse.lock().unwrap() = vec!["signage_web.manage"];
    let (st, _) = send(&r, Method::GET, "/api/admin/signage-web", None).await;
    assert_eq!(st, StatusCode::OK, "viewing needs only signage_web.view");
    for (m, uri) in [(Method::POST, "/api/admin/signage-web/links"), (Method::PUT, "/api/admin/signage-web/links/aaaaaaaa"), (Method::DELETE, "/api/admin/signage-web/links/aaaaaaaa"), (Method::POST, "/api/admin/signage-web/links/aaaaaaaa/check")] {
        let (st, _) = send(&r, m.clone(), uri, Some(json!({"url": "https://example.org/"}))).await;
        assert_eq!(st, StatusCode::FORBIDDEN, "{m} {uri}");
    }
    *r.gate.refuse.lock().unwrap() = vec!["signage_web.view"];
    let (st, _) = send(&r, Method::GET, "/api/admin/signage-web", None).await;
    assert_eq!(st, StatusCode::FORBIDDEN);
}

#[tokio::test]
async fn signage_plays_the_saved_links_while_the_module_is_on() {
    let r = rig();
    r.answers.set("https://example.org/menu", reply(200, &[], ""));
    r.answers.set("oembed", reply(200, &[], &json!({"title": "Welcome video"}).to_string()));
    let name = |k: &str| format!("{SIGNAGE_SOURCE}{k}");
    assert!(r.host.using::<SignageSource>(&name("youtube")).is_none(), "nothing is offered before it starts");
    r.module.start(r.host.clone(), json!({})).await.unwrap();
    let web = r.host.using::<SignageSource>(&name("web_page")).unwrap();
    let video = r.host.using::<SignageSource>(&name("youtube")).unwrap();
    assert_eq!((web.label.as_str(), video.label.as_str()), ("Web page", "YouTube video or playlist"));
    let (_, w) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": "https://example.org/menu", "name": "Menu"}))).await;
    let (_, y) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": format!("https://youtu.be/{V}")}))).await;
    let (_, p) = send(&r, Method::POST, "/api/admin/signage-web/links", Some(json!({"url": format!("https://www.youtube.com/playlist?list={L}"), "name": "Loop"}))).await;
    let (w, y, p) = (w["id"].as_str().unwrap().to_owned(), y["id"].as_str().unwrap().to_owned(), p["id"].as_str().unwrap().to_owned());

    let names: Vec<Value> = (web.choices)().iter().map(|c| c["name"].clone()).collect();
    assert_eq!(names, [json!("Menu")]);
    let choices = (video.choices)();
    assert_eq!(choices.iter().map(|c| (c["name"].as_str().unwrap(), c["kind"].as_str().unwrap())).collect::<Vec<_>>(), [("Welcome video", "video"), ("Loop", "")], "a single video is added as a video, which plays to its end");

    assert_eq!((web.resolve)(&w).unwrap()["probe"], "https://example.org/");
    assert!((video.resolve)(&y).unwrap()["end_when"].is_array());
    assert!((video.resolve)(&p).unwrap().get("end_when").is_none());
    assert!((web.resolve)(&y).is_none(), "a source plays only its own kind of link");
    assert!((video.resolve)(&w).is_none());
    assert_eq!((web.describe)(&w), json!({"name": "Menu", "thumb": ""}));
    assert_eq!((video.describe)("deadbeef"), json!({"name": "deadbeef", "thumb": "", "missing": true}));

    send(&r, Method::DELETE, &format!("/api/admin/signage-web/links/{w}"), None).await;
    assert!((web.resolve)(&w).is_none(), "removing a link makes playlists skip it");
    assert_eq!((web.describe)(&w)["missing"], true);

    r.module.stop().await;
    assert!(r.host.using::<SignageSource>(&name("web_page")).is_none() && r.host.using::<SignageSource>(&name("youtube")).is_none(), "switched off, both are withdrawn");
}

#[test]
fn the_module_describes_itself_as_python_did() {
    let m = SignageWeb::new();
    assert_eq!(m.defaults(), json!({"links": []}));
    assert_eq!(m.clean(&json!({"links": [{"url": "https://example.org/"}], "junk": 1}))["links"].as_array().unwrap().len(), 1);
    assert!(m.clean(&json!("x"))["links"].as_array().unwrap().is_empty());
    let ids: Vec<&str> = m.permissions().iter().map(|p| p.id).collect();
    assert_eq!(ids, ["signage_web.view", "signage_web.manage"]);
    assert_eq!(m.manifest().uses, &["signage"]);
    assert_eq!(m.manifest().route_prefixes, &["/api/admin/signage-web"]);
    assert_eq!(links::MAX_LINKS, 200);
}
