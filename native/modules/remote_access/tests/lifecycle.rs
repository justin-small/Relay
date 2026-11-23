//! Remote access end to end (#662), offline: a fake relay-cloud on 127.0.0.1 and a fake cloudflared (a shell script). Enrolling,
//! checking in, running the tunnel with its token in the environment (never on the command line), releasing, and the gate on the
//! routes. One test, because the fake cloudflared is chosen with an environment variable the whole process shares.
#![cfg(unix)]

use std::os::unix::fs::PermissionsExt;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::{to_bytes, Body};
use axum::http::request::Parts;
use axum::http::{Request, StatusCode};
use axum::response::Response;
use relay_core::module::BoxFuture;
use relay_core::{Authorizer, Config, Host, Module, Net, Registry, Shutdown};
use relay_module_remote_access::cloud::Cloud;
use relay_module_remote_access::service::Service;
use relay_module_remote_access::tunnel::{State, Tunnel};
use relay_module_remote_access::RemoteAccess;
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tower::ServiceExt;

const GOOD_CODE: &str = "ABCD-EFGH-JKMN";

#[derive(Default)]
struct CloudState {
    seen: Vec<(String, String, Value, String)>, // method, path, body, authorization
    secret: Option<String>,
    released: bool,
    fail_next: Option<&'static str>,
    check_ins: u32,
}

async fn fake_cloud(state: Arc<Mutex<CloudState>>) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("http://{}", listener.local_addr().unwrap());
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else { return };
            let state = Arc::clone(&state);
            tokio::spawn(async move {
                let mut buf = Vec::new();
                let mut chunk = [0u8; 4096];
                let (head, body) = loop {
                    let n = sock.read(&mut chunk).await.unwrap_or(0);
                    if n == 0 {
                        return;
                    }
                    buf.extend_from_slice(&chunk[..n]);
                    if let Some(at) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                        let head = String::from_utf8_lossy(&buf[..at]).to_string();
                        let want = head
                            .lines()
                            .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse::<usize>().unwrap_or(0)))
                            .unwrap_or(0);
                        if buf.len() >= at + 4 + want {
                            break (head, buf[at + 4..at + 4 + want].to_vec());
                        }
                    }
                };
                let mut first = head.lines().next().unwrap_or("").split(' ');
                let (method, path) = (first.next().unwrap_or("").to_owned(), first.next().unwrap_or("").to_owned());
                let auth = head
                    .lines()
                    .find_map(|l| l.strip_prefix("Authorization: ").or_else(|| l.strip_prefix("authorization: ")))
                    .unwrap_or("")
                    .to_owned();
                let body: Value = serde_json::from_slice(&body).unwrap_or(Value::Null);
                let (code, out) = respond(&state, &method, &path, &body, &auth);
                let raw = out.map(|o| o.to_string()).unwrap_or_default();
                let reply = format!("HTTP/1.1 {code} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{raw}", raw.len());
                let _ = sock.write_all(reply.as_bytes()).await;
            });
        }
    });
    url
}

fn respond(state: &Mutex<CloudState>, method: &str, path: &str, body: &Value, auth: &str) -> (u16, Option<Value>) {
    let mut s = state.lock().unwrap();
    s.seen.push((method.to_owned(), path.to_owned(), body.clone(), auth.to_owned()));
    let install = |port: u64| json!({ "install_id": "inst-1", "hostname": "k7m2p9qa.cloud.relaysystems.dev", "tunnel_token": format!("tok-{port}") });
    if let Some(kind) = s.fail_next.take() {
        return (502, Some(json!({ "error": kind })));
    }
    let authed = !s.released && s.secret.as_deref().is_some_and(|sec| auth == format!("Bearer {sec}"));
    match (method, path) {
        ("GET", "/health") => (200, Some(json!({ "ok": true }))),
        ("POST", "/v1/enroll") => {
            if body["code"].as_str().unwrap_or("").replace(' ', "").to_uppercase() != GOOD_CODE {
                return (403, Some(json!({ "error": "bad_code" })));
            }
            let secret = body["secret"].as_str().unwrap_or("").to_owned();
            if s.secret.as_ref().is_some_and(|old| *old != secret) {
                return (409, Some(json!({ "error": "code_used" })));
            }
            s.secret = Some(secret);
            (201, Some(install(body["viewer_port"].as_u64().unwrap_or(0))))
        }
        ("PUT", "/v1/install") if authed => {
            s.check_ins += 1;
            (200, Some(install(body["viewer_port"].as_u64().unwrap_or(0))))
        }
        ("DELETE", "/v1/install") if authed => {
            s.released = true;
            (204, None)
        }
        ("PUT" | "DELETE", _) => (401, Some(json!({ "error": "unauthorised" }))),
        _ => (404, Some(json!({ "error": "not_found" }))),
    }
}

struct Everyone;

impl Authorizer for Everyone {
    fn require<'a>(&'a self, _r: &'a Parts, _p: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async { Ok(()) })
    }
}

async fn call(app: &axum::Router, method: &str, uri: &str, body: Value) -> (StatusCode, Value) {
    let req = Request::builder().method(method).uri(uri).header("content-type", "application/json").body(Body::from(body.to_string())).unwrap();
    let resp = app.clone().oneshot(req).await.unwrap();
    let status = resp.status();
    let bytes = to_bytes(resp.into_body(), 1 << 20).await.unwrap();
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
}

async fn until(what: &str, mut ready: impl FnMut() -> bool) {
    for _ in 0..200 {
        if ready() {
            return;
        }
        tokio::time::sleep(Duration::from_millis(25)).await;
    }
    panic!("timed out waiting for {what}");
}

#[tokio::test]
async fn enroll_check_in_run_the_tunnel_and_release() {
    let dir = std::env::temp_dir().join(format!("relay-remote-access-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let log = dir.join("cloudflared.json");
    let fake = dir.join("cloudflared");
    std::fs::write(
        &fake,
        format!(
            "#!/bin/sh\nprintf '{{\"argv\":\"%s\",\"token\":\"%s\"}}' \"$*\" \"$TUNNEL_TOKEN\" > {}\n\
             echo '2026-11-13T00:00:00Z INF Registered tunnel connection connIndex=0' >&2\nsleep 60\n",
            log.display()
        ),
    )
    .unwrap();
    std::fs::set_permissions(&fake, std::fs::Permissions::from_mode(0o755)).unwrap();
    std::env::set_var("RELAY_CLOUDFLARED", &fake);

    let cloud_state = Arc::new(Mutex::new(CloudState::default()));
    let cloud_url = fake_cloud(Arc::clone(&cloud_state)).await;

    let path = dir.join("config.json");
    std::fs::write(&path, json!({ "remote_access": { "cloud_url": cloud_url } }).to_string()).unwrap();
    let config = Arc::new(Config::load(&path).unwrap());
    let host = Host::new(Shutdown::new());
    host.set_viewer_port(8000);
    let _registry = Registry::new(Arc::clone(&config), host.clone());
    let tunnel = Tunnel::with_backoff(Net::unrouted(), vec![Duration::from_millis(50)]);
    let service = Service::with(host.clone(), Cloud::new(Net::unrouted()), tunnel.clone(), Duration::from_millis(200));
    let module = RemoteAccess::with_service(service.clone());
    let app = module.routes();

    // The routes are closed until the accounts are handed over.
    let (status, _) = call(&app, "GET", "/api/admin/remote-access", json!({})).await;
    assert_eq!(status, StatusCode::UNAUTHORIZED, "closed before accounts exist");
    host.set_authorizer(Arc::new(Everyone));

    let (status, view) = call(&app, "GET", "/api/admin/remote-access", json!({})).await;
    assert_eq!(status, StatusCode::OK);
    assert_eq!((view["enrolled"].clone(), view["url"].clone(), view["state"].clone()), (json!(false), json!(""), json!("off")));
    assert_eq!(view["cloudflared"], json!(true));

    // A wrong or empty code is refused, with a fixed sentence.
    let (status, bad) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": "" })).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(bad["error"], "Enter the enrollment code you were given.");
    let (status, bad) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": "WRONG-CODE" })).await;
    assert_eq!(status, StatusCode::BAD_REQUEST);
    assert_eq!(bad["error"], "That enrollment code isn't right. Check it and try again.");

    // The cloud failing is a 502 the operator can retry; the saved pending secret makes the retry send the same secret.
    cloud_state.lock().unwrap().fail_next = Some("provision_failed");
    let (status, _) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": GOOD_CODE })).await;
    assert_eq!(status, StatusCode::BAD_GATEWAY);
    let pending = host.settings("remote_access").unwrap()["pending_secret"].as_str().unwrap().to_owned();
    assert!(pending.len() >= 40, "a 32-byte URL-safe secret");

    let (status, view) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": " abcd-efgh-jkmn " })).await;
    assert_eq!(status, StatusCode::OK, "{view}");
    assert_eq!(view["enrolled"], json!(true));
    assert_eq!(view["url"], "https://k7m2p9qa.cloud.relaysystems.dev");
    {
        let s = cloud_state.lock().unwrap();
        let enrols: Vec<&Value> = s.seen.iter().filter(|(m, p, ..)| m == "POST" && p == "/v1/enroll").map(|(_, _, b, _)| b).collect();
        assert_eq!(enrols.last().unwrap()["secret"], json!(pending), "the retry sent the same secret");
        assert_eq!(enrols.last().unwrap()["viewer_port"], json!(8000));
    }
    let saved = host.settings("remote_access").unwrap();
    assert_eq!((saved["secret"].as_str().unwrap(), saved["pending_secret"].as_str().unwrap()), (pending.as_str(), ""));
    let (status, _) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": GOOD_CODE })).await;
    assert_eq!(status, StatusCode::CONFLICT, "already enrolled");

    // Enrolling checked in and started cloudflared with the cloud's token in its environment, not on its command line.
    until("the tunnel to connect", || tunnel.state() == State::Connected).await;
    let ran: Value = serde_json::from_str(&std::fs::read_to_string(&log).unwrap()).unwrap();
    assert_eq!(ran["token"], "tok-8000");
    assert!(!ran["argv"].as_str().unwrap().contains("tok-8000"), "the token is never an argument: {ran}");
    assert!(ran["argv"].as_str().unwrap().ends_with(" run"), "{ran}");
    assert_eq!(module.health().state, relay_core::HealthState::Ok);

    // The heartbeat checks in again by itself.
    let before = cloud_state.lock().unwrap().check_ins;
    until("the heartbeat", || cloud_state.lock().unwrap().check_ins > before).await;

    // Switching off stops cloudflared and keeps the address.
    module.stop().await;
    assert_eq!(tunnel.state(), State::Off);
    assert!(host.settings("remote_access").unwrap()["hostname"].as_str().unwrap().ends_with("cloud.relaysystems.dev"));

    // Releasing gives it up for good: the cloud deletes it and the settings are cleared.
    service.begin().await;
    until("the tunnel to reconnect", || tunnel.state() == State::Connected).await;
    let (status, view) = call(&app, "POST", "/api/admin/remote-access/release", json!({})).await;
    assert_eq!(status, StatusCode::OK, "{view}");
    assert_eq!((view["enrolled"].clone(), view["state"].clone()), (json!(false), json!("off")));
    assert!(cloud_state.lock().unwrap().released);
    let saved = host.settings("remote_access").unwrap();
    assert_eq!((saved["secret"].as_str().unwrap(), saved["tunnel_token"].as_str().unwrap(), saved["hostname"].as_str().unwrap()), ("", "", ""));

    // A cloud that no longer knows this install (released from its side) stops the tunnel and is reported.
    cloud_state.lock().unwrap().released = false;
    let (status, _) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": GOOD_CODE })).await;
    assert_eq!(status, StatusCode::BAD_REQUEST, "the cloud still remembers the first install's secret: code_used");
    cloud_state.lock().unwrap().secret = None;
    let (status, _) = call(&app, "POST", "/api/admin/remote-access/enroll", json!({ "code": GOOD_CODE })).await;
    assert_eq!(status, StatusCode::OK);
    until("the tunnel", || tunnel.state() == State::Connected).await;
    cloud_state.lock().unwrap().released = true;
    until("the tunnel to stop after the release", || tunnel.state() == State::Off && service.cloud_error() == "unauthorised").await;
    assert_eq!(module.health().state, relay_core::HealthState::Error);

    // With no cloudflared the tunnel says so instead of retrying forever.
    module.stop().await;
    std::env::set_var("RELAY_CLOUDFLARED", dir.join("nope"));
    tunnel.start("tok");
    until("missing", || tunnel.state() == State::Missing).await;
    tunnel.stop().await;
    let _ = std::fs::remove_dir_all(dir);
}
