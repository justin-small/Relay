//! ProPresenter drives Services LIVE (#138 stage 6; `pccontrol.drive`): the same Next / Previous presses a panel uses, one at a time, and NEVER taking
//! control. Planning Center is a fake that behaves as a real account does (Next and Previous are refused, 403, without control; `toggle_control` takes it).

use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex};

use relay_core::future::BoxFuture;
use relay_core::{Host, Module, SettingsStore, Shutdown};
use relay_module_planning_center::client::{Kind, Op, PcError, Rate, Services};
use relay_module_planning_center::follower::{MakeClient, Track};
use relay_module_planning_center::PlanningCenter;
use serde_json::{json, Value};

const ME: &str = "9116316";
const OTHER: &str = "777";
const ORDER: [&str; 6] = ["1", "2", "3", "4", "5", "6"];

#[derive(Default)]
struct Live {
    controller: Option<String>,
    cur: Option<usize>,
    skip: HashSet<String>,
    fail: Option<PcError>,
    /// After this many Next or Previous presses control passes to someone else.
    lose_after: Option<usize>,
    unknown_item: bool,
    posts: Vec<String>,
}

struct World(Mutex<Live>);

impl World {
    fn doc(l: &Live) -> Value {
        let controller = l.controller.as_ref().map(|c| json!({"id": c, "name": if c == ME { "Relay Owner" } else { "Dana Ortiz" }}));
        let current = if l.unknown_item {
            Some(json!({"item": "zzz", "plan_time": "pt", "live_start_at": 1.0, "live_end_at": null, "id": "tz", "exclude": false}))
        } else {
            l.cur.map(|i| json!({"item": ORDER[i], "plan_time": "pt", "live_start_at": 1.0, "live_end_at": null, "id": format!("t{}", ORDER[i]), "exclude": false}))
        };
        json!({"id": "700", "controller": controller, "current": current, "next": null})
    }
}

impl Services for World {
    fn call(&self, op: Op) -> BoxFuture<'_, Result<Value, PcError>> {
        Box::pin(async move {
            let mut l = self.0.lock().unwrap();
            if let Some(e) = l.fail.clone() {
                return Err(e);
            }
            match op {
                Op::Me => Ok(json!(ME)),
                Op::Live { .. } => Ok(Self::doc(&l)),
                Op::LiveAction { action, .. } => {
                    l.posts.push(action.to_owned());
                    if action == "toggle_control" {
                        l.controller = if l.controller.as_deref() == Some(ME) { None } else { Some(ME.to_owned()) };
                    } else if l.controller.as_deref() != Some(ME) {
                        return Err(PcError::new(Kind::Auth, "no").with_status(403));
                    } else if action == "go_to_next_item" {
                        let mut i = l.cur.map_or(0, |c| c + 1);
                        while i < ORDER.len() - 1 && l.skip.contains(ORDER[i]) {
                            i += 1;
                        }
                        l.cur = Some(i.min(ORDER.len() - 1));
                    } else {
                        let mut i = l.cur.unwrap_or(0).saturating_sub(1);
                        while i > 0 && l.skip.contains(ORDER[i]) {
                            i -= 1;
                        }
                        l.cur = Some(i);
                    }
                    let presses = l.posts.iter().filter(|p| *p != "toggle_control").count();
                    if l.lose_after.is_some_and(|n| presses >= n) {
                        l.controller = Some(OTHER.to_owned());
                    }
                    Ok(Self::doc(&l))
                }
                _ => Ok(Value::Null),
            }
        })
    }

    fn rate(&self) -> Rate {
        Rate::default()
    }

    fn download<'a>(&'a self, _url: &'a str, _limit: u64) -> BoxFuture<'a, Result<relay_module_planning_center::client::Download, PcError>> {
        Box::pin(async { Err(PcError::new(Kind::Http, "no files here")) })
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

/// A module following one plan of eight items, with the fake account behind it.
async fn rig(items: usize) -> (PlanningCenter, Arc<World>, Vec<String>) {
    let store = Arc::new(Store(Mutex::new(HashMap::new())));
    store.replace("planning_center", &relay_module_planning_center::settings::clean_stored(&json!({"app_id": "id", "secret": "s", "service_types": ["7"]}))).unwrap();
    let host = Host::new(Shutdown::new()).with_store(store);
    let world = Arc::new(World(Mutex::new(Live::default())));
    let w = Arc::clone(&world);
    let maker: MakeClient = Arc::new(move |_| Some(Arc::clone(&w) as Arc<dyn Services>));
    let module = PlanningCenter::with_client(maker);
    module.attach(&host);
    let follower = Arc::clone(&module.ctx().unwrap().follower);
    follower.tick(1.0).await; // installs the client for the saved token
    let mut track = Track::new("7");
    track.plan = Some(json!({"id": "700", "title": "Sunday"}));
    follower.put_track(track);
    let order: Vec<String> = (1..=items).map(|n| n.to_string()).collect();
    (module, world, order)
}

async fn drive(m: &PlanningCenter, order: &[String], targets: &[&str], back: bool) -> relay_core::caps::DriveResult {
    let targets: Vec<String> = targets.iter().map(|t| (*t).to_owned()).collect();
    m.ctx().unwrap().control.drive("7", order, &targets, back).await
}

fn set(w: &World, f: impl FnOnce(&mut Live)) {
    f(&mut w.0.lock().unwrap());
}

fn posts(w: &World) -> Vec<String> {
    w.0.lock().unwrap().posts.clone()
}

#[tokio::test]
async fn it_moves_forward_with_the_same_presses_a_panel_uses_and_stops_when_it_arrives() {
    let (m, w, order) = rig(6).await;
    set(&w, |l| (l.controller, l.cur) = (Some(ME.into()), Some(0)));
    let r = drive(&m, &order, &["3"], false).await;
    assert_eq!((r.result.as_str(), r.steps), ("advanced", 2), "two Next presses took it from the first item to the third");
    assert_eq!(posts(&w), ["go_to_next_item", "go_to_next_item"]);
    assert!(!posts(&w).iter().any(|p| p == "toggle_control"), "no toggle_control, ever");
    let r = drive(&m, &order, &["3"], false).await;
    assert_eq!((r.result.as_str(), r.steps), ("already", 0), "already there: no press");
    assert_eq!(posts(&w).len(), 2);
    let recent = m.ctx().unwrap().control.recent();
    assert_eq!((recent[0]["panel_name"].clone(), recent[0]["action"].clone(), recent[1]["result"].clone()), (json!("ProPresenter"), json!("drive"), json!("advanced (2 steps)")), "it is in the panel actions list as ProPresenter");
}

#[tokio::test]
async fn it_never_takes_control() {
    let (m, w, order) = rig(6).await;
    set(&w, |l| l.cur = Some(0));
    let r = drive(&m, &order, &["3"], false).await;
    assert_eq!((r.result.as_str(), posts(&w).len()), ("nobody", 0), "nobody in control: no press");
    set(&w, |l| l.controller = Some(OTHER.into()));
    let r = drive(&m, &order, &["3"], false).await;
    assert_eq!((r.result.as_str(), r.who.as_str(), posts(&w).len()), ("someone_else", "Dana Ortiz", 0), "someone else in control: no press, and it names who");
    assert_eq!(w.0.lock().unwrap().controller.as_deref(), Some(OTHER), "Relay still does not hold control");
    set(&w, |l| (l.controller, l.lose_after) = (Some(ME.into()), Some(1)));
    let r = drive(&m, &order, &["5"], false).await;
    assert_eq!((r.result.as_str(), r.steps), ("lost_control", 1), "control lost while moving: it stops");
}

#[tokio::test]
async fn going_back_needs_allow_back_and_stops_exactly_at_the_item() {
    let (m, w, order) = rig(6).await;
    set(&w, |l| (l.controller, l.cur) = (Some(ME.into()), Some(4)));
    let r = drive(&m, &order, &["2"], false).await;
    assert_eq!((r.result.as_str(), posts(&w).len()), ("behind", 0), "behind the current item and going back is off: no press");
    let r = drive(&m, &order, &["2"], true).await;
    assert_eq!((r.result.as_str(), r.steps), ("went_back", 3), "allowed: it stops exactly at the matched item");
    assert_eq!(posts(&w), ["go_to_previous_item"; 3]);
    assert_eq!(drive(&m, &order, &["99"], false).await.result, "not_in_plan", "an item the plan does not have");
}

#[tokio::test]
async fn skipped_items_and_a_runaway_are_bounded() {
    let (m, w, order) = rig(6).await;
    set(&w, |l| {
        l.controller = Some(ME.into());
        l.cur = Some(0);
        l.skip = ["3".to_owned()].into();
    });
    let r = drive(&m, &order, &["3"], false).await;
    assert_eq!(r.result, "overshot", "an excluded item is stepped over: it stops where it landed");
    assert!(r.steps <= 3, "no runaway");

    let (m, w, order) = rig(40).await;
    set(&w, |l| (l.controller, l.cur) = (Some(ME.into()), None));
    let r = drive(&m, &order, &["40"], false).await;
    assert_eq!((r.result.as_str(), r.steps), ("too_far", 12), "one move never presses more than twelve times");
    assert_eq!(posts(&w).len(), 12);
}

#[tokio::test]
async fn an_unknown_current_item_and_planning_center_failing_say_a_fixed_code() {
    let (m, w, order) = rig(6).await;
    set(&w, |l| (l.controller, l.unknown_item) = (Some(ME.into()), true));
    assert_eq!(drive(&m, &order, &["3"], false).await.result, "unknown_item", "Planning Center's current item is not one Relay knows");
    set(&w, |l| l.unknown_item = false);
    set(&w, |l| l.fail = Some(PcError::new(Kind::Network, "cannot reach host with S3cr3t-TOKEN")));
    let r = drive(&m, &order, &["3"], false).await;
    assert_eq!((r.result.as_str(), r.steps), ("unreachable", 0), "the network failing is a fixed code");
    set(&w, |l| l.fail = Some(PcError::new(Kind::Http, "bad").with_status(500)));
    assert_eq!(drive(&m, &order, &["3"], false).await.result, "refused", "a refusal is a fixed code");
    assert!(!format!("{:?}", m.ctx().unwrap().control.recent()).contains("S3cr3t"), "no secret in the actions list");
}
