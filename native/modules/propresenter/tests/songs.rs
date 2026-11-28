//! What `tests/test_ppfile.py`, `test_ppsongparse.py` and `test_ppsongs.py` check (#497, #633): a song ProPresenter exported is read the way the program
//! shows it, a song Relay builds reads back the same, a pasted lyric sheet or a PDF becomes sections of slides, and a request becomes files for the helper.
//! The helper is the capability the server provides (`core.helpers`), held in memory here; the helper program has its own tests.

mod fake;

use std::sync::{Arc, Mutex};

use axum::http::Method;
use base64::Engine;
use fake::{anonymous, rig, send_to};
use prost::Message;
use relay_core::caps::{HelperError, Helpers, HelpersCap, HELPERS};
use relay_core::future::BoxFuture;
use relay_module_propresenter::ppfile::{self as pf, FileError, Section, Slide, SlideAction, Song};
use relay_module_propresenter::songparse as sp;
use relay_module_propresenter::songs;
use serde_json::{json, Value};

fn b64(data: &[u8]) -> String {
    base64::engine::general_purpose::STANDARD.encode(data)
}

fn exported() -> Vec<u8> {
    std::fs::read(std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../tests/fixtures/pp/exported-song.pro")).expect("the exported sample is in tests/fixtures/pp")
}

fn slides(texts: &[&str]) -> Vec<Slide> {
    texts.iter().map(|t| Slide::text(t)).collect()
}

// ------------------------------------------------------------------------------------------------------------------------ the file
#[test]
fn a_song_propresenter_wrote_is_read_the_way_the_program_shows_it() {
    let s = pf::parse_song(&exported()).unwrap();
    assert_eq!(s.name, "Exported sample");
    assert_eq!(s.sections.iter().map(|x| x.name.as_str()).collect::<Vec<_>>(), ["Chorus", "Bridge"], "sections in order");
    assert_eq!(s.sections.iter().map(|x| x.slides.len()).collect::<Vec<_>>(), [2, 3], "slides per section");
    assert_eq!(s.sections[0].slides[0].text, "Sample line 1\nSecond line 1", "slide text, lines kept");
    assert_eq!(s.sections[0].colour, Some((1.0, 0.0, 0.0)), "group colour read");
    assert!(s.lyrics().contains("Bridge") && s.lyrics().contains("Sample line 5"), "lyrics() joins every section");
}

#[test]
fn rtf_text_decodes_escapes_and_round_trips() {
    assert_eq!(pf::rtf_to_text(br"{\rtf1\ansi{\fonttbl\f0 Arial;}\pard A\'e9 b\line c\par d \u233? e}"), "A\u{e9} b\nc\nd \u{e9} e", "escapes");
    let rtf = pf::text_to_rtf(b"{\\rtf1\\strokec0 old}", "a{b}\\c\nd \u{e9}");
    assert_eq!(pf::rtf_to_text(&rtf), "a{b}\\c\nd \u{e9}", "text_to_rtf round-trips braces, backslash, accents");
}

fn built() -> Song {
    Song {
        name: "Built song".into(),
        ccli_number: 7_654_321,
        author: "A. Writer".into(),
        publisher: "Pub".into(),
        copyright_year: 2001,
        sections: vec![Section::new("Verse 1", slides(&["One\nTwo", "Three\nFour"])), Section::new("Chorus", slides(&["Sing \u{e9}"]))],
        arrangements: vec![("Default".into(), ["Verse 1", "Chorus", "Verse 1"].map(str::to_owned).to_vec())],
        ..Song::default()
    }
}

#[test]
fn a_song_relay_builds_reads_back_the_same_and_every_build_has_its_own_identity() {
    let song = built();
    let data = pf::build_song(&song).unwrap();
    let r = pf::parse_song(&data).unwrap();
    assert_eq!(r.name, "Built song");
    let sections: Vec<(String, Vec<String>)> = r.sections.iter().map(|x| (x.name.clone(), x.slides.iter().map(|s| s.text.clone()).collect())).collect();
    assert_eq!(sections, [("Verse 1".to_owned(), vec!["One\nTwo".to_owned(), "Three\nFour".to_owned()]), ("Chorus".to_owned(), vec!["Sing \u{e9}".to_owned()])]);
    assert_eq!(r.arrangements, song.arrangements, "arrangement");
    assert_eq!((r.ccli_number, r.author.as_str(), r.publisher.as_str(), r.copyright_year), (7_654_321, "A. Writer", "Pub", 2001), "CCLI");
    assert_ne!(pf::parse_song(&pf::build_song(&song).unwrap()).unwrap().uuid, r.uuid, "every build gets its own identity");
    // no two slides or actions share an identity
    let pool = prost_reflect::DescriptorPool::decode(include_bytes!("../../../../app/modules/propresenter/pro7/pro7.desc").as_slice()).unwrap();
    let pres = prost_reflect::DynamicMessage::decode(pool.get_message_by_name("rv.data.Presentation").unwrap(), data.as_slice()).unwrap();
    let cues = pres.get_field_by_name("cues").unwrap().into_owned();
    let ids: Vec<String> = cues.as_list().unwrap().iter().map(|c| c.as_message().unwrap().get_field_by_name("uuid").unwrap().as_message().unwrap().get_field_by_name("string").unwrap().as_str().unwrap().to_owned()).collect();
    assert_eq!(ids.iter().collect::<std::collections::BTreeSet<_>>().len(), ids.len(), "no two slides share an identity");
    let _ = pres.encode_to_vec();
}

#[test]
fn what_is_not_a_song_and_what_cannot_be_written_is_refused_with_a_sentence() {
    for (bad, why) in [(&b"not a protobuf at all \xff\xff"[..], "garbage"), (&b""[..], "empty")] {
        assert_eq!(pf::parse_song(bad).unwrap_err().message, "This is not a ProPresenter song file.", "{why}");
    }
    assert!(pf::build_song(&Song { name: String::new(), sections: vec![Section::new("V", slides(&["x"]))], ..Song::default() }).is_err(), "no name");
    assert!(pf::build_song(&Song { name: "X".into(), sections: vec![Section::new("V", vec![])], ..Song::default() }).is_err(), "no slides");
    let one = |slide: Slide| Song { name: "X".into(), sections: vec![Section::new("V", vec![slide])], ..Song::default() };
    let refused = |label: &str, song: Song| assert!(matches!(pf::build_song(&song), Err(FileError { .. })), "{label}");
    refused("bad hot key", one(Slide { text: "x".into(), hot_key: "!!".into(), ..Slide::default() }));
    refused("bad key", Song { music_key: "H".into(), ..one(Slide::text("x")) });
    refused("bad reference", Song { bible_reference: "nonsense".into(), ..one(Slide::text("x")) });
    refused("arrangement naming a missing section", Song { arrangements: vec![("A".into(), vec!["Nope".into()])], ..one(Slide::text("x")) });
    refused("unknown selected arrangement", Song { selected_arrangement: "Z".into(), ..one(Slide::text("x")) });
    refused("chord outside the text", one(Slide { text: "ab".into(), chords: vec![(9, "G".into())], ..Slide::default() }));
    refused("completion without a time", one(Slide { text: "x".into(), completion: "next".into(), ..Slide::default() }));
    assert!(SlideAction::new("nope").is_err(), "unknown action");
    assert!(SlideAction { kind: "timer".into(), what: "dance".into(), ..SlideAction::default() }.checked().is_err(), "unknown timer action");
    assert!(SlideAction { kind: "clear".into(), what: "sky".into(), ..SlideAction::default() }.checked().is_err(), "unknown clear layer");
}

#[test]
fn every_feature_of_a_song_round_trips() {
    let act = |kind: &str, target: &str| SlideAction { kind: kind.into(), target: target.into(), ..SlideAction::default() };
    let first = Slide {
        text: "Amazing grace".into(),
        translation: "Gracia sublime".into(),
        label: "Open".into(),
        label_colour: Some((1.0, 0.5, 0.0)),
        notes: "Softly".into(),
        chords: vec![(0, "G".into()), (8, "D".into())],
        hot_key: "1".into(),
        advance_after: 4.0,
        completion: "next".into(),
        transition: 1.5,
        actions: vec![
            SlideAction { uuid: "11111111-1111-1111-1111-111111111111".into(), ..act("macro", "Lights up") },
            act("look", "Worship look"),
            SlideAction { what: "start".into(), ..act("timer", "Countdown") },
            SlideAction { what: "messages".into(), ..act("clear", "") },
            act("prop", "Lower third"),
            act("message", "Welcome"),
            SlideAction { screen: "Stage 1".into(), ..act("stage", "Singers") },
            act("clear_group", "Everything off"),
        ],
        ..Slide::default()
    };
    let full = Song {
        name: "Everything".into(),
        category: "Worship".into(),
        notes: "Capo 2".into(),
        music_key: "Bb".into(),
        original_key: "G".into(),
        album: "Album".into(),
        artist_credits: "Band".into(),
        ccli_number: 42,
        show_ccli: true,
        background: Some((0.1, 0.2, 0.3)),
        slide_show_seconds: 8.0,
        transition: 0.5,
        announcements: true,
        bible_reference: "John 3:16-17".into(),
        selected_arrangement: "Short".into(),
        sections: vec![
            Section { name: "Verse 1".into(), hot_key: "V".into(), slides: vec![first.clone(), Slide { text: "Disabled one".into(), enabled: false, advance_after: 2.0, completion: "first".into(), ..Slide::default() }], colour: None },
            Section::new("Chorus", slides(&["Sing"])),
        ],
        arrangements: vec![("Long".into(), ["Verse 1", "Chorus", "Verse 1"].map(str::to_owned).to_vec()), ("Short".into(), vec!["Chorus".into()])],
        ..Song::default()
    };
    let r = pf::parse_song(&pf::build_song(&full).unwrap()).unwrap();
    assert_eq!(
        (r.category.clone(), r.notes.clone(), r.music_key.clone(), r.original_key.clone(), r.album.clone(), r.artist_credits.clone(), r.ccli_number, r.show_ccli),
        (full.category.clone(), full.notes.clone(), full.music_key.clone(), full.original_key.clone(), full.album.clone(), full.artist_credits.clone(), 42, true)
    );
    assert_eq!((r.background, r.slide_show_seconds, r.transition, r.announcements, r.bible_reference.as_str(), r.selected_arrangement.as_str()), (full.background, 8.0, 0.5, true, "John 3:16-17", "Short"));
    assert_eq!(r.arrangements, full.arrangements);
    let (s1, want) = (&r.sections[0].slides[0], &first);
    assert_eq!((s1.text.as_str(), s1.translation.as_str(), s1.label.as_str(), s1.label_colour, s1.notes.as_str()), ("Amazing grace", "Gracia sublime", "Open", Some((1.0, 0.5, 0.0)), "Softly"));
    assert_eq!((&s1.chords, s1.hot_key.as_str(), s1.advance_after, s1.completion.as_str(), s1.transition), (&want.chords, "1", 4.0, "next", 1.5));
    let kinds: Vec<(String, String, String, String)> = s1.actions.iter().map(|a| (a.kind.clone(), a.target.clone(), a.what.clone(), a.screen.clone())).collect();
    let wanted: Vec<(String, String, String, String)> = want.actions.iter().map(|a| (a.kind.clone(), a.target.clone(), a.what.clone(), a.screen.clone())).collect();
    assert_eq!(kinds, wanted, "slide actions");
    assert_eq!(s1.actions[0].uuid, "11111111-1111-1111-1111-111111111111", "macro id kept");
    assert_eq!(r.sections[0].hot_key, "V", "section hot key");
    let s2 = &r.sections[0].slides[1];
    assert_eq!((s2.enabled, s2.advance_after, s2.completion.as_str()), (false, 2.0, "first"), "disabled slide");
}

// -------------------------------------------------------------------------------------------------------------------- the sheet
const SHEET: &str = "Verse 1\nG        C\nI love You, Lord\n/ Te amo, Se\u{f1}or\nFor Your mercy never fails me\nAll my days, I've been held\n\nChorus\n[G]Cause You are good\nAll the time\n\nChorus\n[G]Cause You are good\nAll the time\n";

#[test]
fn section_headings_and_chords() {
    assert_eq!(sp::is_header("Verse 1"), "Verse 1", "verse number");
    assert_eq!(sp::is_header("[CHORUS 2]:"), "Chorus 2", "bracketed and shouted");
    assert_eq!(sp::is_header("v2"), "Verse 2", "short form");
    assert_eq!(sp::is_header("[C]"), "", "a chord is not a heading");
    assert_eq!(sp::is_header("Verse of the day we sing"), "", "a lyric is not a heading");
    assert_eq!(sp::split_chords("I [G]love [C/E]You"), ("I love You".to_owned(), vec![(2, "G".to_owned()), (7, "C/E".to_owned())]), "inline chords leave the words");
    assert_eq!(sp::with_chords("I love You", &[(2, "G".into()), (7, "C/E".into())]), "I [G]love [C/E]You", "and go back");
}

#[test]
fn a_pasted_sheet_becomes_sections_of_slides() {
    let r = sp::parse_text(SHEET, 2, true, true, true, true).unwrap();
    let names: Vec<&str> = r["sections"].as_array().unwrap().iter().map(|s| s["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["Verse 1", "Chorus"], "sections");
    let v = r["sections"][0]["slides"].as_array().unwrap();
    assert_eq!(v.iter().map(|x| x["text"].as_str().unwrap().split('\n').count()).collect::<Vec<_>>(), [2, 1], "two lines a slide");
    assert!(v[0]["text"].as_str().unwrap().starts_with("[G]I love "), "the chord above the first line is inline");
    assert_eq!(v[0]["translation"], "Te amo, Se\u{f1}or", "translation line kept with its lyric");
    assert_eq!(r["arrangement"], json!(["Verse 1", "Chorus", "Chorus"]), "a repeated chorus is kept once and repeats in the order");
    assert!(r["warnings"].as_array().unwrap().iter().any(|w| w.as_str().unwrap().contains("again")), "and says so");
}

#[test]
fn the_options_change_how_a_sheet_is_read() {
    let one = sp::parse_text("Verse 1\nA line\nAnother\n\nChorus\nThird", 4, true, false, true, true).unwrap();
    assert_eq!(one["sections"].as_array().unwrap().len(), 1, "no headings: one section");
    assert_eq!(sp::parse_text("one\ntwo\n\nthree", 0, false, true, true, true).unwrap()["sections"][0]["slides"].as_array().unwrap().len(), 1, "a blank line can stop splitting");
    assert_eq!(sp::parse_text("one\ntwo\n\nthree", 0, true, true, true, true).unwrap()["sections"][0]["slides"].as_array().unwrap().len(), 2, "a blank line splits");
    assert_eq!(sp::parse_text("one\n/ two", 0, true, true, false, true).unwrap()["sections"][0]["slides"][0]["text"], "one\n/ two", "slash read as lyric when off");
    for bad in ["", "   "] {
        assert!(!sp::parse_text(bad, 4, true, true, true, true).unwrap_err().message.is_empty(), "nothing to read is refused: {bad:?}");
    }
    let r = sp::parse_text("Verse 1\n\n", 4, true, true, true, true).unwrap();
    assert!(r["warnings"].as_array().unwrap().iter().any(|w| w.as_str().unwrap().contains("no lines")), "a heading with no lines is kept, with a note");
}

#[test]
fn slides_are_cut_again_at_n_lines() {
    let secs = vec![json!({"name": "Verse 1", "slides": [{"text": "a\nb\nc\nd\ne", "translation": "A\nB\nC\nD\nE", "hot_key": "1", "label": "V"}]})];
    let out = sp::resplit(&secs, 2);
    let slides = out[0]["slides"].as_array().unwrap();
    assert_eq!(slides.iter().map(|x| x["text"].as_str().unwrap()).collect::<Vec<_>>(), ["a\nb", "c\nd", "e"], "slides");
    assert_eq!(slides.iter().map(|x| x["translation"].as_str().unwrap()).collect::<Vec<_>>(), ["A\nB", "C\nD", "E"], "translation stays with its line");
    assert_eq!((slides[0]["hot_key"].clone(), slides[0]["label"].clone()), (json!("1"), json!("V")), "the first slide keeps its hot key and label");
    assert_eq!(sp::resplit(&secs, 0)[0]["slides"].as_array().unwrap().len(), 1, "zero is one slide");
}

fn tiny_pdf(body: &[u8]) -> Vec<u8> {
    let objs: Vec<Vec<u8>> = vec![
        b"<< /Type /Catalog /Pages 2 0 R >>".to_vec(),
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>".to_vec(),
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>".to_vec(),
        [format!("<< /Length {} >>\nstream\n", body.len()).as_bytes(), body, b"\nendstream"].concat(),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>".to_vec(),
    ];
    let mut pdf = b"%PDF-1.4\n".to_vec();
    let mut offsets = Vec::new();
    for (i, o) in objs.iter().enumerate() {
        offsets.push(pdf.len());
        pdf.extend(format!("{} 0 obj\n", i + 1).as_bytes());
        pdf.extend(o);
        pdf.extend(b"\nendobj\n");
    }
    let x = pdf.len();
    pdf.extend(format!("xref\n0 {}\n0000000000 65535 f \n", objs.len() + 1).as_bytes());
    for o in offsets {
        pdf.extend(format!("{o:010} 00000 n \n").as_bytes());
    }
    pdf.extend(format!("trailer\n<< /Size {} /Root 1 0 R >>\nstartxref\n{x}\n%%EOF", objs.len() + 1).as_bytes());
    pdf
}

#[test]
fn a_pdf_lyric_sheet_is_read_and_a_bad_one_is_refused_with_a_sentence() {
    let pdf = tiny_pdf(b"BT /F1 12 Tf 72 700 Td (Verse 1) Tj 0 -16 Td (I love You, Lord) Tj ET");
    let r = songs::parse_text(&json!({"pdf_b64": b64(&pdf)})).unwrap();
    assert_eq!(r["sections"].as_array().unwrap().iter().map(|s| s["name"].as_str().unwrap()).collect::<Vec<_>>(), ["Verse 1"], "sections from a PDF");
    assert_eq!(r["sections"][0]["slides"][0]["text"], "I love You, Lord", "its text");
    // chords printed above a lyric stay above it, so the parser can put them in
    let sheet = tiny_pdf(b"BT /F1 12 Tf 72 700 Td (Verse 1) Tj 0 -16 Td (G) Tj ET BT /F1 12 Tf 72 668 Td (I love You, Lord) Tj ET");
    let text = sp::pdf_text(&sheet).unwrap();
    assert!(text.lines().any(|l| l.trim() == "G"), "the chord is on its own line above the lyric: {text:?}");
    for (label, data) in [("not a PDF", b"hello".to_vec()), ("empty", Vec::new())] {
        let e = songs::parse_text(&json!({"pdf_b64": b64(&data)})).unwrap_err();
        assert!(!e.message.is_empty(), "{label} is refused with a sentence");
    }
}

#[test]
fn a_song_with_inline_chords_is_built_and_an_opened_file_shows_them() {
    let song = songs::song_from_payload(&json!({"name": "T", "sections": [{"name": "Verse 1", "slides": [{"text": "I [G]love You"}]}]})).unwrap();
    let s = &song.sections[0].slides[0];
    assert_eq!((s.text.as_str(), s.chords.clone()), ("I love You", vec![(2, "G".to_owned())]), "text without markers, and chord positions");
    let back = songs::read_song(&json!(b64(&pf::build_song(&song).unwrap())), true).unwrap();
    assert_eq!(back["sections"][0]["slides"][0]["text"], "I [G]love You", "an opened file shows its chords inline");
}

#[test]
fn bad_requests_are_refused() {
    for body in [json!(null), json!({"text": "x", "lines": "many"}), json!({"text": "x", "lines": 99})] {
        assert!(songs::parse_text(&body).is_err(), "refused: {body}");
    }
    assert!(songs::split_song(&json!({"lines": 2})).is_err(), "split needs sections");
}

// ---------------------------------------------------------------------------------------------------------------------- the request
fn song() -> Value {
    json!({"name": "Amazing Grace", "ccli_number": 22025, "author": "John Newton", "music_key": "G",
           "sections": [{"name": "Verse 1", "slides": [{"text": "Amazing grace, how sweet the sound", "translation": "Gracia sublime", "chords": [[0, "G"]], "notes": "[relay: look Worship]", "label": "Open"}, "That saved a wretch like me"]},
                        {"name": "Chorus", "slides": ["Sing it out"]}],
           "arrangements": [{"name": "Default", "sections": ["Verse 1", "Chorus", "Verse 1"]}]})
}

#[test]
fn a_request_becomes_files_named_for_the_songs() {
    let mut second = song();
    second["name"] = json!("a/b:c*?");
    let files = songs::build_files(&json!([song(), song(), second])).unwrap();
    assert_eq!(files.iter().map(|f| f["name"].as_str().unwrap()).collect::<Vec<_>>(), ["Amazing Grace.pro", "Amazing Grace 2.pro", "abc.pro"], "one file per song, named for the song");
    let back = pf::parse_song(&base64::engine::general_purpose::STANDARD.decode(files[0]["data_b64"].as_str().unwrap()).unwrap()).unwrap();
    assert_eq!((back.name.as_str(), back.ccli_number, back.author.as_str(), back.music_key.as_str()), ("Amazing Grace", 22025, "John Newton", "G"), "the song survives");
    let first = &back.sections[0].slides[0];
    assert_eq!((first.chords.clone(), first.notes.as_str(), first.label.as_str(), first.translation.as_str()), (vec![(0, "G".to_owned())], "[relay: look Worship]", "Open", "Gracia sublime"), "with its chords, notes, label and translation");
    assert_eq!(back.arrangements, [("Default".to_owned(), vec!["Verse 1".to_owned(), "Chorus".to_owned(), "Verse 1".to_owned()])], "and its arrangement");
    assert_eq!(songs::file_name("../../etc/passwd", &mut Vec::new()), "etcpasswd.pro", "a file name is never a path");
    assert_eq!(songs::file_name("***", &mut Vec::new()), "Song.pro", "an empty title still gets a name");
}

#[test]
fn a_bad_request_is_refused_with_a_sentence() {
    let ok = json!([{"name": "X", "sections": [{"name": "V", "slides": ["x"]}]}]);
    assert!(songs::build_files(&ok).is_ok());
    let refused = |label: &str, songs: Value, text: &str| {
        let e = songs::build_files(&songs).unwrap_err();
        assert!(e.message.contains(text), "{label}: {}", e.message);
    };
    refused("nothing to send", json!([]), "at least one");
    refused("not a list", json!("x"), "at least one");
    refused("too many", Value::Array(vec![song(); 51]), "At most");
    refused("a song that is not an object", json!(["x"]), "object");
    refused("no name", json!([{"sections": [{"name": "V", "slides": ["x"]}]}]), "name");
    refused("no slides", json!([{"name": "X", "sections": []}]), "slide");
    refused("too many slides", json!([{"name": "X", "sections": [{"name": "V", "slides": vec!["x"; 301]}]}]), "at most");
    refused("a bad colour", json!([{"name": "X", "background": [2, 0, 0], "sections": [{"name": "V", "slides": ["x"]}]}]), "colour");
    refused("a bad key", json!([{"name": "X", "music_key": "H", "sections": [{"name": "V", "slides": ["x"]}]}]), "musical key");
    refused("a bad slide action", json!([{"name": "X", "sections": [{"name": "V", "slides": [{"text": "x", "actions": [{"kind": "nope"}]}]}]}]), "Unknown");
    refused("a bad number", json!([{"name": "X", "ccli_number": "abc", "sections": [{"name": "V", "slides": ["x"]}]}]), "number");
}

#[test]
fn reading_a_file_back() {
    let files = songs::build_files(&json!([song()])).unwrap();
    let got = songs::read_song(&files[0]["data_b64"], false).unwrap();
    assert_eq!((got["name"].clone(), got["ccli_number"].clone()), (json!("Amazing Grace"), json!(22025)), "name and CCLI");
    let secs: Vec<(String, Vec<String>)> = got["sections"].as_array().unwrap().iter().map(|s| (s["name"].as_str().unwrap().to_owned(), s["slides"].as_array().unwrap().iter().map(|x| x["text"].as_str().unwrap().to_owned()).collect())).collect();
    assert_eq!(secs, [("Verse 1".to_owned(), vec!["Amazing grace, how sweet the sound".to_owned(), "That saved a wretch like me".to_owned()]), ("Chorus".to_owned(), vec!["Sing it out".to_owned()])]);
    assert_eq!(got["arrangements"], json!([{"name": "Default", "sections": ["Verse 1", "Chorus", "Verse 1"]}]), "arrangements as objects");
    for bad in [json!("!!!"), json!(""), json!(b64(b"not a song \xff"))] {
        assert!(songs::read_song(&bad, false).is_err(), "{bad} refused");
    }
}

/// A helper in memory: one connected, recording what it was asked, answering as scripted.
struct Helper {
    calls: Mutex<Vec<Value>>,
    answer: Mutex<Result<Value, String>>,
}

impl Helpers for Helper {
    fn find(&self, cap: &str, op: &str) -> Option<String> {
        (cap == "propresenter" && op == "open_files").then(|| "FOH Mac".to_owned())
    }
    fn paired(&self) -> usize {
        1
    }
    fn call<'a>(&'a self, cap: &'a str, op: &'a str, args: Value) -> BoxFuture<'a, Result<Value, HelperError>> {
        Box::pin(async move {
            assert_eq!((cap, op), ("propresenter", "open_files"));
            self.calls.lock().unwrap().push(args);
            self.answer.lock().unwrap().clone().map_err(HelperError)
        })
    }
}

#[tokio::test]
async fn the_routes_send_through_the_helper_and_answer_with_sentences() {
    const B: &str = "/api/admin/propresenter";
    let r = rig().await;
    r.configure(json!({}));
    let post = |path: &str, body: Value| {
        let path = format!("{B}{path}");
        let r = &r;
        async move { r.send(Method::POST, &path, Some(body)).await }
    };
    let (_, state, _) = r.send(Method::GET, &format!("{B}/songs/helper"), None).await;
    assert_eq!((state["connected"].clone(), state["paired"].clone()), (json!(false), json!(0)), "no helper yet");
    let (st, body, _) = post("/songs/send", json!({"songs": [song()]})).await;
    assert_eq!((st.as_u16(), body["detail"].as_str().unwrap_or("").to_lowercase().contains("helper")), (409, true), "sending with no helper says so");
    assert_eq!(post("/songs/send", json!({"songs": []})).await.0.as_u16(), 400, "a bad request is a 400");
    // roles
    r.gate.refuse.lock().unwrap().push("propresenter.manage");
    assert_eq!(post("/songs/send", json!({"songs": [song()]})).await.0.as_u16(), 403, "an operator cannot send");
    let data = songs::build_files(&json!([song()])).unwrap()[0]["data_b64"].clone();
    assert_eq!(post("/songs/read", json!({"data_b64": data})).await.0.as_u16(), 200, "an operator can read a file");
    assert_eq!(post("/songs/read", json!({"data_b64": "!!"})).await.0.as_u16(), 400, "a bad file is a 400");
    assert_eq!(send_to(&anonymous(), Method::POST, &format!("{B}/songs/send"), Some(json!({"songs": [song()]}))).await.0.as_u16(), 401, "nobody signed in cannot");
    r.gate.refuse.lock().unwrap().clear();

    let helper = Arc::new(Helper { calls: Mutex::new(Vec::new()), answer: Mutex::new(Ok(json!({"opened": 2}))) });
    r.host.provide("core", HELPERS, Arc::new(HelpersCap(helper.clone())));
    let (_, state, _) = r.send(Method::GET, &format!("{B}/songs/helper"), None).await;
    assert_eq!((state["connected"].clone(), state["helper"].clone()), (json!(true), json!("FOH Mac")), "the helper is seen");
    let mut other = song();
    other["name"] = json!("Second");
    let (st, body, _) = post("/songs/send", json!({"songs": [song(), other]})).await;
    assert_eq!((st.as_u16(), body["sent"].clone(), body["opened"].clone()), (200, json!(2), json!(2)), "the route answers");
    let asked = helper.calls.lock().unwrap()[0].clone();
    let names: Vec<&str> = asked["files"].as_array().unwrap().iter().map(|f| f["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["Amazing Grace.pro", "Second.pro"], "a file per song");
    assert!(asked["files"].as_array().unwrap().iter().all(|f| pf::parse_song(&base64::engine::general_purpose::STANDARD.decode(f["data_b64"].as_str().unwrap()).unwrap()).is_ok()), "each a real song file");
    *helper.answer.lock().unwrap() = Err("ProPresenter would not open the files (is it installed?).".into());
    let (st, body, _) = post("/songs/send", json!({"songs": [song()]})).await;
    assert_eq!((st.as_u16(), body["detail"].clone()), (409, json!("ProPresenter would not open the files (is it installed?).")), "a helper's failure comes back as its sentence");

    // the parse and split routes read only
    let (st, body, _) = post("/songs/parse", json!({"text": SHEET, "lines": 2})).await;
    assert_eq!((st.as_u16(), body["sections"].as_array().map(Vec::len)), (200, Some(2)));
    let (st, body, _) = post("/songs/split", json!({"sections": body["sections"].clone(), "lines": 1})).await;
    assert_eq!((st.as_u16(), body["sections"][0]["slides"].as_array().map(|s| s.len() >= 2)), (200, Some(true)));
    assert_eq!(post("/songs/parse", json!({"text": "x", "lines": 99})).await.0.as_u16(), 400);
}
