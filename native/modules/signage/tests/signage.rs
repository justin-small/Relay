//! Signage on a real SQLite file made by the module's own migrations, with a fake directory of core screens: what `tests/test_signage.py`,
//! `test_signage_routes.py` and `test_signage_web.py` check of the Python module, minus the sources other modules provide.

use std::collections::HashMap;
use std::net::SocketAddr;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{header, Method, Request, StatusCode};
use axum::response::Response;
use http_body_util::BodyExt;
use relay_core::future::BoxFuture;
use relay_core::screens::{AlertsCap, ScreenDirectory, ScreenResult, ALERTS};
use relay_core::timewin::Date;
use relay_core::{Authorizer, Host, Module, SettingsStore, Shutdown};
use relay_module_signage::live::Live;
use relay_module_signage::media::{self, Media};
use relay_module_signage::store::{Error, Store};
use relay_module_signage::{Service, Signage};
use serde_json::{json, Value};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};
use tower::ServiceExt;

/// An in-memory directory of core screens.
#[derive(Default)]
struct Directory {
    screens: Mutex<Vec<(i64, String, Vec<String>)>>,
}

impl ScreenDirectory for Directory {
    fn by_key<'a>(&'a self, key: &'a str) -> BoxFuture<'a, ScreenResult<Option<Value>>> {
        Box::pin(async move { Ok(self.screens.lock().unwrap().iter().find(|(_, _, k)| k.iter().any(|x| x == key)).map(|(id, n, _)| json!({"id": id, "name": n}))) })
    }
    fn adopt<'a>(&'a self, key: &'a str, name: &'a str, _heard: bool) -> BoxFuture<'a, ScreenResult<Value>> {
        Box::pin(async move {
            let mut s = self.screens.lock().unwrap();
            if let Some((id, n, k)) = s.iter_mut().find(|(_, n, _)| n.to_lowercase() == name.to_lowercase()) {
                k.push(key.to_owned());
                return Ok(json!({"id": *id, "name": n.clone()}));
            }
            let id = s.len() as i64 + 1;
            s.push((id, name.to_owned(), vec![key.to_owned()]));
            Ok(json!({"id": id, "name": name}))
        })
    }
    fn move_key<'a>(&'a self, old: &'a str, new: &'a str) -> BoxFuture<'a, ScreenResult<()>> {
        Box::pin(async move {
            for (_, _, k) in self.screens.lock().unwrap().iter_mut() {
                for x in k.iter_mut().filter(|x| x.as_str() == old) {
                    *x = new.to_owned();
                }
            }
            Ok(())
        })
    }
    fn forget_key<'a>(&'a self, key: &'a str) -> BoxFuture<'a, ScreenResult<()>> {
        Box::pin(async move {
            for (_, _, k) in self.screens.lock().unwrap().iter_mut() {
                k.retain(|x| x != key);
            }
            Ok(())
        })
    }
    fn rename<'a>(&'a self, id: i64, name: &'a str, _panel_ok: bool) -> BoxFuture<'a, ScreenResult<i64>> {
        Box::pin(async move {
            if let Some(s) = self.screens.lock().unwrap().iter_mut().find(|(i, _, _)| *i == id) {
                s.1 = name.to_owned();
            }
            Ok(id)
        })
    }
    fn keys<'a>(&'a self, id: i64) -> BoxFuture<'a, ScreenResult<Vec<String>>> {
        Box::pin(async move { Ok(self.screens.lock().unwrap().iter().find(|(i, _, _)| *i == id).map(|(_, _, k)| k.clone()).unwrap_or_default()) })
    }
    fn get<'a>(&'a self, id: i64) -> BoxFuture<'a, ScreenResult<Option<Value>>> {
        Box::pin(async move {
            Ok(self.screens.lock().unwrap().iter().find(|(i, _, _)| *i == id).map(|(i, n, _)| {
                json!({"id": i, "name": n, "normal_kind": "", "normal_ref": "", "during_kind": "", "during_ref": "", "during_schedule": "", "hold_kind": "", "hold_ref": "", "last_board": ""})
            }))
        })
    }
    fn touch<'a>(&'a self, _id: i64, _address: &'a str) -> BoxFuture<'a, ScreenResult<()>> {
        Box::pin(async { Ok(()) })
    }
}

/// Settings held in memory.
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

/// Every route open, and every audit entry kept.
#[derive(Default)]
struct Open(Mutex<Vec<String>>);

impl Authorizer for Open {
    fn require<'a>(&'a self, _r: &'a Parts, _p: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async { Ok(()) })
    }
    fn note<'a>(&'a self, _r: &'a Parts, action: &'a str, _s: &'a str, _t: &'a str, _d: Option<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move { self.0.lock().unwrap().push(action.to_owned()) })
    }
}

struct Rig {
    store: Arc<Service>,
    router: axum::Router,
    audit: Arc<Open>,
    dir: std::path::PathBuf,
    host: Host,
    settings: Arc<Settings>,
}

impl Drop for Rig {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

fn st(r: &Rig) -> &Store {
    &r.store.store
}

async fn rig() -> Rig {
    let dir = std::env::temp_dir().join(format!("relay-signage-{}-{:08x}", std::process::id(), rand::random::<u32>()));
    std::fs::create_dir_all(&dir).unwrap();
    let pool = SqlitePoolOptions::new().max_connections(2).connect_with(SqliteConnectOptions::new().filename(dir.join("relay.db")).create_if_missing(true).foreign_keys(true)).await.unwrap();
    let settings = Arc::new(Settings(Mutex::new(HashMap::from([
        ("signage".to_owned(), json!({"port": 8090, "override_until": "next_change", "alert_screens": "all", "alert_groups": []})),
        ("timezone".to_owned(), json!("America/New_York")),
        ("schedules".to_owned(), json!([{"id": "svc1", "name": "Sunday services", "enabled": true, "windows": [{"repeat": "weekly", "days": [0], "start_time": "10:00", "stop_time": "12:00"}]}])),
    ]))));
    let host = Host::new(Shutdown::new()).with_store(settings.clone());
    let audit = Arc::new(Open::default());
    host.set_authorizer(audit.clone());
    let media = Media::new(&dir, &dir.join("backups"));
    let store = Store::open(pool, Arc::new(Directory::default()), Arc::new(Live::default()), host.clone(), media).await.unwrap();
    let service = Service { store, app_dir: std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../app") };
    let module = Signage::with_service(host.clone(), service);
    let router = module.routes();
    let store = module_service(&module);
    Rig { store, router, audit, dir, host, settings }
}

fn module_service(m: &Signage) -> Arc<Service> {
    m.slot().lock().unwrap().clone().unwrap()
}

async fn send(r: &Rig, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value, axum::http::HeaderMap) {
    let (status, bytes, headers) = send_raw(r, method, uri, body.map(|b| b.to_string().into_bytes()), "application/json").await;
    (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null), headers)
}

async fn send_raw(r: &Rig, method: Method, uri: &str, body: Option<Vec<u8>>, ctype: &str) -> (StatusCode, Vec<u8>, axum::http::HeaderMap) {
    let mut req = Request::builder().method(method).uri(uri);
    if body.is_some() {
        req = req.header(header::CONTENT_TYPE, ctype);
    }
    let mut req = req.body(body.map_or_else(Body::empty, Body::from)).unwrap();
    req.extensions_mut().insert("127.0.0.1:1".parse::<SocketAddr>().unwrap());
    let resp = r.router.clone().oneshot(req).await.unwrap();
    let (parts, body) = resp.into_parts();
    (parts.status, body.collect().await.unwrap().to_bytes().to_vec(), parts.headers)
}

/// A tiny JPEG-shaped file (the type is read from its first bytes) with a distinguishing tail.
fn jpeg(tag: u8) -> Vec<u8> {
    let mut v = vec![0xff, 0xd8, 0xff, 0xe0, 0, 16, b'J', b'F', b'I', b'F', 0, 1, 1, 0, 0, 1];
    v.extend(std::iter::repeat_n(tag, 200));
    v
}

async fn upload(r: &Rig, name: &str, bytes: Vec<u8>) -> Value {
    let (s, b, _) = send_raw(r, Method::POST, &format!("/api/admin/signage/media?name={name}&width=640&height=360"), Some(bytes), "application/octet-stream").await;
    assert_eq!(s, StatusCode::OK, "{}", String::from_utf8_lossy(&b));
    serde_json::from_slice(&b).unwrap()
}

fn epoch_of(y: i64, m: i64, d: i64, hh: i64, mm: i64) -> i64 {
    Date::new(y, m, d).days() * 86400 + hh * 3600 + mm * 60
}

#[tokio::test]
async fn media_is_named_by_its_content_and_its_type_is_read_from_its_bytes() {
    assert_eq!(media::sniff(&jpeg(1)), Some("jpg"));
    assert_eq!(media::sniff(b"\x89PNG\r\n\x1a\n\0\0\0\0"), Some("png"));
    assert_eq!(media::sniff(b"RIFF\0\0\0\0WEBPVP8 "), Some("webp"));
    assert_eq!(media::sniff(b"GIF89a\0\0\0\0\0\0"), Some("gif"));
    assert_eq!(media::sniff(b"\0\0\0\x18ftypmp42\0\0"), Some("mp4"));
    assert_eq!(media::sniff(b"\x1a\x45\xdf\xa3\0\0\0\0\0\0\0\0"), Some("webm"));
    assert_eq!(media::sniff(b"MZ\x90\0\0\0\0\0\0\0\0\0"), None);
    assert!(media::parse_file_name(&format!("{}.jpg", "a".repeat(64))).is_some());
    assert!(media::parse_file_name("../etc/passwd").is_none() && media::parse_file_name(&format!("{}.exe", "a".repeat(64))).is_none());

    let r = rig().await;
    let a = upload(&r, "Welcome.jpg", jpeg(1)).await;
    let again = upload(&r, "Same.jpg", jpeg(1)).await;
    assert_eq!(a["sha256"], again["sha256"], "the same bytes are one file");
    assert_eq!((a["kind"].clone(), a["mime"].clone(), a["width"].clone(), a["name"].clone()), (json!("image"), json!("image/jpeg"), json!(640), json!("Welcome.jpg")));
    let name = format!("{}.jpg", a["sha256"].as_str().unwrap());
    let (s, body, h) = send_raw(&r, Method::GET, &format!("/signage/_/media/{name}"), None, "").await;
    assert_eq!((s, body.len(), h["cache-control"].to_str().unwrap()), (StatusCode::OK, jpeg(1).len(), "public, max-age=31536000, immutable"));
    assert_eq!(send(&r, Method::GET, "/signage/_/media/..%2Fconfig.json", None).await.0, StatusCode::NOT_FOUND);
    assert!(r.dir.join("backups").join("signage-media").join(&name).is_file(), "a copy goes in the backups folder");
    let (s, b, _) = send_raw(&r, Method::POST, "/api/admin/signage/media", Some(b"MZ this is a program, not a picture".to_vec()), "application/octet-stream").await;
    assert_eq!(s, StatusCode::UNSUPPORTED_MEDIA_TYPE);
    assert!(String::from_utf8_lossy(&b).contains("not an image or video signage can play"));
    let lib = send(&r, Method::GET, "/api/admin/signage", None).await.1;
    assert_eq!(lib["media"].as_array().unwrap().len(), 2, "two rows, one file");

    // a thumbnail is made by the browser; the server only keeps it
    let id = a["id"].as_str().unwrap();
    let (s, _, _) = send_raw(&r, Method::PUT, &format!("/api/admin/signage/media/{id}/thumb"), Some(b"RIFF\0\0\0\0WEBPVP8 data".to_vec()), "image/webp").await;
    assert_eq!(s, StatusCode::OK);
    let (s, _, h) = send_raw(&r, Method::GET, &format!("/api/admin/signage/media/{id}/thumb"), None, "").await;
    assert_eq!((s, h["content-type"].to_str().unwrap()), (StatusCode::OK, "image/webp"));
    assert_eq!(send_raw(&r, Method::PUT, &format!("/api/admin/signage/media/{id}/thumb"), Some(b"not an image at all".to_vec()), "image/webp").await.0, StatusCode::UNSUPPORTED_MEDIA_TYPE);

    // renaming, and deleting a file a playlist uses is refused with the playlist's name
    assert_eq!(send(&r, Method::PATCH, &format!("/api/admin/signage/media/{id}"), Some(json!({"name": "  Hello   there "}))).await.0, StatusCode::OK);
    let pl = send(&r, Method::POST, "/api/admin/signage/playlists", Some(json!({"name": "Lobby loop"}))).await.1;
    let pid = pl["id"].as_i64().unwrap();
    assert_eq!(send(&r, Method::PUT, &format!("/api/admin/signage/playlists/{pid}/items"), Some(json!({"items": [{"ref": id, "duration_s": 5}]}))).await.0, StatusCode::OK);
    let (s, body, _) = send(&r, Method::DELETE, &format!("/api/admin/signage/media/{id}"), None).await;
    assert_eq!(s, StatusCode::CONFLICT);
    assert_eq!(body["error"], "That file is in use in Lobby loop. Take it out of those playlists first.");
    send(&r, Method::PUT, &format!("/api/admin/signage/playlists/{pid}/items"), Some(json!({"items": []}))).await;
    assert_eq!(send(&r, Method::DELETE, &format!("/api/admin/signage/media/{id}"), None).await.0, StatusCode::OK);
    assert!(r.dir.join("signage").join("media").join(&name).is_file(), "the other row still names the file");
    let other = again["id"].as_str().unwrap();
    send(&r, Method::DELETE, &format!("/api/admin/signage/media/{other}"), None).await;
    assert!(!r.dir.join("signage").join("media").join(&name).exists(), "the last row going takes the file");
    assert!(r.audit.0.lock().unwrap().contains(&"signage.media_upload".to_owned()));
}

#[tokio::test]
async fn playlists_groups_and_screens_check_what_they_are_given() {
    let r = rig().await;
    let m = upload(&r, "One.jpg", jpeg(2)).await;
    let mid = m["id"].as_str().unwrap();
    let s = st(&r);
    assert_eq!(s.playlist_create(&json!("  ")).await, Err(Error::Invalid("Give the playlist a name.".into(), 400)));
    let p = s.playlist_create(&json!(" Sunday  loop ")).await.unwrap();
    let pid = p["id"].as_i64().unwrap();
    assert_eq!(p["name"], "Sunday loop");
    let bad: Vec<(Value, &str)> = vec![
        (json!([{"ref": "nope"}]), "An item names a file that is not in the library."),
        (json!([{"ref": mid, "duration_s": -1}]), "A duration must be between 0 seconds and a day."),
        (json!([{"ref": mid, "duration_s": "slow"}]), "A duration must be a number of seconds."),
        (json!([{"ref": mid, "start_date": "2026-02-30"}]), "That date does not exist."),
        (json!([{"ref": mid, "start_date": "12/25/2026"}]), "Dates are written YYYY-MM-DD."),
        (json!([{"ref": mid, "start_date": "2026-12-25", "end_date": "2026-12-01"}]), "An item ends before it starts."),
        (json!([{"ref": mid, "transition": "spin"}]), "Transition is cut or fade, and fit is fit or fill."),
        (json!([{"ref": ""}]), "An item has no file or source."),
        (json!(["x"]), "Each item must be an object."),
    ];
    for (items, why) in bad {
        assert_eq!(s.playlist_set_items(pid, &items).await, Err(Error::Invalid(why.into(), 400)), "{why}");
    }
    s.playlist_set_items(pid, &json!([{"ref": mid, "duration_s": 7, "start_date": "2026-12-01", "end_date": "2026-12-31"}, {"ref": mid}])).await.unwrap();
    let lists = s.playlists().await.unwrap();
    assert_eq!(lists[0]["items"].as_array().unwrap().len(), 2);
    assert_eq!((lists[0]["items"][0]["name"].clone(), lists[0]["items"][1]["duration_s"].clone(), lists[0]["items"][1]["transition"].clone()), (json!("One.jpg"), json!(10.0), json!("fade")));
    let copy = s.playlist_duplicate(pid, "").await.unwrap();
    assert_eq!(copy["name"], "Sunday loop (copy)");
    assert_eq!(s.playlist_set_items(999, &json!([])).await, Err(Error::Invalid("That playlist does not exist.".into(), 404)));
    assert_eq!(s.playlist_delete(copy["id"].as_i64().unwrap()).await, Ok(()));

    let g = s.group_save(None, &json!({"name": "Foyer", "playlist_id": pid})).await.unwrap();
    assert_eq!(g["playlist_id"], pid);
    assert_eq!(s.group_save(None, &json!({"name": "X", "playlist_id": 999})).await, Err(Error::Invalid("That playlist does not exist.".into(), 404)));
    let sc = s.screen_save(None, &json!({"name": "Lobby Left", "slug": "lobby-left", "group_id": g["id"], "sound": true})).await.unwrap();
    assert_eq!((sc["slug"].clone(), sc["sound"].clone(), sc["core_id"].clone()), (json!("lobby-left"), json!(true), json!(1)));
    assert_eq!(s.screen_save(None, &json!({"name": "Dup", "slug": "lobby-left"})).await, Err(Error::Invalid("Another screen already uses that address.".into(), 409)));
    assert_eq!(s.screen_save(None, &json!({"name": "Bad", "slug": "Bad Slug"})).await, Err(Error::Invalid("The address name uses lowercase letters, numbers and dashes, like lobby-left.".into(), 400)));
    assert_eq!(s.screen_save(None, &json!({"name": "G", "slug": "g", "group_id": 99})).await, Err(Error::Invalid("That group does not exist.".into(), 404)));
    let renamed = s.screen_save(sc["id"].as_i64(), &json!({"name": "Lobby L", "slug": "lobby-l", "group_id": g["id"]})).await.unwrap();
    assert_eq!(renamed["name"], "Lobby L", "the core screen follows the rename");
    assert_eq!(s.screen_by_slug("lobby-l").await.unwrap().unwrap()["core_id"], sc["core_id"], "and keeps its core screen when the address moves");
    assert!(s.screen_by_slug("lobby-left").await.unwrap().is_none());
    let made = s.ensure_screen(&json!({"id": 50, "name": "Hall TV"})).await.unwrap();
    assert_eq!(made["slug"], "hall-tv");
    s.set_default_playlist(made["core_id"].as_i64().unwrap(), Some(pid)).await.unwrap();
    assert_eq!(s.screen_by_slug("hall-tv").await.unwrap().unwrap()["playlist_id"], pid);
    s.screen_delete(made["id"].as_i64().unwrap()).await.unwrap();
    assert_eq!(s.screen_delete(made["id"].as_i64().unwrap()).await, Err(Error::Invalid("That screen does not exist.".into(), 404)));
}

#[tokio::test]
async fn what_a_screen_plays_follows_overrides_slots_and_defaults() {
    let r = rig().await;
    let m = upload(&r, "A.jpg", jpeg(3)).await;
    let mid = m["id"].as_str().unwrap();
    let s = st(&r);
    let mut ids = Vec::new();
    for name in ["Default", "Group", "Sunday", "Range", "Hand"] {
        let p = s.playlist_create(&json!(name)).await.unwrap();
        s.playlist_set_items(p["id"].as_i64().unwrap(), &json!([{"ref": mid}])).await.unwrap();
        ids.push(p["id"].as_i64().unwrap());
    }
    let g = s.group_save(None, &json!({"name": "Foyer", "playlist_id": ids[1]})).await.unwrap();
    let lone = s.screen_save(None, &json!({"name": "Alone", "slug": "alone", "playlist_id": ids[0]})).await.unwrap();
    let grouped = s.screen_save(None, &json!({"name": "Grouped", "slug": "grouped", "group_id": g["id"], "playlist_id": ids[0]})).await.unwrap();
    let nothing = s.screen_save(None, &json!({"name": "Bare", "slug": "bare"})).await.unwrap();

    // 2026-11-22 is a Sunday; 15:00 UTC is 10:00 in New York
    let sun_10 = epoch_of(2026, 11, 22, 15, 0);
    let plan = |sc: &Value, at: i64| {
        let (sc, s) = (sc.clone(), st(&r));
        async move { s.resolve(&sc, at, None, None).await.unwrap() }
    };
    assert_eq!((plan(&lone, sun_10).await["layer"].clone(), plan(&grouped, sun_10).await["layer"].clone(), plan(&nothing, sun_10).await["layer"].clone()), (json!("screen"), json!("group"), json!("none")));
    assert_eq!(plan(&grouped, sun_10).await["playlist_id"], ids[1], "a group's playlist beats the screen's own");
    assert_eq!(plan(&nothing, sun_10).await["why"], "Nothing assigned");

    // slots: bad ones are refused, good ones win by level, then a date range over a weekly slot
    for (slot, why) in [
        (json!({"target": "all"}), "Choose a playlist."),
        (json!({"target": "screen", "playlist_id": ids[2], "days": [0]}), "Choose a screen or a group."),
        (json!({"target": "all", "playlist_id": ids[2]}), "Choose the days of the week, or a range of dates."),
        (json!({"target": "all", "playlist_id": ids[2], "days": [9]}), "Days are 0 (Sunday) to 6 (Saturday)."),
        (json!({"target": "all", "playlist_id": ids[2], "days": [0], "start_time": "09:00"}), "Give both a start and an end time, or neither for the whole day."),
        (json!({"target": "all", "playlist_id": ids[2], "days": [0], "start_time": "9am", "end_time": "10:00"}), "Times are written HH:MM, 24-hour."),
        (json!({"target": "all", "playlist_id": ids[2], "days": [0], "start_time": "09:00", "end_time": "09:00"}), "The start and end time must differ."),
        (json!({"target": "all", "playlist_id": ids[2], "start_date": "2026-12-01"}), "A date range needs both a first and a last day."),
        (json!({"target": "all", "playlist_id": ids[2], "start_date": "2026-12-05", "end_date": "2026-12-01"}), "The range ends before it starts."),
        (json!({"target": "all", "playlist_id": ids[2], "schedule_ref": "bogus"}), "Choose one of the named schedules."),
    ] {
        assert_eq!(s.slot_save(None, &slot).await, Err(Error::Invalid(why.into(), 400)), "{why}");
    }
    assert_eq!(s.slot_save(None, &json!({"target": "all", "playlist_id": 999, "days": [0]})).await, Err(Error::Invalid("That playlist does not exist.".into(), 404)));
    let weekly = s.slot_save(None, &json!({"target": "all", "playlist_id": ids[2], "days": [0], "start_time": "09:00", "end_time": "11:30"})).await.unwrap();
    assert_eq!(weekly["days"], "0");
    let slots = s.slots().await.unwrap();
    assert_eq!(slots[0]["summary"], "Sun 9:00 AM-11:30 AM");
    assert_eq!(plan(&lone, sun_10).await["playlist_id"], ids[2], "a slot beats the default");
    assert_eq!(plan(&lone, epoch_of(2026, 11, 22, 17, 0)).await["layer"], "screen", "outside its hours the default is back");
    let range = s.slot_save(None, &json!({"target": "all", "playlist_id": ids[3], "start_date": "2026-11-20", "end_date": "2026-11-25"})).await.unwrap();
    let both = plan(&lone, sun_10).await;
    assert_eq!((both["playlist_id"].clone(), both["layer"].clone()), (json!(ids[3]), json!("schedule")), "a date range beats a weekly slot");
    assert!(both["why"].as_str().unwrap().ends_with("(1 other slot also match)"), "{}", both["why"]);
    let own = s.slot_save(None, &json!({"target": "screen", "target_id": lone["id"], "playlist_id": ids[4], "days": [0]})).await.unwrap();
    assert_eq!(plan(&lone, sun_10).await["playlist_id"], ids[4], "a screen's own slot beats the all-screens ones");
    assert_eq!(plan(&grouped, sun_10).await["playlist_id"], ids[3]);
    s.slot_delete(own["id"].as_i64().unwrap()).await.unwrap();
    s.slot_delete(range["id"].as_i64().unwrap()).await.unwrap();

    // a slot that names a schedule from the Schedules page covers its windows (Sundays 10:00 to 12:00)
    let named = s.slot_save(None, &json!({"target": "all", "playlist_id": ids[3], "schedule_ref": "schedule:relay:svc1"})).await.unwrap();
    assert_eq!(named["summary"], Value::Null);
    assert_eq!(s.slots().await.unwrap().iter().find(|x| x["id"] == named["id"]).unwrap()["summary"], "Schedule: Sunday services");
    assert_eq!(plan(&lone, sun_10).await["playlist_id"], ids[3]);
    assert_eq!(plan(&lone, epoch_of(2026, 11, 22, 14, 0)).await["layer"], "schedule", "09:00 matches the weekly slot, not the named one");
    assert_eq!(s.schedule_users("schedule:relay:svc1").await.unwrap()[0]["what"], "Slot on all screens");
    s.slot_delete(named["id"].as_i64().unwrap()).await.unwrap();

    // an override put on by hand lasts until the next change, or until cleared
    let until = s.override_set("screen", lone["id"].as_i64().unwrap(), ids[4], "next_change").await.unwrap();
    assert_eq!(until["expires_at"].as_str().unwrap().len(), 20, "an expiry is worked out from the schedule");
    assert_eq!(s.override_set("all", 0, ids[4], "cleared").await.unwrap()["expires_at"], "");
    assert_eq!(s.override_set("everything", 0, ids[4], "cleared").await, Err(Error::Invalid("Choose a screen, a group or all screens.".into(), 400)));
    assert_eq!(s.override_set("group", 99, ids[4], "cleared").await, Err(Error::Invalid("That group does not exist.".into(), 404)));
    let (now, over) = (epoch_of(2026, 11, 22, 15, 0), s.overrides().await.unwrap());
    assert_eq!(s.resolve(&nothing, now, None, Some(&over)).await.unwrap()["layer"], "override", "an override reaches a screen with nothing else");
    assert_eq!(s.resolve(&lone, now, None, Some(&over)).await.unwrap()["why"], "Put on by hand for this screen", "the more specific one wins");
    s.override_clear("screen", lone["id"].as_i64().unwrap()).await.unwrap();
    s.override_clear("all", 0).await.unwrap();
    assert_eq!(s.override_clear("all", 0).await, Err(Error::Invalid("There is no override to clear there.".into(), 404)));

    // the player's state: only items in date today, and the media url
    let state = s.player_state(&lone, epoch_of(2026, 11, 22, 17, 0)).await.unwrap();
    assert_eq!((state["screen"]["slug"].clone(), state["playlist"]["name"].clone(), state["today"].clone()), (json!("alone"), json!("Default"), json!("2026-11-22")));
    assert!(state["items"][0]["url"].as_str().unwrap().starts_with("/signage/_/media/"));
}

#[tokio::test]
async fn takeovers_banner_and_the_player_routes() {
    let r = rig().await;
    let m = upload(&r, "Push.jpg", jpeg(4)).await;
    let mid = m["id"].as_str().unwrap();
    let s = st(&r);
    let lone = s.screen_save(None, &json!({"name": "Alone", "slug": "alone"})).await.unwrap();
    let other = s.screen_save(None, &json!({"name": "Other", "slug": "other"})).await.unwrap();

    // push one file to one screen for a set time, and a live view to all
    assert_eq!(s.push_set("screen", 99, mid, "cleared", 0).await, Err(Error::Invalid("That screen does not exist.".into(), 404)));
    assert_eq!(s.push_set("screen", lone["id"].as_i64().unwrap(), "nope", "cleared", 0).await, Err(Error::Invalid("That file is not in the media library.".into(), 404)));
    assert_eq!(s.push_set("all", 0, mid, "seconds", 0).await, Err(Error::Invalid("Choose a time from 1 second to 24 hours.".into(), 400)));
    assert_eq!(s.live_view_set("all", 0, "weather").await, Err(Error::Invalid("Choose live captions or the service view.".into(), 400)));
    let pushed = s.push_set("screen", lone["id"].as_i64().unwrap(), mid, "once", 0).await.unwrap();
    s.live_view_set("all", 0, "captions").await.unwrap();
    let taken = s.takeover_rows().await.unwrap();
    assert_eq!(taken.iter().map(|t| t["label"].clone()).collect::<Vec<_>>(), vec![json!("Push.jpg"), json!("Live captions (translation)")]);
    assert_eq!(s.takeover_pick(&lone, &taken).unwrap()["kind"], "push", "a push beats a live view");
    assert_eq!(s.takeover_pick(&other, &taken).unwrap()["kind"], "live");
    let payload = s.takeover_payload(&lone, &taken).await.unwrap().unwrap();
    assert_eq!((payload["seconds"].clone(), payload["item"]["type"].clone(), payload["item"]["id"].clone()), (json!(10), json!("image"), json!(-pushed["id"].as_i64().unwrap())));
    let live = s.takeover_payload(&other, &taken).await.unwrap().unwrap();
    assert_eq!((live["path"].clone(), live["label"].clone()), (json!("/screen?stream=translation"), json!("Live captions (translation)")));
    // a "once" push the screen has played to its end is skipped
    s.finish_playing("alone", pushed["id"].as_i64().unwrap());
    assert_eq!(s.takeover_pick(&lone, &taken).unwrap()["kind"], "live");

    // alerts show as a banner on the chosen screens
    r.host.provide("test", ALERTS, Arc::new(AlertsCap(Box::new(|| vec![json!({"id": "a1", "to": "FOH booth", "from": "Drums"}), json!({"id": "a2", "to": "Stage", "from": ""})]))));
    assert_eq!(s.banner(&lone).unwrap(), json!({"text": "FOH booth (from Drums)", "count": 2}));
    assert_eq!(s.alert_sig(), vec!["a1".to_owned(), "a2".to_owned()]);
    r.settings.save("signage", &json!({"alert_screens": "none"})).unwrap();
    assert!(s.banner(&lone).is_none());
    r.settings.save("signage", &json!({"alert_screens": "groups", "alert_groups": [7]})).unwrap();
    assert!(s.banner(&lone).is_none(), "a screen in no group is not in the chosen groups");
    r.settings.save("signage", &json!({"alert_screens": "all"})).unwrap();

    // what the routes say: the player page, its state, the events, and a screen that is not there
    let (status, page, h) = send_raw(&r, Method::GET, "/signage/alone", None, "").await;
    let page = String::from_utf8(page).unwrap();
    assert_eq!(status, StatusCode::OK);
    assert!(page.contains("<title>Alone</title>") && page.contains("var slug = \"alone\";") && page.contains("/signage/_/events/'"), "{page}");
    assert_eq!((h["referrer-policy"].to_str().unwrap(), h["cache-control"].to_str().unwrap()), ("no-referrer", "no-cache"));
    assert_eq!(send(&r, Method::GET, "/signage/nope", None).await.0, StatusCode::NOT_FOUND);
    assert_eq!(send(&r, Method::GET, "/signage/Bad%20Slug", None).await.0, StatusCode::NOT_FOUND);
    let (status, state, h) = send(&r, Method::GET, "/signage/_/state/alone", None).await;
    assert_eq!((status, h["cache-control"].to_str().unwrap()), (StatusCode::OK, "no-store"));
    assert_eq!((state["screen"]["name"].clone(), state["takeover"]["kind"].clone(), state["banner"]["count"].clone()), (json!("Alone"), json!("live"), json!(2)));
    assert!(state["server_time"].as_i64().unwrap() > 1_700_000_000_000);
    let (status, js, h) = send_raw(&r, Method::GET, "/signage/_/player.js", None, "").await;
    assert_eq!((status, h["content-type"].to_str().unwrap(), js.starts_with(b"/*") || !js.is_empty()), (StatusCode::OK, "text/javascript", true));

    // the player reports what it shows, and that it finished a push
    assert_eq!(send(&r, Method::POST, "/signage/_/now/alone", Some(json!({"item": 4, "label": "Slide"}))).await.1, json!({"ok": true}));
    assert_eq!(st(&r).now_playing("alone").unwrap().label, "Slide");
    assert_eq!(send_raw(&r, Method::POST, "/signage/_/now/alone", Some(b"{nope".to_vec()), "application/json").await.0, StatusCode::BAD_REQUEST);
    assert_eq!(send(&r, Method::POST, "/signage/_/done/alone", Some(json!({"id": 3}))).await.0, StatusCode::OK);
    assert_eq!(send(&r, Method::POST, "/signage/_/now/nope", Some(json!({}))).await.0, StatusCode::NOT_FOUND);

    // the event stream opens with the state, and sends again when something changes
    let mut req = Request::builder().method(Method::GET).uri("/signage/_/events/alone").body(Body::empty()).unwrap();
    req.extensions_mut().insert("127.0.0.1:1".parse::<SocketAddr>().unwrap());
    let resp = r.router.clone().oneshot(req).await.unwrap();
    assert_eq!(resp.headers()["content-type"], "text/event-stream");
    let mut body = resp.into_body();
    let first = next_event(&mut body).await;
    assert_eq!(first["screen"]["slug"], "alone");
    s.takeover_clear_all().await.unwrap();
    let second = next_event(&mut body).await;
    assert_eq!(second["takeover"], Value::Null, "clearing the takeovers reaches the player");
    s.screen_delete(lone["id"].as_i64().unwrap()).await.unwrap();
    assert_eq!(next_event(&mut body).await, json!({"gone": true}), "a deleted screen is told it is gone");
}

async fn next_event(body: &mut Body) -> Value {
    let mut text = String::new();
    loop {
        let chunk = tokio::time::timeout(Duration::from_secs(5), body.frame()).await.expect("an event arrives").expect("the stream is open").unwrap();
        if let Some(d) = chunk.data_ref() {
            text.push_str(&String::from_utf8_lossy(d));
        }
        while let Some(end) = text.find("\n\n") {
            let frame: String = text.drain(..end + 2).collect();
            if let Some(j) = frame.strip_prefix("data: ") {
                let mut v: Value = serde_json::from_str(j.trim_end()).unwrap();
                if v.get("gone").is_some() {
                    v.as_object_mut().unwrap().remove("server_time");
                }
                return v;
            }
        }
    }
}

#[tokio::test]
async fn admin_routes_are_audited_and_settings_are_checked() {
    let r = rig().await;
    let (s, state, _) = send(&r, Method::GET, "/api/admin/signage", None).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!((state["timezone"].clone(), state["named_schedules"][0]["summary"].clone(), state["sources"][0]["kind"].clone(), state["views"].as_array().unwrap().len()),
        (json!("America/New_York"), json!("Every Sun · 10:00 AM – 12:00 PM"), json!("media"), 3));
    assert_eq!(state["timezones"].as_array().unwrap().len(), 7);
    for (body, why) in [
        (json!({"port": 80}), "The port is a number from 1024 to 65535."),
        (json!({"port": "x"}), "The port is a number from 1024 to 65535."),
        (json!({"override_until": "never"}), "A hand-chosen playlist lasts until the next scheduled change or until cleared."),
        (json!({"alert_screens": "some"}), "Alerts show on all screens, on chosen groups, or not at all."),
        (json!({"alert_groups": ["a"]}), "Choose groups from the list."),
        (json!({}), "Nothing to change."),
    ] {
        let (s, b, _) = send(&r, Method::PUT, "/api/admin/signage/settings", Some(body)).await;
        assert_eq!((s, b["error"].as_str().unwrap()), (StatusCode::BAD_REQUEST, why));
    }
    let (s, b, _) = send(&r, Method::PUT, "/api/admin/signage/settings", Some(json!({"port": 8123, "alert_screens": "groups"}))).await;
    assert_eq!((s, b["settings"]["port"].clone(), b["settings"]["alert_screens"].clone()), (StatusCode::OK, json!(8123), json!("groups")));
    assert_eq!(send(&r, Method::PUT, "/api/admin/signage/settings", Some(json!(["not", "an", "object"]))).await.0, StatusCode::UNPROCESSABLE_ENTITY);
    let (s, g, _) = send(&r, Method::POST, "/api/admin/signage/groups", Some(json!({"name": "Foyer"}))).await;
    assert_eq!(s, StatusCode::OK);
    assert_eq!(send(&r, Method::PUT, &format!("/api/admin/signage/groups/{}", g["id"]), Some(json!({"name": "Foyer 2"}))).await.1["name"], "Foyer 2");
    assert_eq!(send(&r, Method::DELETE, "/api/admin/signage/groups/x", None).await.0, StatusCode::NOT_FOUND);
    assert_eq!(send(&r, Method::DELETE, &format!("/api/admin/signage/groups/{}", g["id"]), None).await.0, StatusCode::OK);
    assert_eq!(send(&r, Method::DELETE, &format!("/api/admin/signage/groups/{}", g["id"]), None).await.0, StatusCode::NOT_FOUND);
    assert_eq!(send(&r, Method::GET, "/api/admin/signage/sources/propresenter_slide", None).await.0, StatusCode::NOT_FOUND);
    assert_eq!(send(&r, Method::GET, "/api/admin/signage/sources/media", None).await.1, json!({"choices": []}));
    let seen = r.audit.0.lock().unwrap().clone();
    for a in ["signage.settings", "signage.group_create", "signage.group_update", "signage.group_delete"] {
        assert!(seen.contains(&a.to_owned()), "{a} in {seen:?}");
    }
    assert!(st(&r).live_version() > 0, "every change bumps the version the players watch");
}
