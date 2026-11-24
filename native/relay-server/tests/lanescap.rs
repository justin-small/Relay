//! Core's lane bus as a module that drives a network source or sender sees it (#658): `relay-core`'s `LaneBus` over `relay-audio`'s bus on the
//! engine's fake sound card. Blocks for an input the bus does not run are refused, and two drivers' status are merged, not replaced.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;

use relay_audio::{AudioBus, Backing, Channels, Routing};
use relay_auth::{Auth, Cost};
use relay_core::lanes::{input_id, LaneBus};
use relay_server::audio::ConfigStore;
use relay_server::lanescap::CoreLanes;
use serde_json::json;

static N: AtomicUsize = AtomicUsize::new(0);

async fn lanes() -> (CoreLanes, Auth) {
    let dir = std::env::temp_dir().join(format!("relay-lanescap-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("dir");
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.expect("open");
    sqlx::raw_sql("create table gear (id integer primary key)").execute(auth.pool()).await.expect("gear");
    sqlx::raw_sql(include_str!("../../../app/migrations/0014_channels.sql")).execute(auth.pool()).await.expect("channels");
    sqlx::raw_sql(include_str!("../../../app/migrations/0015_routes.sql")).execute(auth.pool()).await.expect("routes");
    let store = Arc::new(ConfigStore(Arc::new(relay_config::Settings::new(&dir.join("config.json")))));
    let channels = Channels::new(auth.pool().clone());
    let routing = Routing::new(channels.clone());
    let bus = AudioBus::new(Backing::fake(), store, channels, routing);
    (CoreLanes::new(bus), auth)
}

#[test]
fn an_input_is_named_source_then_device_in_lower_case() {
    assert_eq!(input_id(" NDI ", " Mic (Desk) "), "ndi:mic (desk)");
    assert_eq!(input_id("ndi", ""), "ndi:");
}

#[tokio::test(flavor = "multi_thread")]
async fn a_bus_that_is_not_running_takes_nothing() {
    let (core, _auth) = lanes().await;
    assert!(!core.running());
    assert!(!core.push("ndi:mic", &[0.0; 8], 2, 48000), "no such input");
    assert!(core.pull("o1", 10).is_none(), "no such output");
    assert_eq!(core.config()["lanes"], json!([]));
}

#[tokio::test(flavor = "multi_thread")]
async fn two_drivers_status_are_merged() {
    let (core, _auth) = lanes().await;
    core.set_status("ndi", json!({"ndi:a": {"state": "open"}}), json!({"o1": {"state": "open"}}));
    core.set_status("other", json!({"other:b": {"state": "waiting"}}), json!({}));
    let ext = core.bus.external();
    assert_eq!(ext["inputs"]["ndi:a"]["state"], "open");
    assert_eq!(ext["inputs"]["other:b"]["state"], "waiting");
    assert_eq!(ext["outputs"]["o1"]["state"], "open");
    core.set_status("ndi", json!({}), json!({}));
    let ext = core.bus.external();
    assert!(ext["inputs"].get("ndi:a").is_none(), "a driver that stops takes its entries away");
    assert_eq!(ext["inputs"]["other:b"]["state"], "waiting", "and leaves the other's");
}
