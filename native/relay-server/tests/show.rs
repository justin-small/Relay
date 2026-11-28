//! Alerts, stage messages and panels over HTTP (#660), on a real database and a real config.json: what `tests/test_alerts.py` and
//! `tests/test_panels.py` check of the Python routes, driven through the router the server serves.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;

use axum::body::Body;
use axum::http::{header, Method, Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use relay_auth::{Auth, Cost, NewUser};
use relay_server::accounts::Accounts;
use relay_server::forward::Conn;
use relay_server::pages::Pages;
use relay_server::show::{routes, start};
use serde_json::{json, Value};
use tower::ServiceExt;

const LAN: &str = "192.168.1.50:5000";

fn password() -> &'static str {
    static P: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    P.get_or_init(|| format!("{:016x}{:016x}", rand::random::<u64>(), rand::random::<u64>()))
}

fn pid(n: u32) -> String {
    format!("{n:032x}")
}

struct Site {
    dir: PathBuf,
    auth: Auth,
    router: Router,
    shutdown: relay_core::Shutdown,
}

impl Drop for Site {
    fn drop(&mut self) {
        self.shutdown.trigger();
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

struct Reply {
    status: StatusCode,
    body: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }
}

async fn site(tag: &str) -> Site {
    let dir = std::env::temp_dir().join(format!("relay-show-routes-{tag}-{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.json"), "{}").unwrap();
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.unwrap();
    for sql in [include_str!("../../../app/migrations/0005_alert_history.sql"), include_str!("../../../app/migrations/0008_screens_places.sql")] {
        sqlx::raw_sql(sql).execute(auth.pool()).await.unwrap();
    }
    let pool = auth.pool().clone();
    let app_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app");
    let acc = Accounts::new(auth.clone(), Pages::new(&app_dir), dir.join("config.json"));
    let shutdown = relay_core::Shutdown::new();
    let api = start(pool, acc.clone(), &dir.join("config.json"), None, shutdown.clone()).await.expect("the tables are there");
    Site { dir, auth, router: routes(api), shutdown }
}

impl Site {
    async fn raw(&self, method: Method, uri: &str, cookie: &str, secure: bool, body: Option<Vec<u8>>) -> axum::http::Response<Body> {
        let mut req = Request::builder().method(method).uri(uri);
        if !cookie.is_empty() {
            req = req.header("cookie", cookie);
        }
        let body = match body {
            Some(b) => {
                req = req.header(header::CONTENT_TYPE, "application/json");
                Body::from(b)
            }
            None => Body::empty(),
        };
        let mut req = req.body(body).unwrap();
        req.extensions_mut().insert(Conn { peer: LAN.parse::<SocketAddr>().unwrap(), local_port: if secure { 8443 } else { 8000 }, secure });
        self.router.clone().oneshot(req).await.unwrap()
    }

    async fn send(&self, method: Method, uri: &str, cookie: &str, secure: bool, body: Option<Vec<u8>>) -> Reply {
        let resp = self.raw(method, uri, cookie, secure, body).await;
        let (parts, body) = resp.into_parts();
        Reply { status: parts.status, body: String::from_utf8_lossy(&body.collect().await.unwrap().to_bytes()).into_owned() }
    }

    /// A viewer-port request (plain HTTP, no login).
    async fn viewer(&self, method: Method, uri: &str, body: Option<Value>) -> Reply {
        self.send(method, uri, "", false, body.map(|b| b.to_string().into_bytes())).await
    }

    async fn admin(&self, method: Method, uri: &str, cookie: &str, body: Option<Value>) -> Reply {
        self.send(method, uri, cookie, true, body.map(|b| b.to_string().into_bytes())).await
    }

    async fn cookie(&self, username: &str, role: &str) -> String {
        let user = self.auth.create_user(NewUser { username, name: username, role, password: password(), temporary: false }, None).await.unwrap();
        format!("relay_admin={}", self.auth.start_session(&user, "password", None, "").await.unwrap().token)
    }

    async fn audited(&self, kind: &str) -> Vec<String> {
        let found = self.auth.search_audit(relay_auth::Search { kind: Some(kind), ..Default::default() }).await.unwrap();
        found.entries.iter().map(|e| e.action.clone()).collect()
    }

    fn config(&self) -> Value {
        serde_json::from_str(&std::fs::read_to_string(self.dir.join("config.json")).unwrap()).unwrap()
    }

    async fn checkin(&self, n: u32, name: &str) -> Reply {
        self.viewer(Method::POST, "/api/panels/checkin", Some(json!({"id": pid(n), "name": name, "version": "0.5.0", "scripts": ["latin"], "feeds": [{"stream": "alert"}]}))).await
    }
}

/// The first `count` SSE frames of a stream, read off the body as they arrive.
async fn frames(resp: axum::http::Response<Body>, count: usize) -> Vec<Value> {
    let mut body = resp.into_body();
    let mut text = String::new();
    let mut out = Vec::new();
    while out.len() < count {
        let chunk = tokio::time::timeout(Duration::from_secs(5), body.frame()).await.expect("a frame arrives").expect("the stream is open").unwrap();
        if let Some(data) = chunk.data_ref() {
            text.push_str(&String::from_utf8_lossy(data));
        }
        while let Some(end) = text.find("\n\n") {
            let frame: String = text.drain(..end + 2).collect();
            if let Some(json) = frame.strip_prefix("data: ") {
                out.push(serde_json::from_str(json.trim_end()).unwrap());
            }
        }
    }
    out
}

#[tokio::test]
async fn panels_check_in_on_the_viewer_port_and_the_operator_manages_them() {
    let s = site("panels").await;
    let r = s.checkin(1, "Hall 1").await;
    assert_eq!((r.status, r.json()["name"].clone(), r.json()["interval_s"].clone()), (StatusCode::OK, json!("Hall 1"), json!(30)));
    assert_eq!(s.checkin(2, "").await.json()["name"], "Panel 0000");
    assert_eq!(s.viewer(Method::POST, "/api/panels/checkin", Some(json!({"id": "nope"}))).await.status, StatusCode::BAD_REQUEST);
    assert_eq!(s.viewer(Method::POST, "/api/panels/checkin", Some(json!([1]))).await.status, StatusCode::BAD_REQUEST);
    assert_eq!(s.send(Method::POST, "/api/panels/checkin", "", false, Some(b"{nope".to_vec())).await.status, StatusCode::BAD_REQUEST);
    let big = json!({"id": pid(1), "name": "x".repeat(panels_cap() + 1)});
    assert_eq!(s.viewer(Method::POST, "/api/panels/checkin", Some(big)).await.status, StatusCode::PAYLOAD_TOO_LARGE);
    assert_eq!(s.config().get("panels"), None, "a check-in alone writes nothing to config.json");

    // the admin side needs a sign-in
    assert_eq!(s.admin(Method::GET, "/api/admin/panels", "", None).await.status, StatusCode::UNAUTHORIZED);
    let op = s.cookie("pat", "admin").await;
    let list = s.admin(Method::GET, "/api/admin/panels", &op, None).await.json();
    assert_eq!(list["type"], "panels");
    assert_eq!(list["panels"].as_array().unwrap().len(), 2);
    assert_eq!((list["limit"].clone(), list["offline_after_s"].clone()), (json!(64), json!(90.0)));
    let r = s.admin(Method::POST, &format!("/api/admin/panels/{}", pid(1)), &op, Some(json!({"name": "Lobby", "feeds": [{"stream": "translation", "lang": "french"}]}))).await;
    assert_eq!((r.status, r.json()["name"].clone(), r.json()["set_feeds"].clone()), (StatusCode::OK, json!("Lobby"), json!([{"stream": "translation", "lang": "FRENCH"}])));
    assert_eq!(s.config()["panels"][pid(1)]["feed_rev"], 1);
    assert_eq!(s.checkin(1, "Hall 1").await.json()["set_feed"]["rev"], 1, "the next check-in carries the pending feed");
    for bad in [json!({"feeds": [{"stream": "nope"}]}), json!({"name": 5}), json!({"rotation": 45})] {
        assert_eq!(s.admin(Method::POST, &format!("/api/admin/panels/{}", pid(1)), &op, Some(bad)).await.status, StatusCode::BAD_REQUEST);
    }
    assert_eq!(s.admin(Method::POST, &format!("/api/admin/panels/{}", pid(77)), &op, Some(json!({"name": "x"}))).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.admin(Method::POST, &format!("/api/admin/panels/{}/reboot", pid(1)), &op, None).await.status, StatusCode::CONFLICT, "no stream open");
    let viewer_role = s.cookie("viewer1", "viewer").await;
    assert_eq!(s.admin(Method::POST, &format!("/api/admin/panels/{}", pid(1)), &viewer_role, Some(json!({"name": "x"}))).await.status, StatusCode::FORBIDDEN);
    assert_eq!(s.admin(Method::GET, "/api/admin/panels", &viewer_role, None).await.status, StatusCode::OK, "a viewer may look");
    assert_eq!(s.audited("panels.change").await.len(), 1);

    // the panel's event stream opens with what it has not applied, and a panel that never checked in is refused
    assert_eq!(s.viewer(Method::GET, &format!("/api/panels/{}/events", pid(9)), None).await.status, StatusCode::NOT_FOUND);
    let first = frames(s.raw(Method::GET, &format!("/api/panels/{}/events", pid(1)), "", false, None).await, 1).await;
    assert_eq!(first[0]["type"], "set_feed");
    let r = s.viewer(Method::POST, &format!("/api/panels/{}/update", pid(1)), Some(json!({"state": "downloading", "progress": 40}))).await;
    assert_eq!(r.json(), json!({"ok": true}));
    assert_eq!(s.viewer(Method::POST, &format!("/api/panels/{}/update", pid(9)), Some(json!({}))).await.status, StatusCode::NOT_FOUND);
    let r = s.admin(Method::DELETE, &format!("/api/admin/panels/{}", pid(1)), &op, None).await;
    assert_eq!(r.status, StatusCode::OK);
    assert_eq!(s.admin(Method::DELETE, &format!("/api/admin/panels/{}", pid(1)), &op, None).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.audited("panels.forget").await.len(), 1);
}

fn panels_cap() -> usize {
    relay_show::panels::CHECKIN_MAX_BYTES
}

#[tokio::test]
async fn an_alert_flows_from_a_phone_to_a_panel_and_back() {
    let s = site("alerts").await;
    s.checkin(1, "Booth").await;
    s.checkin(2, "Wedge").await;
    let op = s.cookie("pat", "admin").await;
    assert_eq!(s.admin(Method::GET, "/api/admin/alerts", &op, None).await.json()["alerts"]["auto_stop_s"], 120);
    let r = s.admin(Method::POST, "/api/admin/alerts", &op, Some(json!({"auto_stop_s": 60, "buttons": [
        {"name": "FOH booth", "icon": "headphones", "panels": [pid(1)], "screens": ["Booth TV"]}, {"name": "Stage", "panels": [pid(2)]}]}))).await;
    assert_eq!(r.status, StatusCode::OK);
    let id = r.json()["alerts"]["buttons"][0]["id"].as_str().unwrap().to_owned();
    assert_eq!(s.config()["alerts"]["auto_stop_s"], 60);
    let r = s.admin(Method::POST, "/api/admin/alerts", &op, Some(json!({"auto_stop_s": 10, "buttons": []}))).await;
    assert!(r.status == StatusCode::BAD_REQUEST && r.json()["detail"].as_str().unwrap().contains("auto-stop"), "{}", r.body);
    assert_eq!(s.audited("alerts.change").await.len(), 1);
    assert_eq!(s.viewer(Method::GET, "/api/admin/alerts", None).await.status, StatusCode::UNAUTHORIZED, "no login on the viewer port");

    // a sender stream opens with every button idle; a panel's stream opens quiet
    let sender = s.raw(Method::GET, "/stream?stream=alert", "", false, None).await;
    assert_eq!(sender.headers()["content-type"], "text/event-stream");
    let mut sender_body = sender.into_body();
    let panel = s.raw(Method::GET, &format!("/api/panels/{}/events", pid(1)), "", false, None).await;
    let mut panel_body = panel.into_body();
    let screen = s.raw(Method::GET, "/api/alerts/screen?name=Booth%20TV", "", false, None).await;
    let mut screen_body = screen.into_body();

    let r = s.viewer(Method::POST, &format!("/api/alerts/{id}/send"), Some(json!({"from": "Drums\n\x07"}))).await;
    assert_eq!(r.status, StatusCode::OK);
    let row = &r.json()["buttons"][0];
    assert_eq!((r.json()["ok"].clone(), row["state"].clone(), row["from"].clone(), row["icon"].clone(), row["ends_in_s"].clone()), (json!(true), json!("on"), json!("Drums"), json!("headphones"), json!(60)));
    let alert = next_with(&mut panel_body, "alert").await;
    assert_eq!((alert["from"].clone(), alert["to"].clone(), alert["hz"].clone()), (json!("Drums"), json!("FOH booth"), json!(1)));
    let alert_id = alert["id"].as_str().unwrap().to_owned();
    assert_eq!(next_with(&mut screen_body, "alert").await["id"], json!(alert_id));
    let seen_idle = next_with(&mut sender_body, "alerts").await;
    assert_eq!(seen_idle["buttons"][0]["state"], "idle");
    assert_eq!(next_with(&mut sender_body, "alerts").await["buttons"][0]["state"], "on");

    assert_eq!(s.viewer(Method::POST, "/api/alerts/ffffffff/send", Some(json!({}))).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.viewer(Method::POST, "/api/alerts/ack", Some(json!({"id": alert_id, "panel": pid(2)}))).await.status, StatusCode::FORBIDDEN);
    assert_eq!(s.viewer(Method::POST, "/api/alerts/ack", Some(json!({"id": "x"}))).await.status, StatusCode::BAD_REQUEST);
    assert_eq!(s.viewer(Method::POST, "/api/alerts/ack", Some(json!({"id": alert_id, "panel": "short"}))).await.status, StatusCode::BAD_REQUEST);
    let r = s.viewer(Method::POST, "/api/alerts/ack", Some(json!({"id": alert_id, "screen": "BOOTH tv"}))).await;
    assert_eq!(r.json(), json!({"ok": true, "state": "seen", "seen_on": "Booth TV", "reply": ""}));
    assert_eq!(next_with(&mut panel_body, "alert_end").await["why"], "seen");
    assert_eq!(next_with(&mut screen_body, "alert_end").await["why"], "seen");

    // the history row is written as the alert ends
    let mut rows = Value::Null;
    for _ in 0..100 {
        rows = s.admin(Method::GET, "/api/admin/alerts/history", &op, None).await.json();
        if !rows["entries"].as_array().unwrap().is_empty() {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await; // allow-sleep: the row is written by a task after the answer
    }
    let e = &rows["entries"][0];
    assert_eq!((e["kind"].clone(), e["name"].clone(), e["outcome"].clone(), e["seen_on"].clone(), e["sender"].clone()), (json!("alert"), json!("FOH booth"), json!("seen"), json!("Booth TV"), json!("Drums")));
    assert_eq!((rows["more"].clone(), rows["keep_days"].clone()), (json!(false), json!(30)));
    assert_eq!(s.admin(Method::GET, "/api/admin/alerts/history", "", None).await.status, StatusCode::UNAUTHORIZED);
    let r = s.admin(Method::POST, "/api/admin/alerts/test", &op, Some(json!({"panel": pid(1)}))).await;
    assert_eq!(r.json(), json!({"ok": true, "delivered": 1}));
    assert_eq!(s.audited("alerts.test").await.len(), 1);
    assert_eq!(s.admin(Method::POST, "/api/admin/alerts/test", &op, Some(json!({}))).await.status, StatusCode::BAD_REQUEST);
}

/// Frames off an open stream until one of `type` shows up.
async fn next_with(body: &mut Body, kind: &str) -> Value {
    let mut text = String::new();
    loop {
        let chunk = tokio::time::timeout(Duration::from_secs(5), body.frame()).await.expect("a frame arrives").expect("the stream is open").unwrap();
        if let Some(data) = chunk.data_ref() {
            text.push_str(&String::from_utf8_lossy(data));
        }
        while let Some(end) = text.find("\n\n") {
            let frame: String = text.drain(..end + 2).collect();
            if let Some(json) = frame.strip_prefix("data: ") {
                let v: Value = serde_json::from_str(json.trim_end()).unwrap();
                if v["type"] == kind {
                    return v;
                }
            }
        }
    }
}

#[tokio::test]
async fn stage_messages_and_signed_in_sending() {
    let s = site("messages").await;
    s.checkin(1, "Booth").await;
    let op = s.cookie("pat", "admin").await;
    s.admin(Method::POST, "/api/admin/alerts", &op, Some(json!({"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [pid(1)]}],
        "messages": {"presets": [{"id": "0a0a0a0a", "text": "Wrap up", "panels": [pid(1)], "level": "attention"}], "free": {"panels": [pid(1)]}}}))).await;
    let r = s.viewer(Method::POST, "/api/alerts/messages/send", Some(json!({"preset": "0a0a0a0a", "from": "Pat"}))).await;
    assert_eq!((r.status, r.json()["live"][0]["text"].clone(), r.json()["live"][0]["from"].clone()), (StatusCode::OK, json!("Wrap up"), json!("Pat")));
    assert_eq!(s.viewer(Method::POST, "/api/alerts/messages/send", Some(json!({"preset": "zzzzzzzz"}))).await.status, StatusCode::NOT_FOUND);
    let bad = s.viewer(Method::POST, "/api/alerts/messages/send", Some(json!({"text": "  "}))).await;
    assert_eq!((bad.status, bad.json()["error"].clone()), (StatusCode::BAD_REQUEST, json!("write the message first")));
    let stream = frames(s.raw(Method::GET, "/api/alerts/messages/stream", "", false, None).await, 1).await;
    assert_eq!((stream[0]["type"].clone(), stream[0]["live"].as_array().unwrap().len()), (json!("messages"), 1));
    let r = s.viewer(Method::POST, "/api/alerts/messages/clear", Some(json!({"id": 5}))).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    assert_eq!(s.viewer(Method::POST, "/api/alerts/messages/clear", Some(json!({}))).await.json()["live"], json!([]));

    // with sign-in required, a phone sends only as an account holding alerts.send, over the operator port
    s.admin(Method::POST, "/api/admin/alerts", &op, Some(json!({"auto_stop_s": 60, "require_signin": true, "buttons": [{"name": "A", "panels": [pid(1)]}]}))).await;
    let id = s.config()["alerts"]["buttons"][0]["id"].as_str().unwrap().to_owned();
    assert_eq!(s.viewer(Method::POST, &format!("/api/alerts/{id}/send"), Some(json!({"from": "Drums"}))).await.status, StatusCode::UNAUTHORIZED, "the viewer port refuses");
    assert_eq!(s.admin(Method::POST, &format!("/api/alerts/{id}/send"), "", Some(json!({}))).await.status, StatusCode::UNAUTHORIZED);
    let member = s.cookie("drummer", "member").await;
    let r = s.admin(Method::POST, &format!("/api/alerts/{id}/send"), &member, Some(json!({"from": "ignored"}))).await;
    assert_eq!((r.status, r.json()["buttons"][0]["from"].clone()), (StatusCode::OK, json!("drummer")), "named by the account");
    let r = s.viewer(Method::POST, &format!("/api/alerts/{id}/send"), Some(json!({"panel": pid(1)}))).await;
    assert_eq!(r.status, StatusCode::OK, "a panel still sends by its id");
    assert_eq!(s.viewer(Method::POST, &format!("/api/alerts/{id}/send"), Some(json!({"panel": pid(9)}))).await.status, StatusCode::NOT_FOUND);
}

#[tokio::test]
async fn firmware_routes_refuse_what_is_not_an_image() {
    let s = site("firmware").await;
    let op = s.cookie("pat", "admin").await;
    let fw = s.admin(Method::GET, "/api/admin/panels/firmware", &op, None).await.json();
    assert_eq!((fw["firmware"].clone(), fw["boards"][0]["board"].clone()), (Value::Null, json!("crowpanel-adv7")));
    assert_eq!(s.admin(Method::GET, "/api/admin/panels/firmware?board=nope", &op, None).await.status, StatusCode::BAD_REQUEST);
    assert_eq!(s.admin(Method::GET, "/api/admin/panels/firmware?board=waveshare-p4-10.1", &op, None).await.status, StatusCode::BAD_REQUEST, "a board on hold");
    let r = s.send(Method::POST, "/api/admin/panels/firmware?filename=x.bin", &op, true, Some(b"not firmware".to_vec())).await;
    assert!(r.status == StatusCode::BAD_REQUEST && r.json()["detail"].as_str().unwrap().starts_with("That file is not panel firmware"), "{}", r.body);
    assert_eq!(s.admin(Method::POST, "/api/admin/panels/firmware/update", &op, Some(json!({}))).await.status, StatusCode::CONFLICT, "none uploaded");
    assert_eq!(s.admin(Method::POST, "/api/admin/panels/firmware/update", &op, Some(json!({"id": pid(3)}))).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.viewer(Method::GET, "/api/panels/firmware/crowpanel-adv7/0.6.0.bin", None).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.admin(Method::GET, "/api/admin/panels/flash/crowpanel-adv7.json", &op, None).await.status, StatusCode::NOT_FOUND);
    let r = s.admin(Method::DELETE, "/api/admin/panels/firmware", &op, None).await;
    assert_eq!(r.json(), json!({"ok": true, "removed": false}));
    assert_eq!(s.audited("panels.firmware_delete").await.len(), 1);

    // an image a Python Relay stored is offered to a panel and served
    let folder = s.dir.join("panel-firmware").join("crowpanel-adv7");
    std::fs::create_dir_all(&folder).unwrap();
    std::fs::write(folder.join("panel.bin"), vec![7u8; 64]).unwrap();
    std::fs::write(folder.join("panel.json"), json!({"version": "0.6.0", "size": 64, "sha256": "ab", "uploaded_at": 1.0, "filename": "x.bin", "board": "crowpanel-adv7"}).to_string()).unwrap();
    s.checkin(1, "Booth").await;
    let list = s.admin(Method::GET, "/api/admin/panels", &op, None).await.json();
    assert_eq!((list["panels"][0]["update_available"].clone(), list["boards"][0]["outdated"].clone(), list["firmware"]["version"].clone()), (json!(true), json!(1), json!("0.6.0")));
    let r = s.admin(Method::POST, "/api/admin/panels/firmware/update", &op, Some(json!({"id": pid(1), "now": true}))).await;
    assert_eq!(r.json()["panel"]["update_requested"], true);
    let reply = s.checkin(1, "Booth").await.json();
    assert_eq!((reply["update"]["version"].clone(), reply["update"]["path"].clone()), (json!("0.6.0"), json!("/api/panels/firmware/crowpanel-adv7/0.6.0.bin")));
    let image = s.raw(Method::GET, "/api/panels/firmware/crowpanel-adv7/0.6.0.bin", "", false, None).await;
    assert_eq!((image.status(), image.headers()["content-type"].clone(), image.headers()["cache-control"].clone()), (StatusCode::OK, "application/octet-stream".parse().unwrap(), "no-store".parse().unwrap()));
    assert_eq!(image.into_body().collect().await.unwrap().to_bytes().len(), 64);
    assert_eq!(s.viewer(Method::GET, "/api/panels/firmware/crowpanel-adv7/0.5.0.bin", None).await.status, StatusCode::NOT_FOUND, "only the version held");
}
