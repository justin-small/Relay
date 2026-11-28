//! The line-committing rules of `tests/test_realtime.py` and `tests/test_transcribe.py`, on a clock the test moves.

use relay_captions::transcript::{is_fatal, Dialect, Emit, Transcript};
use serde_json::json;

fn delta(t: &mut Transcript, text: &str, now: f64) -> Vec<Emit> {
    t.handle(&json!({"type": "session.input_transcript.delta", "delta": text}), now, now).0
}

fn fin(s: &str) -> Emit {
    Emit::Final(s.to_owned())
}

#[test]
fn a_line_ending_a_sentence_commits_once_its_deltas_go_quiet() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    assert_eq!(delta(&mut t, "Hello there.", 0.0), [Emit::Delta("Hello there.".into())]);
    assert!(t.commit_idle(0.5).is_empty(), "not quiet long enough");
    assert_eq!(t.commit_idle(1.1), [fin("Hello there.")]);
    assert!(t.commit_idle(5.0).is_empty(), "committed once");
}

#[test]
fn a_mid_sentence_pause_waits_then_commits_up_to_the_last_space() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "I like cher", 0.0);
    assert!(t.commit_idle(3.0).is_empty(), "a pause inside a sentence is not a boundary");
    assert_eq!(t.commit_idle(10.5), [fin("I like")]);
    assert_eq!(t.partial(), "cher", "the half word is kept");
    delta(&mut t, "ries.", 11.0);
    assert_eq!(t.commit_idle(12.2), [fin("cherries.")], "the word is whole when the rest arrives");
}

#[test]
fn a_single_unfinished_word_keeps_waiting() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "cerez", 0.0);
    assert!(t.commit_idle(30.0).is_empty());
    assert_eq!(t.partial(), "cerez");
}

#[test]
fn punctuation_alone_is_the_tail_of_the_last_line() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, ".", 0.0);
    assert!(t.commit_idle(20.0).is_empty());
}

#[test]
fn a_new_utterance_after_a_pause_is_not_glued_on() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "So,", 0.0);
    assert_eq!(delta(&mut t, "Why", 0.9), [Emit::Delta(" Why".into())]);
    // Inside the gap, or after a letter, nothing is added.
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "So,", 0.0);
    assert_eq!(delta(&mut t, "Why", 0.2), [Emit::Delta("Why".into())]);
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "cerez", 0.0);
    assert_eq!(delta(&mut t, "os", 2.0), [Emit::Delta("os".into())]);
}

#[test]
fn a_completed_event_commits_only_what_is_still_in_flight() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "Hello there.", 0.0);
    t.commit_idle(2.0);
    let done = json!({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Hello there."});
    assert!(t.handle(&done, 2.1, 2.1).0.is_empty(), "the whole turn is not published a second time");
    delta(&mut t, "And more", 3.0);
    assert_eq!(t.handle(&done, 3.1, 3.1).0, [fin("And more")]);
}

#[test]
fn a_socket_that_dies_flushes_the_line() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    delta(&mut t, "half a li", 0.0);
    assert_eq!(t.flush(), [fin("half a li")]);
    assert!(t.flush().is_empty());
}

#[test]
fn the_wait_comes_from_the_settings() {
    let mut t = Transcript::new(Dialect::OpenAi, Some(0.2), Some(0.5));
    delta(&mut t, "Hi there", 0.0);
    assert_eq!(t.commit_idle(0.6), [fin("Hi")], "the backstop is 0.5 s here");
}

#[test]
fn an_error_event_names_whether_to_retry() {
    let mut t = Transcript::new(Dialect::OpenAi, None, None);
    let (_, fault) = t.handle(&json!({"type": "error", "error": {"message": "Incorrect API key provided"}}), 0.0, 0.0);
    let fault = fault.unwrap();
    assert_eq!(fault.message, "Incorrect API key provided");
    assert!(fault.fatal);
    let (_, fault) = t.handle(&json!({"type": "x.failed", "error": {"message": "overloaded"}}), 0.0, 0.0);
    assert!(!fault.unwrap().fatal, "a *.failed event that is not fatal keeps retrying");
    let (_, fault) = t.handle(&json!({"type": "error", "error": "plain words"}), 0.0, 0.0);
    assert_eq!(fault.unwrap().message, "plain words");
}

#[test]
fn which_messages_are_fatal() {
    for msg in ["Incorrect API key provided", "HTTP 401", "HTTP 403", "insufficient_quota", "model_not_found", "You exceeded your current quota"] {
        assert!(is_fatal(msg), "{msg}");
    }
    assert!(!is_fatal("TimeoutError: timed out"));
    assert!(!is_fatal(""));
}

fn scribe(t: &mut Transcript, evt: serde_json::Value, now: f64) -> Vec<Emit> {
    t.handle(&evt, now, now).0
}

#[test]
fn scribe_shows_partials_while_they_only_grow_and_the_final_replaces_them() {
    let mut t = Transcript::new(Dialect::Scribe, None, None);
    assert_eq!(scribe(&mut t, json!({"type": "transcript-partial", "text": "Good morning, Evie."}), 0.0), [Emit::Delta("Good morning, Evie.".into())]);
    assert_eq!(scribe(&mut t, json!({"type": "transcript-partial", "text": "Good morning, Evie. Welcome"}), 0.3), [Emit::Delta(" Welcome".into())]);
    // A revision that is not growth shows nothing new.
    assert!(scribe(&mut t, json!({"type": "transcript-partial", "text": "Good morning, everyone."}), 0.6).is_empty());
    assert_eq!(
        scribe(&mut t, json!({"type": "transcript-final", "text": "Good morning, everyone. Welcome."}), 0.9),
        [fin("Good morning, everyone. Welcome.")]
    );
    // The next turn starts clean.
    assert_eq!(scribe(&mut t, json!({"type": "transcript-partial", "text": "Next"}), 2.0), [Emit::Delta("Next".into())]);
}

#[test]
fn scribe_with_deltas_ignores_partials_for_that_turn() {
    let mut t = Transcript::new(Dialect::Scribe, None, None);
    assert_eq!(scribe(&mut t, json!({"type": "transcript-delta", "delta": "Hel"}), 0.0), [Emit::Delta("Hel".into())]);
    assert!(scribe(&mut t, json!({"type": "transcript-partial", "text": "Hello"}), 0.1).is_empty());
    // An empty final falls back to what the deltas built.
    assert_eq!(scribe(&mut t, json!({"type": "transcript-final", "text": ""}), 0.2), [fin("Hel")]);
}

#[test]
fn scribe_commits_nothing_on_its_own() {
    let mut t = Transcript::new(Dialect::Scribe, None, None);
    scribe(&mut t, json!({"type": "transcript-delta", "delta": "Hello there."}), 0.0);
    assert!(t.commit_idle(60.0).is_empty(), "the gateway commits each turn");
}
