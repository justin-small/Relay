//! What `tests/test_propresenter_signage.py` checks (#208): announcement slides copied into signage as pictures and a playlist, and the slide-note tags that
//! switch screens (parsing, the layer, pushes, live views, resume), with signage on and off. ProPresenter is a fake over real sockets; signage is the
//! capabilities it offers (`signage.content`, `signage.control`), held in memory here, because a module depends on no other module. That the real signage
//! module resolves the tag layer is checked in its own tests.

mod fake;

use std::collections::BTreeSet;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::http::Method;
use fake::{lobby_items, rig, until, Rig};
use http_body_util::BodyExt;
use relay_core::caps::{ContentCap, ContentError, ContentResult, ControlCap, SignageContent, SignageControl, SignageLayer, SIGNAGE_CONTENT, SIGNAGE_CONTROL};
use relay_core::future::BoxFuture;
use relay_core::Module;
use relay_module_propresenter::signagesync::{PLAYLIST_NAME, PREFIX};
use relay_module_propresenter::slidetags::{self, parse, Tag, Tags};
use relay_module_propresenter::{settings, signagetags};
use serde_json::{json, Value};
use sha1::{Digest, Sha1};

const B: &str = "/api/admin/propresenter";

// ------------------------------------------------------------------------------------------------------------ signage, in memory
#[derive(Default)]
struct Library {
    media: Mutex<Vec<Value>>,
    lists: Mutex<Vec<Value>>,
    thumbs: Mutex<Vec<String>>,
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
            if !bytes.starts_with(&[0xff, 0xd8, 0xff]) {
                return Ok(None);
            }
            let sha: String = Sha1::digest(&bytes).iter().map(|b| format!("{b:02x}")).collect();
            Ok(Some((sha, "jpg".to_owned(), bytes.len() as u64)))
        })
    }
    fn media_add<'a>(&'a self, name: &'a str, sha: &'a str, ext: &'a str, size: u64) -> BoxFuture<'a, ContentResult<Value>> {
        Box::pin(async move {
            let mut m = self.media.lock().unwrap();
            let row = json!({"id": format!("m{}", m.len() + 1), "name": name, "sha256": sha, "ext": ext, "size": size, "kind": "image"});
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
            let mut l = self.lists.lock().unwrap();
            if let Some(p) = l.iter_mut().find(|p| p["id"] == id) {
                p["items"] = items.clone();
            }
            Ok(())
        })
    }
    fn media_thumb<'a>(&'a self, id: &'a str, _data: &'a [u8]) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move {
            self.thumbs.lock().unwrap().push(id.to_owned());
            Ok(())
        })
    }
}

/// What a module that drives screens may do, on screens, groups, playlists and pictures held in memory.
struct Screens {
    library: Arc<Library>,
    takeovers: Mutex<Vec<Value>>,
    bumps: Mutex<u32>,
}

impl Screens {
    fn row(id: i64, name: &str, group: Option<i64>) -> Value {
        json!({"id": id, "name": name, "group_id": group})
    }
}

impl SignageControl for Screens {
    fn screens(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async { Ok(vec![Self::row(1, "Lobby TV", Some(1)), Self::row(2, "Cafe", None), Self::row(3, "Hall TV", Some(2))]) })
    }
    fn groups(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async { Ok(vec![json!({"id": 1, "name": "Lobby"}), json!({"id": 2, "name": "Hall: east"})]) })
    }
    fn playlists(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async { Ok(vec![json!({"id": 11, "name": "Welcome"}), json!({"id": 12, "name": "Kids: Sunday"})]) })
    }
    fn media_list(&self) -> BoxFuture<'_, ContentResult<Vec<Value>>> {
        Box::pin(async move {
            let mut rows = vec![json!({"id": "poster", "name": "Poster", "kind": "image"}), json!({"id": "promo", "name": "Promo", "kind": "video"})];
            rows.extend(self.library.media.lock().unwrap().iter().cloned());
            Ok(rows)
        })
    }
    fn push_set<'a>(&'a self, target: &'a str, target_id: i64, media_id: &'a str) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move {
            self.takeovers.lock().unwrap().push(json!({"kind": "push", "mode": "once", "target": target, "target_id": target_id, "media": media_id}));
            Ok(())
        })
    }
    fn live_view_set<'a>(&'a self, target: &'a str, target_id: i64, view: &'a str) -> BoxFuture<'a, ContentResult<()>> {
        Box::pin(async move {
            self.takeovers.lock().unwrap().push(json!({"kind": "live", "target": target, "target_id": target_id, "view": view}));
            Ok(())
        })
    }
    fn takeover_clear<'a>(&'a self, target: &'a str, target_id: i64) -> BoxFuture<'a, ContentResult<bool>> {
        Box::pin(async move {
            let mut t = self.takeovers.lock().unwrap();
            let before = t.len();
            t.retain(|x| !(x["target"] == target && x["target_id"] == target_id));
            Ok(t.len() != before)
        })
    }
    fn takeover_clear_all(&self) -> BoxFuture<'_, ContentResult<u64>> {
        Box::pin(async move {
            let mut t = self.takeovers.lock().unwrap();
            let n = t.len() as u64;
            t.clear();
            Ok(n)
        })
    }
    fn bump(&self) {
        *self.bumps.lock().unwrap() += 1;
    }
}

/// Signage on: its capabilities offered to the host, the way the real module does while it is on.
fn signage_on(r: &Rig) -> (Arc<Library>, Arc<Screens>) {
    let library = Arc::new(Library::default());
    let screens = Arc::new(Screens { library: Arc::clone(&library), takeovers: Mutex::new(Vec::new()), bumps: Mutex::new(0) });
    r.host.provide("signage", SIGNAGE_CONTENT, Arc::new(ContentCap(library.clone())));
    r.host.provide("signage", SIGNAGE_CONTROL, Arc::new(ControlCap(screens.clone())));
    (library, screens)
}

fn signage_off(r: &Rig) {
    r.host.withdraw_all("signage");
}

async fn announcements(extra: Value) -> Rig {
    let r = rig().await;
    {
        let mut f = r.fake.lock();
        f.lobby_items = lobby_items();
        f.slides.insert("P-A".into(), vec!["a1".into(), "a2".into()]);
        f.slides.insert("P-B".into(), vec!["b1".into()]);
    }
    let mut s = json!({"signage": {"enabled": true, "playlist": "PL-ANN"}});
    for (k, v) in extra.as_object().into_iter().flatten() {
        s[k] = v.clone();
    }
    r.configure(s);
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await, "ProPresenter is up");
    r
}

fn names(lib: &Library) -> Vec<String> {
    let l = lib.lists.lock().unwrap();
    l.iter().find(|p| p["name"] == PLAYLIST_NAME).map_or_else(Vec::new, |p| p["items"].as_array().unwrap().iter().map(|i| i["ref"].as_str().unwrap().to_owned()).collect())
}

fn thumb_calls(r: &Rig) -> Vec<String> {
    r.fake.lock().requests.iter().filter(|q| q.1.contains("/thumbnail/")).map(|q| q.1.clone()).collect()
}

#[tokio::test]
async fn announcement_slides_become_signage_pictures_and_one_playlist() {
    let r = announcements(json!({})).await;
    let (lib, _) = signage_on(&r);
    // settings
    assert_eq!(settings::default_signage(), json!({"enabled": false, "playlist": ""}), "off and no playlist by default");
    assert_eq!(settings::signage_fields(&json!({"playlist": "a b!"})).1, "That is not a ProPresenter playlist.", "a bad playlist id is refused");
    assert_eq!(serde_json::Value::Object(settings::signage_fields(&json!({"enabled": true})).0), json!({"enabled": true}), "a key left out stays out");
    assert_eq!(r.send(Method::POST, B, Some(json!({"signage": {"enabled": true, "playlist": "PL-ANN"}}))).await.0.as_u16(), 200, "the page saves them");
    assert_eq!(r.ctx().follower.settings()["signage"], json!({"enabled": true, "playlist": "PL-ANN"}), "they are stored");
    assert_eq!(r.send(Method::POST, B, Some(json!({"signage": {"playlist": "x y"}}))).await.0.as_u16(), 400, "a bad one is a 400");

    let sync = &r.ctx().sync;
    let st = sync.sync(false).await;
    assert_eq!(st["error"], "", "no error");
    assert_eq!(st["slides"], 3, "three slides (a header and a video are skipped)");
    let lists: Vec<Value> = lib.lists.lock().unwrap().iter().filter(|p| p["name"] == PLAYLIST_NAME).cloned().collect();
    assert_eq!(lists.len(), 1, "one signage playlist was made");
    let items = lists[0]["items"].as_array().unwrap().clone();
    let media_names = |ids: &[Value]| -> Vec<String> { ids.iter().map(|i| lib.media.lock().unwrap().iter().find(|m| m["id"] == i["ref"]).map(|m| m["name"].as_str().unwrap().to_owned()).unwrap()).collect() };
    assert_eq!(media_names(&items), [format!("{PREFIX}Welcome 1"), format!("{PREFIX}Welcome 2"), format!("{PREFIX}Coffee 1")], "three items in order");
    assert!(items.iter().all(|i| i["source"] == "media" && i["duration_s"] == 10), "plain library pictures, each with a default time");
    assert_eq!(thumb_calls(&r).iter().filter(|p| p.contains("quality=1280")).count(), 3, "large pictures were fetched");
    assert_eq!(lib.thumbs.lock().unwrap().len(), 3, "and each got its small one for the admin page");
    let before = thumb_calls(&r).len();
    sync.sync(false).await;
    assert_eq!(thumb_calls(&r).len(), before, "nothing is fetched again while the deck is unchanged");

    // a changed slide gets a new picture, the rest are kept; a kept slide keeps its time
    {
        let mut l = lib.lists.lock().unwrap();
        l[0]["items"][0]["duration_s"] = json!(25);
    }
    {
        let mut f = r.fake.lock();
        f.redrawn.insert("1-1".into());
        f.slides.insert("P-A".into(), vec!["a1".into(), "a2 changed".into()]);
    }
    sync.sync(false).await;
    let items2 = lib.lists.lock().unwrap()[0]["items"].as_array().unwrap().clone();
    let same: Vec<bool> = items.iter().zip(&items2).map(|(a, b)| a["ref"] == b["ref"]).collect();
    assert_eq!(same, [true, false, true], "a changed slide gets a new picture, the rest are kept");
    assert_eq!(items2[0]["duration_s"], 25, "a kept slide keeps its time");
    assert!(!lib.media.lock().unwrap().iter().any(|m| m["id"] == items[1]["ref"]), "the old picture was deleted");
    assert_eq!(lib.media.lock().unwrap().iter().filter(|m| m["name"].as_str().unwrap().starts_with(PREFIX)).count(), 3, "only the playlist's pictures are left");
    let mine = lib.media_add("Welcome poster", &"0".repeat(40), "jpg", 5).await.unwrap();
    sync.sync(true).await;
    assert!(lib.media.lock().unwrap().iter().any(|m| m["id"] == mine["id"]), "an upload is never touched");
    r.fake.lock().lobby_items.truncate(2);
    sync.sync(false).await;
    assert_eq!(names(&lib).len(), 2, "a shortened playlist shrinks the signage one");

    // refusals say why
    let f = Arc::clone(&r.ctx().follower);
    r.fake.lock().refuse = true;
    r.fake.kill_streams();
    assert!(until(10.0, || !f.is_up()).await);
    assert_eq!(sync.sync(true).await["error"], "ProPresenter is not connected.", "ProPresenter away");
    r.fake.lock().refuse = false;
    assert!(until(10.0, || f.is_up()).await);
    r.amend(json!({"signage": {"enabled": true, "playlist": ""}}));
    assert_eq!(sync.sync(false).await["error"], "Pick the announcement playlist first.", "no playlist picked");
    r.amend(json!({"signage": {"enabled": false, "playlist": "PL-ANN"}}));
    assert_eq!(sync.sync(false).await["error"], "", "off does nothing");
    r.amend(json!({"signage": {"enabled": true, "playlist": "PL-ANN"}}));
    signage_off(&r);
    assert_eq!(sync.sync(true).await["error"], "Signage is switched off.", "signage off");
    let (st, body, _) = r.send(Method::POST, &format!("{B}/signage/sync"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["error"].clone()), (409, json!("Signage is switched off.")), "the sync route says so too");
    signage_on(&r);
    assert_eq!(r.send(Method::POST, &format!("{B}/signage/sync"), Some(json!({}))).await.0.as_u16(), 200, "sync now answers");
    r.amend(json!({"signage": {"enabled": false, "playlist": "PL-ANN"}}));
    assert_eq!(r.send(Method::POST, &format!("{B}/signage/sync"), Some(json!({}))).await.1["error"], "Switch on announcement slides for signage first.");
    r.module.stop().await;
}

#[test]
fn the_signage_tags_are_read() {
    for (text, want) in [
        ("[relay:signage-screen=Lobby TV:Welcome]", Tag { action: "signage-screen".into(), value: Some("Lobby TV:Welcome".into()) }),
        ("[relay:signage-all=Welcome]", Tag { action: "signage-all".into(), value: Some("Welcome".into()) }),
        ("[relay:signage-captions]", Tag { action: "signage-captions".into(), value: None }),
        ("[relay:signage-captions-group=Lobby]", Tag { action: "signage-captions-group".into(), value: Some("Lobby".into()) }),
        ("[relay:SIGNAGE-RESUME]", Tag { action: "signage-resume".into(), value: None }),
        ("[relay:signage-push=Poster]", Tag { action: "signage-push".into(), value: Some("Poster".into()) }),
    ] {
        assert_eq!(parse(text).slide, [want], "parses {text}");
    }
    assert!(parse("[relay:signage-screen]").slide.is_empty(), "a value is needed");
    assert!(parse("[relay:signage-resume=x]").slide.is_empty(), "resume takes none for all screens");
    assert_eq!(parse("[relay:lang=es][relay:lyrics]").slide, [Tag { action: "lang".into(), value: Some("SPANISH".into()) }, Tag { action: "lyrics".into(), value: None }], "the existing tags are unchanged");
    assert_eq!(signagetags::names().len(), 15, "every signage action is listed");
}

async fn act(r: &Rig, name: &str, value: Option<&str>) -> String {
    match r.ctx().signage.run(name, value).await {
        Ok(done) => done,
        Err(why) => format!("ERR {why}"),
    }
}

fn plays(r: &Rig, screen: Value) -> Option<i64> {
    r.ctx().signage.layer(&screen)?["playlist_id"].as_i64()
}

#[tokio::test]
async fn the_actions_put_playlists_pictures_and_live_views_on_screens() {
    let r = rig().await;
    let (_, screens) = signage_on(&r);
    let lobby_tv = json!({"id": 1, "name": "Lobby TV", "group_id": 1});
    let cafe = json!({"id": 2, "name": "Cafe", "group_id": null});
    let hall = json!({"id": 3, "name": "Hall TV", "group_id": 2});
    assert!(act(&r, "signage-screen", Some("lobby tv:welcome")).await.starts_with("Lobby TV now plays Welcome"), "one screen");
    assert_eq!(plays(&r, lobby_tv.clone()), Some(11), "its layer is the tag");
    assert_eq!(plays(&r, cafe.clone()), None, "another screen is untouched");
    assert!(act(&r, "signage-group", Some("Lobby:Kids: Sunday")).await.starts_with("Lobby now plays Kids: Sunday"), "a group");
    assert_eq!(plays(&r, lobby_tv.clone()), Some(11), "the screen's own beats its group's");
    assert!(act(&r, "signage-group", Some("Hall: east:Welcome")).await.starts_with("Hall: east now plays Welcome"), "a name with a colon");
    assert_eq!(plays(&r, hall.clone()), Some(11), "the hall screen plays it");
    assert!(act(&r, "signage-all", Some("Kids: Sunday")).await.starts_with("every screen now plays"), "all screens");
    assert_eq!(plays(&r, cafe.clone()), Some(12), "the cafe plays it");
    assert!(act(&r, "signage-all", Some("Nope")).await.contains("no playlist called"), "an unknown playlist says so");
    assert!(act(&r, "signage-screen", Some("Nobody:Welcome")).await.contains("no screen called"), "an unknown screen says so");
    assert!(act(&r, "signage-screen", Some("Lobby TV")).await.contains("Write it as"), "a missing playlist half says how to write it");
    let held = r.ctx().signage.state().await;
    assert!(held["held"].as_array().unwrap().iter().any(|h| h["playlist"] == "Kids: Sunday"), "the panel lists what tags hold");

    assert!(act(&r, "signage-push", Some("poster")).await.starts_with("Sent Poster to every screen"), "a push");
    let first = screens.takeovers.lock().unwrap()[0].clone();
    assert_eq!((first["mode"].clone(), first["target"].clone()), (json!("once"), json!("all")), "it is a once push to all");
    assert!(act(&r, "signage-push-screen", Some("Cafe:Poster")).await.starts_with("Sent Poster to Cafe"), "a push to a screen");
    assert!(act(&r, "signage-push", Some("Promo")).await.contains("no picture called"), "a video is not a picture");
    screens.library.media.lock().unwrap().push(json!({"id": "w1", "name": format!("{PREFIX}Welcome 1"), "kind": "image"}));
    assert!(act(&r, "signage-push", Some("Welcome 1")).await.starts_with(&format!("Sent {PREFIX}Welcome 1")), "a slide by its short name");
    assert!(act(&r, "signage-captions-group", Some("Lobby")).await.contains("shows the live captions"), "captions on one group");
    assert!(act(&r, "signage-service", None).await.contains("shows the live service"), "service on all screens");
    assert!(act(&r, "signage-captions-screen", Some("Nobody")).await.starts_with("ERR"), "an unknown view target");
    assert!(act(&r, "signage-resume-screen", Some("Cafe")).await.ends_with("back on its playlist."), "resume one screen");
    assert!(act(&r, "signage-resume", None).await.starts_with("Every screen is back"), "resume all");
    assert!(screens.takeovers.lock().unwrap().is_empty(), "nothing is taken over");
    assert_eq!(plays(&r, lobby_tv), None, "no tag layer left");
    assert!(*screens.bumps.lock().unwrap() > 0, "the players were told");

    signage_off(&r);
    assert_eq!(act(&r, "signage-all", Some("Welcome")).await, format!("ERR {}", signagetags::OFF), "a tag says signage is off");
    assert_eq!(r.ctx().signage.state().await, json!({"on": false, "held": []}), "the panel says so");
}

#[tokio::test]
async fn a_slides_tag_reaches_its_action_once_it_has_settled() {
    let ran = Arc::new(Mutex::new(Vec::<Option<String>>::new()));
    let tags = Tags::new();
    let log = Arc::clone(&ran);
    tags.set_action("signage-all", Arc::new(move |v: Option<String>| {
        let log = Arc::clone(&log);
        Box::pin(async move {
            log.lock().unwrap().push(v);
            Ok("ok".to_owned())
        })
    }));
    tags.observe(&slidetags::Observed { enabled: true, settle_s: 0.05, uuid: Some("U"), title: "Deck", index: Some(0), notes: "[relay:signage-all=Welcome]", deck: None });
    assert!(until(3.0, || ran.lock().unwrap().len() == 1).await, "a slide's tag reaches the action once settled");
    assert_eq!(*ran.lock().unwrap(), [Some("Welcome".to_owned())]);
    assert_eq!(tags.state()["seen"]["resolved"], json!(["signage-all=Welcome"]), "the panel's resolved list shows it");
    let _ = (BTreeSet::<u8>::new(), Duration::ZERO);
}

#[tokio::test]
async fn the_tag_layer_goes_with_the_module() {
    use relay_core::caps::SIGNAGE_LAYER;
    let r = rig().await;
    r.configure(json!({}));
    r.start().await;
    let layer = r.host.using::<SignageLayer>(&format!("{SIGNAGE_LAYER}tag"));
    assert!(layer.is_some(), "the layer is offered while the module is on");
    r.module.stop().await;
    assert!(r.host.using::<SignageLayer>(&format!("{SIGNAGE_LAYER}tag")).is_none(), "and gone when it stops");
    r.start().await;
    assert!(r.host.using::<SignageLayer>(&format!("{SIGNAGE_LAYER}tag")).is_some(), "and comes back");
    r.module.stop().await;
}
