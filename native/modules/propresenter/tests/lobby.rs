//! What `tests/test_pclobby.py` checks (#138 stage 7): the lobby rotation's timing, pausing and resuming when the operator puts something else up,
//! stopping, view-only refusal; the slide images' proxy and cache; the overlay's timer and media-progress arithmetic; and who may see what.
//! A fake ProPresenter over real sockets whose announcements layer moves when a trigger arrives and which pushes the change; the rotation's clock is the
//! test's, so a minute of rotation takes the time the follower needs to see each change.

mod fake;

use std::sync::{Arc, Mutex};

use axum::http::{Method, StatusCode};
use fake::{lobby_item, lobby_items, rig, until, Rig};
use relay_core::{Module, SettingsStore};
use relay_module_propresenter::lobby::{Lobby, LobbyError, Refusal, GRACE_S, RESUME_S, ROTATE_TYPES};
use relay_module_propresenter::view::{media_row, timer_row};
use relay_module_propresenter::{control::ControlError, settings};
use serde_json::{json, Value};

const B: &str = "/api/admin/propresenter";

struct Bench {
    r: Rig,
    lobby: Arc<Lobby>,
    t: Arc<Mutex<f64>>,
}

fn trig(i: usize) -> String {
    format!("/v1/playlist/PL-LOBBY/{i}/trigger")
}

fn cue(n: usize) -> String {
    format!("/v1/announcement/active/{n}/trigger")
}

/// The paths of the triggers and clears sent so far.
fn triggers(r: &Rig) -> Vec<String> {
    r.fake.lock().requests.iter().filter(|q| q.0 == "GET" && (q.1.ends_with("/trigger") || q.1.contains("/clear/"))).map(|q| q.1.clone()).collect()
}

fn writes(r: &Rig) -> usize {
    triggers(r).len() + r.fake.requests("PUT").len() + r.fake.requests("DELETE").len() + r.fake.requests("POST").len()
}

async fn bench(extra: Value) -> Bench {
    let r = rig().await;
    r.fake.lock().lobby_items = lobby_items();
    let mut lobby = json!({"playlist": "PL-LOBBY", "interval_s": 8, "loop": true});
    for (k, v) in extra.as_object().into_iter().flatten() {
        lobby[k] = v.clone();
    }
    r.configure(json!({"lobby": lobby}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up()).await, "ProPresenter is up");
    assert!(until(6.0, || f.data("status/layers").get("announcements").is_some()).await, "the layers are read");
    let t = Arc::new(Mutex::new(1000.0));
    let clock = Arc::clone(&t);
    let lobby = Lobby::with_clock(f, Arc::clone(&r.ctx().control), Arc::new(move || *clock.lock().unwrap()));
    Bench { r, lobby, t }
}

impl Bench {
    fn set_time(&self, t: f64) {
        *self.t.lock().unwrap() = t;
    }

    /// What the follower reads once a second, waited for here: the layer, and the presentation on it.
    async fn sync(&self) {
        let f = Arc::clone(&self.r.ctx().follower);
        let ok = until(6.0, || {
            let (on, uuid, cue) = self.r.fake.lock().world();
            f.data("status/layers")["announcements"] == json!(on)
                && f.announcement().map(|a| a.uuid).unwrap_or_default() == uuid
                && f.announcement_index() == cue.map(|c| i64::try_from(c).unwrap_or(0))
        })
        .await;
        assert!(ok, "the follower sees what is on the announcements layer");
    }

    async fn settle(&self, seconds: f64, observe: bool) {
        let end = *self.t.lock().unwrap() + seconds;
        while *self.t.lock().unwrap() < end {
            *self.t.lock().unwrap() += 1.0;
            if observe {
                self.sync().await;
            }
            self.lobby.tick(None).await;
        }
    }

    fn put_up(&self, on: bool, uuid: &str, cue: Option<usize>) {
        self.r.fake.lock().set_world(on, uuid, cue);
    }
}

fn failure(e: &LobbyError) -> String {
    match e {
        LobbyError::Refused(r) => r.message().to_owned(),
        LobbyError::Control(ControlError::Pro(p)) => format!("{:?}", p.kind),
        LobbyError::Control(other) => format!("{other:?}"),
    }
}

#[tokio::test]
async fn settings_and_the_save_route_check_the_lobby_and_the_view() {
    let d = settings::default_lobby();
    assert_eq!((d["playlist"].clone(), d["interval_s"].clone(), d["loop"].clone()), (json!(""), json!(8.0), json!(true)), "defaults: no playlist, 8 s, loop");
    assert_eq!(settings::default_view(), json!({"timer": "", "public": false}));
    assert!(d.get("running").is_none(), "a rotation is not a setting");
    assert_eq!(settings::lobby_fields(&json!({"interval_s": 1})).1, "The time per slide is a number of seconds from 3 to 600.", "a bad time is refused with a fixed sentence");
    assert!(settings::lobby_fields(&json!({"interval_s": "8"})).0.is_empty(), "and a string time too");
    assert_eq!(settings::lobby_fields(&json!({"playlist": "../x"})).1, "That is not a ProPresenter playlist.");
    assert_eq!(settings::view_fields(&json!({"timer": "a/b"})).1, "That is not a ProPresenter timer.");
    assert_eq!(Value::Object(settings::lobby_fields(&json!({"loop": "yes"})).0), json!({"loop": false}), "a string for loop is not on");
    assert_eq!(Value::Object(settings::view_fields(&json!({"public": "true"})).0), json!({"public": false}), "public needs a real true");

    let r = rig().await;
    r.configure(json!({}));
    let (st, body, _) = r.send(Method::POST, B, Some(json!({"lobby": {"interval_s": 0}}))).await;
    assert_eq!((st.as_u16(), body["error"].clone()), (400, json!("The time per slide is a number of seconds from 3 to 600.")), "the save route answers a bad time with 400 and the sentence");
    let (st, body, _) = r.send(Method::POST, B, Some(json!({"lobby": {"playlist": "PL-LOBBY", "interval_s": 12, "loop": false}}))).await;
    assert_eq!((st.as_u16(), body["settings"]["lobby"].clone()), (200, json!({"playlist": "PL-LOBBY", "interval_s": 12.0, "loop": false})), "a good save");
    let (st, body, _) = r.send(Method::POST, B, Some(json!({"view": {"timer": "T-1"}}))).await;
    assert_eq!((st.as_u16(), body["settings"]["view"].clone()), (200, json!({"timer": "T-1", "public": false})), "a view save keeps public off");
    assert_eq!(body["lobby"]["running"], false, "the answer carries the lobby state");
}

#[tokio::test]
async fn nothing_of_the_lobby_or_view_is_open_to_anyone_who_has_not_signed_in() {
    let r = rig().await;
    r.configure(json!({}));
    let anon = fake::anonymous();
    for (m, p) in [("GET", "/lobby"), ("POST", "/lobby/start"), ("POST", "/lobby/resume"), ("POST", "/lobby/stop"), ("GET", "/live"), ("GET", "/live/image/current?v=x")] {
        let method = if m == "GET" { Method::GET } else { Method::POST };
        assert_eq!(fake::send_to(&anon, method, &format!("{B}{p}"), if m == "POST" { Some(json!({})) } else { None }).await.0, StatusCode::UNAUTHORIZED, "{m} {p} needs a sign-in");
    }
    for p in ["/stage", "/api/stage/live", "/api/stage/live/image/current?v=x", "/api/stage/live/image/next?v=x"] {
        assert_eq!(r.fetch(p).await.0, StatusCode::NOT_FOUND, "{p} is a 404 while the public switch is off (the default)");
    }
    assert!(r.fake.lock().requests.is_empty(), "and nothing was asked of ProPresenter");
    assert_eq!(r.fetch(&format!("{B}/live/image/other?v=x")).await.0, StatusCode::NOT_FOUND, "an unknown image name is refused even for admin");
}

#[tokio::test]
async fn start_is_refused_with_a_sentence_and_writes_nothing() {
    let b = bench(json!({"playlist": ""})).await;
    let e = b.lobby.start_rotation().await.unwrap_err();
    assert_eq!(failure(&e), "Pick the announcement playlist first.", "no playlist picked is refused with a fixed sentence");
    let r2 = rig().await; // never started: ProPresenter is not connected
    r2.fake.lock().lobby_items = lobby_items();
    r2.configure(json!({"lobby": {"playlist": "PL-LOBBY"}}));
    let lobby = Lobby::new(Arc::clone(&r2.ctx().follower), Arc::clone(&r2.ctx().control));
    assert_eq!(failure(&lobby.start_rotation().await.unwrap_err()), "ProPresenter is not connected.", "ProPresenter not connected is refused");
    assert_eq!((writes(&b.r), writes(&r2)), (0, 0), "nothing was written by either refusal");
    b.r.module.stop().await;
}

#[tokio::test]
async fn it_steps_a_slide_every_interval_cues_first_then_the_next_item_then_round_again() {
    let b = bench(json!({})).await;
    b.set_time(1000.0);
    let state = b.lobby.start_rotation().await.unwrap();
    assert_eq!(triggers(&b.r), [trig(1)], "start puts up the first presentation item (not the header)");
    assert_eq!((state["running"].clone(), state["item"].clone(), state["item_number"].clone(), state["item_count"].clone(), state["cue"].clone()), (json!(true), json!("Welcome"), json!(1), json!(3), json!(1)));
    assert!(ROTATE_TYPES == ["presentation", "media"], "types rotated are presentations and media only");
    b.r.fake.lock().requests.clear();
    b.settle(7.0, true).await;
    assert!(triggers(&b.r).is_empty(), "nothing before the interval has passed");
    b.settle(1.0, true).await;
    assert_eq!(triggers(&b.r), [cue(1)], "at 8 s the next cue of the same presentation");
    b.settle(8.0, true).await;
    assert_eq!(triggers(&b.r).last(), Some(&cue(2)), "at 16 s the third cue");
    b.settle(8.0, true).await;
    assert_eq!(triggers(&b.r).last(), Some(&trig(2)), "at 24 s the next playlist item (the media)");
    b.settle(8.0, true).await;
    assert_eq!(triggers(&b.r).last(), Some(&trig(3)), "at 32 s the one after (Coffee)");
    b.settle(8.0, true).await;
    assert_eq!(triggers(&b.r).last(), Some(&trig(1)), "at 40 s it starts over (loop is on)");
    assert_eq!(triggers(&b.r).len(), 5, "five triggers, none extra");
    b.settle(60.0, true).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["paused"].clone()), (json!(true), json!("")), "a minute later the same rhythm, still running and unpaused");
    b.r.module.stop().await;
}

#[tokio::test]
async fn an_interval_change_applies_and_the_playlist_is_read_again_at_the_wrap() {
    let b = bench(json!({"interval_s": 20})).await;
    b.set_time(5000.0);
    b.lobby.start_rotation().await.unwrap();
    b.r.fake.lock().requests.clear();
    b.settle(19.0, true).await;
    assert!(triggers(&b.r).is_empty(), "20 s interval: nothing at 19 s");
    b.settle(1.0, true).await;
    assert_eq!(triggers(&b.r), [cue(1)], "and the step at 20 s");
    b.r.fake.lock().lobby_items.push(lobby_item(6, "New one", "presentation", "P-X", false));
    b.settle(20.0 * 5.0, true).await; // a cue 2, the media, Coffee, then the wrap re-reads the playlist
    assert!(b.r.fake.requests("GET").iter().filter(|q| q.0 == "/v1/playlist/PL-LOBBY").count() >= 1, "the playlist is read again at the wrap");
    assert!(b.lobby.state()["item_count"].as_i64().unwrap_or(0) >= 4, "an item added to the playlist is picked up at the wrap");
    b.lobby.stop_rotation(false).await.unwrap();
    b.r.module.stop().await;
}

#[tokio::test]
async fn without_a_loop_it_ends_on_the_last_slide_and_stays_there() {
    let b = bench(json!({"loop": false, "interval_s": 3})).await;
    b.set_time(6000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(3.0 * 8.0, true).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["finished"].clone()), (json!(false), json!(true)), "finished, not running");
    let (on, uuid, _) = b.r.fake.lock().world();
    assert_eq!((on, uuid.as_str()), (true, "P-B"), "the last slide was left up");
    let n = triggers(&b.r).len();
    b.settle(30.0, true).await;
    assert_eq!(triggers(&b.r).len(), n, "nothing is written once finished");
    b.r.module.stop().await;
}

#[tokio::test]
async fn the_operator_puts_something_else_up_and_it_pauses_then_carries_on_when_it_is_gone() {
    let b = bench(json!({})).await;
    b.set_time(7000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(2.0, true).await;
    b.put_up(true, "P-X", Some(0)); // a presentation that is not in the playlist
    b.settle(GRACE_S + 3.0, true).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["paused"].clone()), (json!(true), json!("operator")), "paused for the operator");
    b.r.fake.lock().requests.clear();
    b.settle(40.0, true).await;
    assert!(triggers(&b.r).is_empty(), "40 s later it has written nothing");
    b.put_up(false, "", None); // the operator cleared theirs
    b.settle(RESUME_S - 2.0, true).await;
    assert!(triggers(&b.r).is_empty(), "not at once: the layer must stay empty a few seconds");
    b.settle(4.0, true).await;
    assert_eq!(triggers(&b.r), [trig(1)], "then it starts again from the first item");
    assert_eq!(b.lobby.state()["paused"], "", "unpaused");
    b.r.module.stop().await;
}

#[tokio::test]
async fn the_operators_own_slides_of_the_playlist_do_not_pause_it_and_a_return_resumes_without_a_write() {
    let b = bench(json!({})).await;
    b.set_time(8000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(2.0, true).await;
    b.put_up(true, "P-A", Some(2)); // nudged to the last cue of the same presentation
    b.settle(GRACE_S + 1.0, true).await;
    assert_eq!(b.lobby.state()["paused"], "", "not paused");
    b.r.fake.lock().requests.clear();
    b.settle(10.0, true).await;
    assert_eq!(triggers(&b.r), [trig(2)], "it moved on from where the operator left it (the next item after cue 3)");

    // the operator comes back to the playlist after a pause: carried on, no restart
    b.lobby.stop_rotation(false).await.unwrap();
    b.set_time(9000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(2.0, true).await;
    b.put_up(true, "P-X", Some(0));
    b.settle(GRACE_S + 2.0, true).await;
    assert_eq!(b.lobby.state()["paused"], "operator", "paused");
    b.put_up(true, "P-B", Some(0));
    b.r.fake.lock().requests.clear();
    b.settle(2.0, true).await;
    assert_eq!((b.lobby.state()["paused"].clone(), triggers(&b.r)), (json!(""), vec![]), "resumed from where it is, without a write");
    b.r.module.stop().await;
}

#[tokio::test]
async fn a_deliberate_clear_is_not_undone_until_resume() {
    let b = bench(json!({})).await;
    b.set_time(10000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(2.0, true).await;
    b.put_up(false, "", None);
    b.settle(GRACE_S + 2.0, true).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["paused"].clone()), (json!(true), json!("cleared")), "paused because it was cleared");
    b.r.fake.lock().requests.clear();
    b.settle(60.0, true).await;
    assert!(triggers(&b.r).is_empty(), "a deliberate clear is not undone");
    let st = b.lobby.resume().await.unwrap();
    assert_eq!(triggers(&b.r), [trig(1)], "Resume starts again from the top");
    assert_eq!((st["running"].clone(), st["paused"].clone()), (json!(true), json!("")), "and the state is running unpaused");
    b.r.module.stop().await;
}

#[tokio::test]
async fn it_survives_proppresenter_dropping_off_and_coming_back() {
    let b = bench(json!({})).await;
    b.set_time(11000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(2.0, true).await;
    let f = Arc::clone(&b.r.ctx().follower);
    b.r.fake.lock().refuse = true;
    b.r.fake.kill_streams();
    assert!(until(10.0, || !f.is_up()).await, "ProPresenter goes away");
    b.r.fake.lock().requests.clear();
    b.settle(30.0, false).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["waiting"].clone(), writes(&b.r)), (json!(true), json!(true), 0), "still running, waiting, and nothing written while it is away");
    b.r.fake.lock().refuse = false;
    assert!(until(10.0, || f.is_up()).await, "it comes back");
    b.sync().await;
    b.settle(1.0, true).await;
    assert_eq!((triggers(&b.r), b.lobby.state()["paused"].clone()), (vec![cue(1)], json!("")), "back with the layer as it was: carries on (the overdue step), does not restart the playlist");

    // ProPresenter was restarted: the layer came back empty
    b.lobby.stop_rotation(false).await.unwrap();
    b.set_time(12000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(2.0, true).await;
    b.r.fake.lock().refuse = true;
    b.r.fake.kill_streams();
    assert!(until(10.0, || !f.is_up()).await);
    b.settle(10.0, false).await;
    b.put_up(false, "", None);
    b.r.fake.lock().refuse = false;
    assert!(until(10.0, || f.is_up()).await);
    b.sync().await;
    b.r.fake.lock().requests.clear();
    b.settle(2.0, true).await;
    assert_eq!((triggers(&b.r).first().cloned(), b.lobby.state()["paused"].clone()), (Some(trig(1)), json!("")), "back with an empty layer: it starts the playlist over, not paused");
    b.r.module.stop().await;
}

#[tokio::test]
async fn view_only_refuses_the_start_and_ends_a_running_rotation_with_the_reason() {
    let b = bench(json!({})).await;
    b.r.amend(json!({"view_only": true}));
    b.r.fake.lock().requests.clear();
    let e = b.lobby.start_rotation().await.unwrap_err();
    assert_eq!(failure(&e), "ViewOnly", "start is refused as view-only");
    assert_eq!(writes(&b.r), 0, "and nothing was written");
    assert_eq!(b.lobby.state()["running"], false, "and no rotation was started");
    b.r.amend(json!({"view_only": false}));
    b.set_time(13000.0);
    b.lobby.start_rotation().await.unwrap();
    b.r.amend(json!({"view_only": true}));
    b.r.fake.lock().requests.clear();
    b.settle(10.0, true).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["error"].as_str().unwrap_or("").contains("View-only")), (json!(false), true), "view-only switched on mid-run ends it, with the reason");
    assert_eq!(writes(&b.r), 0, "with no write");
    b.r.module.stop().await;
}

#[tokio::test]
async fn stop_shutdown_and_switching_proppresenter_off_end_it_without_writing() {
    let b = bench(json!({})).await;
    b.set_time(14000.0);
    b.lobby.start_rotation().await.unwrap();
    b.r.fake.lock().requests.clear();
    let st = b.lobby.stop_rotation(false).await.unwrap();
    assert_eq!((st["running"].clone(), writes(&b.r)), (json!(false), 0), "stop stops and writes nothing");
    b.settle(30.0, true).await;
    assert_eq!((b.lobby.state()["running"].clone(), writes(&b.r)), (json!(false), 0), "and stays stopped");
    b.lobby.start_rotation().await.unwrap();
    b.r.fake.lock().requests.clear();
    let st = b.lobby.stop_rotation(true).await.unwrap();
    assert_eq!((st["running"].clone(), triggers(&b.r)), (json!(false), vec!["/v1/clear/layer/announcements".to_owned()]), "stop and clear also clears announcements");
    b.lobby.start_rotation().await.unwrap();
    b.r.amend(json!({"view_only": true}));
    b.r.fake.lock().requests.clear();
    let e = b.lobby.stop_rotation(true).await.unwrap_err();
    assert_eq!((failure(&e), b.lobby.state()["running"].clone()), ("ViewOnly".to_owned(), json!(false)), "view-only refuses the clear but the rotation is stopped");
    b.r.amend(json!({"view_only": false}));
    b.lobby.start_rotation().await.unwrap();
    b.r.amend(json!({"enabled": false}));
    b.r.fake.lock().requests.clear();
    b.settle(30.0, true).await;
    assert_eq!((b.lobby.state()["running"].clone(), writes(&b.r)), (json!(false), 0), "switching ProPresenter off in admin stops it, with no write");
    b.r.amend(json!({"enabled": true}));
    let f = Arc::clone(&b.r.ctx().follower);
    assert!(until(10.0, || f.is_up()).await);
    b.lobby.start_rotation().await.unwrap();
    b.r.amend(json!({"lobby": {"playlist": "PL-OTHER", "interval_s": 8, "loop": true}}));
    b.r.fake.lock().requests.clear();
    b.settle(3.0, true).await;
    assert_eq!(b.lobby.state()["running"], false, "picking another playlist ends the rotation");
    // shutdown (cancel) stops it and writes nothing
    b.r.amend(json!({"lobby": {"playlist": "PL-LOBBY", "interval_s": 8, "loop": true}}));
    b.lobby.start_rotation().await.unwrap();
    b.r.fake.lock().requests.clear();
    b.lobby.start();
    b.lobby.cancel();
    b.settle(30.0, true).await;
    assert_eq!((b.lobby.state()["running"].clone(), writes(&b.r)), (json!(false), 0), "shutdown (cancel) stops it and writes nothing");
    b.r.module.stop().await;
}

#[tokio::test]
async fn a_failing_step_is_retried_not_skipped_and_says_so_without_detail() {
    let b = bench(json!({})).await;
    b.set_time(15000.0);
    b.lobby.start_rotation().await.unwrap();
    b.settle(7.0, true).await;
    b.r.fake.lock().forced.push((cue(1), 503, "boom with a secret detail".into()));
    b.r.fake.lock().requests.clear();
    b.settle(5.0, true).await;
    let st = b.lobby.state();
    assert_eq!((st["running"].clone(), st["error"].clone()), (json!(true), json!("ProPresenter did not take the last step; Relay will try again.")), "still running, with a fixed sentence");
    assert!(!st.to_string().contains("boom"), "the secret detail stays in the log only");
    b.r.fake.lock().forced.clear();
    b.r.fake.lock().requests.clear();
    b.settle(2.0, true).await;
    assert_eq!(triggers(&b.r).first(), Some(&cue(1)), "the same step happens when it recovers");
    assert_eq!(b.lobby.state()["error"], "", "and the error clears");
    b.r.module.stop().await;
}

#[tokio::test]
async fn the_routes_answer_refusals_with_fixed_sentences() {
    let r = rig().await;
    r.fake.lock().lobby_items = lobby_items();
    r.configure(json!({"lobby": {"playlist": ""}}));
    let (st, body, _) = r.send(Method::POST, &format!("{B}/lobby/start"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["error"].clone()), (409, json!("Pick the announcement playlist first.")), "no playlist: 409 and the sentence");
    r.configure(json!({"lobby": {"playlist": "PL-LOBBY"}}));
    assert_eq!(r.send(Method::POST, &format!("{B}/lobby/start"), Some(json!({}))).await.0.as_u16(), 409, "not connected: 409");
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up() && f.data("status/layers").get("announcements").is_some()).await);
    r.amend(json!({"view_only": true}));
    r.fake.lock().requests.clear();
    let (st, body, _) = r.send(Method::POST, &format!("{B}/lobby/start"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["error"].as_str().unwrap_or("").contains("View-only mode"), writes(&r)), (409, true, 0), "view-only: 409 and the sentence, nothing written");
    r.amend(json!({"view_only": false}));
    r.fake.lock().forced.push((trig(1), 503, "boom secret".into()));
    let (st, _, text) = r.send(Method::POST, &format!("{B}/lobby/start"), Some(json!({}))).await;
    assert!(matches!(st.as_u16(), 502 | 504) && !text.contains("boom") && !text.contains("secret"), "a failing ProPresenter is a fixed sentence with no exception text");
    r.fake.lock().forced.clear();
    r.fake.lock().requests.clear();
    let (st, body, _) = r.send(Method::POST, &format!("{B}/lobby/start"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["running"].clone(), triggers(&r)), (200, json!(true), vec![trig(1)]), "a good start through the route");
    let (st, body, _) = r.send(Method::POST, &format!("{B}/lobby/stop"), Some(json!({"clear": true}))).await;
    assert_eq!((st.as_u16(), body["running"].clone(), triggers(&r).last().cloned()), (200, json!(false), Some("/v1/clear/layer/announcements".to_owned())), "and a stop with clear");
    r.amend(json!({"view_only": true}));
    assert_eq!(r.send(Method::POST, &format!("{B}/lobby/stop"), Some(json!({"clear": true}))).await.0.as_u16(), 409, "view-only stop and clear is a 409");
    r.module.stop().await;
}

// ------------------------------------------------------------------------------------------------------------------ the view
#[test]
fn the_overlays_timer_and_media_arithmetic() {
    let cfg = json!({"amber_s": 120, "red_s": 30, "show": []});
    let timer = |value: f64, state: &str, dir: &str| json!({"id": "T", "name": "Segment", "state": state, "dir": dir, "value": value});
    let at = 5000.0;
    let row = timer_row(&timer(130.0, "running", "down"), at - 3.0, at, &cfg);
    assert_eq!((row["value"].clone(), row["level"].clone()), (json!(127.0), json!("ok")), "a running countdown read 3 s ago is 3 s lower; 127 is ok");
    assert_eq!(timer_row(&timer(130.0, "running", "down"), at - 10.0, at, &cfg)["value"], 125.0, "a reading older than 5 s is carried only 5 s");
    assert_eq!(timer_row(&timer(120.0, "running", "down"), at - 8.0, at, &cfg)["level"], "amber", "under the amber threshold");
    assert_eq!(timer_row(&timer(20.0, "running", "down"), at - 8.0, at, &cfg)["level"], "red", "under the red threshold");
    assert_eq!(timer_row(&timer(30.0, "running", "up"), at - 4.0, at, &cfg)["value"], 34.0, "an elapsed timer counts up");
    assert_eq!(timer_row(&timer(60.0, "stopped", "down"), at - 4.0, at, &cfg)["value"], 60.0, "a stopped timer does not move");
    assert_eq!(timer_row(&timer(-5.0, "overrunning", "down"), at - 4.0, at, &cfg)["value"], -9.0, "an overrunning countdown goes further negative");

    let media = |time: f64, duration: f64, playing: bool, age: f64| json!({"time": time, "duration": duration, "playing": playing, "name": "Intro video", "audio_only": false, "read_at": at - age});
    let m = media_row(&media(10.0, 40.0, true, 2.0), at).unwrap();
    assert_eq!((m["time"].clone(), m["remaining"].clone(), m["percent"].clone(), m["playing"].clone()), (json!(12.0), json!(28.0), json!(30.0), json!(true)), "a playing video is carried forward by the reading's age");
    let m = media_row(&media(10.0, 40.0, false, 2.0), at).unwrap();
    assert_eq!((m["time"].clone(), m["percent"].clone(), m["playing"].clone()), (json!(10.0), json!(25.0), json!(false)), "a paused one stays where it is");
    let m = media_row(&media(39.0, 40.0, true, 4.0), at).unwrap();
    assert_eq!((m["time"].clone(), m["percent"].clone(), m["remaining"].clone()), (json!(40.0), json!(100.0), json!(0.0)), "it cannot run past its end");
    assert!(media_row(&media(1.0, 0.0, true, 1.0), at).is_none(), "a still (no length) has no progress bar");
}

#[tokio::test]
async fn the_snapshot_the_media_read_the_images_and_the_public_switch() {
    let r = rig().await;
    {
        let mut f = r.fake.lock();
        f.song("Welcome", "Songs", vec![("Verse 1", vec!["a1", "a2", "a3"])]);
        f.timer_rows = json!([{"id": {"uuid": "T-DOWN", "name": "Segment", "index": 0}, "time": "00:02:10", "state": "running"}]);
        f.timer_cfg = Some(json!([{"id": {"uuid": "T-DOWN", "name": "Segment", "index": 0}, "countdown": {"duration": 300}}]));
    }
    r.configure(json!({"view": {"timer": "T-DOWN", "public": false}}));
    r.start().await;
    let f = Arc::clone(&r.ctx().follower);
    assert!(until(6.0, || f.is_up() && f.presentation().is_some() && f.current().text == "a1" && f.timers().len() == 1).await, "up, with the slide and the timer");
    let snap = r.ctx().view.snapshot("/api/admin/propresenter/live");
    assert_eq!((snap["live"].clone(), snap["title"].clone(), snap["part"].clone(), snap["part_color"].clone(), snap["slide_number"].clone(), snap["slide_count"].clone()), (json!(true), json!("Welcome"), json!("Verse 1"), json!("#ff0080"), json!(1), json!(3)), "live with the title and part");
    assert_eq!((snap["timer"]["id"].clone(), snap["timer"]["state"].clone(), snap["timer"]["dir"].clone()), (json!("T-DOWN"), json!("running"), json!("down")), "the chosen timer is on it");
    // the media: read only while the slide or media layer has something
    assert!(r.ctx().follower.media().is_none(), "no media on the fake: nothing is shown");
    r.fake.lock().media = Some((json!(12.5), json!({"is_playing": true, "uuid": "M", "name": "Clip  one", "artist": "", "audio_only": false, "duration": 90.0})));
    assert!(until(6.0, || f.media().is_some()).await, "the slide layer is live: the media is read");
    let m = f.media().unwrap();
    assert_eq!((m["time"].clone(), m["duration"].clone(), m["playing"].clone(), m["name"].clone()), (json!(12.5), json!(90.0), json!(true), json!("Clip one")), "and shaped");
    r.fake.lock().clear();
    r.fake.push(&["presentation/current", "presentation/slide_index", "status/layers"]);
    assert!(until(6.0, || f.media().is_none()).await, "layers cleared: the reading is dropped");
    r.fake.lock().song("Welcome", "Songs", vec![("Verse 1", vec!["a1", "a2", "a3"])]);
    r.fake.push(&["presentation/current", "presentation/slide_index", "status/layers"]);
    assert!(until(6.0, || f.presentation().is_some() && f.current().text == "a1").await);
    r.fake.lock().media = None;
    assert!(until(6.0, || f.media().is_none()).await, "a failed read is no media, not a crash");

    // images: proxied, cached, bigger, and only for the slide that is up
    let snap = r.ctx().view.snapshot("/api/admin/propresenter/live");
    let current = snap["current"]["image"].as_str().unwrap().to_owned();
    assert!(current.starts_with("/api/admin/propresenter/live/image/current?v="), "the image URLs are Relay's own, under the route");
    assert!(!current.contains("127.0.0.1") && !current.contains("presentation/"), "and none points at ProPresenter");
    assert_eq!((snap["current"]["text"].clone(), snap["next"]["text"].clone()), (json!("a1"), json!("a2")), "the text is there as a fallback");
    r.fake.lock().thumb_calls = 0;
    r.fake.lock().requests.clear();
    let (st, ctype, bytes) = r.fetch(&current).await;
    assert_eq!((st, ctype.as_str()), (StatusCode::OK, "image/jpeg"), "the image comes back with its type");
    assert!(bytes.starts_with(b"\xff\xd8JPEG"));
    assert!(r.fake.lock().requests.iter().any(|q| q.1.contains("quality=960")), "it was asked at the large size, not the grid's");
    r.fetch(&current).await;
    assert_eq!(r.fake.lock().thumb_calls, 1, "the same slide again is served from the cache");
    r.fetch(snap["next"]["image"].as_str().unwrap()).await;
    assert_eq!(r.fake.lock().thumb_calls, 2, "the next slide is its own picture");
    let (st, body, _) = r.send(Method::GET, &format!("{B}/live/image/current?v=stale"), None).await;
    assert_eq!((st.as_u16(), body["error"].clone()), (404, json!("That slide is not up any more.")), "a marker that is out of date is a 404 with a fixed sentence");
    assert_eq!(r.fake.lock().thumb_calls, 2, "and ProPresenter was not asked");

    // the public switch
    assert_eq!(r.fetch("/api/stage/live").await.0, StatusCode::NOT_FOUND, "off by default: 404");
    r.amend(json!({"view": {"timer": "", "public": true}}));
    let (st, body, _) = r.send(Method::GET, "/api/stage/live", None).await;
    assert_eq!((st.as_u16(), body["live"].clone()), (200, json!(true)), "on: the viewer port gets the snapshot");
    let pub_image = body["current"]["image"].as_str().unwrap().to_owned();
    assert!(pub_image.starts_with("/api/stage/live/image/current?v="), "its image URLs are under /api/stage");
    let (st, _, bytes) = r.fetch(&pub_image).await;
    assert_eq!((st, bytes.starts_with(b"\xff\xd8JPEG")), (StatusCode::OK, true), "and the picture");
    let (st, _, page) = r.fetch("/stage").await;
    assert_eq!((st, String::from_utf8_lossy(&page).contains("slideview.js")), (StatusCode::OK, true), "the page is served");
    assert_eq!(r.fetch("/api/stage/live/image/2?v=x").await.0, StatusCode::NOT_FOUND, "it offers only the live and next slide, no arbitrary slide");
    r.amend(json!({"view": {"timer": "", "public": false}}));
    assert_eq!(r.fetch("/api/stage/live").await.0, StatusCode::NOT_FOUND, "off again: 404");
    r.amend(json!({"enabled": false, "view": {"timer": "", "public": true}}));
    assert_eq!(r.fetch("/api/stage/live").await.0, StatusCode::NOT_FOUND, "public on but ProPresenter off: still 404");
    r.module.stop().await;
    let _ = (Refusal::Empty, r.store.read("propresenter"));
}
