//! The messages Relay and a helper exchange: JSON text frames over one WebSocket the helper opens.
//! The same shapes are documented in docs/helper.md and read by app/helperhub.py.

use serde::{Deserialize, Serialize};
use serde_json::Value;

/// What a helper can do, as it tells Relay when it connects.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct CapInfo {
    pub id: String,
    pub version: u32,
    /// The operations Relay may call, so the admin page can show them.
    pub ops: Vec<String>,
}

/// Relay to helper.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum ToHelper {
    /// Sent after `Hello` is accepted.
    Welcome { relay_version: String },
    /// Answer to `Pair`: keep the token, connect with it from now on.
    Paired { helper_id: String, token: String },
    /// Run `op` of capability `cap`; answer with `Result` carrying the same `id`.
    Call { id: u64, cap: String, op: String, #[serde(default)] args: Value },
    Ping,
    /// Relay refuses the connection (bad token, helper removed); the helper stops retrying until paired again.
    Refused { reason: String },
}

/// Helper to Relay.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum FromHelper {
    /// First message with a token: who this is and what it can do.
    Hello { helper_id: String, name: String, os: String, version: String, capabilities: Vec<CapInfo> },
    /// First message without a token: trade a pairing code (made on Relay's admin page) for one.
    Pair { code: String, name: String, os: String, version: String },
    Result {
        id: u64,
        ok: bool,
        #[serde(skip_serializing_if = "Option::is_none")]
        data: Option<Value>,
        #[serde(skip_serializing_if = "Option::is_none")]
        error: Option<String>,
    },
    Pong,
    /// Something a capability noticed on its own (for example ProPresenter started).
    Event { cap: String, name: String, data: Value },
}

impl FromHelper {
    pub fn ok(id: u64, data: Value) -> Self {
        FromHelper::Result { id, ok: true, data: Some(data), error: None }
    }
    pub fn err(id: u64, message: impl Into<String>) -> Self {
        FromHelper::Result { id, ok: false, data: None, error: Some(message.into()) }
    }
}

/// The largest frame either side sends or accepts: songs are kilobytes, so this is generous.
pub const MAX_FRAME: usize = 24 * 1024 * 1024;
