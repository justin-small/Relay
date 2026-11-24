//! Boards (the rules of `tests/test_boards.py` that need no server): bounded loading, widget settings checked against their
//! definitions, pages, what decides the page shown, templates, create, update, export and import.

use relay_show::boards::{self, BoardError, Choices};
use relay_show::layout;
use relay_show::schedules;
use relay_show::timewin::{self, Date};
use serde_json::{json, Map, Value};

fn cat() -> Map<String, Value> {
    boards::catalogue(&Choices::default(), &[], &[])
}

#[test]
fn stored_boards_are_bounded_on_load() {
    let got = boards::clean_list(&json!([
        {"id": "lobby", "name": "Lobby"}, {"id": "lobby", "name": "Again"}, {"id": "Bad Id"},
        {"id": "x", "name": "", "ratio": "21:9", "items": [{"type": "lighting.cue", "props": {"cue": "a", "n": [1]}}, {"type": "nope!"}]}, "junk"]));
    assert_eq!(got.iter().map(|b| b["id"].clone()).collect::<Vec<_>>(), vec![json!("lobby"), json!("x")], "one per address, bad addresses dropped");
    let x = &got[1];
    assert_eq!((x["name"].clone(), x["ratio"].clone(), x["cols"].clone(), x["rows"].clone()), (json!("Board"), json!("16:9"), json!(12), json!(8)));
    assert_eq!(x["pages"][0]["items"][0]["props"], json!({"cue": "a", "n": null}), "a module widget is kept with plain settings only");
    assert_eq!(x["pages"][0]["items"].as_array().unwrap().len(), 1, "an item with a bad type is dropped");
    assert_eq!((x["pages"][0]["id"].clone(), x["pages"][0]["name"].clone()), (json!("p1"), json!("Page 1")));
    assert_eq!((x["rotation"]["mode"].clone(), x["place"].clone()), (json!("one"), json!("")));
}

#[test]
fn a_widget_definition_is_checked() {
    assert!(boards::clean_definition(&json!({"type": "obs.scene"}), "lighting").is_none(), "its type must start with the module's id");
    assert!(boards::clean_definition(&json!({"type": "a.b"}), "").is_none(), "core types have no dot");
    let d = boards::clean_definition(
        &json!({"type": "lighting.cue", "kind": "rocket", "w": 99, "fields": [{"key": "Bad Key", "type": "text"}, {"key": "cue", "type": "select", "options": [["a", "A"], "x"]}]}),
        "lighting",
    )
    .unwrap();
    assert_eq!((d["kind"].clone(), d["w"].clone()), (json!("text"), json!(24)));
    assert_eq!(d["fields"].as_array().unwrap().iter().map(|f| f["key"].clone()).collect::<Vec<_>>(), vec![json!("cue")]);
    assert_eq!(d["fields"][0]["options"], json!([["a", "A"]]));
}

#[test]
fn laying_a_board_out_checks_each_widget_against_its_definition() {
    let cat = cat();
    for t in ["captions", "clock", "countdown", "text", "link", "stream", "route", "spl", "rta"] {
        assert!(cat.contains_key(t), "{t}");
    }
    let board = boards::create(&[], &json!({"name": "Lobby TV", "ratio": "4:3"}), &cat).unwrap();
    assert_eq!((board["id"].clone(), board["cols"].clone(), board["rows"].clone()), (json!("lobby-tv"), json!(8), json!(6)));
    let long = "x".repeat(500);
    let laid = boards::update(
        &board,
        &json!({"items": [
            {"id": "cap", "type": "captions", "x": 0, "y": 0, "w": 8, "h": 3, "props": {"lang": "KLINGON", "lines": "9"}},
            {"id": "clk", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 2, "props": {"h24": "yes"}},
            {"id": "cd", "type": "countdown", "x": 0, "y": 3, "w": 2, "h": 1, "props": {"at": "25:99"}},
            {"id": "txt", "type": "text", "x": 2, "y": 3, "w": 3, "h": 1, "props": {"text": format!("Hi\n  there  {long}"), "evil": 1}},
            {"id": "cue", "type": "lighting.cue", "x": 5, "y": 3, "w": 2, "h": 1, "props": {"cue": "walk"}, "style": {"bg": "url(javascript:x)", "label": "<b>Walk</b>"}},
            {"id": "lnk", "type": "link", "x": 0, "y": 4, "w": 2, "h": 1, "props": {"board": "lobby-tv-2"}},
            {"id": "bad", "type": "marquee", "x": 0, "y": 5}]}),
        &cat,
    )
    .unwrap();
    let items: std::collections::HashMap<String, Value> = laid["pages"][0]["items"].as_array().unwrap().iter().map(|i| (i["id"].as_str().unwrap().to_owned(), i.clone())).collect();
    assert!(!items.contains_key("bad"), "an unknown core widget is dropped");
    assert_eq!(items["cap"]["props"], json!({"lang": "SOURCE", "lines": "3"}), "a language that is not offered falls back to the original speech");
    assert_eq!((items["clk"]["x"].clone(), items["clk"]["y"].clone()), (json!(0), json!(3)), "an overlapping widget moves to the first free cells");
    let cells: Vec<(i64, i64)> = items.values().flat_map(|i| (0..i["w"].as_i64().unwrap()).flat_map(move |dx| (0..i["h"].as_i64().unwrap()).map(move |dy| (i["x"].as_i64().unwrap() + dx, i["y"].as_i64().unwrap() + dy)))).collect();
    let unique: std::collections::HashSet<_> = cells.iter().collect();
    assert_eq!(cells.len(), unique.len(), "nothing overlaps");
    assert_eq!(items["cd"]["props"]["at"], "10:30", "a bad time falls back");
    let t = items["txt"]["props"]["text"].as_str().unwrap();
    assert!(items["txt"]["props"].as_object().unwrap().len() == 1 && t.starts_with("Hi\nthere") && t.chars().count() <= boards::TEXT_MAX, "plain lines, cut, unknown settings dropped: {t}");
    assert_eq!(items["cue"]["style"]["bg"], "#18181b", "a colour cannot carry anything but a colour");
    assert_eq!(items["cue"]["style"]["label"], "<b>Walk</b>", "a label stays text");
    assert_eq!(items["cue"]["props"], json!({"cue": "walk"}), "a module widget keeps its plain settings");
    assert_eq!(items["lnk"]["props"], json!({"board": ""}), "a link is kept only to a board that is on the list");
    let with_boards = boards::catalogue(&Choices::default(), &[], &[json!({"id": "lobby-tv-2", "name": "B"})]);
    let again = boards::update(&laid, &json!({"name": "Front lobby"}), &with_boards).unwrap();
    assert_eq!(again["id"], "lobby-tv", "the address never changes with the name");
    assert_eq!(boards::update(&laid, &json!({"name": "  "}), &cat), Err(BoardError("Give the board a name.".into())));
    assert_eq!(boards::find_item(&laid, "cue").unwrap()["type"], "lighting.cue");
}

#[test]
fn select_choices_that_come_from_outside_are_kept() {
    let cat = cat();
    let b = boards::create(&[], &json!({"name": "Snd", "pages": [{"items": [{"id": "s1", "type": "spl", "x": 0, "y": 0, "w": 3, "h": 2,
        "props": {"weighting": "Z", "response": "fast", "source": "dante:main"}}]}]}), &cat)
    .unwrap();
    assert_eq!(b["pages"][0]["items"][0]["props"], json!({"weighting": "A", "response": "fast", "source": "dante:main"}), "a source a module registered is not lost on save");
}

#[test]
fn naming_pages_and_rotations() {
    let cat = cat();
    let board = boards::create(&[], &json!({"name": "Pages"}), &cat).unwrap();
    let clock = json!({"id": "c1", "type": "clock", "x": 0, "y": 0, "w": 2, "h": 2, "props": {}});
    let mut pages = vec![json!({"id": "p1", "name": "Welcome", "items": [clock.clone()]}), json!({"name": "  Captions  ", "items": [clock.clone(), {"id": "cue2", "type": "lighting.cue", "x": 2, "y": 0, "w": 2, "h": 1}]}), json!({"id": "BAD ID", "name": "", "items": []})];
    pages.extend((0..20).map(|n| json!({"name": format!("Extra {n}")})));
    let b = boards::update(&board, &json!({"pages": pages}), &cat).unwrap();
    assert_eq!(b["pages"].as_array().unwrap().len(), boards::MAX_PAGES);
    assert_eq!(b["pages"].as_array().unwrap().iter().take(3).map(|p| (p["id"].clone(), p["name"].clone())).collect::<Vec<_>>(),
        vec![(json!("p1"), json!("Welcome")), (json!("p2"), json!("Captions")), (json!("p3"), json!("Page 3"))]);
    let ids: Vec<&str> = boards::all_items(&b).iter().map(|i| i["id"].as_str().unwrap()).collect();
    let unique: std::collections::HashSet<_> = ids.iter().collect();
    assert_eq!(ids.len(), unique.len(), "widget ids are unique across the whole board");

    let three = boards::update(&b, &json!({"pages": b["pages"].as_array().unwrap()[..3]}), &cat).unwrap();
    let b = boards::update(&three, &json!({"rotation": {"mode": "schedule", "every": 2, "pages": ["p2", "nope", "p1"],
        "schedule": [{"days": "sun", "from": "9:00", "to": "10:30", "show": "p2"}, {"days": "weekdays", "from": "22:00", "to": "02:00", "show": "rotate"},
            {"days": "someday", "from": "99:99", "to": "x", "show": "gone"}],
        "other": "p3", "follow": {"rules": [{"match": "group", "value": " Sermon ", "page": "p2"}, {"match": "x", "value": ""}], "none": "gone"}}}), &cat)
    .unwrap();
    let rot = &b["rotation"];
    assert_eq!((rot["every"].clone(), rot["pages"].clone()), (json!(5), json!(["p2", "p1"])));
    assert_eq!(rot["schedule"].as_array().unwrap().iter().map(|x| (x["days"].clone(), x["from"].clone(), x["to"].clone(), x["show"].clone())).collect::<Vec<_>>(),
        vec![(json!("sun"), json!("09:00"), json!("10:30"), json!("p2")), (json!("weekdays"), json!("22:00"), json!("02:00"), json!("rotate")), (json!("all"), json!("09:00"), json!("10:00"), json!("p1"))]);
    assert_eq!((rot["follow"]["rules"].as_array().unwrap().len(), rot["follow"]["none"].clone()), (1, json!("keep")));

    let sun = Date::new(2026, 11, 22);
    assert_eq!(sun.weekday(), 0, "2026-11-22 is a Sunday");
    let (m930, m1100) = (9 * 60 + 30, 11 * 60);
    let got = boards::current(&b, sun, m930, None, None, None);
    assert_eq!((got["page"].clone(), got["rotate"].clone(), got["why"].clone()), (json!("p2"), Value::Null, json!("Schedule: Captions until 10:30, then Page 3")));
    assert_eq!(boards::current(&b, sun, m1100, None, None, None)["page"], "p3", "outside every row: the other page");
    assert!(!boards::current(&b, Date::new(2026, 11, 24), 60, None, None, None)["rotate"].is_null(), "a row past midnight belongs to the day it started");
    assert_eq!(boards::current(&b, Date::new(2026, 11, 23), 60, None, None, None)["page"], "p3");
    assert_eq!(boards::current(&b, Date::new(2026, 11, 23), 23 * 60, None, None, None)["rotate"], json!({"pages": ["p2", "p1"], "every": 5}));

    // a row may name a schedule instead of its own days and times
    let named_ref = "schedule:relay:svc1";
    let mut named = b.clone();
    named["rotation"]["schedule"] = json!([{"days": "all", "from": "09:00", "to": "10:00", "schedule": named_ref, "show": "p2"}]);
    let open: &dyn Fn(&str, Date, i64) -> bool = &|r, _, m| r == named_ref && m / 60 == 11;
    let got = boards::current(&named, sun, m1100, None, None, Some(open));
    assert_eq!((got["page"].clone(), got["key"].clone()), (json!("p2"), json!("s0")));
    assert_eq!(boards::current(&named, sun, m930, None, None, Some(open))["page"], "p3");
    assert_eq!(boards::current(&named, sun, m1100, None, None, None)["page"], "p3", "with no way to ask, a named row never covers");
    assert_eq!(boards::clean_rotation(&json!({"mode": "schedule", "schedule": [{"schedule": "bogus", "show": "p1"}, {"schedule": named_ref, "show": "p1"}]}), &["p1".to_owned()])["schedule"][0]["schedule"], "");

    // following
    let mut f = b.clone();
    f["rotation"]["mode"] = json!("follow");
    assert_eq!(boards::current(&f, sun, m1100, Some(&json!({"group": "sermon"})), None, None)["page"], "p2");
    assert_eq!(boards::current(&f, sun, m1100, Some(&json!({"group": "Worship"})), Some("p3"), None)["page"], "p3", "nothing matches: the page stays");
    assert_eq!(boards::current(&f, sun, m1100, None, Some("p2"), None)["key"], "follow-off");
    let mut one = b.clone();
    one["rotation"]["mode"] = json!("one");
    assert_eq!(boards::current(&one, sun, m930, None, None, None)["page"], "p1");
    assert_eq!(boards::show_label(&b, "rotate", false), "Rotate 1 and 2, every 5 s");
}

#[test]
fn templates_create_export_import_and_screen_names() {
    let cat = cat();
    let t = boards::templates(&cat);
    assert_eq!(t.iter().map(|x| x["id"].clone()).collect::<Vec<_>>(), ["lobby", "stage", "captions", "buttons", "sound", "blank"].map(|s| json!(s)).to_vec());
    let stage = boards::create(&[], &json!({"name": "Stage", "template": "stage", "place": "  Stage  "}), &cat).unwrap();
    assert_eq!(stage["pages"][0]["items"].as_array().unwrap().iter().map(|i| i["type"].clone()).collect::<Vec<_>>(), vec![json!("clock"), json!("countdown"), json!("captions")]);
    assert_eq!(stage["place"], "Stage");
    assert_eq!(boards::create(&[], &json!({"name": "Pad", "template": "buttons"}), &cat).unwrap()["ratio"], "4:3", "a template's shape is used unless another is asked for");
    assert_eq!(boards::create(&[], &json!({"name": "Odd", "template": "nope"}), &cat).unwrap()["pages"][0]["items"], json!([]), "an unknown template is a blank board");
    assert_eq!(boards::create(&[], &json!({"name": "  "}), &cat), Err(BoardError("Give the board a name.".into())));
    let first = boards::create(&[], &json!({"name": "Lobby TV"}), &cat).unwrap();
    assert_eq!(boards::create(std::slice::from_ref(&first), &json!({"name": "Lobby TV"}), &cat).unwrap()["id"], "lobby-tv-2");
    assert_eq!(boards::slug_for("  Café & Bar!! ", &[]), "caf-bar");
    assert_eq!(boards::slug_for("???", &["board".to_owned()]), "board-2");

    let data = boards::export(&stage);
    assert!(data["relay_board"] == 2 && data.get("id").is_none() && data.get("screen_names").is_none());
    let imported = boards::import_board(std::slice::from_ref(&stage), &data, &cat).unwrap();
    assert_eq!(imported["id"], "stage-2", "an import gets an address of its own");
    let old = json!({"relay_board": 1, "name": "Old", "ratio": "16:9", "items": [{"type": "clock", "x": 0, "y": 0, "w": 2, "h": 2}]});
    assert_eq!(boards::import_board(&[], &old, &cat).unwrap()["pages"][0]["items"].as_array().unwrap().len(), 1, "a file saved before pages imports onto page 1");
    assert_eq!(boards::import_board(&[], &json!({"name": "x"}), &cat), Err(BoardError("That is not a Relay board file.".into())));

    let named = boards::name_screen(&stage, "scr-AAAA1111", &json!("  Foyer   TV left ")).unwrap();
    assert_eq!(named["screen_names"], json!({"scr-AAAA1111": "Foyer TV left"}));
    assert_eq!(boards::name_screen(&stage, "x", &json!("TV")), Err(BoardError("That is not a screen.".into())));
    assert_eq!(boards::name_screen(&named, "scr-AAAA1111", &json!("")).unwrap()["screen_names"], json!({}));
}

#[test]
fn layout_time_and_schedules() {
    assert_eq!(layout::text(&json!("  a \n b  "), 10), "a b");
    assert_eq!(layout::clean_style(&json!({"bg": "#ABCDEF", "shape": "star", "icon": "mic", "color": "red"}), None)["bg"], "#abcdef");
    assert_eq!(layout::clean_style(&json!({"shape": "star"}), None)["shape"], "rounded");
    let placed = layout::place(vec![json!({"x": 0, "y": 0, "w": 2, "h": 2}), json!({"x": 0, "y": 0, "w": 2, "h": 2}), json!({"x": 0, "y": 0, "w": 9, "h": 9}), json!({"x": 1, "y": 1})], 3, 2);
    assert_eq!((placed[1]["x"].clone(), placed[1]["y"].clone(), placed[1]["w"].clone()), (json!(2), json!(0), json!(1)), "shrunk to one cell in the first free spot");
    assert_eq!(placed.len(), 3, "no room left for the fourth: dropped");
    assert_eq!(layout::number(&json!(true), 0, 9, 4), 4);
    assert_eq!(timewin::clean_clock_str("9:30", true), Some("09:30".into()));
    assert_eq!(timewin::clean_clock_str("9:30", false), None);
    assert_eq!(timewin::clean_clock_str("24:00", true), None);
    assert_eq!(Date::parse("2026-02-30"), None);
    assert_eq!(Date::parse("2024-02-29").map(Date::weekday), Some(4));
    assert_eq!(Date::new(1970, 1, 1).weekday(), 4);

    // a named schedule: Sundays 10:00 to 12:00 Eastern, and an overnight one
    let sunday = json!({"id": "svc1", "enabled": true, "windows": [{"repeat": "weekly", "days": [0], "start_date": null, "end_date": null, "start_time": "10:00", "stop_time": "12:00"}]});
    let sun = Date::new(2026, 11, 22);
    assert!(schedules::is_open_local(&sunday, sun, 10 * 60, "America/New_York"));
    assert!(!schedules::is_open_local(&sunday, sun, 12 * 60, "America/New_York"));
    assert!(!schedules::is_open_local(&sunday, Date::new(2026, 11, 23), 11 * 60, "America/New_York"), "not on a Monday");
    let night = json!({"id": "n", "windows": [{"repeat": "weekly", "days": [6], "start_time": "22:00", "stop_time": "02:00"}]});
    assert!(schedules::is_open_local(&night, Date::new(2026, 11, 22), 60, "America/New_York"), "Saturday's window runs into Sunday");
    assert!(!schedules::is_open_local(&night, Date::new(2026, 11, 23), 60, "America/New_York"));
    let once = json!({"id": "o", "windows": [{"repeat": "once", "start_date": "2026-12-25", "start_time": "09:00", "stop_time": "10:00"}]});
    assert!(schedules::is_open_local(&once, Date::new(2026, 12, 25), 9 * 60 + 30, "America/Chicago"));
    assert!(!schedules::is_open_local(&once, Date::new(2026, 12, 26), 9 * 60 + 30, "America/Chicago"));
    let ranged = json!({"id": "r", "windows": [{"repeat": "weekly", "days": [0], "start_date": "2026-12-01", "end_date": "2026-12-31", "start_time": "10:00", "stop_time": "11:00"}]});
    assert!(!schedules::is_open_local(&ranged, sun, 10 * 60 + 5, "America/New_York"), "before its start date");
    // spring forward 2027-03-14 (a Sunday): a window at 02:30 does not exist, so it opens at 03:00 wall time
    let gap = json!({"id": "g", "windows": [{"repeat": "weekly", "days": [0], "start_time": "02:30", "stop_time": "04:00"}]});
    let spring = Date::new(2027, 3, 14);
    assert!(schedules::is_open_local(&gap, spring, 3 * 60 + 30, "America/New_York"));
    assert!(!schedules::is_open_local(&gap, spring, 60, "America/New_York"));
    assert_eq!(schedules::ref_id("schedule:relay:a1_b-2"), Some("a1_b-2"));
    assert_eq!(schedules::ref_id("schedule:relay:"), None);
    let all = json!([{"id": "svc1"}]);
    assert!(schedules::find(&all, "schedule:relay:svc1").is_some() && schedules::find(&all, "svc1").is_some() && schedules::find(&all, "x").is_none());
    assert_eq!(schedules::clean_zone("Europe/Paris"), "America/New_York");
}
