//! Properties that hold for any input, for the code that decides from arbitrary text: the blocklist, the run id, the pairing and the
//! line-committing rules.

use quickcheck::{Arbitrary, Gen, QuickCheck, TestResult};
use relay_captions::clock::utc;
use relay_captions::exporting::{pair_lines, Row, PAIR_TOLERANCE_S};
use relay_captions::recorder::is_run_id;
use relay_captions::redact::{parse_terms, Redactor};
use relay_captions::transcript::{Dialect, Emit, Transcript};
use relay_core::hub::Redact;
use serde_json::json;

/// Text from a small alphabet that keeps hitting the banned words and their fragments, so the interesting cases come up.
#[derive(Clone, Debug)]
struct Speech(String);

impl Arbitrary for Speech {
    fn arbitrary(g: &mut Gen) -> Self {
        const BITS: [&str; 16] = ["damn", "hell", "off the record", " ", " ", "-", ",", ".", "Okay", "niño", "nino", "da", "mn", "x", "!", "\n"];
        let n = usize::arbitrary(g) % 12;
        Speech((0..n).map(|_| *g.choose(&BITS).unwrap()).collect())
    }
}

#[derive(Clone, Debug)]
struct Cuts(Vec<usize>);

impl Arbitrary for Cuts {
    fn arbitrary(g: &mut Gen) -> Self {
        Cuts((0..8).map(|_| usize::arbitrary(g) % 6 + 1).collect())
    }
}

fn terms() -> Vec<String> {
    ["damn", "off the record", "niño"].iter().map(|s| (*s).to_owned()).collect()
}

fn chunked(text: &str, cuts: &[usize]) -> Vec<String> {
    let chars: Vec<char> = text.chars().collect();
    let (mut out, mut i, mut k) = (Vec::new(), 0, 0);
    while i < chars.len() {
        let n = cuts[k % cuts.len()].min(chars.len() - i);
        out.push(chars[i..i + n].iter().collect());
        i += n;
        k += 1;
    }
    out
}

#[test]
fn a_banned_word_is_never_on_screen_however_the_text_is_split() {
    fn prop(text: Speech, cuts: Cuts) -> bool {
        let mut r = Redactor::new(&terms());
        let mut shown = String::new();
        for d in chunked(&text.0, &cuts.0) {
            shown.push_str(&r.feed(&d));
        }
        shown.push_str(&r.flush());
        // Whole words only: "damn" inside a longer word is not the banned word, so look for it as a word.
        let folded = shown.to_lowercase();
        !folded.split(|c: char| !c.is_alphanumeric()).any(|w| w == "damn")
    }
    QuickCheck::new().tests(2000).quickcheck(prop as fn(Speech, Cuts) -> bool);
}

#[test]
fn a_redactor_never_invents_text() {
    // Every character shown came from the input: removal and recapitalising, nothing else (and the spaces it closes up).
    fn prop(text: Speech, cuts: Cuts) -> bool {
        let mut r = Redactor::new(&terms());
        let mut shown = String::new();
        for d in chunked(&text.0, &cuts.0) {
            shown.push_str(&r.feed(&d));
        }
        shown.push_str(&r.flush());
        let source: std::collections::HashSet<char> = text.0.chars().flat_map(char::to_lowercase).chain(text.0.chars().flat_map(char::to_uppercase)).chain([' ']).collect();
        shown.chars().all(|c| source.contains(&c))
    }
    QuickCheck::new().tests(2000).quickcheck(prop as fn(Speech, Cuts) -> bool);
}

#[test]
fn text_with_no_banned_word_loses_no_letter_however_it_is_split() {
    // Spacing is tidied (runs of blanks close up, a blank before punctuation goes), as in Python; the words are all still there.
    fn prop(text: Speech, cuts: Cuts) -> TestResult {
        let lower = text.0.to_lowercase();
        let squashed: String = lower.chars().map(|c| if c.is_whitespace() || c == '-' { ' ' } else { c }).collect();
        if squashed.contains("damn") || squashed.contains("off the record") || squashed.contains("niño") || squashed.contains("nino") {
            return TestResult::discard();
        }
        let mut r = Redactor::new(&terms());
        let mut shown = String::new();
        for d in chunked(&text.0, &cuts.0) {
            shown.push_str(&r.feed(&d));
        }
        shown.push_str(&r.flush());
        let letters = |s: &str| s.chars().filter(|c| c.is_alphanumeric()).collect::<String>();
        TestResult::from_bool(letters(&shown) == letters(&text.0))
    }
    QuickCheck::new().tests(3000).quickcheck(prop as fn(Speech, Cuts) -> TestResult);
}

#[test]
fn parsing_terms_is_stable_and_never_longer() {
    fn prop(raw: Vec<String>) -> bool {
        let once = parse_terms(&raw);
        let twice = parse_terms(&once);
        once == twice && once.len() <= raw.len() && once.iter().all(|t| !t.trim().is_empty() && t == &t.split_whitespace().collect::<Vec<_>>().join(" "))
    }
    QuickCheck::new().tests(1000).quickcheck(prop as fn(Vec<String>) -> bool);
}

#[test]
fn a_run_id_never_names_a_path_outside_the_recordings_folder() {
    fn prop(id: String) -> bool {
        !is_run_id(&id) || (!id.contains('/') && !id.contains('\\') && !id.contains("..") && id.len() <= 64 && id.chars().all(|c| c.is_ascii_digit() || c == '-'))
    }
    QuickCheck::new().tests(5000).quickcheck(prop as fn(String) -> bool);
    // And every id the recorder itself makes is one it accepts.
    fn made(secs: u32, n: u8) -> bool {
        let stamp = utc(f64::from(secs)).run_stamp();
        is_run_id(&stamp) && is_run_id(&format!("{stamp}-{}", u32::from(n) + 2))
    }
    QuickCheck::new().tests(2000).quickcheck(made as fn(u32, u8) -> bool);
}

#[test]
fn utc_round_trips_the_seconds_of_the_day() {
    fn prop(secs: u32) -> bool {
        let c = utc(f64::from(secs));
        let day_secs = u64::from(secs) % 86_400;
        u64::from(c.hour) * 3600 + u64::from(c.minute) * 60 + u64::from(c.second) == day_secs && (1..=12).contains(&c.month) && (1..=31).contains(&c.day)
    }
    QuickCheck::new().tests(5000).quickcheck(prop as fn(u32) -> bool);
}

#[derive(Clone, Debug)]
struct Lines(Vec<(u16, u8)>);

impl Arbitrary for Lines {
    fn arbitrary(g: &mut Gen) -> Self {
        Lines((0..usize::arbitrary(g) % 8).map(|_| (u16::arbitrary(g) % 400, u8::arbitrary(g) % 6 + 1)).collect())
    }
}

#[test]
fn pairing_loses_no_line_and_never_claims_a_translation_twice() {
    fn rows(stream: &str, lang: &str, lines: &Lines, base: u64) -> Vec<Row> {
        lines.0.iter().enumerate().map(|(i, (t, len))| Row::from_json(&json!({"stream": stream, "lang": lang, "seq": base + i as u64, "t0": 1000.0 + f64::from(*t), "t1": 1000.0 + f64::from(*t) + f64::from(*len), "text": format!("{stream}{i}")}))).collect()
    }
    fn prop(source: Lines, target: Lines) -> bool {
        let (s, t) = (rows("transcription", "ENGLISH", &source, 0), rows("translation", "SPANISH", &target, 1000));
        let pairs = pair_lines(&s, &t, PAIR_TOLERANCE_S, utc);
        // Every translation line is in exactly one pair, and every source line is in at least one.
        let target_seen: Vec<_> = pairs.iter().filter(|p| !p.target.is_empty()).map(|p| p.target.clone()).collect();
        let every_target_once = t.iter().all(|r| target_seen.iter().filter(|x| **x == r.text).count() == 1);
        let every_source_seen = s.iter().all(|r| pairs.iter().any(|p| p.source.split(' ').any(|w| w == r.text)));
        let in_time_order = pairs.windows(2).all(|w| w[0].t0 <= w[1].t0);
        every_target_once && every_source_seen && in_time_order
    }
    QuickCheck::new().tests(2000).quickcheck(prop as fn(Lines, Lines) -> bool);
}

#[test]
fn a_transcript_never_commits_more_than_it_was_given_and_flush_empties_it() {
    #[derive(Clone, Debug)]
    struct Deltas(Vec<(String, u8)>);
    impl Arbitrary for Deltas {
        fn arbitrary(g: &mut Gen) -> Self {
            const WORDS: [&str; 7] = ["Hello", " there", ".", ", so", "cher", "ries", " ok"];
            Deltas((0..usize::arbitrary(g) % 10).map(|_| ((*g.choose(&WORDS).unwrap()).to_owned(), u8::arbitrary(g) % 30)).collect())
        }
    }
    fn prop(d: Deltas, idle: u8) -> bool {
        let mut t = Transcript::new(Dialect::OpenAi, Some(0.1 + f64::from(idle % 5) / 10.0), Some(2.0));
        let (mut now, mut said, mut got) = (0.0, String::new(), String::new());
        for (word, gap) in &d.0 {
            now += f64::from(*gap) / 10.0;
            for e in t.commit_idle(now) {
                if let Emit::Final(x) = e {
                    got.push_str(&x);
                    got.push(' ');
                }
            }
            let (emits, _) = t.handle(&json!({"type": "conversation.item.input_audio_transcription.delta", "delta": word}), now, now);
            if emits.iter().any(|e| matches!(e, Emit::Final(_))) {
                return false;
            }
            said.push_str(word);
        }
        for e in t.flush() {
            if let Emit::Final(x) = e {
                got.push_str(&x);
                got.push(' ');
            }
        }
        // Everything said was committed, in order: the same letters, perhaps with spaces added or dropped at the seams.
        let letters = |s: &str| s.chars().filter(|c| c.is_alphanumeric()).collect::<String>();
        letters(&got) == letters(&said) && t.flush().is_empty() && t.partial().is_empty()
    }
    QuickCheck::new().tests(3000).quickcheck(prop as fn(Deltas, u8) -> bool);
}
