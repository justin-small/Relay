//! What Planning Center announces and hears (#666): `plan.changed` when the followed plans change, the offered service feeds when one is
//! followed or dropped (the panels' pickers), and a rename in the directory rewriting the names saved in the rundown.

use std::collections::HashMap;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use relay_core::caps::{Described, DirItem, Directory, DirectoryCap, DIRECTORY, DIRECTORY_CHANGED, OFFERED_CHANGED, PLAN_CHANGED};
use relay_core::{Host, Module, SettingsStore, Shutdown};
use relay_module_planning_center::client::Services;
use relay_module_planning_center::follower::{MakeClient, Track};
use relay_module_planning_center::PlanningCenter;
use serde_json::{json, Value};

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

fn rig(saved: &Value) -> (PlanningCenter, Host, Arc<Store>) {
    let store = Arc::new(Store(Mutex::new(HashMap::new())));
    store.replace("planning_center", &relay_module_planning_center::settings::clean_stored(saved)).unwrap();
    let host = Host::new(Shutdown::new()).with_store(store.clone());
    // No client: nothing is fetched, the follower is driven by hand.
    let maker: MakeClient = Arc::new(|_| None::<Arc<dyn Services>>);
    let module = PlanningCenter::with_client(maker);
    module.attach(&host);
    (module, host, store)
}

fn counter(host: &Host, topic: &str) -> Arc<AtomicUsize> {
    let n = Arc::new(AtomicUsize::new(0));
    let seen = Arc::clone(&n);
    host.listen("test", topic, Arc::new(move |_| {
        seen.fetch_add(1, Ordering::SeqCst);
    }));
    n
}

#[tokio::test]
async fn a_changed_plan_is_announced_once_and_a_followed_type_reaches_the_pickers() {
    let (module, host, _store) = rig(&json!({"app_id": "id", "secret": "s", "service_types": ["77"]}));
    let (plans, offered) = (counter(&host, PLAN_CHANGED), counter(&host, OFFERED_CHANGED));
    let follower = Arc::clone(&module.ctx().unwrap().follower);
    follower.put_track(Track::new("77"));
    follower.put_name("77", "Sunday");
    follower.apply_live("77", json!({"current": {}}), Some(1.0));
    assert_eq!(plans.load(Ordering::SeqCst), 1, "the followed plans changed: plan.changed was announced");
    assert_eq!(offered.load(Ordering::SeqCst), 1, "a followed service type now shows on the panels");
    follower.apply_live("77", json!({"current": {}}), Some(2.0));
    assert_eq!((plans.load(Ordering::SeqCst), offered.load(Ordering::SeqCst)), (1, 1), "nothing changed, nothing announced");
}

/// A directory that lists one position under whatever name it is given.
struct Names(Mutex<String>);

impl Directory for Names {
    fn positions(&self) -> Vec<DirItem> {
        Vec::new()
    }
    fn describe(&self, reference: &str, saved: &str) -> Described {
        if reference == "position:pco:1/2" {
            return Described { name: self.0.lock().unwrap().clone(), missing: false, unmatched: false };
        }
        Described { name: saved.to_owned(), missing: false, unmatched: reference.is_empty() && !saved.is_empty() }
    }
    fn knows(&self, reference: &str) -> bool {
        reference == "position:pco:1/2"
    }
}

#[tokio::test]
async fn a_rename_in_planning_center_rewrites_the_saved_names_while_the_module_is_on() {
    let saved = json!({"rundown": {"leader_position_ref": "position:pco:1/2", "leader_position": "Worship Leader"}});
    let (module, host, store) = rig(&saved);
    let names = Arc::new(Names(Mutex::new("Worship Leader".to_owned())));
    host.provide("core", DIRECTORY, Arc::new(DirectoryCap(names.clone())));
    let leader = || store.read("planning_center").unwrap()["rundown"]["leader_position"].as_str().unwrap().to_owned();
    module.start(host.clone(), json!({})).await.unwrap();

    *names.0.lock().unwrap() = "Vocal Lead".to_owned();
    host.announce(DIRECTORY_CHANGED, "other");
    tokio::time::sleep(Duration::from_millis(100)).await; // allow-sleep: shows another provider's rename changes nothing
    assert_eq!(leader(), "Worship Leader", "a rename in another provider is not ours");

    host.announce(DIRECTORY_CHANGED, "pco");
    for _ in 0..50 {
        if leader() == "Vocal Lead" {
            break;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    assert_eq!(leader(), "Vocal Lead", "the saved name follows the rename");
    assert_eq!(store.read("planning_center").unwrap()["rundown"]["leader_position_ref"], "position:pco:1/2", "and the ref stays");

    module.stop().await;
    *names.0.lock().unwrap() = "Soloist".to_owned();
    host.announce(DIRECTORY_CHANGED, "pco");
    tokio::time::sleep(Duration::from_millis(100)).await; // allow-sleep: shows a module that is off hears nothing
    assert_eq!(leader(), "Vocal Lead", "switched off, it hears nothing");
}
