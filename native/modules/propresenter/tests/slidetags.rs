//! What `tests/test_slidetags.py` checks (#138, stage 5): the parser, the deck-wide fallback, the settle (debounce) and no-re-fire rules, and that nothing
//! fires while tags are off. The watcher is driven directly with `observe`, on a real runtime with short settle times; the follower reading notes from a
//! fake ProPresenter is in `follower.rs` and `control.rs`.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use relay_module_propresenter::slidetags::{actions_table, parse, parse_deck, resolve, Deck, Observed, Tag, Tags, DECKS_KEEP, HISTORY_KEEP, VALUE_MAX};
use serde_json::{json, Value};

fn tag(action: &str, value: Option<&str>) -> Tag {
    Tag { action: action.to_owned(), value: value.map(str::to_owned) }
}

fn slide_of(notes: &str) -> Vec<Tag> {
    parse(notes).slide
}

#[test]
fn parsing() {
    assert_eq!(slide_of("[relay:lyrics]"), [tag("lyrics", None)], "a plain tag");
    assert_eq!(slide_of("[relay:alert=Sound desk needed]"), [tag("alert", Some("Sound desk needed"))], "a tag with a value");
    assert_eq!(slide_of("[RELAY:Capture-Start] [Relay:LYRICS]"), [tag("capture-start", None), tag("lyrics", None)], "action names are not case-sensitive");
    assert_eq!(slide_of("[relay:capture-start][relay:lyrics][relay:lang=es]"), [tag("capture-start", None), tag("lyrics", None), tag("lang", Some("SPANISH"))], "several to a slide, in the order written");
    assert_eq!(slide_of("Pause here.\n[relay:lyrics]\nthen sing [x] [relay] relay:lyrics"), [tag("lyrics", None)], "anything else in the notes is ignored");
    assert_eq!(slide_of("Start the band [relay:captions] now"), [tag("captions", None)], "a tag mid-sentence");
    assert_eq!(slide_of("[relay: lyrics ] [relay:alert =  Sound   desk ]"), [tag("lyrics", None), tag("alert", Some("Sound desk"))], "spaces around the name and value are trimmed");
    assert_eq!(slide_of("[relay:alert=Keys: mic 2, please!]"), [tag("alert", Some("Keys: mic 2, please!"))], "a value may hold spaces and punctuation");
    assert_eq!(slide_of("[relay:alert=Check [mic] now]"), [tag("alert", Some("Check [mic] now"))], "a value may hold a balanced [pair]");
    assert_eq!(slide_of("[relay:alert=Level 3\\] reached]"), [tag("alert", Some("Level 3] reached"))], "a value may hold an escaped ]");
    assert_eq!(slide_of("[relay:alert=a=b]"), [tag("alert", Some("a=b"))], "a value may hold = signs");
    assert_eq!(slide_of("[relay:lyrics][relay:lyrics]"), [tag("lyrics", None), tag("lyrics", None)], "a repeated tag is kept in the parse (resolve de-duplicates)");
    assert!(slide_of("").is_empty(), "empty notes");
    assert!(slide_of("x[relayx:lyrics] [notrelay:lyrics]").is_empty(), "a relay tag is not read from the middle of another word");

    let p = parse("[relay:lyrics");
    assert_eq!((p.slide.len(), p.warnings.len()), (0, 1), "a tag with no closing ] is skipped and said");
    let p = parse("[relay:alert=never closed\n[relay:lyrics]");
    assert_eq!((p.slide, p.warnings.len()), (vec![tag("lyrics", None)], 1), "a value that runs to the end of its line is skipped; the next line is still read");
    let p = parse("[relay:] [relay:=x] [relay:two words]");
    assert_eq!((p.slide.len(), p.warnings.len()), (0, 3), "tags with no usable action name are skipped with a warning each");
    let p = parse("[relay:alert=[unbalanced]");
    assert_eq!((p.slide.len(), p.warnings.len()), (0, 1), "an unbalanced [ inside a value never closes");
    let p = parse("[relay:dance]");
    assert_eq!((p.slide.len(), p.warnings), (0, vec!["Unknown action \"dance\" ignored.".to_owned()]), "an unknown action is ignored and listed");
    let p = parse("[relay:lang]");
    assert!(p.slide.is_empty() && p.warnings[0].contains("needs a value"), "a missing value is a warning: {:?}", p.warnings);
    let p = parse("[relay:alert=]");
    assert!(p.slide.is_empty() && p.warnings[0].contains("needs a value"), "an empty value is a warning");
    let p = parse("[relay:capture-start=now]");
    assert!(p.slide.is_empty() && p.warnings[0].contains("takes no value"), "a value on an action that takes none is a warning and the tag does not run");
    assert_eq!(slide_of("[relay:lang=es]"), [tag("lang", Some("SPANISH"))], "lang accepts a code");
    assert_eq!(slide_of("[relay:lang=Portuguese] [relay:lang=FRENCH]"), [tag("lang", Some("PORTUGUESE")), tag("lang", Some("FRENCH"))], "lang accepts a name, any capitals");
    assert_eq!(slide_of("[relay:lang=hmn]"), [tag("lang", Some("HMONG"))], "lang accepts a code that is not two letters (hmn)");
    let p = parse("[relay:lang=klingon]");
    assert!(p.slide.is_empty() && p.warnings[0].contains("not a language"), "lang with a language Relay does not have is a warning");
    let p = parse("[relay-deck:lyrics] [relay:captions]");
    assert_eq!((p.deck, p.slide), (vec![tag("lyrics", None)], vec![tag("captions", None)]), "deck tags and slide tags are told apart");
    assert_eq!(slide_of(&format!("[relay:alert={}]", "x".repeat(500)))[0].value.as_ref().map(String::len), Some(VALUE_MAX), "the long value is cut");
    assert!(parse(&format!("{}[relay:lyrics]", "a".repeat(30000))).slide.is_empty(), "a huge notes box is only read so far");
}

#[test]
fn deck_wide_defaults() {
    let notes: Vec<String> = ["Intro [relay-deck:lyrics]", "", "Last verse [relay-deck:lyrics][relay-deck:lang=es] [relay-deck:oops]"].map(str::to_owned).to_vec();
    let deck = parse_deck(&notes);
    assert_eq!(deck.tags, [tag("lyrics", None), tag("lang", Some("SPANISH"))], "deck tags are collected from every slide, each once");
    assert!(deck.warnings.iter().any(|w| w.starts_with("Slide 3:")), "a deck warning names the slide");
    assert_eq!(resolve(&[], &deck.tags), [tag("lyrics", None), tag("lang", Some("SPANISH"))], "a slide with no tag of its own gets the deck's");
    assert_eq!(resolve(&[tag("captions", None)], &deck.tags), [tag("captions", None)], "a slide with its own tag does not");
    assert_eq!(resolve(&[tag("lyrics", None), tag("lyrics", None)], &[]), [tag("lyrics", None)], "resolve drops repeats");
    assert!(resolve(&[], &[]).is_empty(), "no tags anywhere resolves to nothing");

    let t = Tags::default();
    let d1 = t.deck_for("U1", &notes);
    let d2 = t.deck_for("U1", &notes.clone());
    assert!(Arc::ptr_eq(&d1, &d2) && t.deck_reads() == 1, "a presentation's notes are parsed once, then served from the cache");
    let mut more = notes.clone();
    more.push("[relay-deck:captions]".into());
    t.deck_for("U1", &more);
    assert_eq!(t.deck_reads(), 2, "changed notes are parsed again");
    t.deck_for("U2", &notes);
    assert_eq!(t.deck_reads(), 3, "another presentation with the same notes is its own entry");
    for i in 0..DECKS_KEEP + 5 {
        t.deck_for(&format!("X{i}"), &[format!("[relay-deck:lyrics] {i}")]);
    }
    assert_eq!(t.deck_reads() as usize, 3 + DECKS_KEEP + 5, "each new one was read once");
    let again = t.deck_for("U2", &notes);
    assert!(Arc::strong_count(&again) >= 1);
    assert_eq!(t.deck_reads() as usize, 3 + DECKS_KEEP + 6, "the cache is bounded: an old one was let go and is read again");
}

// ---------------------------------------------------------------------------------------------------------------- the watcher
type Calls = Arc<Mutex<Vec<(String, Option<String>)>>>;

/// A `Tags` with every action recording a call, and a way to say what the follower would.
struct Harness {
    t: Arc<Tags>,
    calls: Calls,
    settle: f64,
}

impl Harness {
    fn new() -> Self {
        let t = Tags::new();
        let calls: Calls = Arc::default();
        for (name, _) in actions_table() {
            let c = Arc::clone(&calls);
            let n = name.clone();
            t.set_action(&name, Arc::new(move |value: Option<String>| {
                let (c, n) = (Arc::clone(&c), n.clone());
                Box::pin(async move {
                    c.lock().unwrap().push((n.clone(), value));
                    Ok(format!("{n} done"))
                })
            }));
        }
        Self { t, calls, settle: 0.12 }
    }

    fn see(&self, uuid: Option<&str>, index: Option<i64>, notes: &str, deck: Option<&Deck>, enabled: bool) {
        self.t.observe(&Observed { enabled, settle_s: self.settle, uuid, title: "Song", index, notes, deck });
    }

    fn slide(&self, index: i64, notes: &str) {
        self.see(Some("U"), Some(index), notes, None, true);
    }

    fn with_deck(&self, index: i64, notes: &str, deck: &Deck) {
        self.see(Some("U"), Some(index), notes, Some(deck), true);
    }

    /// The settle time and a little more, so what was going to run has.
    async fn settled(&self) {
        tokio::time::sleep(Duration::from_millis(270)).await; // allow-sleep: the settle time is what is being measured
    }

    fn calls(&self) -> Vec<(String, Option<String>)> {
        self.calls.lock().unwrap().clone()
    }
}

fn call(name: &str, value: Option<&str>) -> (String, Option<String>) {
    (name.to_owned(), value.map(str::to_owned))
}

#[tokio::test]
async fn settle_debounces_a_fast_run_of_slides() {
    let h = Harness::new();
    h.slide(0, "[relay:capture-start]");
    tokio::time::sleep(Duration::from_millis(40)).await; // allow-sleep: shows nothing runs before the slide has settled
    assert!(h.calls().is_empty(), "nothing runs before the slide has settled");
    let seen = h.t.state()["seen"].clone();
    assert!(seen["settling"] == true && seen["resolved"] == json!(["capture-start"]), "the slide and its tag are shown straight away, marked as settling");
    h.settled().await;
    assert_eq!(h.calls(), [call("capture-start", None)], "it runs once the slide has stayed");
    assert_eq!(h.t.state()["seen"]["settling"], false, "and is no longer settling");

    let h = Harness::new();
    for i in 0..8 {
        h.slide(i, &format!("[relay:alert=Slide {i}]")); // a fast run, each slide gone before the settle time
        tokio::time::sleep(Duration::from_millis(30)).await; // allow-sleep: slides arrive faster than the settle time
    }
    h.settled().await;
    assert_eq!(h.calls(), [call("alert", Some("Slide 7"))], "a fast run of slides runs only the one it stopped on");

    let h = Harness::new();
    h.slide(0, "[relay:capture-start]");
    tokio::time::sleep(Duration::from_millis(50)).await; // allow-sleep: the slide is gone before it settles
    h.slide(1, "");
    h.settled().await;
    assert!(h.calls().is_empty(), "a slide that was gone before the settle time runs nothing");
}

#[tokio::test]
async fn the_same_set_runs_once_and_a_different_one_runs() {
    let deck = parse_deck(&["[relay-deck:lyrics]".to_owned(), String::new(), String::new()]);
    let h = Harness::new();
    h.with_deck(0, "", &deck);
    h.settled().await;
    for i in [1, 2, 1, 0] {
        h.with_deck(i, "", &deck);
        h.settled().await;
    }
    assert_eq!(h.calls(), [call("lyrics", None)], "a deck-wide tag runs once on entry, not at every slide");

    let h = Harness::new();
    h.slide(0, "[relay:lyrics]");
    h.settled().await;
    h.slide(1, "[relay:lyrics]");
    h.settled().await;
    assert_eq!(h.calls(), [call("lyrics", None)], "two slides that each carry the same tag run it once");
    h.slide(2, "[relay:captions]");
    h.settled().await;
    assert_eq!(h.calls(), [call("lyrics", None), call("captions", None)], "a different set runs");
    h.slide(3, "");
    h.settled().await;
    h.slide(4, "[relay:captions]");
    h.settled().await;
    assert_eq!(h.calls()[2..], [call("captions", None)], "the same tag after a slide with none runs again");

    let h = Harness::new();
    h.slide(0, "[relay:lyrics]");
    tokio::time::sleep(Duration::from_millis(40)).await; // allow-sleep: the slide has not settled yet
    h.slide(1, "");
    tokio::time::sleep(Duration::from_millis(40)).await; // allow-sleep: neither has this one
    h.slide(2, "[relay:lyrics]");
    h.settled().await;
    assert_eq!(h.calls(), [call("lyrics", None)], "a slide that never settled does not count as the set that last ran");

    let h = Harness::new();
    h.slide(0, "[relay:lyrics][relay:lang=fr]");
    h.settled().await;
    h.slide(1, "[relay:lang=fr][relay:lyrics]");
    h.settled().await;
    let order: Vec<String> = h.calls().into_iter().map(|c| c.0).collect();
    assert_eq!(order, ["lyrics", "lang", "lang", "lyrics"], "the same actions in another order are another sequence");
}

#[tokio::test]
async fn the_deck_is_the_fallback_while_moving() {
    let deck = parse_deck(&["[relay-deck:lyrics]".to_owned(), "[relay-deck:lang=es]".to_owned(), String::new()]);
    let h = Harness::new();
    h.with_deck(0, "", &deck);
    h.settled().await;
    h.with_deck(1, "[relay:captions]", &deck);
    h.settled().await;
    h.with_deck(2, "", &deck);
    h.settled().await;
    assert_eq!(h.calls(), [call("lyrics", None), call("lang", Some("SPANISH")), call("captions", None), call("lyrics", None), call("lang", Some("SPANISH"))], "the deck's set, the slide's own, then the deck's again");
    assert_eq!(h.t.state()["seen"]["deck_tags"], json!(["lyrics", "lang=SPANISH"]), "the admin state shows deck tags and warnings");

    let h = Harness::new();
    h.slide(0, "Go [relay-deck:lyrics]");
    h.settled().await;
    assert_eq!(h.calls(), [call("lyrics", None)], "a deck tag on the live slide counts before the deck has been read");
}

#[tokio::test]
async fn off_and_nothing_live() {
    let h = Harness::new();
    h.see(Some("U"), Some(0), "[relay:capture-start]", None, false);
    h.settled().await;
    assert!(h.calls().is_empty(), "tags off: nothing fires");
    assert_eq!(h.t.state()["seen"], Value::Null, "tags off: nothing is shown as seen");
    h.slide(0, "[relay:capture-start]");
    tokio::time::sleep(Duration::from_millis(40)).await; // allow-sleep: the slide is settling when tags are switched off
    h.see(Some("U"), Some(0), "[relay:capture-start]", None, false);
    h.settled().await;
    assert!(h.calls().is_empty(), "switched off while a slide is settling: it does not fire");
    h.slide(0, "[relay:capture-start]");
    h.settled().await;
    assert_eq!(h.calls(), [call("capture-start", None)], "switched back on: the slide is read afresh");

    let h = Harness::new();
    h.slide(0, "[relay:lyrics]");
    h.settled().await;
    h.see(None, None, "", None, true);
    h.settled().await;
    h.slide(0, "[relay:lyrics]");
    h.settled().await;
    assert_eq!(h.calls(), [call("lyrics", None), call("lyrics", None)], "nothing live (ProPresenter cleared) is a set of its own, so the tag runs again after it");
}

#[tokio::test]
async fn results_and_failures_are_kept_for_the_admin_page() {
    let h = Harness::new();
    h.t.set_action("capture-start", Arc::new(|_v: Option<String>| Box::pin(async { Err("No OpenAI API key set.".to_owned()) })));
    h.slide(0, "[relay:capture-start][relay:lyrics]");
    h.settled().await;
    let fired = h.t.state()["fired"].clone();
    let rows: Vec<(String, bool)> = fired.as_array().unwrap().iter().rev().map(|r| (r["action"].as_str().unwrap().to_owned(), r["ok"].as_bool().unwrap())).collect();
    assert_eq!(rows, [("capture-start".to_owned(), false), ("lyrics".to_owned(), true)], "a failing action is recorded and the next one still runs");
    assert_eq!(fired[1]["result"], "No OpenAI API key set.", "the reason is kept for admin");
    h.t.clear_actions();
    h.slide(1, "[relay:lyrics]");
    h.settled().await;
    let fired = h.t.state()["fired"].clone();
    assert_eq!((fired[0]["ok"].clone(), fired[0]["action"].clone()), (json!(false), json!("lyrics")), "an action with nothing wired is recorded, not raised");
    assert_eq!(fired[0]["result"], "Relay has nothing wired to that action.");
    for i in 0..HISTORY_KEEP + 5 {
        h.slide(100 + i as i64, "[relay:lyrics]");
        h.settled().await;
        if i > 2 {
            break;
        }
    }
    assert!(h.t.state()["fired"].as_array().unwrap().len() <= HISTORY_KEEP, "history is bounded");
}
