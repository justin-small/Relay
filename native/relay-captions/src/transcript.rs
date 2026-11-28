//! What a transcription session does with the events a backend sends (`app/realtime.py` `RealtimeSession`, `TranscriptionSession`
//! and `app/transcribe.py` `ScribeSession`), with no socket and no clock of its own: a caller passes the time in, so a test moves it.
//!
//! The socket loop (connect, send audio, reconnect with backoff) is in `session.rs`; this is the part that decides when a line
//! is committed. Both backends carry the source language only (the realtime translation channel went in #496), so there is one
//! buffer, not one per channel.

use serde_json::Value;

/// A committed line should end where a sentence does (the CJK forms included).
pub const SENTENCE_END: &[char] = &['.', '?', '!', '\u{2026}', '\u{3002}', '\u{ff01}', '\u{ff1f}'];
/// A held fragment ending in punctuation is a finished clause: the next utterance's deltas, after a pause, carry no leading space.
pub const CLAUSE_END: &[char] = &['.', ',', ';', ':', '!', '?', '\u{2026}', '\u{3002}', '\u{ff01}', '\u{ff1f}', '\u{ff0c}', '\u{3001}'];
pub const UTTERANCE_GAP_S: f64 = 0.8;
/// Quiet this long and ending a sentence: commit.
pub const SEGMENT_IDLE_S: f64 = 1.0;
/// Quiet this long: commit up to the last whitespace, even mid-sentence.
pub const SEGMENT_MAX_IDLE_S: f64 = 10.0;

const OPENAI_DELTA: &[&str] = &["session.input_transcript.delta", "conversation.item.input_audio_transcription.delta"];
const OPENAI_DONE: &[&str] = &[
    "session.input_transcript.done",
    "session.input_transcript.completed",
    "conversation.item.input_audio_transcription.completed",
];

/// Faults that retrying cannot fix: stop and tell the operator instead of hammering the API with a credential it has rejected.
const FATAL_MARKERS: &[&str] = &[
    "incorrect api key",
    "invalid_api_key",
    "invalid api key",
    "no api key",
    "insufficient_quota",
    "exceeded your current quota",
    "must be verified",
    "does not have access",
    "http 401",
    "http 403",
    // A retired or misspelled model name fails every request the same way.
    "model_not_found",
];

pub fn is_fatal(msg: &str) -> bool {
    let low = msg.to_lowercase();
    FATAL_MARKERS.iter().any(|m| low.contains(m))
}

/// Which wire format the events are in.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dialect {
    /// OpenAI's `gpt-live-transcribe`: deltas, and a completed event per committed turn that only commits what is still in flight.
    OpenAi,
    /// ElevenLabs Scribe through the Vercel gateway: the gateway commits each turn itself (`transcript-final`).
    Scribe,
}

/// Something for the caller to publish.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Emit {
    /// A piece of the open line.
    Delta(String),
    /// A committed line.
    Final(String),
}

/// The API reported an error.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Fault {
    pub message: String,
    pub fatal: bool,
}

#[derive(Debug)]
pub struct Transcript {
    dialect: Dialect,
    idle_s: f64,
    max_idle_s: f64,
    partial: String,
    /// When the open line's last delta arrived (the caller's monotonic seconds).
    last_delta: Option<f64>,
    /// Wall-clock seconds of the last text, for health.
    pub last_delta_ts: Option<f64>,
    // Scribe: whether this turn has put text on screen through deltas, and the cumulative text of the last partial.
    turn_text: bool,
    last_partial: String,
}

impl Transcript {
    pub fn new(dialect: Dialect, idle_s: Option<f64>, max_idle_s: Option<f64>) -> Self {
        Self {
            dialect,
            idle_s: idle_s.unwrap_or(SEGMENT_IDLE_S),
            max_idle_s: max_idle_s.unwrap_or(SEGMENT_MAX_IDLE_S),
            partial: String::new(),
            last_delta: None,
            last_delta_ts: None,
            turn_text: false,
            last_partial: String::new(),
        }
    }

    /// The open line, for a test.
    pub fn partial(&self) -> &str {
        &self.partial
    }

    fn clear(&mut self) {
        self.partial.clear();
        self.last_delta = None;
    }

    fn delta(&mut self, text: &str, now: f64, wall: f64, out: &mut Vec<Emit>) {
        if text.is_empty() {
            return;
        }
        let glue = !self.partial.is_empty()
            && self.last_delta.is_some_and(|t| now - t >= UTTERANCE_GAP_S)
            && self.partial.ends_with(CLAUSE_END)
            && !text.chars().next().is_some_and(char::is_whitespace);
        let text = if glue { format!(" {text}") } else { text.to_owned() };
        self.partial.push_str(&text);
        self.last_delta_ts = Some(wall);
        self.last_delta = Some(now);
        out.push(Emit::Delta(text));
    }

    fn commit_rest(&mut self, wall: f64, out: &mut Vec<Emit>) {
        let rest = self.partial.trim().to_owned();
        self.clear();
        if !rest.is_empty() {
            self.last_delta_ts = Some(wall);
            out.push(Emit::Final(rest));
        }
    }

    /// One event from the socket. `now` is monotonic seconds, `wall` is the wall clock. A fault is the API's own words.
    pub fn handle(&mut self, evt: &Value, now: f64, wall: f64) -> (Vec<Emit>, Option<Fault>) {
        let mut out = Vec::new();
        let etype = evt.get("type").and_then(Value::as_str).unwrap_or("");
        if self.dialect == Dialect::Scribe {
            match etype {
                "transcript-partial" => {
                    // Partials are the whole turn so far and the model revises them, with no deltas at all. Show the text while it
                    // only grows; the final replaces whatever was shown.
                    let text = evt.get("text").and_then(Value::as_str).unwrap_or("");
                    if !self.turn_text && text.starts_with(&self.last_partial) && text.len() > self.last_partial.len() {
                        let grown = text[self.last_partial.len()..].to_owned();
                        self.delta(&grown, now, wall, &mut out);
                        self.last_partial = text.to_owned();
                    }
                    return (out, None);
                }
                "transcript-final" => {
                    let rest = self.partial.trim().to_owned();
                    self.clear();
                    self.last_partial.clear();
                    self.turn_text = false;
                    let said = evt.get("text").and_then(Value::as_str).unwrap_or("").trim();
                    let text = if said.is_empty() { rest } else { said.to_owned() };
                    if !text.is_empty() {
                        self.last_delta_ts = Some(wall);
                        out.push(Emit::Final(text));
                    }
                    return (out, None);
                }
                "transcript-delta" => {
                    if !delta_of(evt).is_empty() {
                        self.turn_text = true;
                    }
                    let d = delta_of(evt);
                    self.delta(&d, now, wall, &mut out);
                    return (out, None);
                }
                _ => {}
            }
        } else {
            // A completed event carries the turn's whole text, which the segmenter has usually committed already, line by line;
            // publishing it as well put every line on screen twice. So it only commits what is still in flight.
            if OPENAI_DONE.contains(&etype) {
                self.commit_rest(wall, &mut out);
                return (out, None);
            }
            if OPENAI_DELTA.contains(&etype) {
                let d = delta_of(evt);
                self.delta(&d, now, wall, &mut out);
                return (out, None);
            }
        }
        if etype == "error" || etype.ends_with(".failed") {
            let err = evt.get("error").filter(|e| !e.is_null()).unwrap_or(evt);
            let message = match err.get("message") {
                Some(Value::String(s)) if err.is_object() => s.clone(),
                Some(v) if err.is_object() => v.to_string(),
                _ if err.is_object() => "None".to_owned(),
                _ => err.as_str().map_or_else(|| err.to_string(), str::to_owned),
            };
            let fatal = is_fatal(&message);
            return (out, Some(Fault { message, fatal }));
        }
        (out, None)
    }

    /// Commit the open line once its deltas go quiet. Stands in for the done event an endpoint never sends, so each utterance lands
    /// as its own line. The gateway commits Scribe's turns itself, so Scribe commits nothing here.
    pub fn commit_idle(&mut self, now: f64) -> Vec<Emit> {
        let mut out = Vec::new();
        if self.dialect == Dialect::Scribe {
            return out;
        }
        let body = self.partial.trim().to_owned();
        // Punctuation with no word in it is the tail of the line already committed: hold it for the next delta.
        if body.is_empty() || !body.chars().any(char::is_alphanumeric) {
            return out;
        }
        let Some(last) = self.last_delta else { return out };
        let idle = now - last;
        if idle < self.idle_s {
            return out;
        }
        let ends = body.ends_with(SENTENCE_END);
        if idle < self.max_idle_s && !ends {
            return out; // a mid-sentence pause: more of this line is coming
        }
        if !ends {
            // The backstop fired mid-sentence. Commit only as far as the last whitespace: a stall can land inside a word
            // ("tierna" arriving as "tier" + "na"), and splitting there corrupts it.
            match body.rfind(' ') {
                Some(cut) if cut > 0 => {
                    self.partial = body[cut..].trim_start().to_owned();
                    self.last_delta = Some(now);
                    out.push(Emit::Final(body[..cut].to_owned()));
                }
                _ => {} // a single unfinished word: keep waiting
            }
            return out;
        }
        self.clear();
        out.push(Emit::Final(body));
        out
    }

    /// Commit whatever was in flight when the socket died.
    pub fn flush(&mut self) -> Vec<Emit> {
        let rest = self.partial.trim().to_owned();
        self.partial.clear();
        self.last_delta = None;
        if rest.is_empty() {
            Vec::new()
        } else {
            vec![Emit::Final(rest)]
        }
    }
}

/// A delta event's text: a string, or an object with `text`.
fn delta_of(evt: &Value) -> String {
    match evt.get("delta") {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Object(o)) => o.get("text").and_then(Value::as_str).unwrap_or("").to_owned(),
        _ => String::new(),
    }
}
