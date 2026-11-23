//! The account routes (#655) over HTTP, on a real database: what tests/test_auth.py, test_users.py and test_audit.py check of
//! the Python server, driven through the router the server serves.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::time::Duration;

use axum::body::Body;
use axum::http::{header, Method, Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use relay_auth::{Auth, Cost, NewUser};
use relay_server::accounts::{routes, Accounts, Shared};
use relay_server::forward::Conn;
use relay_server::pages::Pages;
use serde_json::{json, Value};
use tower::ServiceExt;


/// Test passwords are random, made when the test runs: the name only says which one (the same name gives the same password
/// within one run), and none of the text is written in the source.
fn secret(name: &str) -> &'static str {
    static MADE: std::sync::Mutex<Vec<(String, &'static str)>> = std::sync::Mutex::new(Vec::new());
    let mut made = MADE.lock().unwrap_or_else(|e| e.into_inner());
    if let Some((_, v)) = made.iter().find(|(k, _)| k == name) {
        return v;
    }
    let value: &'static str = Box::leak(format!("{:016x}{:016x}", rand::random::<u64>(), rand::random::<u64>()).into_boxed_str());
    made.push((name.to_string(), value));
    value
}

struct Site {
    dir: PathBuf,
    acc: Shared,
    router: Router,
}

impl Drop for Site {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

struct Reply {
    status: StatusCode,
    headers: axum::http::HeaderMap,
    body: String,
}

impl Reply {
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or_else(|_| panic!("not JSON: {}", self.body))
    }

    fn cookie(&self, name: &str) -> Option<String> {
        self.headers
            .get_all(header::SET_COOKIE)
            .iter()
            .filter_map(|v| v.to_str().ok())
            .find_map(|c| c.strip_prefix(&format!("{name}=")).map(|v| v.split(';').next().unwrap_or("").to_owned()))
    }
}

async fn site(tag: &str) -> Site {
    let dir = std::env::temp_dir().join(format!("relay-accounts-{tag}-{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
    std::fs::create_dir_all(&dir).unwrap();
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.unwrap();
    let app_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app");
    let acc = {
        let a = Accounts::new(auth, Pages::new(&app_dir), dir.join("config.json"));
        std::sync::Arc::try_unwrap(a).ok().map(|mut a| {
            a.fail_delay = Duration::ZERO;
            std::sync::Arc::new(a)
        })
    }
    .unwrap();
    let router = routes(acc.clone());
    Site { dir, acc, router }
}

const LAN: &str = "192.168.1.50:5000";

impl Site {
    async fn send(&self, method: Method, uri: &str, headers: &[(&str, &str)], body: Option<(&str, String)>, peer: &str) -> Reply {
        let mut req = Request::builder().method(method).uri(uri);
        for (k, v) in headers {
            req = req.header(*k, *v);
        }
        let body = match body {
            Some((ct, b)) => {
                req = req.header(header::CONTENT_TYPE, ct);
                Body::from(b)
            }
            None => Body::empty(),
        };
        let mut req = req.body(body).unwrap();
        req.extensions_mut().insert(Conn { peer: peer.parse::<SocketAddr>().unwrap(), local_port: 8443, secure: true });
        let resp = self.router.clone().oneshot(req).await.unwrap();
        let (parts, body) = resp.into_parts();
        Reply { status: parts.status, headers: parts.headers, body: String::from_utf8_lossy(&body.collect().await.unwrap().to_bytes()).into_owned() }
    }

    async fn get(&self, uri: &str, cookie: &str) -> Reply {
        self.send(Method::GET, uri, &[("cookie", cookie)], None, LAN).await
    }

    async fn api(&self, method: Method, uri: &str, cookie: &str, body: Value) -> Reply {
        self.send(method, uri, &[("cookie", cookie)], Some(("application/json", body.to_string())), LAN).await
    }

    async fn form(&self, uri: &str, cookie: &str, fields: &[(&str, &str)], peer: &str) -> Reply {
        let body = serde_urlencoded::to_string(fields).unwrap();
        self.send(Method::POST, uri, &[("cookie", cookie)], Some(("application/x-www-form-urlencoded", body)), peer).await
    }

    async fn user(&self, username: &str, role: &str, temporary: bool) {
        self.acc
            .auth
            .create_user(NewUser { username, name: username, role, password: secret("good"), temporary }, None)
            .await
            .unwrap();
    }

    /// Sign in with the form and return the cookie header to send back.
    async fn sign_in(&self, username: &str) -> String {
        let r = self.form("/admin/login", "", &[("username", username), ("password", secret("good"))], LAN).await;
        assert_eq!(r.status, StatusCode::SEE_OTHER, "{}", r.body);
        format!("relay_admin={}", r.cookie("relay_admin").expect("a session cookie"))
    }
}

#[tokio::test]
async fn the_sign_in_form_lands_on_the_panel_or_the_home_page_and_sets_a_strict_cookie() {
    let s = site("form").await;
    s.user("boss", "owner", false).await;
    s.user("mem", "member", false).await;
    let r = s.form("/admin/login", "", &[("username", "Boss"), ("password", secret("good"))], LAN).await;
    assert_eq!((r.status, r.headers[header::LOCATION].to_str().unwrap()), (StatusCode::SEE_OTHER, "/admin"));
    let set = r.headers.get_all(header::SET_COOKIE).iter().next().unwrap().to_str().unwrap().to_owned();
    assert!(set.contains("HttpOnly") && set.contains("SameSite=Strict") && set.contains("Path=/") && set.contains("Secure"), "{set}");
    let r = s.form("/admin/login", "", &[("username", "mem"), ("password", secret("good"))], LAN).await;
    assert_eq!(r.headers[header::LOCATION], "/home", "a member never opens the panel");
}

#[tokio::test]
async fn a_wrong_password_shows_the_form_with_the_sentence_and_is_audited_without_the_typed_name() {
    let s = site("wrong").await;
    s.user("boss", "owner", false).await;
    let r = s.form("/admin/login", "", &[("username", "boss"), ("password", secret("wrong"))], LAN).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    assert!(r.body.contains("Wrong username or password."), "{}", r.body);
    assert_eq!(r.headers[header::CACHE_CONTROL], "no-store");
    let r = s.form("/admin/login", "", &[("username", "nobody-at-all"), ("password", "x")], LAN).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    let cookie = {
        let owner = s.acc.auth.get_by_username("boss").await.unwrap().unwrap();
        format!("relay_admin={}", s.acc.auth.start_session(&owner, "password", None, "").await.unwrap().token)
    };
    let log = s.get("/api/admin/audit?kind=auth", &cookie).await.json();
    let targets: Vec<&str> = log["entries"].as_array().unwrap().iter().map(|e| e["target"].as_str().unwrap()).collect();
    assert_eq!(targets, vec!["(unknown username)", "boss"]);
    assert!(!log.to_string().contains("nobody-at-all"), "what was typed as a username is never written");
}

#[tokio::test]
async fn five_failures_lock_the_address_and_only_the_fifth_is_audited() {
    let s = site("lock").await;
    s.user("boss", "owner", false).await;
    for _ in 0..5 {
        let r = s.form("/admin/login", "", &[("username", "boss"), ("password", secret("wrong"))], "10.9.9.9:1").await;
        assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    }
    let r = s.form("/admin/login", "", &[("username", "boss"), ("password", secret("good"))], "10.9.9.9:1").await;
    assert_eq!(r.status, StatusCode::TOO_MANY_REQUESTS, "even the right password is refused during the lock-out");
    assert!(r.body.contains("Too many attempts. Try again in 10 min."), "{}", r.body);
    let ok = s.form("/admin/login", "", &[("username", "boss"), ("password", secret("good"))], "10.9.9.8:1").await;
    assert_eq!(ok.status, StatusCode::SEE_OTHER, "another address is unaffected");
    let locked = s.acc.auth.search_audit(relay_auth::Search { kind: Some("auth.locked"), ..Default::default() }).await.unwrap();
    assert_eq!(locked.entries.len(), 1);
}

#[tokio::test]
async fn a_forwarded_address_counts_only_from_a_loopback_peer() {
    let s = site("xff").await;
    s.user("boss", "owner", false).await;
    let fail = |peer: &'static str| {
        let s = &s;
        async move {
            s.send(
                Method::POST,
                "/admin/login",
                &[("x-forwarded-for", "7.7.7.7")],
                Some(("application/x-www-form-urlencoded", format!("username=boss&password={}", secret("wrong")))),
                peer,
            )
            .await
        }
    };
    for _ in 0..5 {
        fail("192.168.1.9:1").await; // a LAN client's header is ignored: its own address is counted
    }
    assert!(s.acc.lockout.locked("192.168.1.9").is_some() && s.acc.lockout.locked("7.7.7.7").is_none());
    for _ in 0..5 {
        fail("127.0.0.1:1").await; // a loopback peer (the reverse proxy) speaks for the client
    }
    assert!(s.acc.lockout.locked("7.7.7.7").is_some());
}

#[tokio::test]
async fn the_api_wants_a_session_a_permission_and_a_finished_password_change() {
    let s = site("require").await;
    s.user("boss", "owner", false).await;
    s.user("view", "viewer", false).await;
    s.user("temp", "admin", true).await;
    let r = s.get("/api/admin/me", "").await;
    assert_eq!((r.status, r.json()), (StatusCode::UNAUTHORIZED, json!({"detail": "Sign in required"})));
    let boss = s.sign_in("boss").await;
    let me = s.get("/api/admin/me", &boss).await.json();
    assert_eq!(me["user"]["username"], "boss");
    assert_eq!((me["pin_set"].clone(), me["device"].clone()), (json!(false), json!({"trusted": false, "name": null})));
    assert!(me["permissions"].as_array().unwrap().iter().any(|p| p == "credentials.manage"));
    let view = s.sign_in("view").await;
    let r = s.get("/api/admin/users", &view).await;
    assert_eq!((r.status, r.json()), (StatusCode::FORBIDDEN, json!({"detail": "Not allowed"})));
    let temp = s.sign_in("temp").await;
    let r = s.get("/api/admin/users", &temp).await;
    assert_eq!((r.status, r.json()), (StatusCode::FORBIDDEN, json!({"detail": "password_change_required"})));
    assert_eq!(s.get("/api/admin/me", &temp).await.status, StatusCode::OK, "/me is how the page learns it must change it");
    let r = s.api(Method::POST, "/api/admin/me/password", &temp, json!({"current": secret("good"), "new": secret("another")})).await;
    assert_eq!(r.json(), json!({"ok": true}));
    assert_eq!(s.get("/api/admin/users", &temp).await.status, StatusCode::OK);
}

#[tokio::test]
async fn the_users_routes_follow_the_rules_and_leave_audit_entries() {
    let s = site("users").await;
    s.user("boss", "owner", false).await;
    s.user("adm", "admin", false).await;
    let boss = s.sign_in("boss").await;
    let adm = s.sign_in("adm").await;
    let r = s.api(Method::POST, "/api/admin/users", &adm, json!({"username": "newbie", "name": "New Bie", "role": "owner", "password": secret("good")})).await;
    assert_eq!((r.status, r.json()["detail"].clone()), (StatusCode::FORBIDDEN, json!("You cannot give a role higher than your own.")));
    let r = s.api(Method::POST, "/api/admin/users", &adm, json!({"username": "newbie", "name": "New Bie", "role": "operator", "password": secret("good")})).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    let id = r.json()["user"]["id"].as_i64().unwrap();
    assert_eq!(r.json()["user"]["must_change_password"], true);
    let dup = s.api(Method::POST, "/api/admin/users", &adm, json!({"username": "NEWBIE", "name": "x", "role": "viewer", "password": secret("good")})).await;
    assert_eq!(dup.status, StatusCode::CONFLICT);
    let r = s.api(Method::PUT, &format!("/api/admin/users/{id}"), &adm, json!({"role": "viewer", "disabled": "yes"})).await;
    assert_eq!((r.status, r.json()["detail"].clone()), (StatusCode::BAD_REQUEST, json!("That request is not valid.")));
    let r = s.api(Method::PUT, &format!("/api/admin/users/{id}"), &adm, json!({"role": "viewer"})).await;
    assert_eq!(r.json()["user"]["role"], "viewer");
    let owner_id = s.acc.auth.get_by_username("boss").await.unwrap().unwrap().id;
    let r = s.api(Method::DELETE, &format!("/api/admin/users/{owner_id}"), &adm, json!({})).await;
    assert_eq!((r.status, r.json()["detail"].clone()), (StatusCode::FORBIDDEN, json!("You cannot change an account with a higher role than yours.")));
    let r = s.api(Method::POST, &format!("/api/admin/users/{id}/password"), &adm, json!({"password": secret("reset")})).await;
    assert_eq!(r.json(), json!({"ok": true}));
    assert_eq!(s.api(Method::POST, &format!("/api/admin/users/{id}/signout"), &adm, json!({})).await.status, StatusCode::OK);
    assert_eq!(s.api(Method::POST, "/api/admin/users/9999/signout", &adm, json!({})).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.api(Method::DELETE, &format!("/api/admin/users/{id}"), &adm, json!({})).await.json(), json!({"ok": true}));
    let list = s.get("/api/admin/users", &boss).await.json();
    assert_eq!(list["roles"][4], json!({"id": "owner", "label": "Owner", "rank": 5}));
    assert!(list["permissions"].as_array().unwrap().len() >= 38);
    assert_eq!(list["users"].as_array().unwrap().len(), 2);
    let log = s.get("/api/admin/audit?kind=user", &boss).await.json();
    let actions: Vec<&str> = log["entries"].as_array().unwrap().iter().map(|e| e["action"].as_str().unwrap()).collect();
    assert_eq!(actions, vec!["user.delete", "user.signout", "user.password_reset", "user.update", "user.create"]);
    let update = log["entries"].as_array().unwrap().iter().find(|e| e["action"] == "user.update").unwrap();
    assert_eq!(update["summary"], "Moved New Bie from Operator to Viewer");
    assert_eq!(update["detail"]["changes"]["role"], json!(["operator", "viewer"]));
    assert_eq!(update["actor_username"], "adm");
}

#[tokio::test]
async fn an_api_token_acts_as_its_user_less_the_barred_permissions_and_not_on_its_own_routes() {
    let s = site("token").await;
    s.user("adm", "admin", false).await;
    let adm = s.sign_in("adm").await;
    let made = s.api(Method::POST, "/api/admin/me/tokens", &adm, json!({"name": "ci", "days": 30})).await;
    assert_eq!(made.headers[header::CACHE_CONTROL], "no-store");
    let secret = made.json()["secret"].as_str().unwrap().to_owned();
    assert!(secret.starts_with("relay_"));
    let bearer = format!("Bearer {secret}");
    let as_token = |method, uri: &'static str| {
        let (s, bearer) = (&s, bearer.clone());
        async move { s.send(method, uri, &[("authorization", &bearer)], None, LAN).await }
    };
    assert_eq!(as_token(Method::GET, "/api/admin/me").await.status, StatusCode::OK);
    assert_eq!(as_token(Method::GET, "/api/admin/users").await.status, StatusCode::FORBIDDEN, "users.view is barred to tokens");
    assert_eq!(as_token(Method::GET, "/api/admin/audit").await.status, StatusCode::FORBIDDEN);
    let r = as_token(Method::GET, "/api/admin/me/tokens").await;
    assert_eq!((r.status, r.json()["detail"].clone()), (StatusCode::FORBIDDEN, json!("Not allowed")), "a token cannot manage tokens");
    let r = s.send(Method::GET, "/api/admin/me", &[("authorization", "Bearer relay_nope"), ("cookie", &adm)], None, LAN).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED, "a bad token never falls back to the cookie");
    assert_eq!(r.json()["detail"], "That API token is not valid, has expired, or its user is turned off.");
    let r = s.send(Method::GET, "/api/admin/me", &[("authorization", "Basic abc")], None, LAN).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    let listed = s.get("/api/admin/me/tokens", &adm).await.json();
    assert_eq!((listed["expiry_days"].clone(), listed["default_days"].clone()), (json!([30, 90, 365, null]), json!(90)));
    assert_eq!(s.api(Method::POST, "/api/admin/me/tokens", &adm, json!({"name": "x", "days": true})).await.status, StatusCode::BAD_REQUEST);
    let id = listed["tokens"][0]["id"].as_i64().unwrap();
    assert_eq!(s.api(Method::DELETE, &format!("/api/admin/me/tokens/{id}"), &adm, json!({})).await.json(), json!({"ok": true}));
    assert_eq!(as_token(Method::GET, "/api/admin/me").await.status, StatusCode::UNAUTHORIZED);
}

#[tokio::test]
async fn a_pin_signs_in_from_a_trusted_browser_and_the_device_cookie_is_long_lived() {
    let s = site("pin").await;
    s.user("boss", "owner", false).await;
    s.user("fran", "operator", false).await;
    let boss = s.sign_in("boss").await;
    let fran = s.sign_in("fran").await;
    let set = s.api(Method::POST, "/api/admin/me/pin", &fran, json!({"password": secret("good"), "pin": "4321"})).await;
    assert_eq!(set.json(), json!({"ok": true, "pin_set": true}));
    let fran_id = s.acc.auth.get_by_username("fran").await.unwrap().unwrap().id;
    let r = s.form("/admin/login/pin", "", &[("user_id", &fran_id.to_string()), ("pin", "4321")], LAN).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED, "not from a browser nobody trusted");
    assert!(r.body.contains("This browser is not a trusted device."), "{}", r.body);
    let trust = s.api(Method::POST, "/api/admin/trusted-devices", &boss, json!({"name": "Booth iPad"})).await;
    assert_eq!(trust.status, StatusCode::OK, "{}", trust.body);
    let device = format!("relay_device={}", trust.cookie("relay_device").unwrap());
    assert!(trust.headers.get_all(header::SET_COOKIE).iter().next().unwrap().to_str().unwrap().contains(&format!("Max-Age={}", 2 * 365 * 24 * 3600)));
    let r = s.form("/admin/login/pin", &device, &[("user_id", &fran_id.to_string()), ("pin", "0000")], LAN).await;
    assert_eq!(r.status, StatusCode::UNAUTHORIZED);
    assert!(r.body.contains("That PIN is not right."), "{}", r.body);
    let r = s.form("/admin/login/pin", &device, &[("user_id", &fran_id.to_string()), ("pin", "4321")], LAN).await;
    assert_eq!((r.status, r.headers[header::LOCATION].to_str().unwrap()), (StatusCode::SEE_OTHER, "/admin"));
    assert!(r.headers.get_all(header::SET_COOKIE).iter().next().unwrap().to_str().unwrap().contains("Max-Age=43200"), "a PIN session lasts 12 hours");
    let devices = s.get("/api/admin/trusted-devices", &format!("{boss}; {device}")).await.json();
    assert_eq!(devices["devices"][0]["name"], "Booth iPad");
    assert_eq!(devices["this"], devices["devices"][0]["id"]);
    let id = devices["devices"][0]["id"].as_i64().unwrap();
    assert_eq!(s.api(Method::DELETE, &format!("/api/admin/trusted-devices/{id}"), &boss, json!({})).await.json(), json!({"ok": true}));
    assert_eq!(s.api(Method::DELETE, &format!("/api/admin/trusted-devices/{id}"), &boss, json!({})).await.status, StatusCode::NOT_FOUND);
    assert_eq!(s.api(Method::DELETE, "/api/admin/me/pin", &fran, json!({})).await.json(), json!({"ok": true, "pin_set": false}));
}

#[tokio::test]
async fn the_password_form_and_sign_out_end_sessions() {
    let s = site("logout").await;
    s.user("erin", "operator", true).await;
    let cookie = s.sign_in("erin").await;
    let r = s.form("/admin/password", &cookie, &[("current", secret("good")), ("new", secret("another")), ("confirm", secret("diff"))], LAN).await;
    assert_eq!(r.status, StatusCode::BAD_REQUEST);
    assert!(r.body.contains("The two passwords do not match."));
    let r = s.form("/admin/password", &cookie, &[("current", secret("wrong")), ("new", secret("another")), ("confirm", secret("another"))], LAN).await;
    assert_eq!(r.status, StatusCode::FORBIDDEN);
    assert!(r.body.contains("The password is wrong."));
    let r = s.form("/admin/password", &cookie, &[("current", secret("good")), ("new", secret("another")), ("confirm", secret("another"))], LAN).await;
    assert_eq!((r.status, r.headers[header::LOCATION].to_str().unwrap()), (StatusCode::SEE_OTHER, "/admin"));
    let r = s.form("/admin/password", "", &[("current", "x")], LAN).await;
    assert_eq!(r.headers[header::LOCATION], "/admin", "no session: back to the sign-in");
    assert_eq!(s.get("/api/admin/me", &cookie).await.status, StatusCode::OK);
    let out = s.form("/admin/logout", &cookie, &[], LAN).await;
    assert_eq!(out.headers[header::LOCATION], "/admin");
    assert!(out.headers.get_all(header::SET_COOKIE).iter().next().unwrap().to_str().unwrap().contains("Max-Age=0"));
    assert_eq!(s.get("/api/admin/me", &cookie).await.status, StatusCode::UNAUTHORIZED, "the copied cookie is dead too");
}

#[tokio::test]
async fn the_setup_token_acts_as_an_owner_only_while_there_are_no_accounts() {
    let s = site("setup").await;
    std::fs::write(s.dir.join("config.json"), r#"{"admin_token": "letmein-token"}"#).unwrap();
    let ask = |token: &'static str| {
        let s = &s;
        async move { s.send(Method::GET, "/api/admin/users", &[("x-admin-token", token)], None, LAN).await }
    };
    assert_eq!(ask("letmein-token").await.status, StatusCode::OK);
    assert_eq!(ask("wrong-token!!").await.status, StatusCode::UNAUTHORIZED);
    s.user("boss", "owner", false).await;
    assert_eq!(ask("letmein-token").await.status, StatusCode::UNAUTHORIZED, "once an account exists the token is dead");
}

#[tokio::test]
async fn a_token_owner_revokes_anyones_token_and_the_audit_page_is_for_admins() {
    let s = site("tokens-all").await;
    s.user("boss", "owner", false).await;
    s.user("adm", "admin", false).await;
    let (boss, adm) = (s.sign_in("boss").await, s.sign_in("adm").await);
    s.api(Method::POST, "/api/admin/me/tokens", &adm, json!({"name": "ci"})).await;
    assert_eq!(s.get("/api/admin/tokens", &adm).await.status, StatusCode::FORBIDDEN);
    let all = s.get("/api/admin/tokens", &boss).await.json();
    let id = all["tokens"][0]["id"].as_i64().unwrap();
    assert_eq!(s.api(Method::DELETE, &format!("/api/admin/tokens/{id}"), &boss, json!({})).await.json(), json!({"ok": true}));
    let log = s.get("/api/admin/audit?kind=token", &adm).await.json();
    assert_eq!(log["entries"][0]["summary"], "Revoked adm's API token");
    assert_eq!(log["keep_days"], 90);
    assert_eq!(s.get("/api/admin/audit?who=system", &adm).await.json()["entries"].as_array().unwrap().len(), 0);
    assert_eq!(s.get("/api/admin/audit?q=ci&days=1", &adm).await.json()["entries"].as_array().unwrap().len(), 2);
}
