//! The directory and the channels as a module sees them (#666): Planning Center's teams and positions read into `directory_items` (the
//! Python migration 0007) and announced when they change, a missing or renamed one, and the audio channels (migration 0014) a rundown
//! picks from, with a merged-away ref still working.

use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

use relay_audio::Channels;
use relay_auth::{Auth, Cost};
use relay_core::caps::{ChannelCatalog, Directory, PlanCap, DIRECTORY_CHANGED, PCO, PLAN};
use relay_core::{Host, Shutdown};
use relay_server::modcaps::{ChannelList, DirectoryStore};
use serde_json::{json, Value};

static N: AtomicUsize = AtomicUsize::new(0);

async fn database() -> (Auth, PathBuf) {
    let dir = std::env::temp_dir().join(format!("relay-modcaps-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("dir");
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.expect("open");
    sqlx::raw_sql("create table gear (id integer primary key)").execute(auth.pool()).await.expect("gear");
    sqlx::raw_sql(include_str!("../../../app/migrations/0007_directory_items.sql")).execute(auth.pool()).await.expect("directory");
    sqlx::raw_sql(include_str!("../../../app/migrations/0014_channels.sql")).execute(auth.pool()).await.expect("channels");
    (auth, dir)
}

/// A Planning Center that lists whatever teams it is told to.
fn plan_with(teams: Arc<Mutex<Vec<Value>>>) -> Arc<PlanCap> {
    Arc::new(PlanCap {
        service_types: Box::new(Vec::new),
        offered: Box::new(Vec::new),
        label: Box::new(String::new),
        starts_at: Box::new(|| None),
        times: Box::new(|_, _| Vec::new()),
        people: Box::new(Vec::new),
        teams: Box::new(move || teams.lock().unwrap().clone()),
    })
}

fn band(name: &str, vocals: &str) -> Value {
    json!({"id": "12", "name": name, "positions": [{"id": "34", "name": vocals}, {"id": "35", "name": "Drums"}]})
}

#[tokio::test(flavor = "multi_thread")]
async fn teams_and_positions_are_cached_renamed_and_marked_missing() {
    let (auth, _) = database().await;
    let host = Host::new(Shutdown::new());
    let dir = DirectoryStore::new(auth.pool().clone(), host.clone());
    let announced = Arc::new(Mutex::new(Vec::<String>::new()));
    let heard = Arc::clone(&announced);
    host.listen("test", DIRECTORY_CHANGED, Arc::new(move |p| heard.lock().unwrap().push(p.to_owned())));

    dir.refresh().await;
    assert!(dir.positions().is_empty(), "Planning Center is off: nothing is listed and nothing is invented");
    assert!(!dir.sync(PCO).await.unwrap());

    let teams = Arc::new(Mutex::new(vec![band("Band", "Vocals")]));
    host.provide("planning_center", PLAN, plan_with(Arc::clone(&teams)));
    dir.refresh().await;
    let names: Vec<(String, String, String)> = dir.positions().into_iter().map(|p| (p.reference, p.name, p.team)).collect();
    assert_eq!(names, [("position:pco:12/35".into(), "Drums".into(), "Band".into()), ("position:pco:12/34".into(), "Vocals".into(), "Band".into())], "positions by name, each with its team's");
    assert_eq!(announced.lock().unwrap().as_slice(), ["pco"], "the first listing is announced");
    assert!(dir.knows("team:pco:12") && dir.knows("position:pco:12/34") && !dir.knows("position:pco:12/99"));

    dir.refresh().await;
    assert_eq!(announced.lock().unwrap().len(), 1, "the same listing is not announced again");

    *teams.lock().unwrap() = vec![band("Band", "Lead Vocals")];
    dir.refresh().await;
    let d = dir.describe("position:pco:12/34", "Vocals");
    assert_eq!((d.name.as_str(), d.missing, d.unmatched), ("Lead Vocals", false, false), "a rename shows the new name");
    assert_eq!(announced.lock().unwrap().len(), 2);

    *teams.lock().unwrap() = vec![json!({"id": "12", "name": "Band", "positions": [{"id": "35", "name": "Drums"}]})];
    dir.refresh().await;
    let d = dir.describe("position:pco:12/34", "Vocals");
    assert_eq!((d.name.as_str(), d.missing), ("Lead Vocals", true), "a position Planning Center dropped is missing, and keeps its last name");
    assert!(dir.positions().iter().any(|p| p.name == "Lead Vocals" && p.missing));

    host.withdraw_all("planning_center");
    *teams.lock().unwrap() = Vec::new();
    let fresh = DirectoryStore::new(auth.pool().clone(), host.clone());
    fresh.refresh().await;
    assert!(fresh.knows("position:pco:12/35"), "with the module off, a fresh start still reads what was cached");
    let d = fresh.describe("", "Typed Name");
    assert_eq!((d.name.as_str(), d.unmatched), ("Typed Name", true), "a name with no ref is kept as typed");
    let d = fresh.describe("position:pco:9/9", "Old");
    assert_eq!((d.name.as_str(), d.missing, d.unmatched), ("Old", false, false), "a ref never heard of keeps its saved name");
}

#[tokio::test(flavor = "multi_thread")]
async fn plan_changed_makes_the_directory_read_planning_center() {
    let (auth, _) = database().await;
    let host = Host::new(Shutdown::new());
    let dir = DirectoryStore::new(auth.pool().clone(), host.clone());
    dir.listen();
    host.provide("planning_center", PLAN, plan_with(Arc::new(Mutex::new(vec![band("Band", "Vocals")]))));
    host.announce(relay_core::caps::PLAN_CHANGED, "");
    for _ in 0..100 {
        if dir.knows("team:pco:12") {
            break;
        }
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
    }
    assert!(dir.knows("team:pco:12"), "the followed plans changed, so the teams were read");
}

#[tokio::test(flavor = "multi_thread")]
async fn audio_channels_are_listed_and_a_merged_ref_still_works() {
    let (auth, _) = database().await;
    let channels = Channels::new(auth.pool().clone());
    let list = ChannelList::new(channels.clone());
    list.refresh().await;
    assert!(list.audio().is_empty());
    channels
        .report("demo", &[
            json!({"key": "in:1", "kind": "audio", "direction": "source", "number": 1, "gear_name": "Lead Vox"}),
            json!({"key": "in:2", "kind": "audio", "direction": "source", "number": 2, "gear_name": "Piano"}),
            json!({"key": "cam", "kind": "video", "direction": "source", "gear_name": "Camera"}),
        ])
        .await;
    list.refresh().await;
    let audio = list.audio();
    assert_eq!(audio.iter().map(|c| c.shown.as_str()).collect::<Vec<_>>(), ["Lead Vox", "Piano"], "only audio, as `listing(\"audio\")`");
    assert_eq!((audio[0].number.as_deref(), audio[0].direction.as_str(), audio[0].missing), (Some("1"), "source", false));
    let (vox, piano) = (audio[0].reference.clone(), audio[1].reference.clone());
    assert_eq!(list.by_ref(&vox).map(|c| c.shown), Some("Lead Vox".to_owned()));
    assert!(list.by_ref("channel:relay:999").is_none() && list.by_ref("not a ref").is_none());

    let (a, b) = (relay_core::caps::channel_id(&vox).unwrap(), relay_core::caps::channel_id(&piano).unwrap());
    channels.merge(a, b).await.expect("merge");
    list.refresh().await;
    assert_eq!(list.by_ref(&piano).map(|c| c.reference), Some(vox.clone()), "the merged-away ref names the channel it went into");
    assert_eq!(list.audio().len(), 1);
}
