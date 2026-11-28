//! `tests/test_redact.py`, case for case, plus the blocklist file.

use relay_captions::redact::{parse_terms, parse_terms_report, read_blocklist_file, write_blocklist_file, Redactor, MAX_TERMS};
use relay_core::hub::Redact;

fn r(terms: &[&str]) -> Redactor {
    Redactor::new(&terms.iter().map(|s| (*s).to_owned()).collect::<Vec<_>>())
}

fn whole(terms: &[&str], text: &str) -> String {
    r(terms).whole(text)
}

fn stream(terms: &[&str], deltas: &[&str]) -> (Vec<String>, String) {
    let mut red = r(terms);
    let (mut seen, mut out) = (Vec::new(), String::new());
    for d in deltas {
        out.push_str(&red.feed(d));
        seen.push(out.clone());
    }
    out.push_str(&red.flush());
    (seen, out)
}

#[test]
fn whole_string() {
    let d = ["damn"];
    assert_eq!(whole(&d, "Well damn, that is fast."), "Well, that is fast.");
    assert_eq!(whole(&d, "DAMN it"), "It");
    assert_eq!(whole(&d, "Well, damn, that was close."), "Well, that was close.");
    assert_eq!(whole(&d, "Damn, that was close."), "That was close.");
    assert_eq!(whole(&d, "Stop. Damn, now."), "Stop. Now.");
    assert_eq!(whole(&["hell"], "It was, hell; awful."), "It was, awful.");
    assert_eq!(whole(&d, "One, two, three."), "One, two, three.");
    assert_eq!(whole(&d, "iPhone damn, ok"), "iPhone, ok");
    assert_eq!(whole(&["ass"], "a class assignment"), "a class assignment");
    assert_eq!(whole(&["nino"], "el niño corre"), "el corre");
    assert_eq!(whole(&["niño"], "el nino corre"), "el corre");
    assert_eq!(whole(&["off the record"], "This is off the record, ok?"), "This is, ok?");
    assert_eq!(whole(&["off the record"], "say off-the-record now"), "say now");
    assert_eq!(whole(&[], "anything at all"), "anything at all");
    assert_eq!(whole(&["x"], "x and x and x"), "and and");
    assert_eq!(whole(&["mierda"], "¡Qué mierda de día!"), "¡Qué de día!");
    assert_eq!(whole(&d, "damnation"), "damnation");
}

#[test]
fn a_word_split_across_deltas_never_shows() {
    let (seen, fin) = stream(&["damn"], &["Well ", "dam", "n", ", ok"]);
    assert!(!seen.iter().any(|s| s.contains("dam")), "{seen:?}");
    assert_eq!(fin, "Well, ok");
    assert_eq!(stream(&["damn"], &["Well, ", "dam", "n", ", ok"]).1, "Well, ok");
    assert_eq!(stream(&["damn"], &["Damn", ", that ", "was close."]).1, "That was close.");

    let (seen, fin) = stream(&["damn"], &["d", "a", "m", "n"]);
    assert!(!seen.iter().any(|s| s.contains("dam")), "{seen:?}");
    assert_eq!(fin, "");

    let (seen, fin) = stream(&["damn"], &["dam", "age control"]);
    assert_eq!(fin, "damage control");
    assert_eq!(seen[0], "");
}

#[test]
fn unrelated_words_are_not_held() {
    let (seen, _) = stream(&["damn"], &["Good ", "afternoon ", "everyone"]);
    assert_eq!(seen[0], "Good");
    assert_eq!(seen[1], "Good afternoon");
}

#[test]
fn phrases() {
    let (seen, fin) = stream(&["off the record"], &["This is ", "off ", "the ", "record", ", ok"]);
    assert!(!seen.iter().any(|s| s.contains("off")), "{seen:?}");
    assert_eq!(fin, "This is, ok");
    assert_eq!(stream(&["off the record"], &["turn ", "off ", "the ", "lights"]).1, "turn off the lights");
}

#[test]
fn spacing_and_punctuation() {
    assert_eq!(stream(&["damn"], &["hello ", "damn", " world"]).1, "hello world");
    assert_eq!(stream(&["damn"], &["damn", " world"]).1, "world");
    assert_eq!(stream(&["damn"], &["hello ", "damn"]).1, "hello");
    assert_eq!(stream(&["damn"], &["hello ", "damn", "."]).1, "hello.");
}

/// A fixed generator, so a failure repeats.
struct Lcg(u64);
impl Lcg {
    fn below(&mut self, n: usize) -> usize {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        ((self.0 >> 33) as usize) % n
    }
}

#[test]
fn any_chunking_matches_the_whole_string() {
    let terms = ["damn", "off the record", "niño", "hell"];
    let texts = [
        "Well damn, this is off the record and the niño ran.",
        "damn",
        "hell no, damn it, off the record please.",
        "nothing here is banned at all",
        "damnation is not damn",
    ];
    let mut rng = Lcg(7);
    for text in texts {
        let want = whole(&terms, text);
        let chars: Vec<char> = text.chars().collect();
        for _ in 0..60 {
            let (mut deltas, mut i) = (Vec::new(), 0);
            while i < chars.len() {
                let n = (1 + rng.below(5)).min(chars.len() - i);
                deltas.push(chars[i..i + n].iter().collect::<String>());
                i += n;
            }
            let refs: Vec<&str> = deltas.iter().map(String::as_str).collect();
            let (_, got) = stream(&terms, &refs);
            assert_eq!(got.trim(), want.trim(), "{text:?} in {deltas:?}");
        }
    }
}

#[test]
fn terms_are_deduped_and_capped() {
    assert_eq!(parse_terms(&["Damn", "damn", "  off   the-record ", "off the record", ""]), ["Damn", "off the-record"]);
    let report = parse_terms_report(&["a", "A", "b"]);
    assert_eq!((report.terms.len(), report.duplicates, report.dropped), (2, 1, 0));
    let many: Vec<String> = (0..MAX_TERMS + 5).map(|i| format!("w{i}")).collect();
    let report = parse_terms_report(&many);
    assert_eq!((report.terms.len(), report.dropped), (MAX_TERMS, 5));
}

#[test]
fn the_blocklist_file_round_trips_and_keeps_comments() {
    let dir = std::env::temp_dir().join(format!("relay-captions-redact-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("blocklist.txt");
    assert!(read_blocklist_file(&path).is_empty(), "a missing file is an empty list");
    std::fs::write(&path, "# my section\ndamn, hell # inline\noff the record\n").unwrap();
    assert_eq!(read_blocklist_file(&path), ["damn", "hell", "off the record"]);
    let saved = write_blocklist_file(&path, &["heck".to_owned(), "Heck".to_owned(), "darn".to_owned()]).unwrap();
    assert_eq!(saved, ["heck", "darn"]);
    let text = std::fs::read_to_string(&path).unwrap();
    assert!(text.starts_with("# my section\n"), "{text}");
    assert!(text.ends_with("heck\ndarn\n"), "{text}");
    std::fs::remove_dir_all(&dir).unwrap();
}
