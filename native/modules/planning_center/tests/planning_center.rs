//! What `tests/test_planningcenter.py` checks of the Python module (save, test, service types, disconnect, the client's headers and rate-limit
//! handling, that the secret never leaves config.json), against a fake Planning Center on 127.0.0.1 over real sockets, plus the plan rules.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{Method, Request, StatusCode};
use axum::response::Response;
use base64::Engine;
use http_body_util::BodyExt;
use relay_core::future::BoxFuture;
use relay_core::{Authorizer, Host, Module, Net, SettingsStore, Shutdown};
use relay_module_planning_center::client::{Client, Kind, Services, Sleeper};
use relay_module_planning_center::follower::MakeClient;
use relay_module_planning_center::plan::{self, Zone};
use relay_module_planning_center::{settings, PlanningCenter};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tower::ServiceExt;

const APP_ID: &str = "appid-1234";
const SECRET: &str = "s3cret-pat-value";

#[derive(Default)]
struct World {
    seen: Vec<(String, HashMap<String, String>)>,
    limit_429: u32,
    retry_after: String,
    forbid: bool,
}

/// A fake Planning Center: answers the service types list, a 401 for a wrong token, a 403 or a run of 429s on request.
async fn fake() -> (String, Arc<Mutex<World>>) {
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let base = format!("http://{}", listener.local_addr().unwrap());
    let world = Arc::new(Mutex::new(World { retry_after: "0".into(), ..World::default() }));
    let w = Arc::clone(&world);
    tokio::spawn(async move {
        loop {
            let Ok((mut sock, _)) = listener.accept().await else { return };
            let w = Arc::clone(&w);
            tokio::spawn(async move {
                let mut buf = vec![0u8; 8192];
                let n = sock.read(&mut buf).await.unwrap_or(0);
                let text = String::from_utf8_lossy(&buf[..n]).into_owned();
                let mut lines = text.split("\r\n");
                let path = lines.next().unwrap_or("").split(' ').nth(1).unwrap_or("").to_owned();
                let headers: HashMap<String, String> = lines.take_while(|l| !l.is_empty()).filter_map(|l| l.split_once(": ")).map(|(k, v)| (k.to_lowercase(), v.to_owned())).collect();
                let want = format!("Basic {}", base64::engine::general_purpose::STANDARD.encode(format!("{APP_ID}:{SECRET}")));
                let (status, extra, body) = {
                    let mut w = w.lock().unwrap();
                    w.seen.push((path.clone(), headers.clone()));
                    if headers.get("authorization") != Some(&want) {
                        (401, String::new(), "HTTP Basic: Access denied.\n".to_owned())
                    } else if w.forbid {
                        (403, String::new(), json!({"errors": [{"code": "403"}]}).to_string())
                    } else if w.limit_429 > 0 {
                        w.limit_429 -= 1;
                        (429, format!("Retry-After: {}\r\nX-PCO-API-Request-Rate-Count: 118\r\nX-PCO-API-Request-Rate-Limit: 100\r\nX-PCO-API-Request-Rate-Period: 20 seconds\r\n", w.retry_after), "{}".to_owned())
                    } else {
                        let types = json!({"data": [
                            {"id": "42", "type": "ServiceType", "attributes": {"name": "Sunday Evening"}},
                            {"id": "7", "type": "ServiceType", "attributes": {"name": "Sunday Morning"}},
                            {"id": "8", "type": "ServiceType", "attributes": {"name": "Old Youth", "archived_at": "2020-01-01T12:00:00Z"}},
                            {"id": "9", "type": "ServiceType", "attributes": {"name": "Gone", "deleted_at": "2020-01-01T12:00:00Z"}}], "links": {}});
                        (200, "X-PCO-API-Request-Rate-Count: 3\r\nX-PCO-API-Request-Rate-Limit: 100\r\nX-PCO-API-Request-Rate-Period: 20 seconds\r\n".to_owned(), types.to_string())
                    }
                };
                let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n{extra}\r\n{body}", body.len());
                let _ = sock.write_all(reply.as_bytes()).await;
            });
        }
    });
    (base, world)
}

fn recording_sleeper() -> (Sleeper, Arc<Mutex<Vec<f64>>>) {
    let slept = Arc::new(Mutex::new(Vec::new()));
    let s = Arc::clone(&slept);
    (Arc::new(move |secs| {
        s.lock().unwrap().push(secs);
        Box::pin(async {})
    }), slept)
}

#[tokio::test]
async fn the_client_sends_its_headers_and_reads_the_rate_limit() {
    let (base, world) = fake().await;
    let (sleeper, _) = recording_sleeper();
    let client = Client::new(Net::unrouted(), APP_ID, SECRET).with_base(&base).with_sleeper(sleeper);
    let types = client.service_types().await.unwrap();
    let names: Vec<&str> = types.as_array().unwrap().iter().map(|t| t["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["Sunday Evening", "Sunday Morning"], "by name, archived and deleted left out");
    let (path, headers) = world.lock().unwrap().seen.last().cloned().unwrap();
    assert!(path.starts_with("/services/v2/service_types"), "{path}");
    assert_eq!(headers["x-pco-api-version"], "2018-11-01");
    assert!(headers["user-agent"].starts_with("Relay/") && headers["user-agent"].ends_with("(church caption relay)"));
    assert_eq!((client.rate().count, client.rate().limit, client.rate().period), (Some(3), Some(100), Some(20)), "'20 seconds' parses");

    // At the ceiling for the window, it waits the period out first.
    {
        let mut w = world.lock().unwrap();
        w.limit_429 = 0;
    }
    let busy = Client::new(Net::unrouted(), APP_ID, SECRET).with_base(&base);
    busy.service_types().await.unwrap();
}

#[tokio::test]
async fn a_429_is_waited_out_and_a_persistent_one_gives_up() {
    let (base, world) = fake().await;
    let (sleeper, slept) = recording_sleeper();
    let client = Client::new(Net::unrouted(), APP_ID, SECRET).with_base(&base).with_sleeper(sleeper);
    {
        let mut w = world.lock().unwrap();
        w.limit_429 = 2;
        w.retry_after = "7".into();
    }
    client.service_types().await.unwrap();
    assert_eq!(*slept.lock().unwrap(), [7.0, 7.0], "waited the Retry-After each time");
    slept.lock().unwrap().clear();
    {
        let mut w = world.lock().unwrap();
        w.limit_429 = 99;
        w.retry_after = "3".into();
    }
    let e = client.service_types().await.unwrap_err();
    assert_eq!((e.kind, e.retry_after), (Kind::Rate, Some(3.0)));
    assert_eq!(*slept.lock().unwrap(), [3.0, 3.0], "after three tries, waiting between");
    world.lock().unwrap().limit_429 = 0;
    // The reading now says count 118 of 100: the next call waits the 20 second period before it spends a request.
    slept.lock().unwrap().clear();
    client.service_types().await.unwrap();
    assert_eq!(*slept.lock().unwrap(), [20.0]);
}

#[tokio::test]
async fn bad_tokens_and_dead_servers_get_fixed_sentences() {
    let (base, world) = fake().await;
    let bad = Client::new(Net::unrouted(), APP_ID, "wrong").with_base(&base);
    let e = bad.service_types().await.unwrap_err();
    assert_eq!(e.kind, Kind::Auth);
    assert_eq!(e.operator_message(), "Planning Center did not accept that application ID and secret.");
    world.lock().unwrap().forbid = true;
    let good = Client::new(Net::unrouted(), APP_ID, SECRET).with_base(&base);
    let e = good.service_types().await.unwrap_err();
    assert!(e.operator_message().contains("does not have access"), "403 is told apart from a bad token");
    let dead = Client::new(Net::unrouted(), APP_ID, SECRET).with_base("http://127.0.0.1:1");
    let e = dead.service_types().await.unwrap_err();
    assert_eq!(e.kind, Kind::Network);
    assert!(!e.to_string().contains(SECRET));
    assert_eq!(e.operator_message(), "Could not reach Planning Center. Check this machine's internet connection.");
    let weird = relay_module_planning_center::client::PcError::new(Kind::Http, "{\"detail\": \"internal stuff\"}").with_status(422);
    assert_eq!(weird.operator_message(), "Planning Center refused the request (HTTP 422).");
}

// ------------------------------------------------------------------------------------------------------ the routes
struct Store(Mutex<HashMap<String, Value>>);

impl SettingsStore for Store {
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
    fn replace(&self, key: &str, value: &Value) -> Result<(), relay_core::AppError> {
        self.0.lock().unwrap().insert(key.to_owned(), value.clone());
        Ok(())
    }
    fn add_secret(&self, _key: &str, _field: &str) {}
}

#[derive(Default)]
struct Gate {
    refuse: Mutex<Vec<&'static str>>,
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
}

struct Rig {
    router: axum::Router,
    store: Arc<Store>,
    gate: Arc<Gate>,
    world: Arc<Mutex<World>>,
}

async fn rig() -> Rig {
    let (base, world) = fake().await;
    let store = Arc::new(Store(Mutex::new(HashMap::new())));
    let host = Host::new(Shutdown::new()).with_store(store.clone());
    let gate = Arc::new(Gate::default());
    host.set_authorizer(gate.clone());
    let maker: MakeClient = Arc::new(move |s: &Value| {
        let (id, secret) = (s["app_id"].as_str().unwrap_or(""), s["secret"].as_str().unwrap_or(""));
        (!id.is_empty() && !secret.is_empty()).then(|| Arc::new(Client::new(Net::unrouted(), id, secret).with_base(&base).with_sleeper(Arc::new(|_| Box::pin(async {})))) as Arc<dyn Services>)
    });
    let module = PlanningCenter::with_client(maker);
    module.attach(&host);
    Rig { router: module.routes(), store, gate, world }
}

async fn send(r: &Rig, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value, String) {
    let req = Request::builder().method(method).uri(uri).header("content-type", "application/json").body(body.map_or_else(Body::empty, |b| Body::from(b.to_string()))).unwrap();
    let res = r.router.clone().oneshot(req).await.unwrap();
    let status = res.status();
    let bytes = res.into_body().collect().await.unwrap().to_bytes();
    let text = String::from_utf8_lossy(&bytes).into_owned();
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null), text)
}

const URL: &str = "/api/admin/planning-center";

fn off() -> Value {
    json!({"connected": false, "service_types": [], "show_notes": false, "rundown_screen": false})
}

#[tokio::test]
async fn connecting_testing_following_and_disconnecting() {
    let r = rig().await;
    let (st, _, _) = send(&r, Method::POST, &format!("{URL}/test"), Some(json!({}))).await;
    assert_eq!(st, StatusCode::BAD_REQUEST, "testing with no token is refused");
    assert!(r.world.lock().unwrap().seen.is_empty(), "no request reached Planning Center");
    let (st, _, _) = send(&r, Method::POST, URL, Some(json!({"app_id": APP_ID}))).await;
    assert_eq!(st, StatusCode::BAD_REQUEST, "saving only an ID is refused");

    let (st, body, text) = send(&r, Method::POST, URL, Some(json!({"app_id": APP_ID, "secret": SECRET}))).await;
    assert_eq!((st, &body), (StatusCode::OK, &json!({"planning_center": {"connected": true, "service_types": [], "show_notes": false, "rundown_screen": false}})));
    assert!(!text.contains(SECRET), "the secret is not in the response");
    let (st, body, _) = send(&r, Method::POST, &format!("{URL}/test"), Some(json!({}))).await;
    assert_eq!(st, StatusCode::OK);
    assert_eq!(body["service_types"].as_array().unwrap().len(), 2);

    let (_, body, _) = send(&r, Method::POST, &format!("{URL}/service-types"), Some(json!({"ids": ["7", "7", "x", "42"]}))).await;
    assert_eq!(body["planning_center"]["service_types"], json!(["7", "42"]), "only numeric ids kept, once each");
    let (st, _, _) = send(&r, Method::POST, &format!("{URL}/service-types"), Some(json!({"ids": "7"}))).await;
    assert_eq!(st, StatusCode::BAD_REQUEST);
    send(&r, Method::POST, URL, Some(json!({"app_id": "", "secret": ""}))).await;
    assert_eq!(settings::clean_stored(&r.store.read("planning_center").unwrap())["service_types"], json!(["7", "42"]), "blank fields keep the token and the choice");
    send(&r, Method::POST, URL, Some(json!({"app_id": APP_ID, "secret": format!("{SECRET}x")}))).await;
    assert_eq!(settings::clean_stored(&r.store.read("planning_center").unwrap())["service_types"], json!([]), "a different token resets the choice");

    let (st, body, text) = send(&r, Method::POST, &format!("{URL}/test"), Some(json!({}))).await;
    assert_eq!((st, body["error"].as_str().unwrap().contains("did not accept")), (StatusCode::BAD_REQUEST, true), "a bad token gets a sentence");
    assert!(!text.contains(SECRET));

    send(&r, Method::POST, URL, Some(json!({"app_id": APP_ID, "secret": SECRET}))).await;
    r.world.lock().unwrap().forbid = true;
    let (st, body, _) = send(&r, Method::POST, &format!("{URL}/test"), Some(json!({}))).await;
    assert_eq!((st, body["error"].as_str().unwrap().contains("does not have access")), (StatusCode::BAD_REQUEST, true));
    r.world.lock().unwrap().forbid = false;

    let (_, body, _) = send(&r, Method::DELETE, URL, None).await;
    assert_eq!(body["planning_center"], off());
    assert!(!r.store.read("planning_center").unwrap().to_string().contains(SECRET), "the secret is gone from config.json");
    let before = r.world.lock().unwrap().seen.len();
    let (st, _, _) = send(&r, Method::POST, &format!("{URL}/test"), Some(json!({}))).await;
    assert_eq!((st, r.world.lock().unwrap().seen.len()), (StatusCode::BAD_REQUEST, before), "nothing reaches Planning Center once disconnected");
}

#[tokio::test]
async fn roles_may_read_or_change_only_what_they_hold() {
    let r = rig().await;
    send(&r, Method::POST, URL, Some(json!({"app_id": APP_ID, "secret": SECRET}))).await;
    r.gate.refuse.lock().unwrap().extend(["planning_center.manage"]);
    for (path, body) in [("", json!({"app_id": "x", "secret": "y"})), ("/test", json!({})), ("/service-types/refresh", json!({})), ("/service-types", json!({"ids": []})), ("/notes", json!({"show": true}))] {
        assert_eq!(send(&r, Method::POST, &format!("{URL}{path}"), Some(body)).await.0, StatusCode::FORBIDDEN, "{path}");
    }
    assert_eq!(send(&r, Method::DELETE, URL, None).await.0, StatusCode::FORBIDDEN, "nor disconnect");
    assert_eq!(send(&r, Method::GET, &format!("{URL}/live"), None).await.0, StatusCode::OK, "an operator reads the live view");
    r.gate.refuse.lock().unwrap().extend(["planning_center.control"]);
    assert_eq!(send(&r, Method::POST, &format!("{URL}/plan"), Some(json!({"service_type": "7", "plan": ""}))).await.0, StatusCode::FORBIDDEN, "a viewer may not pick a plan");
    let (_, _, text) = send(&r, Method::GET, &format!("{URL}/live"), None).await;
    assert!(!text.contains(SECRET) && !text.contains(APP_ID), "the live view leaks no secret");
}

// ------------------------------------------------------------------------------------------------------ plan rules
fn time(id: &str, kind: &str, start: f64, end: Option<f64>) -> Value {
    json!({"id": id, "type": kind, "name": "", "starts_at": start, "ends_at": end, "live_starts_at": null, "live_ends_at": null})
}

#[test]
fn today_is_the_plan_with_a_service_today_in_the_organisations_zone() {
    let zone = Zone::for_name("Eastern Time (US & Canada)").unwrap();
    assert_eq!(zone.name, "America/New_York");
    assert!(Zone::for_name("Nowhere/Land").is_none());
    // 2026-11-15 14:00 UTC = 09:00 in New York; the evening plan starts 23:00 UTC = 18:00.
    let morning = 1_794_751_200.0_f64;
    let plans = vec![
        json!({"id": "1", "title": "Morning", "times": [time("a", "service", morning, Some(morning + 3600.0))]}),
        json!({"id": "2", "title": "Evening", "times": [time("b", "service", morning + 9.0 * 3600.0, None)]}),
        json!({"id": "3", "title": "Tomorrow", "times": [time("c", "service", morning + 86_400.0, None)]}),
    ];
    let pick = |now: f64| plan::pick_plan(&plans, now, &zone).map(|p| p["id"].as_str().unwrap().to_owned());
    assert_eq!(pick(morning - 600.0), Some("1".into()), "before the first, the first");
    assert_eq!(pick(morning + 7200.0), Some("2".into()), "once the morning is over, the evening");
    assert_eq!(pick(morning + 14.0 * 3600.0), Some("2".into()), "after the last, the last");
    assert_eq!(plan::pick_plan(&[plans[2].clone()], morning, &zone), None, "no service today, no plan");
    assert!(plan::near_service(Some(&plans[0]), morning - 3000.0) && !plan::near_service(Some(&plans[0]), morning - 4000.0));
}

#[test]
fn timers_are_worked_out_from_the_current_item() {
    let items: Vec<Value> = [("i1", 600, 1), ("i2", 300, 2)].iter().map(|(id, len, seq)| json!({"id": id, "length": len, "sequence": seq, "position": "during"})).collect();
    let plan = json!({"id": "p", "times": [{"id": "t", "type": "service", "starts_at": 1000.0, "ends_at": null, "live_starts_at": 1000.0, "live_ends_at": null}]});
    let live = json!({"current": {"id": "x", "item": "i2", "plan_time": "t", "live_start_at": 1700.0, "live_end_at": null}, "next": null, "controller": {"id": "9", "name": "A"}});
    let t = plan::timers(Some(&plan), &items, Some(&live), 1800.0, None);
    assert_eq!((t["item_left_s"].as_f64(), t["service_over_s"].as_f64()), (Some(200.0), Some(100.0)), "the first item ran 100 s long");
    let idle = plan::timers(Some(&plan), &items, None, 1800.0, None);
    assert!(idle["current_item"].is_null() && idle["item_left_s"].is_null());
}

#[test]
fn stored_settings_are_cleaned_as_python_cleans_them() {
    let c = settings::clean_stored(&json!({"app_id": " id ", "secret": "s", "service_types": ["7", "7", "x", 42], "show_notes": "yes",
        "plan_overrides": {"7": {"plan": "9", "day": "2026-11-15"}, "8": {"plan": "9", "day": "2026-11-15"}, "42": {"plan": "z", "day": "2026-11-15"}}}));
    assert_eq!((c["app_id"].as_str(), c["service_types"].clone(), c["show_notes"].clone()), (Some("id"), json!(["7", "42"]), json!(false)));
    assert_eq!(c["plan_overrides"], json!({"7": {"plan": "9", "day": "2026-11-15"}}), "only a followed type with a real plan and a day");
    assert_eq!(c["rundown"]["leader_position"], "Worship Leader");
    assert_eq!(settings::clean_stored(&json!({"signage": {"enabled": "yes", "playlist": 1}}))["signage"], settings::default_signage());
}
