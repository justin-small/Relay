//! Core's routes as a module sees them (#658): `relay-core`'s `Routes` over `relay-audio`'s channels and routing on a real SQLite file (the
//! Python migrations 0014 and 0015 applied). A provider a module registers is the one a route asked for on its destination reaches; what it
//! reads back is what core shows; a preset saves and takes; the app's own set of providers does not wipe it.

use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use relay_audio::{Channels, Routing};
use relay_auth::{Auth, Cost};
use relay_core::future::BoxFuture;
use relay_core::routes::{ReadSource, RouteError, RouteProvider, Routes, WantedSource};
use relay_server::routescap::CoreRoutes;
use serde_json::{json, Value};

static N: AtomicUsize = AtomicUsize::new(0);

async fn database() -> (Auth, PathBuf) {
    let dir = std::env::temp_dir().join(format!("relay-routescap-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("dir");
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.expect("open");
    sqlx::raw_sql("create table gear (id integer primary key)").execute(auth.pool()).await.expect("gear");
    sqlx::raw_sql(include_str!("../../../app/migrations/0014_channels.sql")).execute(auth.pool()).await.expect("channels");
    sqlx::raw_sql(include_str!("../../../app/migrations/0015_routes.sql")).execute(auth.pool()).await.expect("routes");
    (auth, dir)
}

/// A module's provider: video, one source per destination, what it was told is what it reads back.
#[derive(Default)]
struct Screen {
    told: Mutex<Vec<(String, Vec<String>)>>,
    shows: Mutex<Option<String>>,
    refuse: Mutex<String>,
}

impl RouteProvider for Screen {
    fn exclusive(&self) -> bool {
        true
    }
    fn gain(&self) -> bool {
        false
    }
    fn permission(&self) -> Option<String> {
        Some("demo.manage".to_owned())
    }
    fn apply<'a>(&'a self, end: &'a Value, wanted: &'a [WantedSource]) -> BoxFuture<'a, String> {
        Box::pin(async move {
            let keys: Vec<String> = wanted.iter().flat_map(|w| w.ends.iter().filter_map(|e| e["key"].as_str().map(str::to_owned))).collect();
            self.told.lock().expect("lock").push((end["key"].as_str().unwrap_or("").to_owned(), keys.clone()));
            let refuse = self.refuse.lock().expect("lock").clone();
            if refuse.is_empty() {
                *self.shows.lock().expect("lock") = keys.first().cloned();
            }
            refuse
        })
    }
    fn read<'a>(&'a self, _end: &'a Value) -> BoxFuture<'a, Option<Vec<ReadSource>>> {
        Box::pin(async move { Some(self.shows.lock().expect("lock").clone().map(|key| ReadSource { provider: Some("demo".into()), key, gain_db: None }).into_iter().collect()) })
    }
}

async fn setup() -> (CoreRoutes, Arc<Screen>, Auth) {
    let (auth, _) = database().await;
    let channels = Channels::new(auth.pool().clone());
    let core = CoreRoutes { routing: Routing::new(channels.clone(), auth.pool().clone()), channels };
    let screen = Arc::new(Screen::default());
    core.register("demo", screen.clone());
    core.report(
        "demo",
        vec![
            json!({"key": "src:Cam 1", "kind": "video", "direction": "source", "gear_name": "Cam 1"}),
            json!({"key": "src:Cam 2", "kind": "video", "direction": "source", "gear_name": "Cam 2"}),
            json!({"key": "out:tv", "kind": "video", "direction": "destination", "gear_name": "TV"}),
        ],
    )
    .await;
    (core, screen, auth)
}

async fn channel_of(core: &CoreRoutes, key: &str) -> String {
    core.channels().await.expect("channels").iter().find(|c| c["ends"].as_array().is_some_and(|e| e.iter().any(|x| x["key"] == key))).and_then(|c| c["ref"].as_str().map(str::to_owned)).expect("channel")
}

#[tokio::test(flavor = "multi_thread")]
async fn a_route_reaches_the_module_that_owns_the_destination() {
    let (core, screen, _auth) = setup().await;
    let (tv, cam1) = (channel_of(&core, "out:tv").await, channel_of(&core, "src:Cam 1").await);
    assert_eq!(core.set(&tv, &cam1).await.expect("set"), Vec::<String>::new());
    assert_eq!(screen.told.lock().expect("lock").as_slice(), [("out:tv".to_owned(), vec!["src:Cam 1".to_owned()])], "the provider was handed the source's ends");
    let views = core.views("demo").await.expect("views");
    assert_eq!((views[0]["state"].clone(), views[0]["destination"].clone()), (json!("same"), json!(tv)), "what the module reads is what was asked");
    assert!(core.clear(&tv).await.expect("clear").is_empty());
    assert_eq!(screen.shows.lock().expect("lock").clone(), None, "clearing told the module to show nothing");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_module_that_cannot_apply_a_route_says_why() {
    let (core, screen, _auth) = setup().await;
    *screen.refuse.lock().expect("lock") = "The TV is off.".to_owned();
    let (tv, cam1) = (channel_of(&core, "out:tv").await, channel_of(&core, "src:Cam 1").await);
    assert_eq!(core.set(&tv, &cam1).await.expect("set"), vec!["The TV is off.".to_owned()]);
    assert_eq!(core.views("demo").await.expect("views")[0]["state"], "differs", "asked for, but the gear does not show it");
}

#[tokio::test(flavor = "multi_thread")]
async fn refusals_are_fixed_sentences() {
    let (core, _screen, _auth) = setup().await;
    let tv = channel_of(&core, "out:tv").await;
    assert_eq!(core.set(&tv, &tv).await, Err(RouteError::Refused("A channel cannot feed itself.")));
    assert_eq!(core.set("channel:relay:999", &tv).await, Err(RouteError::Refused("That channel does not exist.")));
    assert_eq!(core.take_salvo(42).await, Err(RouteError::Refused("That preset does not exist.")));
    assert_eq!(core.save_salvo("  ", vec![tv], "demo", None).await, Err(RouteError::Refused("Give the preset a name.")));
}

#[tokio::test(flavor = "multi_thread")]
async fn presets_save_and_take_by_scope() {
    let (core, screen, _auth) = setup().await;
    let (tv, cam1, cam2) = (channel_of(&core, "out:tv").await, channel_of(&core, "src:Cam 1").await, channel_of(&core, "src:Cam 2").await);
    core.set(&tv, &cam1).await.expect("set");
    let saved = core.save_salvo("Opening", vec![tv.clone()], "demo", None).await.expect("saved");
    core.set(&tv, &cam2).await.expect("set");
    assert_eq!(core.salvos("demo").await.expect("salvos").len(), 1);
    assert!(core.salvos("other").await.expect("salvos").is_empty(), "presets are kept apart by scope");
    assert!(core.take_salvo(saved["id"].as_i64().expect("id")).await.expect("take").is_empty());
    assert_eq!(screen.shows.lock().expect("lock").clone(), Some("src:Cam 1".to_owned()), "taking it pointed the output back");
    core.delete_salvo(saved["id"].as_i64().expect("id")).await.expect("delete");
    assert!(core.salvos("demo").await.expect("salvos").is_empty());
}

#[tokio::test(flavor = "multi_thread")]
async fn reapply_tells_the_module_again_and_a_missing_channel_can_be_forgotten() {
    let (core, screen, _auth) = setup().await;
    let (tv, cam1) = (channel_of(&core, "out:tv").await, channel_of(&core, "src:Cam 1").await);
    core.set(&tv, &cam1).await.expect("set");
    *screen.shows.lock().expect("lock") = None; // the gear forgot it, as a restarted router does
    core.reapply("demo").await.expect("reapply");
    assert_eq!(screen.shows.lock().expect("lock").clone(), Some("src:Cam 1".to_owned()));
    let ghost = core.ensure("demo", "src:Gone", "video", "source", "Gone").await.expect("ensure").expect("ref");
    assert_eq!(core.forget(&tv).await, Err(RouteError::Refused("That channel is still reported; only a missing one can be forgotten.")));
    core.forget(&ghost).await.expect("a channel nothing reports can be forgotten");
}

#[tokio::test(flavor = "multi_thread")]
async fn withdrawing_a_provider_stops_it_being_told() {
    let (core, screen, _auth) = setup().await;
    let (tv, cam1) = (channel_of(&core, "out:tv").await, channel_of(&core, "src:Cam 1").await);
    core.withdraw("demo");
    core.set(&tv, &cam1).await.expect("set");
    assert!(screen.told.lock().expect("lock").is_empty(), "no module that is on applies it, so core keeps it for when one is");
}
