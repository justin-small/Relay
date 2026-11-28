//! What `tests/test_planning_center_signage.py` checks: the files attached to the followed plan and to its items, and text slides for plan
//! items, copied into signage's library and one playlist, with signage on and off. Planning Center and signage are answered from tables.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use axum::body::Body;
use bytes::Bytes;
use http_body_util::BodyExt;
use relay_core::caps::{ContentCap, ContentError, ContentResult, SignageContent, SIGNAGE_CONTENT};
use relay_core::future::BoxFuture;
use relay_core::{Host, SettingsStore, Shutdown};
use relay_module_planning_center::client::{Download, Op, PcError, Rate, Services};
use relay_module_planning_center::follower::{Follower, MakeClient, Track};
use relay_module_planning_center::signagesync::{self, Memory, PlanSync, KIND, PLAYLIST_NAME, PREFIX};
use serde_json::{json, Value};
use sha2_free::sha_hex;

mod sha2_free {
    /// A stand-in digest for the fake library: equal bytes, equal name.
    pub fn sha_hex(b: &[u8]) -> String {
        let mut h: u64 = 1469598103934665603;
        for x in b {
            h = (h ^ u64::from(*x)).wrapping_mul(1099511628211);
        }
        format!("{h:016x}{h:016x}{h:016x}{h:016x}")
    }
}

fn att(id: &str, name: &str, ctype: &str, url: &str, updated: &str, linked: bool) -> Value {
    json!({"id": id, "name": name, "filename": name, "content_type": ctype, "size": 100, "url": url, "linked": linked, "updated_at": updated})
}

#[derive(Default)]
struct Pc {
    item_files: Mutex<HashMap<String, Vec<Value>>>,
    plan_files: Mutex<Vec<Value>>,
    bodies: Mutex<HashMap<String, Vec<u8>>>,
    calls: Mutex<Vec<(String, String)>>,
    downloads: Mutex<Vec<String>>,
    broken: Mutex<bool>,
}

impl Services for Pc {
    fn call(&self, op: Op) -> BoxFuture<'_, Result<Value, PcError>> {
        Box::pin(async move {
            match op {
                Op::ItemAttachments { item, .. } => {
                    self.calls.lock().unwrap().push(("item".into(), item.clone()));
                    Ok(json!(self.item_files.lock().unwrap().get(&item).cloned().unwrap_or_default()))
                }
                Op::PlanAttachments { plan, .. } => {
                    self.calls.lock().unwrap().push(("plan".into(), plan));
                    if *self.broken.lock().unwrap() {
                        return Err(PcError::new(relay_module_planning_center::client::Kind::Network, "no"));
                    }
                    Ok(json!(self.plan_files.lock().unwrap().clone()))
                }
                _ => Ok(Value::Null),
            }
        })
    }

    fn rate(&self) -> Rate {
        Rate::default()
    }

    fn download<'a>(&'a self, url: &'a str, _limit: u64) -> BoxFuture<'a, Result<Download, PcError>> {
        Box::pin(async move {
            self.downloads.lock().unwrap().push(url.to_owned());
            let data = self.bodies.lock().unwrap().get(url).cloned().unwrap_or_default();
            let (tx, rx) = tokio::sync::mpsc::channel(4);
            let (a, b) = data.split_at(data.len().min(30));
            tx.send(Ok(Bytes::copy_from_slice(a))).await.unwrap();
            tx.send(Ok(Bytes::copy_from_slice(b))).await.unwrap();
            Ok(rx)
        })
    }
}

/// Signage's side: a library and playlists in memory, with the type check by first bytes.
#[derive(Default)]
struct Library {
    media: Mutex<Vec<Value>>,
    lists: Mutex<Vec<Value>>,
}

impl SignageContent for Library {
    fn limit(&self, _image: bool) -> u64 {
        1 << 20
    }
    fn media_list(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { Ok(self.media.lock().unwrap().clone()) })
    }
    fn receive(&self, body: Body) -> BoxFuture<'_, ContentResult<Option<(String, String, u64)>>> {
        Box::pin(async move {
            let bytes = body.collect().await.map_err(|_| ContentError("x".into()))?.to_bytes();
            let ext = if bytes.starts_with(&[0xff, 0xd8, 0xff]) { "jpg" } else if bytes.starts_with(b"\x89PNG") { "png" } else if bytes.len() > 8 && &bytes[4..8] == b"ftyp" { "mp4" } else { return Ok(None) };
            Ok(Some((sha_hex(&bytes), ext.to_owned(), bytes.len() as u64)))
        })
    }
    fn media_add<'a>(&'a self, name: &'a str, sha: &'a str, ext: &'a str, size: u64) -> BoxFuture<'a, ContentResult<Value>> {
        Box::pin(async move {
            let mut m = self.media.lock().unwrap();
            let row = json!({"id": format!("m{}", m.len() + 1), "name": name, "sha256": sha, "ext": ext, "size": size});
            m.push(row.clone());
            Ok(row)
        })
    }
    fn media_delete<'a>(&'a self, id: &'a str) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move {
            let used = self.lists.lock().unwrap().iter().any(|p| p["items"].as_array().unwrap().iter().any(|i| i["source"] == "media" && i["ref"] == id));
            if used {
                return Err(ContentError("in use".into()));
            }
            self.media.lock().unwrap().retain(|m| m["id"] != id);
            Ok(())
        })
    }
    fn playlists(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move { Ok(self.lists.lock().unwrap().clone()) })
    }
    fn playlist_create<'a>(&'a self, name: &'a str) -> BoxFuture<'a, ContentResult<Value>> {
        Box::pin(async move {
            let mut l = self.lists.lock().unwrap();
            let row = json!({"id": l.len() + 1, "name": name, "items": []});
            l.push(row.clone());
            Ok(row)
        })
    }
    fn playlist_set_items<'a>(&'a self, id: i64, items: &'a Value) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move {
            for p in self.lists.lock().unwrap().iter_mut() {
                if p["id"] == id {
                    p["items"] = items.clone();
                }
            }
            Ok(())
        })
    }
}

struct Store(Mutex<HashMap<String, Value>>);

impl SettingsStore for Store {
    fn read(&self, key: &str) -> Option<Value> {
        self.0.lock().unwrap().get(key).cloned()
    }
    fn save(&self, _key: &str, _changes: &Value) -> Result<(), relay_core::AppError> {
        Ok(())
    }
    fn replace(&self, key: &str, value: &Value) -> Result<(), relay_core::AppError> {
        self.0.lock().unwrap().insert(key.to_owned(), value.clone());
        Ok(())
    }
    fn add_secret(&self, _key: &str, _field: &str) {}
}

fn jpeg(tag: &str) -> Vec<u8> {
    [&[0xff, 0xd8, 0xff, 0xe0][..], tag.as_bytes(), &[0; 40]].concat()
}

struct Rig {
    sync: Arc<PlanSync>,
    store: Arc<Store>,
    pc: Arc<Pc>,
    lib: Arc<Library>,
    host: Host,
    memory: Arc<Memory>,
}

fn save(r: &Rig, signage: Value) {
    let mut cur = relay_module_planning_center::settings::clean_stored(&r.store.read("planning_center").unwrap_or_else(|| json!({})));
    cur["service_types"] = json!(["77"]);
    for (k, v) in signage.as_object().unwrap() {
        cur["signage"][k.as_str()] = v.clone();
    }
    r.store.replace("planning_center", &cur).unwrap();
}

fn rig() -> Rig {
    let store = Arc::new(Store(Mutex::new(HashMap::new())));
    let host = Host::new(Shutdown::new()).with_store(store.clone());
    let pc = Arc::new(Pc::default());
    pc.item_files.lock().unwrap().insert("i1".into(), vec![att("a1", "welcome.jpg", "image/jpeg", "https://files/a1", "2026-11-01T10:00:00Z", false), att("d1", "sheet.pdf", "application/pdf", "https://files/d1", "x", false), att("l1", "link", "image/jpeg", "", "x", true)]);
    pc.item_files.lock().unwrap().insert("i2".into(), vec![att("v1", "loop.mp4", "video/mp4", "https://files/v1", "2026-11-01T10:00:00Z", false)]);
    *pc.plan_files.lock().unwrap() = vec![att("p1", "banner.png", "image/png", "https://files/p1", "2026-11-01T10:00:00Z", false)];
    {
        let mut b = pc.bodies.lock().unwrap();
        b.insert("https://files/a1".into(), jpeg("a1"));
        b.insert("https://files/a1b".into(), jpeg("a1b"));
        b.insert("https://files/v1".into(), [&b"\x00\x00\x00\x18ftypmp42"[..], b"v1", &[0; 40]].concat());
        b.insert("https://files/p1".into(), [&b"\x89PNG\r\n\x1a\n"[..], b"p1", &[0; 40]].concat());
    }
    let maker: MakeClient = {
        let pc = Arc::clone(&pc);
        Arc::new(move |_| Some(pc.clone() as Arc<dyn Services>))
    };
    let follower = Follower::new(host.clone(), Arc::clone(&maker));
    let mut tr = Track::new("77");
    tr.plan = Some(json!({"id": "9", "title": "Sunday", "dates": "Nov 15", "times": []}));
    tr.items = vec![
        json!({"id": "h", "title": "Header", "type": "header", "notes": []}),
        json!({"id": "i1", "title": "Announcements", "type": "item", "notes": [{"category": "Announcement", "content": "Coffee after\nBring a friend"}, {"category": "Private", "content": "secret"}]}),
        json!({"id": "i2", "title": "Worship <b>", "type": "item", "notes": []}),
        json!({"id": "i3", "title": "Prayer", "type": "item", "notes": []}),
    ];
    follower.put_track(tr);
    let dir = std::env::temp_dir().join(format!("relay-pc-sync-{}-{}", std::process::id(), rand_tag()));
    let memory = Arc::new(Memory::at(dir.join("signage.json")));
    let sync = PlanSync::new(host.clone(), follower, maker, Arc::clone(&memory));
    Rig { sync, store, pc, lib: Arc::new(Library::default()), host, memory }
}

fn rand_tag() -> u64 {
    std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap().subsec_nanos().into()
}

fn signage_on(r: &Rig, on: bool) {
    r.host.withdraw_all("signage");
    if on {
        r.host.provide("signage", SIGNAGE_CONTENT, Arc::new(ContentCap(r.lib.clone())));
    }
}

fn names(r: &Rig) -> Vec<String> {
    let mut v: Vec<String> = r.lib.media.lock().unwrap().iter().map(|m| m["name"].as_str().unwrap().to_owned()).filter(|n| n.starts_with(PREFIX)).collect();
    v.sort();
    v
}

#[test]
fn settings_are_checked_with_fixed_sentences() {
    use relay_module_planning_center::settings::{default_signage, signage_fields};
    assert_eq!(default_signage(), json!({"enabled": false, "service_type": "", "plan_files": true, "item_files": true, "text_slides": false, "note_category": "", "item_match": "", "playlist": false}));
    assert_eq!(signage_fields(&json!({"service_type": "a b"})).1, "That is not a Planning Center service type.");
    assert_eq!(signage_fields(&json!({"enabled": true})).0.len(), 1, "a key left out stays out");
    assert!(!signage_fields(&json!({"item_match": "x".repeat(81)})).1.is_empty());
}

#[tokio::test]
async fn nothing_happens_until_it_is_switched_on_and_set_up() {
    let r = rig();
    signage_on(&r, true);
    let st = r.sync.sync(false).await;
    assert_eq!((st["enabled"].clone(), st["error"].as_str(), r.pc.calls.lock().unwrap().len()), (json!(false), Some(""), 0), "off is quiet");
    save(&r, json!({"enabled": true}));
    assert_eq!(r.sync.sync(false).await["error"], "Pick the service type first.");
    save(&r, json!({"enabled": true, "service_type": "88"}));
    assert_eq!(r.sync.sync(false).await["error"], "That service type is not one Relay follows. Follow it above first.");
    save(&r, json!({"enabled": true, "service_type": "77"}));
    signage_on(&r, false);
    assert_eq!(r.sync.sync(false).await["error"], "Signage is switched off.");
    assert!(r.pc.calls.lock().unwrap().is_empty(), "and nothing was fetched");
}

#[tokio::test]
async fn files_are_copied_once_and_follow_the_plan() {
    let r = rig();
    signage_on(&r, true);
    save(&r, json!({"enabled": true, "service_type": "77"}));
    let st = r.sync.sync(false).await;
    assert_eq!((st["error"].as_str(), st["files"].clone(), st["texts"].clone()), (Some(""), json!(3), json!(0)), "a pdf and a link are skipped");
    assert_eq!(names(&r), [format!("{PREFIX}Announcements welcome.jpg"), format!("{PREFIX}Sunday banner.png"), format!("{PREFIX}Worship <b> loop.mp4")]);
    assert!(r.lib.lists.lock().unwrap().is_empty(), "no playlist unless asked for");
    let mut got = r.pc.downloads.lock().unwrap().clone();
    got.sort();
    assert_eq!(got, ["https://files/a1", "https://files/p1", "https://files/v1"], "each file came down once");
    r.pc.downloads.lock().unwrap().clear();
    r.sync.sync(true).await;
    assert!(r.pc.downloads.lock().unwrap().is_empty(), "a forced pass downloads nothing that has not changed");
    r.pc.item_files.lock().unwrap().get_mut("i1").unwrap()[0] = att("a1", "welcome.jpg", "image/jpeg", "https://files/a1b", "2026-11-09T10:00:00Z", false);
    r.sync.sync(false).await;
    assert_eq!(*r.pc.downloads.lock().unwrap(), ["https://files/a1b"], "a changed file is fetched again");
    r.pc.item_files.lock().unwrap().insert("i2".into(), vec![]);
    r.sync.sync(false).await;
    assert_eq!(names(&r), [format!("{PREFIX}Announcements welcome.jpg"), format!("{PREFIX}Sunday banner.png")], "a file taken off the plan leaves the library");

    // A file nobody here made is never touched.
    r.lib.media.lock().unwrap().push(json!({"id": "up1", "name": "Lobby photo", "sha256": "z", "ext": "jpg", "size": 1}));
    r.pc.plan_files.lock().unwrap().clear();
    r.sync.sync(false).await;
    assert!(r.lib.media.lock().unwrap().iter().any(|m| m["id"] == "up1"), "the upload is still there");
    assert!(!names(&r).iter().any(|n| n.ends_with("banner.png")), "the banner left with the plan");
}

#[tokio::test]
async fn text_slides_and_the_playlist_keep_what_the_operator_set() {
    let r = rig();
    signage_on(&r, true);
    save(&r, json!({"enabled": true, "service_type": "77", "item_match": "announce"}));
    r.sync.sync(false).await;
    assert_eq!(*r.pc.calls.lock().unwrap(), [("item".to_owned(), "i1".to_owned())], "only the matching item was asked, and the plan's own files left out");

    save(&r, json!({"item_match": "", "text_slides": true, "note_category": "announcement", "playlist": true}));
    let st = r.sync.sync(false).await;
    assert_eq!((st["files"].clone(), st["texts"].clone()), (json!(3), json!(3)), "a slide for each real item, not the header");
    let pl = r.lib.lists.lock().unwrap().iter().find(|p| p["name"] == PLAYLIST_NAME).cloned().unwrap();
    let sources: Vec<&str> = pl["items"].as_array().unwrap().iter().map(|i| i["source"].as_str().unwrap()).collect();
    assert_eq!(sources, ["media", "media", KIND, KIND, KIND, "media"]);
    let durations = |kind: &str| -> Vec<i64> { pl["items"].as_array().unwrap().iter().filter(|i| i["source"] == kind).map(|i| i["duration_s"].as_i64().unwrap()).collect() };
    assert_eq!((durations(KIND), durations("media")), (vec![12, 12, 12], vec![10, 10, 10]));

    // The words of a slide are a page, with what was typed in Planning Center escaped.
    let texts = r.memory.texts();
    let ann = texts.values().find(|t| t["title"] == "Announcements").unwrap();
    let page = signagesync::text_page(ann["title"].as_str().unwrap(), ann["body"].as_str().unwrap());
    assert!(page.contains("Coffee after") && page.contains("Bring a friend") && !page.contains("secret"));
    let worship = texts.values().find(|t| t["title"] == "Worship <b>").unwrap();
    let page = signagesync::text_page(worship["title"].as_str().unwrap(), "");
    assert!(page.contains("&lt;b&gt;") && !page.contains("Worship <b>"));
    let source = signagesync::source(Arc::clone(&r.memory));
    assert_eq!((source.choices)().len(), 3);
    assert!((source.resolve)("nope").is_none(), "a missing slide resolves to nothing");
    let key = texts.keys().next().unwrap();
    assert_eq!((source.resolve)(key).unwrap()["type"], "page");

    // An item that stayed keeps its time; a new file joins.
    let mut edited = pl["items"].clone();
    edited.as_array_mut().unwrap().iter_mut().filter(|i| i["source"] == KIND).take(1).for_each(|i| i["duration_s"] = json!(30));
    let first_text = edited.as_array().unwrap().iter().find(|i| i["source"] == KIND).unwrap()["ref"].clone();
    r.lib.lists.lock().unwrap()[0]["items"] = edited;
    r.pc.item_files.lock().unwrap().insert("i3".into(), vec![att("a3", "extra.jpg", "image/jpeg", "https://files/a3", "x", false)]);
    r.pc.bodies.lock().unwrap().insert("https://files/a3".into(), jpeg("a3"));
    r.sync.sync(false).await;
    let pl = r.lib.lists.lock().unwrap()[0].clone();
    let kept = pl["items"].as_array().unwrap().iter().find(|i| i["ref"] == first_text).unwrap();
    assert_eq!(kept["duration_s"], 30, "an item that stayed kept its time");
    assert_eq!(pl["items"].as_array().unwrap().len(), 7, "and a new file joined");
}

#[tokio::test]
async fn a_missing_plan_or_a_failing_planning_center_is_said_and_retried() {
    let r = rig();
    signage_on(&r, true);
    save(&r, json!({"enabled": true, "service_type": "77", "playlist": true}));
    r.sync.sync(false).await;
    let held = r.lib.lists.lock().unwrap().len();
    *r.pc.broken.lock().unwrap() = true;
    assert_eq!(r.sync.sync(true).await["error"], "Planning Center did not give Relay the plan's files; it will try again.");
    assert_eq!(r.lib.lists.lock().unwrap().len(), held, "what was stored is kept");
}
