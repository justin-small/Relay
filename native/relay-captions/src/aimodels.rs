//! Which provider and model an AI task uses (`app/aimodels.py`, #495): the part captions need, `normalize` and `resolve` for the
//! translation task, on the settings as config.json stores them. The AI models page's catalogue is not here.
//!
//! Every task goes through the one Vercel AI Gateway key by default. The OpenAI key stays selectable per task as a fallback, and is
//! used on its own when a task wants Vercel but only the OpenAI key is saved, with a sentence saying why.

use serde_json::{json, Value};

pub const VERCEL: &str = "vercel";
pub const OPENAI: &str = "openai";
pub const VERCEL_CHAT_URL: &str = "https://ai-gateway.vercel.sh/v1/chat/completions";
pub const OPENAI_CHAT_URL: &str = "https://api.openai.com/v1/chat/completions";
pub const MODEL_ID_MAX: usize = 120;

/// One AI task: what it is called and what it starts on.
pub struct Task {
    pub id: &'static str,
    pub label: &'static str,
    /// The Vercel model id.
    pub model: &'static str,
    /// The model used through the OpenAI key.
    pub openai_model: &'static str,
}

pub const TASKS: [Task; 4] = [
    Task { id: "translation", label: "Translation", model: "openai/gpt-6-luna", openai_model: "gpt-6-luna" },
    Task { id: "command_center", label: "Command Center chat", model: "anthropic/claude-sonnet-5.5", openai_model: "gpt-6-luna" },
    Task { id: "summaries", label: "Summaries and notes", model: "openai/gpt-6-luna", openai_model: "gpt-6-luna" },
    Task { id: "speech", label: "Spoken translation", model: "openai/tts-1", openai_model: "tts-1" },
];

pub fn task(id: &str) -> Option<&'static Task> {
    TASKS.iter().find(|t| t.id == id)
}

/// Where one task's requests go right now, and why that is not what the operator chose (`None`: it is).
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Route {
    pub task: &'static str,
    pub provider: &'static str,
    pub model: String,
    pub key: String,
    pub url: &'static str,
    pub warning: Option<String>,
}

impl Route {
    pub fn label(&self) -> &'static str {
        if self.provider == VERCEL {
            "Vercel AI Gateway"
        } else {
            "OpenAI key"
        }
    }
}

fn model_id(value: Option<&Value>, fallback: &str) -> String {
    let text = value.and_then(Value::as_str).unwrap_or("").trim();
    if text.is_empty() || text.chars().count() > MODEL_ID_MAX || text.chars().any(char::is_whitespace) {
        fallback.to_owned()
    } else {
        text.to_owned()
    }
}

/// `ai` as saved: every task present, providers known, model ids plain. A config written before #495 named the translation model in
/// `realtime.text_model` (an OpenAI id): it becomes the translation's OpenAI model, and its Vercel id when it is not the old default.
pub fn normalize(raw: Option<&Value>, legacy_text_model: &str) -> Value {
    let saved = raw.and_then(|r| r.get("tasks")).filter(|t| t.is_object());
    let mut out = serde_json::Map::new();
    for t in &TASKS {
        let cur = saved.and_then(|s| s.get(t.id)).filter(|c| c.is_object());
        let (mut openai_default, mut vercel_default) = (t.openai_model.to_owned(), t.model.to_owned());
        if t.id == "translation" && !legacy_text_model.is_empty() && cur.is_none() {
            openai_default = legacy_text_model.to_owned();
            if legacy_text_model != t.openai_model && !legacy_text_model.contains('/') {
                vercel_default = format!("openai/{legacy_text_model}");
            }
        }
        let provider = match cur.and_then(|c| c.get("provider")).and_then(Value::as_str) {
            Some(OPENAI) => OPENAI,
            _ => VERCEL,
        };
        out.insert(
            t.id.to_owned(),
            json!({
                "provider": provider,
                "model": model_id(cur.and_then(|c| c.get("model")), &vercel_default),
                "openai_model": model_id(cur.and_then(|c| c.get("openai_model")), &openai_default),
            }),
        );
    }
    json!({ "tasks": out })
}

fn key(cfg: &Value, provider: &str) -> String {
    let setting = if provider == VERCEL { "vercel_api_key" } else { "openai_api_key" };
    cfg.get(setting).and_then(Value::as_str).unwrap_or("").trim().to_owned()
}

fn route(t: &'static Task, saved: &Value, cfg: &Value, provider: &'static str, warning: Option<String>) -> Route {
    let (model, url) = if provider == VERCEL {
        (saved["model"].as_str().unwrap_or(t.model), VERCEL_CHAT_URL)
    } else {
        (saved["openai_model"].as_str().unwrap_or(t.openai_model), OPENAI_CHAT_URL)
    };
    Route { task: t.id, provider, model: model.to_owned(), key: key(cfg, provider), url, warning }
}

/// The provider a task runs on. Vercel when chosen and its key is saved; when it is chosen but only the OpenAI key is saved, the
/// OpenAI key with a warning. A task switched to the OpenAI key stays on it. With no usable key the chosen provider comes back with
/// an empty key, so the caller can say which key is missing.
pub fn resolve(cfg: &Value, task_id: &str) -> Option<Route> {
    let t = task(task_id)?;
    let legacy = cfg.get("realtime").and_then(|r| r.get("text_model")).and_then(Value::as_str).unwrap_or("");
    let all = normalize(cfg.get("ai"), legacy);
    let saved = &all["tasks"][t.id];
    let wanted = if saved["provider"] == OPENAI { OPENAI } else { VERCEL };
    if !key(cfg, wanted).is_empty() || wanted == OPENAI {
        return Some(route(t, saved, cfg, wanted, None));
    }
    if !key(cfg, OPENAI).is_empty() {
        let warning = format!("{} is set to the Vercel AI Gateway, but its key is not saved, so the OpenAI key is used.", t.label);
        tracing::warn!("{warning}");
        return Some(route(t, saved, cfg, OPENAI, Some(warning)));
    }
    Some(route(t, saved, cfg, wanted, None))
}
