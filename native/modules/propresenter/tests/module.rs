//! What the module offers the rest of Relay while it is on and takes back when it is off (#667): the feeds a panel can pick, what a board follows, the
//! Service feed's extra, the tag layer; that a panel's own timer thresholds and an alert reach it as events; and its health.

mod fake;

use std::sync::{Arc, Mutex};

use fake::{rig, until};
use relay_core::caps::{BoardFollow, FeedExtra, PanelDirectory, PanelsCap, ProOffers, SignageLayer, ALERT_STAGE, BOARD_FOLLOW, FEED_EXTRA, PANELS, PANEL_TIMERS_CHANGED, PRO_OFFERS, SIGNAGE_LAYER};
use relay_core::{HealthState, Module};
use serde_json::{json, Value};

#[tokio::test]
async fn what_a_panel_may_pick_follows_the_settings_and_goes_with_the_module() {
    let r = rig().await;
    r.configure(json!({}));
    assert!(r.host.using::<ProOffers>(PRO_OFFERS).is_none(), "while off it offers nothing");
    r.start().await;
    let offers = r.host.using::<ProOffers>(PRO_OFFERS).expect("offered while on");
    assert_eq!(((offers.lyrics)(), (offers.timers)()), (true, true), "set up: the lyrics (panels are on by default) and the timers");
    r.amend(json!({"lyrics": {"viewer": false, "screen": true, "overlay": false, "panels": false}}));
    assert_eq!(((offers.lyrics)(), (offers.timers)()), (false, true), "the operator can keep the lyrics off panels");
    r.amend(json!({"enabled": false}));
    assert_eq!(((offers.lyrics)(), (offers.timers)()), (false, false), "not set up: neither");
    r.module.stop().await;
    assert!(r.host.using::<ProOffers>(PRO_OFFERS).is_none(), "and nothing once it stops");
    assert!(r.host.using::<BoardFollow>(&format!("{BOARD_FOLLOW}propresenter")).is_none());
    assert!(r.host.using::<SignageLayer>(&format!("{SIGNAGE_LAYER}tag")).is_none());
    assert!(r.host.using::<FeedExtra>(FEED_EXTRA).is_none());
}

#[tokio::test]
async fn a_board_follows_the_presentation_its_group_and_label() {
    let r = rig().await;
    r.fake.lock().default_song();
    r.configure(json!({}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up() && f.presentation().is_some() && f.current().text == "line one\nline two").await);
    let follow = r.host.using::<BoardFollow>(&format!("{BOARD_FOLLOW}propresenter")).expect("offered while on");
    let seen = (follow.0)().expect("it can say");
    assert_eq!((seen["name"].clone(), seen["connected"].clone()), (json!("ProPresenter"), json!(true)));
    assert_eq!((seen["now"]["presentation"].clone(), seen["now"]["group"].clone()), (json!("Great Song"), json!("Verse 1")));
    assert_eq!(seen["suggest"]["group"], json!(["Verse 1", "Chorus"]), "this presentation's groups to pick from");
    assert_eq!(seen["suggest"]["presentation"], json!(["Great Song"]), "and the presentations seen since start");
    r.fake.lock().song("Second Song", "Songs", vec![("Verse 1", vec!["two a"])]);
    r.fake.push(&["presentation/current", "presentation/slide_index", "status/slide"]);
    assert!(until(6.0, || f.presentation().is_some_and(|p| p.name == "Second Song") && f.current().text == "two a").await);
    let seen = (follow.0)().unwrap();
    assert_eq!(seen["suggest"]["presentation"], json!(["Second Song", "Great Song"]), "the newest first");
    r.fake.nothing();
    assert!(until(6.0, || f.presentation().is_none()).await);
    assert_eq!((follow.0)().unwrap()["now"]["presentation"], "", "nothing up");
    r.module.stop().await;
}

/// The panel registry as far as the feeds read it: one panel with its own thresholds.
struct Panels {
    timers: Mutex<Option<Value>>,
}

impl PanelDirectory for Panels {
    fn known(&self, _id: &str) -> bool {
        true
    }
    fn control_key(&self, _id: &str) -> Option<String> {
        None
    }
    fn service(&self, _id: &str) -> Value {
        json!({})
    }
    fn display_name(&self, _id: &str) -> String {
        "Booth".into()
    }
    fn timers(&self, _id: &str) -> Option<Value> {
        self.timers.lock().unwrap().clone()
    }
}

#[tokio::test]
async fn a_panels_own_thresholds_reach_its_timers_at_once() {
    let r = rig().await;
    {
        let mut f = r.fake.lock();
        f.default_song();
        f.timer_rows = json!([{"id": {"uuid": "T-A", "name": "Segment", "index": 0}, "time": "00:01:30", "state": "stopped"}]);
    }
    r.configure(json!({}));
    let panels = Arc::new(Panels { timers: Mutex::new(None) });
    r.host.provide("core", PANELS, Arc::new(PanelsCap(panels.clone())));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up() && f.timers().len() == 1).await, "the timer is read");
    let pid = "0123456789abcdef0123456789abcdef";
    let (id, mut rx) = r.ctx().timers.subscribe(pid);
    let frame = |b: &bytes::Bytes| -> Value { serde_json::from_str(std::str::from_utf8(b).unwrap().trim_start_matches("data: ").trim()).unwrap() };
    let first = frame(&rx.try_recv().expect("told at once"));
    assert_eq!((first["amber_s"].clone(), first["red_s"].clone(), first["timers"][0]["level"].clone()), (json!(120), json!(30), json!("amber")), "the global thresholds: 90 s left is under the 120 s amber line");
    *panels.timers.lock().unwrap() = Some(json!({"amber_s": 80, "red_s": 20}));
    r.host.announce(PANEL_TIMERS_CHANGED, "");
    let mut next = None;
    assert!(until(4.0, || {
        next = rx.try_recv().ok();
        next.is_some()
    })
    .await, "the panel is told its new thresholds without waiting for a heartbeat");
    let next = frame(&next.unwrap());
    assert_eq!((next["amber_s"].clone(), next["red_s"].clone(), next["timers"][0]["level"].clone()), (json!(80), json!(20), json!("ok")), "its own: 90 s left is above its 80 s amber line");
    r.ctx().timers.unsubscribe(pid, id);
    r.module.stop().await;
}

#[tokio::test]
async fn health_says_what_to_do_and_an_alert_event_reaches_the_stage_display() {
    let r = rig().await;
    assert_eq!(r.module.health().state, HealthState::Warn, "not set up yet");
    r.fake.lock().default_song();
    r.configure(json!({}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await);
    assert_eq!((r.module.health().state, r.module.health().detail), (HealthState::Ok, "Connected"));
    r.host.announce(ALERT_STAGE, &json!({"id": "a1", "text": "Nursery"}).to_string());
    assert!(until(6.0, || r.fake.lock().stage == "Nursery").await, "an alert on a stage button shows on the stage display");
    r.host.announce(ALERT_STAGE, "not json");
    r.host.announce(ALERT_STAGE, &json!({"id": "a1", "text": null}).to_string());
    assert!(until(6.0, || r.fake.lock().stage.is_empty()).await, "and goes when it ends");
    r.module.stop().await;
}
