//! The audio bus, routes, channels and meters on a real SQLite file (the Python migrations 0014 and 0015 applied) and the engine's
//! deterministic fake sound card. The rules are the ones `tests/test_audiobus.py`, `test_routing.py`, `test_channels.py` and
//! `test_metering.py` pin for the Python server.

use std::collections::HashMap;
use std::future::Future;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use relay_audio::channels::{End, Fut, Gear};
use relay_audio::metering::{self, MeterStore};
use relay_audio::normalize::{first_setup, migrate_legacy_input, needs_setup, normalize, Pick};
use relay_audio::routing::{Provider, ReadItem, Wanted};
use relay_audio::{AudioBus, Backing, Channels, Error, Metering, Routing, Store};
use relay_auth::{Auth, Cost};
use serde_json::{json, Value};

static N: AtomicUsize = AtomicUsize::new(0);

fn temp_db() -> PathBuf {
    let dir = std::env::temp_dir().join(format!("relay-audio-test-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.join("relay.db")
}

/// A database with the auth tables and the channels and routes tables of the Python migrations.
async fn database() -> (Auth, PathBuf) {
    let path = temp_db();
    let auth = Auth::open(&path, Cost::fast()).await.expect("open");
    sqlx::raw_sql("create table gear (id integer primary key)").execute(auth.pool()).await.unwrap();
    sqlx::raw_sql(include_str!("../../../app/migrations/0014_channels.sql")).execute(auth.pool()).await.unwrap();
    sqlx::raw_sql(include_str!("../../../app/migrations/0015_routes.sql")).execute(auth.pool()).await.unwrap();
    (auth, path)
}

#[derive(Default)]
struct Memory {
    audio_bus: Mutex<Value>,
    metering: Mutex<Value>,
}

impl Store for Memory {
    fn audio_bus(&self) -> Value {
        self.audio_bus.lock().unwrap().clone()
    }
    fn save_audio_bus(&self, cfg: &Value) -> Value {
        let clean = normalize(cfg);
        *self.audio_bus.lock().unwrap() = clean.clone();
        clean
    }
    fn targets(&self) -> Vec<String> {
        vec!["es".into(), "fr".into()]
    }
}

impl MeterStore for Memory {
    fn metering(&self) -> Value {
        let m = self.metering.lock().unwrap().clone();
        if m.is_null() { json!({"meters": []}) } else { m }
    }
    fn save_metering(&self, cfg: &Value) -> Value {
        let clean = metering::normalize(cfg);
        *self.metering.lock().unwrap() = clean.clone();
        clean
    }
}

struct World {
    _auth: Auth,
    channels: Arc<Channels>,
    routing: Arc<Routing>,
    bus: Arc<AudioBus>,
    store: Arc<Memory>,
}

async fn world() -> World {
    let (auth, _) = database().await;
    let channels = Channels::new(auth.pool().clone());
    let routing = Routing::new(channels.clone());
    let store = Arc::new(Memory::default());
    let bus = AudioBus::new(Backing::fake(), store.clone(), channels.clone(), routing.clone());
    World { _auth: auth, channels, routing, bus, store }
}

async fn wait_for<F, Fut>(what: &str, mut check: F)
where
    F: FnMut() -> Fut,
    Fut: Future<Output = bool>,
{
    let end = Instant::now() + Duration::from_secs(5);
    while Instant::now() < end {
        if check().await {
            return;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    panic!("timed out waiting for {what}");
}

fn report(key: &str, kind: &str, direction: &str, name: &str, number: i64) -> Value {
    json!({"key": key, "kind": kind, "direction": direction, "gear_name": name, "number": number})
}

// ---------------------------------------------------------------------------------------------------------- normalize
#[test]
fn normalize_drops_what_it_cannot_keep() {
    let got = normalize(&json!({
        "lanes": [
            {"lane": 2, "name": "  Stage  ", "source": " NDI ", "device": "", "channel": "MIX"},
            {"lane": 2, "name": "again"},
            {"lane": 33}, {"lane": 0}, "junk",
            {"lane": "1", "device": "Mic", "channel": 999}
        ],
        "outputs": [
            {"id": "Main Out!", "channels": 99, "latency_ms": 5},
            {"id": "bus"}, {"id": ""}, {"id": "main_out"},
            {"id": "aes", "output": "AES67", "channels": "4"}
        ],
        "matrix": [
            {"in": 1, "out": "main_out", "ch": 1, "gain_db": 99},
            {"in": 1, "out": "main_out", "ch": 1},
            {"in": 1, "out": "main_out", "ch": 33},
            {"in": 1, "out": "nowhere", "ch": 1},
            {"in": 2, "out": "aes", "ch": 5},
            {"in": 3, "out": "aes", "ch": 2, "gain_db": "-3.456"}
        ]
    }));
    let lanes = got["lanes"].as_array().unwrap();
    assert_eq!(lanes.iter().map(|l| l["lane"].as_i64().unwrap()).collect::<Vec<_>>(), [1, 2], "sorted, duplicates and out-of-range gone");
    assert_eq!(lanes[0]["channel"], 255, "a channel is clamped");
    assert_eq!(lanes[0]["source"], "portaudio");
    assert_eq!(lanes[1], json!({"lane": 2, "name": "Stage", "source": "ndi", "device": null, "channel": "mix"}));
    let outs = got["outputs"].as_array().unwrap();
    assert_eq!(outs.iter().map(|o| o["id"].as_str().unwrap()).collect::<Vec<_>>(), ["main_out", "aes"], "bus is reserved, ids are tidied, repeats dropped");
    assert_eq!(outs[0]["channels"], 32);
    assert_eq!(outs[0]["latency_ms"], 20);
    assert_eq!(outs[1]["channels"], 4);
    let cells: Vec<(i64, &str, i64, f64)> = got["matrix"].as_array().unwrap().iter().map(|c| (c["in"].as_i64().unwrap(), c["out"].as_str().unwrap(), c["ch"].as_i64().unwrap(), c["gain_db"].as_f64().unwrap())).collect();
    assert_eq!(cells, [(3, "aes", 2, -3.46), (1, "main_out", 1, 24.0)], "sorted by output, channel, lane; gain clamped and rounded");
}

#[test]
fn first_setup_adds_only_what_is_missing() {
    let (cfg, made) = first_setup(&json!({}), &Pick::Default, &Pick::Device("Speakers".into()), 2);
    assert!(made);
    assert_eq!(cfg["lanes"][0], json!({"lane": 1, "name": "Main input", "source": "portaudio", "device": null, "channel": "mix"}));
    assert_eq!(cfg["outputs"][0]["device"], "Speakers");
    assert_eq!(needs_setup(&cfg), json!({"input": false, "output": false}));
    let (again, made) = first_setup(&cfg, &Pick::Device("Other".into()), &Pick::Device("Other".into()), 2);
    assert!(!made, "an output already set up is kept");
    assert_eq!(again, cfg);
    let (later, made) = first_setup(&json!({}), &Pick::Later, &Pick::Later, 2);
    assert!(!made);
    assert_eq!(needs_setup(&later), json!({"input": true, "output": true}));
}

#[test]
fn an_old_install_keeps_its_input_as_a_lane() {
    let mut cfg = json!({"audio_source": "portaudio", "audio_device": "USB Mic", "input_channel": 1, "audio_lane": 3});
    migrate_legacy_input(&mut cfg);
    assert_eq!(cfg["audio_lane"], 3);
    assert_eq!(cfg["audio_bus"]["lanes"][0]["device"], "USB Mic");
    let mut fresh = json!({"port": 80});
    migrate_legacy_input(&mut fresh);
    assert!(fresh.get("audio_bus").is_none(), "a fresh install is left alone");
    let mut lane = json!({"audio_source": "lane", "audio_device": "Lane 7"});
    migrate_legacy_input(&mut lane);
    assert_eq!(lane["audio_lane"], 7);
}

// ---------------------------------------------------------------------------------------------------------- channels
#[tokio::test]
async fn channels_follow_what_modules_report() {
    let w = world().await;
    let c = &w.channels;
    let refs = c.sync("ndi", &[report("cam1", "video", "source", "Camera 1", 1), report("cam2", "video", "source", "Camera 2", 2), json!({"key": "x"})], None, "").await.unwrap();
    assert_eq!(refs.len(), 2, "an entry with no kind is ignored");
    let all = c.listing("").await.unwrap();
    assert_eq!(all.len(), 2);
    assert_eq!(all[0].shown, "Camera 1");
    // a channel no longer reported is missing, not gone, and comes back
    c.sync("ndi", &[report("cam1", "video", "source", "Camera 1", 1)], None, "").await.unwrap();
    assert!(c.listing("").await.unwrap()[1].missing);
    c.sync("ndi", &[report("cam2", "video", "source", "Camera 2", 2)], None, "").await.unwrap();
    assert!(!c.listing("").await.unwrap()[1].missing);
    assert!(c.sync("Bad Provider", &[report("k", "audio", "source", "x", 1)], None, "").await.unwrap().is_empty());
    assert_eq!(c.listing("audio").await.unwrap().len(), 0);
}

#[tokio::test]
async fn merging_keeps_the_old_ref_working_and_splitting_undoes_it() {
    let w = world().await;
    let c = &w.channels;
    c.sync("obs", &[report("a", "audio", "source", "Vocal 1", 1)], None, "").await.unwrap();
    c.sync("console", &[report("b", "audio", "source", "Vocal 1", 1)], None, "").await.unwrap();
    let sug = c.suggestions().await.unwrap();
    assert_eq!(sug.len(), 1, "the same name is suggested, never merged on its own");
    assert_eq!(sug[0].2, "name");
    let merged = c.merge(1, 2).await.unwrap();
    assert_eq!(merged.ends.len(), 2);
    assert_eq!(c.get(2).await.unwrap().unwrap().id, 1, "the merged-away ref follows to the channel that holds its ends");
    assert!(matches!(c.merge(1, 2).await, Err(Error::Refused("same"))));
    assert_eq!(c.names().await.unwrap().get("channel:relay:2").map(String::as_str), Some("Vocal 1"));
    let end = merged.ends[1].id;
    let split = c.split(end).await.unwrap();
    assert_ne!(split.id, 1);
    assert!(matches!(c.split(end).await, Err(Error::Refused("only_end"))));
    c.decline(1, split.id).await.unwrap();
    assert!(c.suggestions().await.unwrap().is_empty(), "a declined pair is not suggested again");
    assert!(matches!(c.forget(1).await, Err(Error::Refused("present"))));
    c.rename(1, "  Lead   vocal ").await.unwrap();
    assert_eq!(c.get(1).await.unwrap().unwrap().shown, "Lead vocal");
}

#[tokio::test]
async fn a_generic_name_is_not_a_reason_to_suggest_a_merge() {
    let w = world().await;
    w.channels.sync("a", &[report("1", "audio", "source", "Input 3", 3)], None, "").await.unwrap();
    w.channels.sync("b", &[report("1", "audio", "source", "Input 3", 4)], None, "").await.unwrap();
    assert!(w.channels.suggestions().await.unwrap().is_empty());
}

struct RenamesAll;
impl Gear for RenamesAll {
    fn can_rename(&self, provider: &str, _key: &str) -> bool {
        provider == "console"
    }
    fn rename<'a>(&'a self, _p: &'a str, _k: &'a str, name: &'a str) -> Fut<'a, Result<(), ()>> {
        Box::pin(async move { if name == "refuse" { Err(()) } else { Ok(()) } })
    }
}

#[tokio::test]
async fn a_rename_on_the_gear_changes_nothing_when_the_gear_refuses() {
    let w = world().await;
    w.channels.sync("console", &[report("a", "audio", "source", "Ch 1", 1)], None, "").await.unwrap();
    assert!(matches!(w.channels.rename_on_device(1, "Vocal").await, Err(Error::Refused("no_device"))), "no module offers a rename yet");
    w.channels.set_gear(Some(Arc::new(RenamesAll)));
    assert!(w.channels.get(1).await.unwrap().unwrap().can_device);
    assert!(matches!(w.channels.rename_on_device(1, "refuse").await, Err(Error::Refused("device"))));
    assert_eq!(w.channels.get(1).await.unwrap().unwrap().gear_name, "Ch 1");
    w.channels.rename_on_device(1, "Vocal").await.unwrap();
    assert_eq!(w.channels.get(1).await.unwrap().unwrap().gear_name, "Vocal");
}

// ----------------------------------------------------------------------------------------------------------- routing
/// A video destination that applies at once, and reports what it was last told.
struct Output {
    told: Mutex<Vec<String>>,
    exclusive: bool,
    gain: bool,
}

impl Provider for Output {
    fn exclusive(&self) -> bool {
        self.exclusive
    }
    fn gain(&self) -> bool {
        self.gain
    }
    fn permission(&self) -> Option<String> {
        Some("ndi.manage".into())
    }
    fn apply<'a>(&'a self, end: &'a End, wanted: &'a [Wanted]) -> Fut<'a, String> {
        Box::pin(async move {
            self.told.lock().unwrap().push(format!("{}<-{}", end.key, wanted.iter().map(|w| w.ends[0].key.clone()).collect::<Vec<_>>().join("+")));
            String::new()
        })
    }
    fn read<'a>(&'a self, end: &'a End) -> Fut<'a, Option<Vec<ReadItem>>> {
        Box::pin(async move {
            let last = self.told.lock().unwrap().last().cloned()?;
            let (to, from) = last.split_once("<-")?;
            (to == end.key).then(|| from.split('+').filter(|s| !s.is_empty()).map(|k| ReadItem { provider: Some("ndi".into()), key: k.to_owned(), gain_db: None }).collect())
        })
    }
}

#[tokio::test]
async fn a_route_is_what_was_asked_and_the_gear_has_the_last_word() {
    let w = world().await;
    let screen = Arc::new(Output { told: Mutex::new(Vec::new()), exclusive: true, gain: false });
    w.routing.set_external(HashMap::from([("ndi".to_owned(), screen.clone() as Arc<dyn Provider>)]));
    w.channels.sync("ndi", &[report("cam1", "video", "source", "Camera 1", 1), report("cam2", "video", "source", "Camera 2", 2), report("tv", "video", "destination", "Lobby TV", 1)], None, "").await.unwrap();
    // Python's route order: a source can't feed itself, a kind must match, an unknown channel is refused
    assert!(matches!(w.routing.set(&json!("channel:relay:3"), &json!("channel:relay:3"), &Value::Null, true).await, Err(Error::Refused("route_same"))));
    assert!(matches!(w.routing.set(&json!("channel:relay:3"), &json!("channel:relay:99"), &Value::Null, true).await, Err(Error::Refused("no_channel"))));
    assert!(matches!(w.routing.set(&json!("channel:relay:1"), &json!("channel:relay:2"), &Value::Null, true).await, Err(Error::Refused("no_destination"))));
    let got = w.routing.set(&json!("channel:relay:3"), &json!("channel:relay:1"), &Value::Null, true).await.unwrap();
    assert_eq!(got["status"]["state"], "same");
    assert_eq!(got["problems"], json!([]));
    // a video output takes one source: setting another replaces
    let got = w.routing.set(&json!("channel:relay:3"), &json!("channel:relay:2"), &Value::Null, true).await.unwrap();
    assert_eq!(got["status"]["sources"][0]["name"], "Camera 2");
    assert_eq!(got["status"]["sources"].as_array().unwrap().len(), 1);
    assert_eq!(*screen.told.lock().unwrap().last().unwrap(), "tv<-cam2");
    // the gear changed under us: a difference is shown, never overwritten
    screen.told.lock().unwrap().push("tv<-cam1".into());
    let view = w.routing.status(&json!("channel:relay:3")).await.unwrap().unwrap();
    assert_eq!(view["state"], "differs");
    assert_eq!(view["wanted"][0]["name"], "Camera 2");
    assert_eq!(view["actual"][0]["name"], "Camera 1");
    // who may route it: network.manage, or the permission the destination's module names
    assert_eq!(w.routing.permissions(&json!("channel:relay:3")).await.unwrap(), ["network.manage", "ndi.manage"]);
    assert!(w.routing.may_route(&json!("channel:relay:3"), &|p| p == "ndi.manage").await.unwrap());
    assert!(!w.routing.may_route(&json!("channel:relay:3"), &|p| p == "audio.manage").await.unwrap());
    // "where does this go?" follows what the gear shows: camera 1 now, not the camera that was asked for
    assert_eq!(w.routing.feeding(&json!("channel:relay:1")).await.unwrap().len(), 1);
    assert!(w.routing.feeding(&json!("channel:relay:2")).await.unwrap().is_empty());
    // clearing
    let cleared = w.routing.clear(&json!("channel:relay:3"), &Value::Null, true).await.unwrap();
    assert_eq!(cleared["status"]["state"], "none", "nothing asked and the gear takes nothing");
}

#[tokio::test]
async fn a_preset_takes_a_set_of_routes_in_one_go() {
    let w = world().await;
    let screen = Arc::new(Output { told: Mutex::new(Vec::new()), exclusive: true, gain: false });
    w.routing.set_external(HashMap::from([("ndi".to_owned(), screen.clone() as Arc<dyn Provider>)]));
    w.channels.sync("ndi", &[report("cam1", "video", "source", "Camera 1", 1), report("cam2", "video", "source", "Camera 2", 2), report("tv", "video", "destination", "Lobby TV", 1)], None, "").await.unwrap();
    w.routing.set(&json!("channel:relay:3"), &json!("channel:relay:1"), &Value::Null, true).await.unwrap();
    let saved = w.routing.save_salvo("  Opening  ", &[json!("channel:relay:3")], "ndi", None).await.unwrap();
    assert_eq!(saved["name"], "Opening");
    assert_eq!(saved["routes"][0]["source_name"], "Camera 1");
    assert!(matches!(w.routing.save_salvo("opening", &[json!("channel:relay:3")], "ndi", None).await, Err(Error::Refused("name_taken"))));
    assert!(matches!(w.routing.save_salvo("   ", &[], "", None).await, Err(Error::Refused("salvo_name"))));
    w.routing.set(&json!("channel:relay:3"), &json!("channel:relay:2"), &Value::Null, true).await.unwrap();
    let problems = w.routing.take_salvo(saved["id"].as_i64().unwrap()).await.unwrap();
    assert!(problems.is_empty());
    assert_eq!(*screen.told.lock().unwrap().last().unwrap(), "tv<-cam1");
    assert_eq!(w.routing.salvos(Some("other")).await.unwrap().len(), 0);
    w.routing.delete_salvo(saved["id"].as_i64().unwrap()).await.unwrap();
    assert!(matches!(w.routing.take_salvo(saved["id"].as_i64().unwrap()).await, Err(Error::Refused("no_salvo"))));
}

#[tokio::test]
async fn with_no_module_to_apply_a_route_is_kept_and_said_so() {
    let w = world().await;
    w.channels.sync("x", &[report("s", "audio", "source", "Src", 1), report("d", "audio", "destination", "Dst", 1)], None, "").await.unwrap();
    let got = w.routing.set(&json!("channel:relay:2"), &json!("channel:relay:1"), &json!(-6), true).await.unwrap();
    assert_eq!(got["problems"][0], relay_audio::routing::NO_PROVIDER);
    assert_eq!(got["status"]["state"], "wanted");
    assert_eq!(got["status"]["sources"][0]["gain_db"], -6.0, "while no module can say, a gain is kept");
}

// ----------------------------------------------------------------------------------------------------------------- bus
fn setup_payload() -> Value {
    json!({
        "lanes": [{"lane": 1, "name": "Mic", "source": "portaudio", "device": "Fake Mic", "channel": "mix"}],
        "outputs": [{"id": "main", "name": "Main", "output": "portaudio", "device": "Fake Speaker", "channels": 2}]
    })
}

#[tokio::test]
async fn the_matrix_comes_from_routes_and_a_change_shows_at_once() {
    let w = world().await;
    let saved = w.bus.save(&json!({"lanes": setup_payload()["lanes"], "outputs": setup_payload()["outputs"], "matrix": [{"in": 1, "out": "main", "ch": 1, "gain_db": -3}, {"in": 1, "out": "main", "ch": 2}]})).await.unwrap();
    assert_eq!(saved["config"]["matrix"].as_array().unwrap().len(), 2);
    assert_eq!(saved["config"]["matrix"][0], json!({"in": 1, "out": "main", "ch": 1, "gain_db": -3.0}));
    assert!(w.store.audio_bus()["matrix"].as_array().unwrap().is_empty(), "the crosspoints are routes, not config");
    // the page's own editor changes a route: the cache is invalidated by the write
    let refs = w.bus.report_channels(&w.bus.config()).await.unwrap();
    let ch2 = refs["out:main:2"].clone();
    w.routing.clear(&json!(ch2), &Value::Null, true).await.unwrap();
    let page = w.bus.page().await;
    assert_eq!(page["config"]["matrix"].as_array().unwrap().len(), 1, "a crosspoint taken off shows on the next read");
    assert_eq!(w.bus.status()["matrix"].as_array().unwrap().len(), 1, "and the engine runs what the routes say");
    assert_eq!(w.bus.matrix_from_routes().await.len(), 1, "a route written through routing is seen by the cache at once");
    // the routing view reads the bus for what it really does
    let view = w.routing.status(&json!(refs["out:main:1"])).await.unwrap().unwrap();
    assert_eq!(view["state"], "same");
    assert_eq!(view["sources"][0]["name"], "Mic");
    assert_eq!(view["gain"], true);
    assert_eq!(view["exclusive"], false);
    w.bus.close();
}

#[tokio::test]
async fn the_bus_runs_a_lane_into_an_output_and_meters_it() {
    let w = world().await;
    w.bus.save(&json!({"lanes": setup_payload()["lanes"], "outputs": setup_payload()["outputs"], "matrix": [{"in": 1, "out": "main", "ch": 1}]})).await.unwrap();
    let bus = w.bus.clone();
    wait_for("the lane to carry sound", || {
        let bus = bus.clone();
        async move { bus.status()["lanes"][0]["state"] == "open" && bus.status()["lanes"][0]["peak_dbfs"].as_f64().unwrap_or(-120.0) > -60.0 }
    })
    .await;
    let st = w.bus.status();
    assert_eq!(st["running"], true);
    assert_eq!(st["lanes"].as_array().unwrap().len(), 32);
    assert_eq!(st["lanes"][1]["state"], "unpatched");
    assert_eq!(st["outputs"][0]["state"], "open");
    assert_eq!(st["lane_rate"], 48000);
    w.bus.close();
}

#[tokio::test]
async fn a_destination_plays_where_it_is_routed() {
    let w = world().await;
    let (cfg, _) = first_setup(&json!({}), &Pick::Device("Fake Mic".into()), &Pick::Device("Fake Speaker".into()), 2);
    let done = w.bus.setup(&json!("Fake Mic"), Some(&json!("Fake Speaker")), |_| Some(2)).await.unwrap();
    assert_eq!(done["setup"], json!({"input": false, "output": false}));
    assert_eq!(done["config"]["outputs"][0]["id"], cfg["outputs"][0]["id"]);
    // first setup patches the alert tones and the test tone into every channel of the first output
    assert_eq!(w.bus.play_to("test", &[0.5; 480], 48000), 2);
    assert_eq!(w.bus.play_to("alerts", &[0.5; 480], 48000), 2);
    assert_eq!(w.bus.play_to("speech:es", &[0.5; 480], 48000), 0, "not patched anywhere");
    let names: Vec<String> = w.bus.destinations().into_iter().map(|d| d.1).collect();
    assert_eq!(names, ["Alert tones", "Test tone", "Spoken translation: Es", "Spoken translation: Fr"]);
    w.bus.close();
}

#[tokio::test]
async fn a_full_bus_answers_the_admin_page_fast() {
    // The load tools/stress.py puts on it: 32 lanes, three 8-channel outputs, 768 crosspoints (#648).
    let w = world().await;
    let lanes: Vec<Value> = (1..=32).map(|n| json!({"lane": n, "name": format!("L{n}"), "source": "ndi", "device": format!("d{n}"), "channel": 0})).collect();
    let outputs: Vec<Value> = (1..=3).map(|n| json!({"id": format!("o{n}"), "output": "aes67", "device": null, "channels": 8})).collect();
    let cells: Vec<Value> = (1..=3).flat_map(|o| (1..=8).flat_map(move |ch| (1..=32).map(move |l| json!({"in": l, "out": format!("o{o}"), "ch": ch, "gain_db": -6})))).collect();
    let started = Instant::now();
    let saved = w.bus.save(&json!({"lanes": lanes, "outputs": outputs, "matrix": cells})).await.unwrap();
    assert_eq!(saved["config"]["matrix"].as_array().unwrap().len(), 768);
    eprintln!("saving 768 crosspoints took {:?}", started.elapsed());
    let mut worst = Duration::ZERO;
    for _ in 0..20 {
        let t = Instant::now();
        let page = w.bus.page().await;
        worst = worst.max(t.elapsed());
        assert_eq!(page["config"]["matrix"].as_array().unwrap().len(), 768);
    }
    eprintln!("the slowest of 20 page reads: {worst:?}");
    assert!(worst < Duration::from_millis(50), "the page answer must stay under 50 ms, took {worst:?}");
    w.bus.close();
}

#[tokio::test]
async fn a_network_source_python_drives_shows_its_state_in_the_status() {
    let w = world().await;
    w.bus.save(&json!({"lanes": [{"lane": 4, "name": "NDI", "source": "ndi", "device": "Cam", "channel": 0}], "outputs": []})).await.unwrap();
    assert_eq!(w.bus.status()["lanes"][3]["patched"], true);
    w.bus.set_external(json!({"inputs": {"ndi:cam": {"state": "waiting", "error": "That audio source is not available.", "label": "Cam", "rate": null, "channels": null, "dropped": 0}}}));
    assert_eq!(w.bus.status()["lanes"][3]["state"], "waiting");
    assert_eq!(w.bus.status()["lanes"][3]["error"], "That audio source is not available.");
    assert!(w.bus.push("ndi:cam", &[0.1; 960], 2, 48000), "a block of a network source reaches the engine");
    w.bus.close();
}

// -------------------------------------------------------------------------------------------------------------- meters
#[test]
fn meters_are_cleaned_and_capped() {
    let got = metering::normalize(&json!({"meters": [
        {"id": "M1", "name": " Front  of house ", "lane": 99, "kind": "bogus", "calibration_db": 500, "warn_db": 100, "limit_db": 50},
        {"id": "m1"}, {"id": "bad id!"}, "junk"
    ]}));
    let m = got["meters"].as_array().unwrap();
    assert_eq!(m.len(), 4);
    assert_eq!(m[0]["id"], "m1");
    assert_eq!(m[0]["name"], "Front of house");
    assert_eq!(m[0]["lane"], 32);
    assert_eq!(m[0]["kind"], "room");
    assert_eq!(m[0]["calibration_db"], 160.0);
    assert_eq!(m[0]["limit_db"], 100.0, "the warning level never sits above the limit");
    let ids: Vec<&str> = m.iter().map(|x| x["id"].as_str().unwrap()).collect();
    assert_eq!(ids.len(), ids.iter().collect::<std::collections::HashSet<_>>().len(), "ids are unique");
    let many = metering::normalize(&json!({"meters": (0..40).map(|i| json!({"id": format!("x{i}")})).collect::<Vec<_>>()}));
    assert_eq!(many["meters"].as_array().unwrap().len(), 32, "one meter per lane at most");
}

#[tokio::test]
async fn a_meter_reads_its_lane_and_calibrates_against_a_reference() {
    let w = world().await;
    w.bus.save(&json!({"lanes": setup_payload()["lanes"], "outputs": []})).await.unwrap();
    let meters = Metering::new(w.bus.clone(), w.store.clone());
    let saved = meters.save(&json!({"meters": [{"id": "m1", "name": "Room", "lane": 1}]})).await;
    assert_eq!(saved["config"]["meters"][0]["calibrated"], false);
    let m = meters.clone();
    wait_for("the meter to hear the lane", || {
        let m = m.clone();
        async move { m.snapshot("m1").is_some_and(|s| s["state"] == "ok" && s["a_slow"].as_f64().unwrap_or(0.0) > 40.0) }
    })
    .await;
    let reading = meters.snapshot("m1").unwrap();
    assert_eq!(reading["bands"].as_array().unwrap().len(), 31);
    assert!(reading["a_slow"].as_f64().unwrap() > 0.0);
    assert!(meters.public().is_some_and(|p| p["sources"]["m1"]["label"] == "Room"));
    assert_eq!(meters.calibrate("zz", &json!(94)).unwrap_err(), "off");
    assert_eq!(meters.calibrate("m1", &json!("loud")).unwrap_err(), "reference");
    let cal = meters.calibrate("m1", &json!(94)).expect("a signal is arriving");
    let done = meters.save_calibration("m1", cal);
    assert_eq!(done["config"]["meters"][0]["calibrated"], true);
    // a hand-set calibration counts as calibrated, unless the post says otherwise
    let hand = meters.save(&json!({"meters": [{"id": "m1", "name": "Room", "lane": 1, "calibration_db": 100.0}]})).await;
    assert_eq!(hand["config"]["meters"][0]["calibrated"], true);
    meters.stop();
    assert_eq!(meters.snapshot("m1").unwrap()["state"], "off");
    w.bus.close();
}

#[tokio::test]
async fn other_tools_readings_go_stale_and_junk_is_dropped() {
    let w = world().await;
    let meters = Metering::new(w.bus.clone(), w.store.clone());
    meters.register_source("osm", "Open Sound Meter").await;
    assert_eq!(meters.public().unwrap()["sources"]["osm"]["state"], "no_input");
    meters.publish("osm", &json!({"a_fast": 71.234, "a_slow": "nope", "bands": [1, 2, 3], "warn_db": 80}));
    let p = meters.public().unwrap();
    assert_eq!(p["sources"]["osm"]["state"], "ok");
    assert_eq!(p["sources"]["osm"]["a_fast"], 71.2);
    assert!(p["sources"]["osm"].get("a_slow").is_none(), "a junk level is dropped");
    assert!(p["sources"]["osm"].get("bands").is_none(), "bands that are not 31 numbers are dropped");
    assert_eq!(p["sources"]["osm"]["warn_db"], 80.0);
    assert_eq!(meters.choices(), [("osm".to_owned(), "Open Sound Meter".to_owned())]);
    meters.unregister_source("osm").await;
    assert!(meters.public().is_none());
    // the source is a channel you can meter
    assert!(w.channels.listing("audio").await.unwrap().iter().any(|c| c.shown == "Open Sound Meter"));
    w.bus.close();
}
