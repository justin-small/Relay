//! Screens and places on a real SQLite file made from the Python migration 0008 (`tests/test_screens.py`'s rules).

use relay_show::screens::{self, Error, Screens};
use serde_json::{json, Value};
use sqlx::sqlite::{SqliteConnectOptions, SqlitePoolOptions};

async fn open() -> (Screens, sqlx::SqlitePool, std::path::PathBuf) {
    let path = std::env::temp_dir().join(format!("relay-show-screens-{}-{}.db", std::process::id(), relay_show::alerts::token_hex(4)));
    let pool = SqlitePoolOptions::new()
        .max_connections(2)
        .connect_with(SqliteConnectOptions::new().filename(&path).create_if_missing(true).foreign_keys(true))
        .await
        .unwrap();
    for sql in [include_str!("../../../app/migrations/0008_screens_places.sql"), include_str!("../../../app/migrations/0005_alert_history.sql")] {
        sqlx::raw_sql(sql).execute(&pool).await.unwrap();
    }
    (Screens::new(pool.clone()), pool, path)
}

#[tokio::test]
async fn adopt_joins_by_name_and_keeps_old_addresses() {
    let (s, pool, path) = open().await;
    screens::check_tables(&pool).await.unwrap();
    let a = s.adopt("board:abc", "Lobby TV", "browser", "10.0.0.5", "lobby", true).await.unwrap();
    assert_eq!((a["name"].clone(), a["address"].clone(), a["last_board"].clone(), a["ref"].clone()), (json!("Lobby TV"), json!("10.0.0.5"), json!("lobby"), json!(format!("screen:relay:{}", a["id"]))));
    let again = s.adopt("board:abc", "", "browser", "", "", true).await.unwrap();
    assert_eq!(again["id"], a["id"], "the same key is the same screen");
    assert_eq!(again["address"], "10.0.0.5", "an empty address keeps the old one");
    let joined = s.adopt("signage:lobby", "lobby tv", "browser", "", "", false).await.unwrap();
    assert_eq!(joined["id"], a["id"], "a display two features call by one name is one screen");
    assert_eq!(s.keys(a["id"].as_i64().unwrap()).await.unwrap(), vec!["board:abc", "signage:lobby"]);
    assert_eq!(s.by_key("signage:lobby").await.unwrap().unwrap()["id"], a["id"]);
    let p = s.adopt("panel:00000000000000000000000000000001", "Booth", "panel", "10.0.0.9", "", true).await.unwrap();
    assert_eq!(p["kind"], "panel");
    let other = s.adopt("alert:foyer", "", "browser", "", "", true).await.unwrap();
    assert_eq!(other["name"], "screen foyer", "no name: made from the key");
    let names = s.names_by_key("signage:").await.unwrap();
    assert_eq!(Value::Object(names), json!({"lobby": "Lobby TV"}));
    assert!(matches!(s.adopt("x:1", "n", "tablet", "", "", true).await, Err(Error::Unavailable(_))));
    pool.close().await;
    let _ = std::fs::remove_file(path);
}

#[tokio::test]
async fn places_rename_merge_and_shows() {
    let (s, pool, path) = open().await;
    let foyer = s.place_add("  Foyer ").await.unwrap();
    assert_eq!(s.place_add("foyer").await.unwrap()["id"], foyer["id"], "a name already taken returns that place");
    assert_eq!(s.place_add("  ").await, Err(Error::Refused("name")));
    let stage = s.place_add("Stage").await.unwrap();
    assert_eq!(s.place_rename(stage["id"].as_i64().unwrap(), "FOYER").await, Err(Error::Refused("name_taken")));
    s.place_rename(stage["id"].as_i64().unwrap(), "Main stage").await.unwrap();
    assert_eq!(s.place_rename(999, "Nope").await, Err(Error::Refused("no_place")));

    let tv = s.adopt("board:tv", "Foyer TV", "browser", "", "", true).await.unwrap();
    let tid = tv["id"].as_i64().unwrap();
    s.set_place(tid, foyer["id"].as_i64()).await.unwrap();
    assert_eq!(s.set_place(tid, Some(999)).await, Err(Error::Refused("no_place")));
    assert_eq!(s.set_place(999, None).await, Err(Error::Refused("no_screen")));
    let places = s.places().await.unwrap();
    assert_eq!(places.iter().map(|p| (p["name"].clone(), p["screens"].clone())).collect::<Vec<_>>(), vec![(json!("Foyer"), json!(1)), (json!("Main stage"), json!(0))]);
    assert_eq!(s.in_place(foyer["id"].as_i64().unwrap()).await.unwrap(), vec![("Foyer TV".to_owned(), String::new())]);
    assert_eq!(screens::place_id(&format!("place:relay:{}", foyer["id"])), foyer["id"].as_i64());
    assert_eq!(screens::place_id("place:other:1"), None);
    assert_eq!(screens::screen_id("screen:relay:7"), Some(7));

    // what a screen shows
    s.set_shows(tid, Some(("board", "lobby")), Some(("signage", "loop")), Some("schedule:relay:2")).await.unwrap();
    assert_eq!(s.set_shows(tid, Some(("board", "")), None, None).await, Err(Error::Refused("shows")));
    assert_eq!(s.set_shows(tid, Some(("movie", "x")), None, None).await, Err(Error::Refused("shows")));
    let row = s.get(tid).await.unwrap().unwrap();
    assert_eq!(screens::showing(&row, |_| false, true), json!({"kind": "board", "ref": "lobby", "why": "all the time"}));
    assert_eq!(screens::showing(&row, |r| r == "schedule:relay:2", true), json!({"kind": "signage", "ref": "loop", "why": "while its schedule is open"}));
    assert_eq!(screens::showing(&row, |_| true, false), json!({"kind": "board", "ref": "lobby", "why": "Signage is off"}), "signage off falls back to the board");
    s.set_hold(tid, "board", "held").await.unwrap();
    let row = s.get(tid).await.unwrap().unwrap();
    assert_eq!(screens::showing(&row, |_| true, true)["why"], "held by hand");
    s.set_hold(tid, "", "").await.unwrap();
    // clearing the during part drops its schedule
    s.set_shows(tid, None, Some(("", "")), None).await.unwrap();
    assert_eq!(s.get(tid).await.unwrap().unwrap()["during_schedule"], "");
    s.forget_board("lobby").await.unwrap();
    assert_eq!(s.get(tid).await.unwrap().unwrap()["normal_kind"], "");

    // renaming to another screen's name merges the two; a panel is renamed on the Panels page
    let other = s.adopt("alert:wall", "Wall", "browser", "", "", true).await.unwrap();
    let merged = s.rename(other["id"].as_i64().unwrap(), "foyer tv", false).await.unwrap();
    assert_eq!(merged, tid, "the screen that had the name keeps it");
    assert!(s.get(other["id"].as_i64().unwrap()).await.unwrap().is_none());
    assert!(s.keys(tid).await.unwrap().contains(&"alert:wall".to_owned()), "and takes the other's keys");
    let panel = s.adopt("panel:00000000000000000000000000000002", "Hall", "panel", "", "", true).await.unwrap();
    assert_eq!(s.rename(panel["id"].as_i64().unwrap(), "x", false).await, Err(Error::Refused("panel_name")));
    s.sync_panel_name(panel["id"].as_i64().unwrap(), "Hall A").await.unwrap();
    assert_eq!(s.get(panel["id"].as_i64().unwrap()).await.unwrap().unwrap()["name"], "Hall A");
    assert_eq!(s.rename(999, "z", false).await, Err(Error::Refused("no_screen")));

    // listing, keys moved and forgotten, deleting a place leaves its screens
    let list = s.listing().await.unwrap();
    assert_eq!(list.iter().find(|r| r["id"] == json!(tid)).unwrap()["place"], "Foyer");
    s.move_key("board:tv", "board:tv2").await.unwrap();
    assert_eq!(s.by_key("board:tv2").await.unwrap().unwrap()["id"], json!(tid));
    s.forget_key("board:tv2").await.unwrap();
    assert!(s.by_key("board:tv2").await.unwrap().is_none());
    s.place_delete(foyer["id"].as_i64().unwrap()).await.unwrap();
    assert_eq!(s.get(tid).await.unwrap().unwrap()["place_id"], Value::Null);
    s.delete(tid).await.unwrap();
    assert_eq!(s.delete(tid).await, Err(Error::Refused("no_screen")));
    assert!(screens::online(&json!({"last_seen": "2023-11-14T22:13:20Z"}), 1_700_000_030.0));
    assert!(!screens::online(&json!({"last_seen": "2023-11-14T22:13:20Z"}), 1_700_000_100.0));
    assert!(!screens::online(&json!({"last_seen": ""}), 1_700_000_000.0));
    pool.close().await;
    let _ = std::fs::remove_file(path);
}
