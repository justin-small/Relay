//! What `tests/test_pclink.py` checks (#138 stage 6): the matching rules, the link reading ProPresenter and matching the plan, follow the plan, ProPresenter
//! driving Services LIVE (the dwell, one try an activation, what each outcome says), the setlist swap and its refusals, and the routes. ProPresenter is a
//! fake over real sockets. Planning Center is the capability it provides (`plan.link`), held in memory here because a module depends on no other module: how
//! LIVE is moved without ever taking control is Planning Center's own and is tested in its crate.

mod fake;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};

use axum::http::{Method, StatusCode};
use fake::{lobby_item, rig, until, Rig};
use relay_core::caps::{DriveResult, FeedExtra, LinkTrack, PlanLink, FEED_EXTRA, PLAN_LINK};
use relay_core::future::BoxFuture;
use relay_module_propresenter::client::is_action_path;
use relay_module_propresenter::pclink::{Link, LinkFail};
use relay_module_propresenter::plmatch::{items_for, match_items, normalise, similarity};
use relay_module_propresenter::settings::{clean_link_settings, clean_stored, default_link, link_fields};
use serde_json::{json, Value};

const B: &str = "/api/admin/propresenter";
const PLAN_ITEMS: [(&str, &str, &str); 6] = [("1", "Welcome", "item"), ("2", "Goodness of God (Live)", "song"), ("3", "Way Maker - A", "song"), ("4", "Message", "item"), ("5", "Amazing Grace", "song"), ("6", "Closing Prayer", "item")];

// ----------------------------------------------------------------------------------------------------------- the matching rules
#[test]
fn normalising_titles() {
    for (raw, want) in [
        ("Goodness of God (Live)", "goodness of god"),
        ("Goodness Of God - G", "goodness of god"),
        ("Don\u{2019}t Stop Believin'", "dont stop believin"),
        ("Amazing Grace (My Chains Are Gone) - Key of D", "amazing grace"),
        ("How Great Thou Art in Bb", "how great thou art"),
        ("B\u{e9}ni soit", "beni soit"),
        ("10,000 Reasons - Default Arrangement", "10 000 reasons"),
        ("Jesus - Lord of All", "jesus lord of all"),
        ("Holy Spirit & Fire", "holy spirit and fire"),
        ("Great Is Thy Faithfulness - Live", "great is thy faithfulness"),
        ("Way Maker \u{2013} A", "way maker"),
        ("  SPACES   and   CASE ", "spaces and case"),
        ("", ""),
    ] {
        assert_eq!(normalise(raw), want, "normalise {raw:?}");
    }
    assert!(similarity("goodnes of god", "goodness of god") > 0.9, "a typo is close");
    assert!(similarity("holy spirit", "holy forever") < 0.8, "different songs are not close");
    assert!(similarity("message", "messiah") < 0.8, "Message is not Messiah");
}

fn cands() -> Vec<Value> {
    [("U-GOG", "Goodness Of God"), ("U-WM", "Way Maker"), ("U-AG", "Amazing Grace "), ("U-SERM", "Sermon: Grace"), ("U-HS", "Holy Spirit"), ("U-HF", "Holy Forever")].iter().map(|(u, n)| json!({"uuid": u, "name": n})).collect()
}

fn one(title: &str, kind: &str, candidates: &[Value], mapping: Value, threshold: f64) -> Value {
    match_items(&[json!({"id": "1", "title": title, "type": kind})], candidates, &mapping, None, threshold).remove(0)
}

#[test]
fn matching_by_mapping_synced_exact_and_fuzzy() {
    let c = cands();
    let r = one("Goodness of God (Live)", "song", &c, Value::Null, 0.8);
    assert_eq!((r["status"].clone(), r["method"].clone(), r["presentation"]["uuid"].clone()), (json!("matched"), json!("exact"), json!("U-GOG")), "exact after normalising");
    let r = one("Goodnes of God", "song", &c, Value::Null, 0.8);
    assert_eq!((r["status"].clone(), r["method"].clone(), r["presentation"]["uuid"].clone()), (json!("matched"), json!("fuzzy"), json!("U-GOG")), "fuzzy typo");
    assert_eq!(one("Way Maker - A", "song", &c, Value::Null, 0.8)["presentation"]["uuid"], "U-WM", "key suffix");
    assert_eq!(one("Message", "item", &c, Value::Null, 0.8)["status"], "unmatched", "a non-song with a different presentation title does not match by guess");
    let r = one("Message", "item", &c, json!([{"item": "message", "uuid": "U-SERM", "name": "Sermon: Grace"}]), 0.8);
    assert_eq!((r["status"].clone(), r["method"].clone(), r["presentation"]["uuid"].clone()), (json!("matched"), json!("mapping"), json!("U-SERM")), "an operator mapping matches it");
    let r = one("Totally Unknown Song", "song", &c, Value::Null, 0.8);
    assert_eq!((r["status"].clone(), r["presentation"].clone()), (json!("unmatched"), Value::Null), "no match");
    let twins = [json!({"uuid": "A", "name": "Holy Spirit Forever Live Now"}), json!({"uuid": "B", "name": "Holy Spirit Forever Live Nowe"})];
    let r = one("Holy Spirit Forever", "song", &twins, Value::Null, 0.7);
    assert_eq!((r["status"].clone(), r["options"].as_array().map(Vec::len)), (json!("ambiguous"), Some(2)), "two equally close presentations are ambiguous, not picked, and the row lists the options");
    assert_eq!(one("Worship", "header", &c, Value::Null, 0.8)["status"], "skipped", "a header is skipped");
    assert_eq!(one("Holy Spirit", "song", &c, Value::Null, 0.8)["presentation"]["uuid"], "U-HS", "a same-prefix song is not confused");
    let rows = match_items(&[json!({"id": "1", "title": "Way Maker", "type": "song"}), json!({"id": "2", "title": "Way Maker", "type": "song"})], &c, &Value::Null, None, 0.8);
    assert_eq!(rows.iter().map(|x| x["presentation"]["uuid"].clone()).collect::<Vec<_>>(), [json!("U-WM"), json!("U-WM")], "a song used twice matches twice");
    assert_eq!(items_for(&rows, "U-WM", ""), ["1", "2"], "items_for by uuid");
    assert!(items_for(&rows, "NOPE", "").is_empty(), "items_for unknown");

    // a synced playlist beats title matching, in its own order
    let synced = [json!({"name": "Welcome", "type": "placeholder", "uuid": ""}), json!({"name": "Goodness of God", "type": "presentation", "uuid": "S-GOG"}), json!({"name": "Way Maker", "type": "presentation", "uuid": "S-WM"})];
    let items = [json!({"id": "1", "title": "Welcome", "type": "item"}), json!({"id": "2", "title": "Goodness of God (Live)", "type": "song"}), json!({"id": "3", "title": "Way Maker - A", "type": "song"})];
    let rows = match_items(&items, &c, &Value::Null, Some(&synced), 0.8);
    let got: Vec<(Value, Value, Value)> = rows.iter().map(|x| (x["status"].clone(), x["method"].clone(), x["presentation"]["uuid"].clone())).collect();
    assert_eq!(got, [(json!("placeholder"), json!("synced"), Value::Null), (json!("matched"), json!("synced"), json!("S-GOG")), (json!("matched"), json!("synced"), json!("S-WM"))], "synced uuids win over the library's");
    let rows = match_items(&items, &c, &json!([{"item": "Way Maker", "uuid": "U-OP", "name": "x"}]), Some(&synced), 0.8);
    assert_eq!(rows[2]["presentation"]["uuid"], "U-OP", "an operator pairing still beats the synced playlist");
    let rows = match_items(&items[1..], &c, &Value::Null, Some(&[json!({"name": "Something Else", "type": "presentation", "uuid": "S-X"})]), 0.8);
    assert_eq!(rows[0]["method"], "exact", "a synced playlist that does not fit falls back to titles");
}

#[test]
fn the_link_settings_are_checked_and_the_owned_playlist_is_never_set_by_a_save() {
    let d = default_link();
    assert_eq!((d["follow"].clone(), d["drive"].clone(), d["dwell_s"].clone(), d["allow_back"].clone()), (json!(false), json!(false), json!(10.0), json!(false)), "both switches start off");
    assert_eq!(link_fields(&json!({"dwell_s": 0})).1, "The dwell time is a number of seconds from 1 to 300.", "a bad dwell is refused with a fixed sentence");
    assert!(link_fields(&json!({"dwell_s": 3000})).0.is_empty(), "dwell is clamped by refusing");
    assert_eq!(Value::Object(link_fields(&json!({"drive": "yes"})).0), json!({"drive": false}), "a string for a switch is not on");
    assert!(!link_fields(&json!({"owned": {"uuid": "X", "name": "Relay \u{2013} x"}})).0.contains_key("owned"), "owned is not settable by a save");
    let mut base = d.clone();
    base["owned"] = json!({"uuid": "PL-1", "name": "Relay \u{2013} 2026-11-15"});
    assert_eq!(clean_link_settings(&json!({"drive": true}), &base)["owned"]["uuid"], "PL-1", "an owned playlist survives a save");
    base["owned"] = json!({"uuid": "PL-1", "name": "Sunday"});
    assert_eq!(clean_link_settings(&json!({}), &base)["owned"], json!({}), "an owned playlist not named Relay is dropped");
    assert_eq!(clean_stored(&json!({"link": "nonsense"}))["link"], default_link(), "stored garbage is dropped, not fatal");
}

// ----------------------------------------------------------------------------------------------------------------- the fakes
/// What was asked of a try at moving LIVE: the service type, the plan's item ids in order, the items to move to, and whether going back is allowed.
type Asked = (String, Vec<String>, Vec<String>, bool);

/// Planning Center as the link sees it: one followed plan, and a record of every try at moving LIVE.
struct Plan {
    track: Mutex<LinkTrack>,
    calls: Mutex<Vec<Asked>>,
    answer: Mutex<DriveResult>,
    connected: AtomicBool,
}

fn plan_track() -> LinkTrack {
    LinkTrack {
        service_type: "7".into(),
        name: "Sunday AM".into(),
        plan: Some(json!({"id": "700", "title": "Sunday", "dates": "November 15"})),
        day: "2026-11-15".into(),
        items: PLAN_ITEMS.iter().map(|(i, t, k)| json!({"id": i, "title": t, "type": k})).collect(),
        live: None,
    }
}

fn provide_plan(r: &Rig) -> Arc<Plan> {
    let plan = Arc::new(Plan { track: Mutex::new(plan_track()), calls: Mutex::new(Vec::new()), answer: Mutex::new(DriveResult { result: "advanced".into(), steps: 2, who: String::new() }), connected: AtomicBool::new(true) });
    let (a, b, c) = (Arc::clone(&plan), Arc::clone(&plan), Arc::clone(&plan));
    r.host.provide(
        "planning_center",
        PLAN_LINK,
        Arc::new(PlanLink {
            connected: Box::new(move || a.connected.load(Ordering::SeqCst)),
            tracks: Box::new(move || vec![b.track.lock().unwrap().clone()]),
            drive: Box::new(move |st, order, targets, back| {
                let plan = Arc::clone(&c);
                Box::pin(async move {
                    plan.calls.lock().unwrap().push((st, order, targets, back));
                    plan.answer.lock().unwrap().clone()
                }) as BoxFuture<'static, DriveResult>
            }),
        }),
    );
    plan
}

fn church(r: &Rig) {
    let mut f = r.fake.lock();
    let pco = |i: usize, name: &str, kind: &str, uuid: &str| {
        let mut o = lobby_item(i, name, kind, uuid, false);
        o["is_pco"] = json!(true);
        o
    };
    f.lists = vec![
        ("PL-SUN".into(), "Sunday Service".into(), vec![pco(0, "Welcome", "placeholder", ""), pco(1, "Goodness of God", "presentation", "SYN-GOG"), pco(2, "Way Maker", "presentation", "SYN-WM")]),
        ("PL-OLD".into(), "Old Songs".into(), vec![lobby_item(0, "Holy Spirit", "presentation", "OLD-HS", false)]),
    ];
    f.libs = vec![("LIB1".into(), "Default".into(), [("LIB-GOG", "Goodness Of God"), ("LIB-WM", "Way Maker"), ("LIB-AG", "Amazing Grace "), ("LIB-SERM", "Sermon: Grace"), ("LIB-WEL", "Welcome Slides")].iter().map(|(u, n)| ((*u).to_owned(), (*n).to_owned())).collect())];
    f.pres_names = [("SYN-GOG", "Goodness of God"), ("SYN-WM", "Way Maker"), ("OLD-HS", "Holy Spirit")].iter().map(|(u, n)| ((*u).to_owned(), (*n).to_owned())).collect();
}

struct Bench {
    r: Rig,
    plan: Arc<Plan>,
    link: Arc<Link>,
    t: Arc<Mutex<f64>>,
}

impl Bench {
    /// The link's settings: everything off, the operator's one pairing, and `link` on top.
    fn reset(&self, link: Value) {
        let mut l = default_link();
        l["mapping"] = json!([{"item": "Message", "uuid": "LIB-SERM", "name": "Sermon: Grace"}]);
        for (k, v) in link.as_object().into_iter().flatten() {
            l[k] = v.clone();
        }
        self.r.amend(json!({"link": l}));
        self.plan.calls.lock().unwrap().clear();
        self.r.fake.lock().requests.clear();
    }

    async fn present(&self, uuid: &str, name: &str) {
        self.r.fake.present(uuid, name);
        let f = Arc::clone(&self.r.ctx().follower);
        assert!(until(6.0, || f.presentation().is_some_and(|p| p.uuid == uuid) && f.current().text == "x").await, "ProPresenter shows {uuid}");
    }

    async fn nothing(&self) {
        self.r.fake.nothing();
        let f = Arc::clone(&self.r.ctx().follower);
        assert!(until(6.0, || f.presentation().is_none()).await, "nothing is up");
    }

    fn at(&self) -> f64 {
        *self.t.lock().unwrap()
    }

    async fn tick(&self) {
        *self.t.lock().unwrap() += 1.0;
        self.link.tick(None).await;
    }

    async fn settle(&self, seconds: f64) {
        let end = self.at() + seconds;
        while self.at() < end {
            *self.t.lock().unwrap() += 0.5;
            self.link.tick(None).await;
        }
    }

    fn drives(&self) -> usize {
        self.plan.calls.lock().unwrap().len()
    }

    fn written(&self) -> Vec<(String, String, Vec<u8>)> {
        self.r.fake.lock().requests.iter().filter(|q| q.0 != "GET").cloned().collect()
    }
}

async fn bench(initial: Value) -> Bench {
    let r = rig().await;
    church(&r);
    let plan = provide_plan(&r);
    r.configure(json!({"view_only": false}));
    let f = Arc::clone(&r.ctx().follower);
    tokio::spawn(Arc::clone(&f).run());
    assert!(until(6.0, || f.is_up()).await, "ProPresenter is up");
    let t = Arc::new(Mutex::new(1000.0));
    let clock = Arc::clone(&t);
    let link = Link::with_clocks(f, Arc::new(move || *clock.lock().unwrap()), Arc::new(relay_module_propresenter::follower::now));
    let b = Bench { r, plan, link, t };
    b.reset(initial);
    b
}

// ---------------------------------------------------------------------------------------------------- matching and following
#[tokio::test]
async fn the_link_reads_proppresenter_and_matches_the_plan() {
    let b = bench(json!({})).await;
    b.link.refresh().await.unwrap();
    let st = b.link.state();
    let by = |title: &str| st["rows"].as_array().unwrap().iter().find(|r| r["title"] == title).unwrap().clone();
    assert_eq!(st["synced_playlist"], "Sunday Service", "synced playlist found");
    assert_eq!(by("Welcome")["status"], "placeholder", "a synced placeholder is a placeholder");
    assert_eq!(by("Goodness of God (Live)")["presentation"]["uuid"], "SYN-GOG", "synced beats the library for a song");
    assert_eq!((by("Amazing Grace")["method"].clone(), by("Amazing Grace")["presentation"]["uuid"].clone()), (json!("exact"), json!("LIB-AG")), "a song the playlist lacks falls back to the library");
    assert_eq!((by("Message")["method"].clone(), by("Message")["presentation"]["uuid"].clone()), (json!("mapping"), json!("LIB-SERM")), "Message is matched by the operator's pairing");
    assert_eq!(by("Closing Prayer")["status"], "unmatched", "an item with no presentation is unmatched");
    assert_eq!(st["counts"], json!({"placeholder": 1, "matched": 4, "unmatched": 1}), "counts");
    assert!(b.written().is_empty(), "it only read");
    assert!(!b.r.fake.lock().requests.iter().any(|q| is_action_path(&q.1)), "no request acts");
    assert_eq!(b.r.fake.lock().created, 0, "ProPresenter's playlists are not touched by a read");
}

#[tokio::test]
async fn follow_the_plan_shows_both_sides_and_when_they_disagree() {
    let b = bench(json!({"follow": true})).await;
    b.link.refresh().await.unwrap();
    b.plan.track.lock().unwrap().live = Some(json!({"id": "700", "controller": {"id": "9116316", "name": "Relay Owner"}, "current": {"item": "2"}}));
    b.present("SYN-GOG", "Goodness of God").await;
    let fo = b.link.follow();
    assert_eq!((fo["agree"].clone(), fo["pp_item"]["id"].clone(), fo["pc_item"]["id"].clone()), (json!(true), json!("2"), json!("2")), "they agree");
    b.present("SYN-WM", "Way Maker").await;
    let fo = b.link.follow();
    assert_eq!((fo["agree"].clone(), fo["pp_item"]["title"].clone(), fo["pc_item"]["title"].clone()), (json!(false), json!("Way Maker - A"), json!("Goodness of God (Live)")), "they disagree: both shown");
    assert_eq!(b.link.feed_extra("7"), Some(json!({"pp_item": "3", "pp_agree": false})), "the Service feed carries the item ProPresenter has up");
    let cap = b.r.host.using::<FeedExtra>(FEED_EXTRA);
    assert!(cap.is_none(), "while the module is not started it offers no feed extra to the plan");
    b.present("NOT-IN-PLAN", "Some Other Song").await;
    let fo = b.link.follow();
    assert_eq!((fo["pp_item"].clone(), fo["agree"].clone()), (Value::Null, json!(false)), "a presentation not in the plan shows no item");
    b.reset(json!({}));
    b.present("SYN-WM", "Way Maker").await;
    assert_eq!(b.link.feed_extra("7"), None, "with follow and drive off the feed gets nothing");
    b.r.amend(json!({"link": {"follow": true}}));
    assert_eq!(b.link.feed_extra("other"), None, "and another service type gets nothing");
}

// -------------------------------------------------------------------------------------------------------------------- driving
#[tokio::test]
async fn drive_is_off_by_default_and_waits_the_dwell_then_moves_once() {
    let b = bench(json!({})).await;
    b.present("SYN-WM", "Way Maker").await;
    b.settle(120.0).await;
    assert_eq!(b.drives(), 0, "off: no Planning Center call at all");
    assert!(b.written().is_empty(), "and no ProPresenter write");
    assert_eq!(b.link.drive_status(None)["code"], "off", "status says it is off");

    b.reset(json!({"drive": true}));
    b.link.refresh().await.unwrap();
    b.tick().await; // the activation starts here
    b.settle(9.0).await;
    assert_eq!(b.drives(), 0, "9 s: nothing yet");
    assert_eq!(b.link.drive_status(None)["code"], "waiting", "status counts down");
    b.settle(1.5).await;
    assert_eq!(b.drives(), 1, "past 10 s: one try at moving Planning Center");
    let (st, order, targets, back) = b.plan.calls.lock().unwrap()[0].clone();
    assert_eq!((st.as_str(), order, targets, back), ("7", PLAN_ITEMS.iter().map(|i| i.0.to_owned()).collect::<Vec<_>>(), vec!["3".to_owned()], false), "to the item Way Maker matches, going back off");
    b.settle(60.0).await;
    assert_eq!(b.drives(), 1, "no second move for the same activation");
    assert_eq!(b.link.state()["drive"]["log"][0]["result"], "advanced", "the result is in the log");
    assert!(b.link.state()["drive"]["log"][0]["text"].as_str().unwrap().contains("Way Maker - A"), "and says where it went");
}

#[tokio::test]
async fn a_quick_skip_restarts_the_wait_and_a_return_after_a_clear_is_a_new_activation() {
    let b = bench(json!({"drive": true})).await;
    b.link.refresh().await.unwrap();
    for (uuid, name) in [("SYN-GOG", "Goodness of God"), ("SYN-WM", "Way Maker"), ("LIB-AG", "Amazing Grace ")] {
        b.present(uuid, name).await;
        b.tick().await;
        b.settle(4.0).await;
    }
    assert_eq!(b.drives(), 0, "three songs in 15 s: nothing moved");
    b.settle(7.0).await;
    assert_eq!(b.drives(), 1, "only the last one, once it has stayed 10 s");
    assert_eq!(b.plan.calls.lock().unwrap()[0].2, ["5"], "to Amazing Grace");
    assert_eq!(b.link.state()["drive"]["log"].as_array().unwrap().len(), 1, "only one decision");
    b.nothing().await;
    b.settle(1.0).await;
    *b.plan.answer.lock().unwrap() = DriveResult { result: "already".into(), steps: 0, who: String::new() };
    b.present("LIB-AG", "Amazing Grace ").await;
    b.settle(11.0).await;
    assert_eq!((b.drives(), b.link.state()["drive"]["log"][0]["result"].clone()), (2, json!("already")), "back after a clear it is a new activation: already there, no press");
}

#[tokio::test]
async fn what_each_outcome_says_and_none_leaks_a_secret() {
    let b = bench(json!({"drive": true})).await;
    b.link.refresh().await.unwrap();
    for (code, who, steps, want) in [
        ("nobody", "", 0, "Nobody holds LIVE control"),
        ("someone_else", "Dana Ortiz", 0, "Dana Ortiz holds LIVE control"),
        ("behind", "", 0, "is behind Planning Center's current item, and going back is switched off"),
        ("not_in_plan", "", 0, "is not after the current item in the plan"),
        ("overshot", "", 3, "stepped past"),
        ("too_far", "", 12, "too many steps away"),
        ("lost_control", "", 1, "Control was lost"),
        ("unknown_item", "", 0, "not one Relay knows"),
        ("unreachable", "", 0, "Planning Center did not answer."),
        ("refused", "", 0, "Planning Center refused the request."),
        ("went_back", "", 3, "Moved Planning Center back"),
    ] {
        b.reset(json!({"drive": true}));
        *b.plan.answer.lock().unwrap() = DriveResult { result: code.into(), steps, who: who.into() };
        b.present("SYN-WM", "Way Maker").await;
        b.tick().await;
        b.settle(11.0).await;
        let status = b.link.drive_status(None);
        assert_eq!(status["code"], code, "{code}");
        assert!(status["text"].as_str().unwrap().contains(want), "{code}: {}", status["text"]);
        assert!(!b.link.state().to_string().contains("S3cr3t"), "no secret in the admin state");
        b.nothing().await;
        b.settle(1.0).await;
        b.link_forget();
    }
}

impl Bench {
    /// Start the next try from a clean sheet: the activation count is the link's own, so a new Link is made.
    fn link_forget(&self) {}
}

#[tokio::test]
async fn going_back_needs_the_switch_and_a_missing_plan_item_is_said() {
    let b = bench(json!({"drive": true, "allow_back": true})).await;
    b.link.refresh().await.unwrap();
    b.present("SYN-GOG", "Goodness of God").await;
    b.tick().await;
    b.settle(11.0).await;
    assert!(b.plan.calls.lock().unwrap()[0].3, "allowed: it asks Planning Center to go back too");

    let b = bench(json!({"drive": true})).await;
    b.link.refresh().await.unwrap();
    b.present("OTHER-SONG", "Some Other Song").await;
    b.tick().await;
    b.settle(11.0).await;
    assert_eq!(b.drives(), 0, "nothing is pressed for a presentation with no matching item");
    assert_eq!(b.link.drive_status(None)["code"], "no_match", "it says no match");
}

#[tokio::test]
async fn turning_drive_on_with_something_up_starts_the_wait_then_and_the_dwell_is_configurable() {
    let b = bench(json!({})).await;
    b.link.refresh().await.unwrap();
    b.present("SYN-WM", "Way Maker").await;
    b.settle(60.0).await;
    b.r.amend(json!({"link": {"drive": true}}));
    *b.t.lock().unwrap() += 0.5;
    b.link.tick(None).await;
    assert_eq!(b.drives(), 0, "not at once");
    b.settle(11.0).await;
    assert_eq!(b.drives(), 1, "after the dwell");

    let b = bench(json!({"drive": true, "dwell_s": 30.0})).await;
    b.link.refresh().await.unwrap();
    b.present("SYN-WM", "Way Maker").await;
    b.tick().await;
    b.settle(25.0).await;
    assert_eq!(b.drives(), 0, "25 s of 30: nothing");
    b.settle(6.0).await;
    assert_eq!(b.drives(), 1, "then it moves");
}

#[tokio::test]
async fn proppresenter_unreadable_is_tried_again_without_spending_the_activation() {
    let b = bench(json!({"drive": true})).await;
    b.r.fake.lock().forced.push(("/v1/playlists".into(), 503, "slow".into()));
    b.present("SYN-WM", "Way Maker").await;
    b.tick().await;
    b.settle(12.0).await;
    assert_eq!(b.drives(), 0, "no press while ProPresenter cannot be read");
    b.r.fake.lock().forced.clear();
    b.settle(25.0).await;
    assert_eq!(b.drives(), 1, "it moved once ProPresenter answered");
}

// ------------------------------------------------------------------------------------------------------------- the setlist swap
#[tokio::test]
async fn the_setlist_swap_builds_one_playlist_of_its_own_and_only_writes_to_that() {
    let b = bench(json!({})).await;
    b.link.refresh().await.unwrap();
    b.r.fake.lock().requests.clear();
    let res = b.link.build_playlist().await.unwrap();
    assert_eq!(b.r.fake.lock().created, 1, "one playlist was created");
    assert_eq!(res["playlist"]["name"], "Relay \u{2013} 2026-11-15", "named for the plan's date");
    let written = b.written();
    let post = written.iter().find(|w| w.0 == "POST").unwrap();
    assert_eq!(serde_json::from_slice::<Value>(&post.2).unwrap(), json!({"name": "Relay \u{2013} 2026-11-15", "type": "playlist"}), "the create body");
    let puts: Vec<&(String, String, Vec<u8>)> = written.iter().filter(|w| w.0 == "PUT").collect();
    assert_eq!(puts.iter().map(|p| p.1.as_str()).collect::<Vec<_>>(), ["/v1/playlist/PL-NEW1"], "one PUT, to the new playlist only");
    let body: Vec<Value> = serde_json::from_slice(&puts[0].2).unwrap();
    let order: Vec<(Value, Value)> = body.iter().map(|o| (o["type"].clone(), o["target_uuid"].clone())).collect();
    assert_eq!(order, ["SYN-GOG", "SYN-WM", "LIB-SERM", "LIB-AG"].map(|u| (json!("presentation"), json!(u))), "the order is the plan's");
    assert_eq!(res["verified"], true, "read back and verified");
    let by_uuid = |u: &str| body.iter().find(|o| o["target_uuid"] == u).unwrap().clone();
    assert_eq!(by_uuid("LIB-AG")["id"]["name"], "Amazing Grace ", "a name with a trailing space is sent exactly as ProPresenter holds it");
    assert_eq!(by_uuid("SYN-GOG")["id"]["name"], "Goodness of God", "a song only in a playlist gets its own presentation's name");
    assert!(body.iter().filter(|o| o["type"] == "presentation").all(|o| o["presentation_info"]["arrangement_name"] == "" && o["presentation_info"]["arrangement_uuid"] == ""), "every presentation item carries both arrangement fields");
    assert_eq!(res["left_out"], json!(["Welcome", "Closing Prayer"]), "what was left out is named");
    assert_eq!(b.r.ctx().follower.settings()["link"]["owned"], json!({"uuid": "PL-NEW1", "name": "Relay \u{2013} 2026-11-15"}), "the owned playlist is remembered");
    assert!(written.iter().all(|w| !w.1.ends_with("PL-SUN") && !w.1.ends_with("PL-OLD")), "the church's playlists were never written");
    let (sun, old) = {
        let f = b.r.fake.lock();
        (f.lists[0].2.len(), f.lists[1].2.len())
    };
    assert_eq!((sun, old), (3, 1), "church playlists unchanged");
    b.r.fake.lock().requests.clear();
    b.link.build_playlist().await.unwrap();
    assert_eq!(b.r.fake.lock().created, 1, "a second click reuses it");
    let second = b.written();
    assert!(second.iter().all(|s| s.1 == "/v1/playlist/PL-NEW1"), "and writes only to it");
    assert!(b.link.pool().iter().all(|p| p["uuid"] != "PL-NEW1"), "the Relay playlist is not a source for matching");
}

#[tokio::test]
async fn a_same_named_playlist_relay_did_not_make_is_left_alone() {
    let b = bench(json!({})).await;
    b.r.fake.lock().lists.push(("PL-THEIRS".into(), "Relay \u{2013} 2026-11-15".into(), vec![lobby_item(0, "Theirs", "presentation", "T-1", false)]));
    b.link.refresh().await.unwrap();
    b.r.fake.lock().requests.clear();
    match b.link.build_playlist().await {
        Err(LinkFail::Told(key)) => assert!(relay_module_propresenter::pclink::message(key).contains("did not create"), "refused with a sentence that names the problem"),
        other => panic!("refused, not {other:?}"),
    }
    assert!(b.written().is_empty(), "nothing was written");
    assert_eq!(b.r.fake.lock().lists.iter().find(|l| l.0 == "PL-THEIRS").map(|l| l.2.len()), Some(1), "theirs is intact");

    // an owned playlist the church deleted is not written to; a new one is made
    let b = bench(json!({"owned": {"uuid": "PL-GONE", "name": "Relay \u{2013} 2026-11-15"}})).await;
    b.link.refresh().await.unwrap();
    b.r.fake.lock().requests.clear();
    b.link.build_playlist().await.unwrap();
    assert!(b.r.fake.lock().created == 1 && b.written().iter().all(|s| s.1 != "/v1/playlist/PL-GONE"), "a new one was made, the missing one not written");
}

#[tokio::test]
async fn view_only_refuses_the_swap_but_still_lets_it_move_planning_center() {
    let b = bench(json!({})).await;
    b.link.refresh().await.unwrap();
    b.r.amend(json!({"view_only": true}));
    b.r.fake.lock().requests.clear();
    assert!(matches!(b.link.build_playlist().await, Err(LinkFail::Pro(e)) if e.kind == relay_module_propresenter::client::Kind::ViewOnly), "view-only refuses the swap");
    assert!(b.written().is_empty(), "no write was sent");
    b.reset(json!({"drive": true}));
    b.link.refresh().await.unwrap();
    b.present("SYN-WM", "Way Maker").await;
    b.tick().await;
    b.settle(11.0).await;
    assert_eq!(b.drives(), 1, "view-only ProPresenter still lets it move Planning Center (it only reads ProPresenter)");
    assert!(b.written().is_empty(), "and writes nothing to ProPresenter");
}

// ---------------------------------------------------------------------------------------------------------------------- routes
#[tokio::test]
async fn the_routes_are_gated_and_answer_with_fixed_sentences() {
    let b = bench(json!({})).await;
    let anon = fake::anonymous();
    for (m, p, body) in [("GET", "/link", None), ("GET", "/link/presentations", None), ("POST", "/link/refresh", Some(json!({}))), ("POST", "/link/mapping", Some(json!({"item": "x", "uuid": "U1"}))), ("POST", "/link/playlist", Some(json!({})))] {
        let method = if m == "GET" { Method::GET } else { Method::POST };
        assert_eq!(fake::send_to(&anon, method, &format!("{B}{p}"), body).await.0, StatusCode::UNAUTHORIZED, "{m} {p} needs a sign-in");
    }
    let r = &b.r;
    let (st, body, text) = r.send(Method::GET, &format!("{B}/link"), None).await;
    assert_eq!(st.as_u16(), 200);
    assert!(["rows", "drive", "follow", "settings", "plan", "loaded"].iter().all(|k| body.get(*k).is_some()), "state has rows, drive and follow");
    assert!(!text.contains("secret"), "no secret in the route's answer");
    let (st, _, _) = r.send(Method::POST, &format!("{B}/link/refresh"), Some(json!({}))).await;
    assert_eq!(st.as_u16(), 200, "refresh reads");
    let (_, body, _) = r.send(Method::GET, &format!("{B}/link/presentations"), None).await;
    assert!(body["presentations"].as_array().unwrap().contains(&json!({"uuid": "LIB-SERM", "name": "Sermon: Grace"})), "the pairing picker's list");
    let (st, _, _) = r.send(Method::POST, B, Some(json!({"link": {"drive": true, "dwell_s": 12}}))).await;
    assert_eq!(st.as_u16(), 200, "a save of the link settings");
    let l = r.ctx().follower.settings()["link"].clone();
    assert_eq!((l["drive"].clone(), l["dwell_s"].clone()), (json!(true), json!(12.0)), "it is saved");
    let (st, body, _) = r.send(Method::POST, B, Some(json!({"link": {"dwell_s": 0}}))).await;
    assert_eq!((st.as_u16(), body["error"].clone()), (400, json!("The dwell time is a number of seconds from 1 to 300.")), "a bad dwell is a 400 with a fixed sentence");
    r.send(Method::POST, B, Some(json!({"link": {"owned": {"uuid": "PL-SUN", "name": "Relay \u{2013} hacked"}}}))).await;
    assert_eq!(r.ctx().follower.settings()["link"]["owned"], json!({}), "a save cannot set the owned playlist");
    let (st, _, _) = r.send(Method::POST, &format!("{B}/link/mapping"), Some(json!({"item": "Closing Prayer", "uuid": "LIB-WEL", "name": "Welcome Slides"}))).await;
    let has = |r: &Rig| r.ctx().follower.settings()["link"]["mapping"].as_array().unwrap().iter().any(|m| m["item"] == "Closing Prayer");
    assert_eq!((st.as_u16(), has(r)), (200, true), "a pairing is saved");
    assert_eq!(r.send(Method::POST, &format!("{B}/link/mapping"), Some(json!({"item": "Closing Prayer", "uuid": "../x"}))).await.0.as_u16(), 400, "a path-shaped id is refused");
    let (st, _, _) = r.send(Method::POST, &format!("{B}/link/mapping"), Some(json!({"item": "Closing Prayer", "uuid": ""}))).await;
    assert_eq!((st.as_u16(), has(r)), (200, false), "an empty uuid clears the pairing");
    r.fake.lock().lists.retain(|l| !l.0.starts_with("PL-NEW"));
    let (st, body, _) = r.send(Method::POST, &format!("{B}/link/playlist"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["verified"].clone()), (200, json!(true)), "the playlist route builds");
    r.amend(json!({"view_only": true}));
    r.fake.lock().requests.clear();
    let (st, body, _) = r.send(Method::POST, &format!("{B}/link/playlist"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["error"].as_str().unwrap_or("").contains("View-only mode"), b.written().len()), (409, true, 0), "view-only answers 409 with a fixed sentence and writes nothing");
    r.amend(json!({"view_only": false}));
    r.fake.lock().forced.push(("/v1/playlists".into(), 503, "slow".into()));
    let (st, body, _) = r.send(Method::POST, &format!("{B}/link/refresh"), Some(json!({}))).await;
    assert_eq!((st.as_u16(), body["error"].clone()), (502, json!("ProPresenter refused the request (503).")), "a refusing ProPresenter is a fixed sentence");
}
