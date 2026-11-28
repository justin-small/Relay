//! Transcription backends: who turns the room's audio into source-language text (`app/transcribe.py`).
//!
//! A feed names a backend in `realtime.backend`; the pipeline asks the registry for it and gets a [`SessionSpec`] back: where to
//! connect, what to say first, how audio goes, and which line-committing dialect the events are in. Two backends are built in:
//! **scribe** (ElevenLabs Scribe v2 Realtime through the one Vercel AI Gateway key, the default) and **openai** (`gpt-live-transcribe`,
//! kept as a fallback). A module can add its own with [`Backends::register`]; core never names a module.
//!
//! An unknown backend, or one whose key is not set, falls back to OpenAI with a warning the operator sees ([`Backends::resolve`]).

use std::collections::BTreeMap;
use std::sync::Arc;

use serde_json::{json, Value};

use crate::transcript::Dialect;

pub const DEFAULT_BACKEND: &str = "scribe";
pub const FALLBACK_BACKEND: &str = "openai";

pub const OPENAI_WS: &str = "wss://api.openai.com/v1/realtime";
pub const GATEWAY_WS: &str = "wss://ai-gateway.vercel.sh/v4/ai/transcription-model";
pub const SCRIBE_MODEL: &str = "elevenlabs/scribe-v2-realtime";
/// Vercel's published price for Scribe v2 Realtime: $0.39 per hour of streamed audio.
pub const SCRIBE_PRICE_PER_HOUR: f64 = 0.39;
/// The gateway's frame limit for one binary audio message.
pub const MAX_AUDIO_FRAME_BYTES: usize = 65536;
pub const DEFAULT_OPENAI_MODEL: &str = "gpt-live-transcribe";
pub const SUBPROTOCOL: &str = "ai-gateway-transcription.v1";
pub const AUTH_PREFIX: &str = "ai-gateway-auth.";

/// How audio goes up the socket.
#[derive(Clone, Debug, PartialEq)]
pub enum Audio {
    /// OpenAI: base64 in a JSON `input_audio_buffer.append`, and a `commit` when the speaker pauses or a turn runs long.
    OpenAiAppend { commit_gap_s: f64, max_turn_s: f64 },
    /// Scribe: binary frames of at most [`MAX_AUDIO_FRAME_BYTES`]; the gateway commits turns itself.
    Binary,
}

/// Everything a session needs to open its socket and read its events.
#[derive(Clone, Debug)]
pub struct SessionSpec {
    pub backend: String,
    pub model: String,
    pub url: String,
    pub subprotocols: Vec<String>,
    pub headers: Vec<(String, String)>,
    /// The first text frame, sent when the socket is open.
    pub start: Value,
    pub audio: Audio,
    pub dialect: Dialect,
    pub price_per_hour: Option<f64>,
    pub idle_s: Option<f64>,
    pub max_idle_s: Option<f64>,
}

/// Makes a session's spec from the settings, the saved key and the source language.
pub type MakeSession = Arc<dyn Fn(&Value, &str, &str) -> SessionSpec + Send + Sync>;

/// One way to transcribe.
#[derive(Clone)]
pub struct Backend {
    pub id: String,
    pub label: String,
    /// The saved secret the backend needs (`None`: it needs none).
    pub key_setting: Option<String>,
    pub key_label: String,
    pub price_per_hour: Option<f64>,
    /// `(cfg, key, source language)` to the session's spec.
    pub session: MakeSession,
}

impl Backend {
    pub fn key(&self, cfg: &Value) -> String {
        self.key_setting.as_deref().map(|k| cfg.get(k).and_then(Value::as_str).unwrap_or("").trim().to_owned()).unwrap_or_default()
    }

    pub fn available(&self, cfg: &Value) -> bool {
        self.key_setting.is_none() || !self.key(cfg).is_empty()
    }
}

fn realtime<'a>(cfg: &'a Value, name: &str) -> Option<&'a Value> {
    cfg.get("realtime").and_then(|r| r.get(name)).filter(|v| !v.is_null())
}

fn text<'a>(cfg: &'a Value, name: &str) -> Option<&'a str> {
    realtime(cfg, name).and_then(Value::as_str).filter(|s| !s.is_empty())
}

fn secs(cfg: &Value, name: &str) -> Option<f64> {
    realtime(cfg, name).and_then(Value::as_f64)
}

/// Relay hears one source language, so its code is the one every backend is told.
fn source_iso(_source: &str) -> &'static str {
    relay_core::languages::SOURCE.iso
}

/// `%`-encode a model id for a query string (everything but unreserved characters).
pub fn quote(s: &str) -> String {
    let mut out = String::new();
    for b in s.bytes() {
        if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') {
            out.push(b as char);
        } else {
            out.push_str(&format!("%{b:02X}"));
        }
    }
    out
}

fn openai_session(cfg: &Value, key: &str, source: &str) -> SessionSpec {
    // Nothing can be translated without the transcript, so an empty transcribe_model falls back to the default.
    let model = text(cfg, "transcribe_model").unwrap_or(DEFAULT_OPENAI_MODEL).to_owned();
    let mut input = json!({
        "format": {"type": "audio/pcm", "rate": 24000},
        "transcription": {"model": model, "languages": [source_iso(source)], "delay": "low"},
        "turn_detection": null,
    });
    if let Some(nr) = text(cfg, "noise_reduction") {
        input["noise_reduction"] = json!({"type": nr});
    }
    SessionSpec {
        backend: "openai".to_owned(),
        model,
        // No model in the URL: that would open a conversation session.
        url: format!("{OPENAI_WS}?intent=transcription"),
        subprotocols: Vec::new(),
        headers: vec![("Authorization".to_owned(), format!("Bearer {key}"))],
        start: json!({"type": "session.update", "session": {"type": "transcription", "audio": {"input": input}}}),
        audio: Audio::OpenAiAppend { commit_gap_s: 0.6, max_turn_s: 15.0 },
        dialect: Dialect::OpenAi,
        price_per_hour: None,
        idle_s: secs(cfg, "segment_idle_s"),
        max_idle_s: secs(cfg, "segment_max_idle_s"),
    }
}

/// The wire format is the one @ai-sdk/gateway speaks: the model id rides the URL, the key rides the `Sec-WebSocket-Protocol`
/// handshake, the first text frame is `transcription-stream.start`, and audio goes as binary frames. ElevenLabs commits a turn on
/// its own after a pause, so nothing is committed from here.
fn scribe_session(cfg: &Value, key: &str, source: &str) -> SessionSpec {
    let model = text(cfg, "scribe_model").unwrap_or(SCRIBE_MODEL).to_owned();
    SessionSpec {
        backend: "scribe".to_owned(),
        url: format!("{GATEWAY_WS}?ai-model-id={}", quote(&model)),
        model,
        subprotocols: vec![SUBPROTOCOL.to_owned(), format!("{AUTH_PREFIX}{key}")],
        headers: Vec::new(),
        start: json!({
            "type": "transcription-stream.start",
            "inputAudioFormat": {"type": "audio/pcm", "rate": 24000},
            "providerOptions": {"elevenlabs": {
                "languageCode": source_iso(source),
                "streaming": {"commitStrategy": "vad", "includeTimestamps": true, "vadSilenceThresholdSecs": 0.8},
            }},
        }),
        audio: Audio::Binary,
        dialect: Dialect::Scribe,
        price_per_hour: Some(SCRIBE_PRICE_PER_HOUR),
        idle_s: secs(cfg, "segment_idle_s"),
        max_idle_s: secs(cfg, "segment_max_idle_s"),
    }
}

/// The backends a start can choose from.
pub struct Backends {
    all: BTreeMap<String, Backend>,
}

impl Default for Backends {
    fn default() -> Self {
        Self::builtin()
    }
}

impl Backends {
    /// Scribe and OpenAI.
    pub fn builtin() -> Self {
        let mut b = Self { all: BTreeMap::new() };
        b.register(Backend {
            id: "scribe".into(),
            label: "ElevenLabs Scribe v2 Realtime (Vercel)".into(),
            key_setting: Some("vercel_api_key".into()),
            key_label: "a Vercel AI Gateway key".into(),
            price_per_hour: Some(SCRIBE_PRICE_PER_HOUR),
            session: Arc::new(scribe_session),
        });
        b.register(Backend {
            id: "openai".into(),
            label: "OpenAI".into(),
            key_setting: Some("openai_api_key".into()),
            key_label: "an OpenAI API key".into(),
            price_per_hour: None,
            session: Arc::new(openai_session),
        });
        b
    }

    pub fn register(&mut self, backend: Backend) {
        self.all.insert(backend.id.clone(), backend);
    }

    /// The built-in backends cannot be removed.
    pub fn unregister(&mut self, id: &str) -> Result<(), &'static str> {
        if id == DEFAULT_BACKEND || id == FALLBACK_BACKEND {
            return Err("the built-in backends cannot be removed");
        }
        self.all.remove(id);
        Ok(())
    }

    pub fn get(&self, id: &str) -> Option<&Backend> {
        self.all.get(id)
    }

    /// Every registered backend, the built-in ones first.
    pub fn list(&self) -> Vec<&Backend> {
        let mut out: Vec<&Backend> = [DEFAULT_BACKEND, FALLBACK_BACKEND].iter().filter_map(|i| self.all.get(*i)).collect();
        out.extend(self.all.iter().filter(|(i, _)| i.as_str() != DEFAULT_BACKEND && i.as_str() != FALLBACK_BACKEND).map(|(_, b)| b));
        out
    }

    /// The backend the main feed runs on, and a sentence for the operator when it is not the one they chose. The chosen backend runs
    /// when it is registered and its key is saved. Otherwise OpenAI runs when its key is saved, with the reason. With neither
    /// usable the chosen backend comes back (or OpenAI, if the choice is unknown) so the start is refused naming the key it needs.
    pub fn resolve(&self, cfg: &Value) -> (&Backend, Option<String>) {
        let wanted = text(cfg, "backend").unwrap_or(DEFAULT_BACKEND);
        let fallback = &self.all[FALLBACK_BACKEND];
        let Some(backend) = self.all.get(wanted) else {
            let warning = format!("The transcription backend \"{wanted}\" is not installed or switched on, so OpenAI is used.");
            tracing::warn!("{warning}");
            return (fallback, Some(warning));
        };
        if backend.available(cfg) {
            return (backend, None);
        }
        if backend.id != FALLBACK_BACKEND && fallback.available(cfg) {
            let needs = if backend.key_label.is_empty() { "its key" } else { backend.key_label.as_str() };
            let warning = format!("{} needs {needs}, which is not set, so OpenAI is used.", backend.label);
            tracing::warn!("{warning}");
            return (fallback, Some(warning));
        }
        (backend, None)
    }
}
