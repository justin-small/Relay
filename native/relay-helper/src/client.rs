//! The connection to Relay: dial out, say hello, answer calls, reconnect with a growing pause when it drops.

use std::sync::Arc;
use std::time::Duration;

use futures_util::{SinkExt, StreamExt};
use tokio::sync::mpsc;
use tokio_tungstenite::tungstenite::client::IntoClientRequest;
use tokio_tungstenite::tungstenite::protocol::WebSocketConfig;
use tokio_tungstenite::tungstenite::Message;

use crate::capability::Registry;
use crate::config::Config;
use crate::protocol::{FromHelper, ToHelper, MAX_FRAME};

pub const VERSION: &str = env!("CARGO_PKG_VERSION");

pub fn os_name() -> String {
    std::env::consts::OS.to_string()
}

fn ws_config() -> WebSocketConfig {
    WebSocketConfig::default().max_message_size(Some(MAX_FRAME)).max_frame_size(Some(MAX_FRAME))
}

fn endpoint(relay_url: &str) -> String {
    format!("{}/ws/helper", relay_url.trim_end_matches('/'))
}

/// Trade a pairing code for a token and keep it in `config`. Returns the saved config.
pub async fn pair(mut config: Config, code: &str) -> Result<Config, String> {
    let request = endpoint(&config.relay_url).into_client_request().map_err(|_| "That is not a Relay address.".to_string())?;
    let (mut ws, _) = tokio_tungstenite::connect_async_with_config(request, Some(ws_config()), false)
        .await
        .map_err(|e| format!("Cannot reach Relay: {e}"))?;
    let name = if config.name.is_empty() { default_name() } else { config.name.clone() };
    let hello = FromHelper::Pair { code: code.trim().to_string(), name: name.clone(), os: os_name(), version: VERSION.to_string() };
    ws.send(Message::text(serde_json::to_string(&hello).map_err(|e| e.to_string())?)).await.map_err(|e| e.to_string())?;
    let reply = tokio::time::timeout(Duration::from_secs(15), ws.next()).await.map_err(|_| "Relay did not answer.".to_string())?;
    match reply {
        Some(Ok(Message::Text(t))) => match serde_json::from_str::<ToHelper>(&t) {
            Ok(ToHelper::Paired { helper_id, token }) => {
                config.helper_id = helper_id;
                config.token = token;
                config.name = name;
                Ok(config)
            }
            Ok(ToHelper::Refused { reason }) => Err(reason),
            _ => Err("Relay answered with something this helper does not understand.".to_string()),
        },
        _ => Err("Relay closed the connection.".to_string()),
    }
}

fn default_name() -> String {
    std::env::var("COMPUTERNAME").or_else(|_| std::env::var("HOSTNAME")).unwrap_or_else(|_| "Show computer".to_string())
}

/// One connection, start to finish. Ok(true) means Relay refused us (stop retrying); Ok(false) means it dropped.
pub async fn session(config: &Config, registry: &Registry) -> Result<bool, String> {
    let request = endpoint(&config.relay_url).into_client_request().map_err(|_| "That is not a Relay address.".to_string())?;
    let (ws, _) = tokio_tungstenite::connect_async_with_config(request, Some(ws_config()), false)
        .await
        .map_err(|e| format!("Cannot reach Relay: {e}"))?;
    let (mut sink, mut stream) = ws.split();
    let hello = FromHelper::Hello {
        helper_id: config.helper_id.clone(),
        name: config.name.clone(),
        os: os_name(),
        version: VERSION.to_string(),
        capabilities: registry.infos(),
    };
    // The token travels in the first frame, not the address: addresses end up in logs.
    let mut first = serde_json::to_value(&hello).map_err(|e| e.to_string())?;
    first["token"] = serde_json::Value::String(config.token.clone());
    sink.send(Message::text(first.to_string())).await.map_err(|e| e.to_string())?;

    let (tx, mut rx) = mpsc::channel::<FromHelper>(64);
    let writer = tokio::spawn(async move {
        while let Some(msg) = rx.recv().await {
            let Ok(text) = serde_json::to_string(&msg) else { continue };
            if sink.send(Message::text(text)).await.is_err() {
                break;
            }
        }
    });
    let mut refused = false;
    while let Some(frame) = stream.next().await {
        let Ok(frame) = frame else { break };
        let Message::Text(text) = frame else {
            if matches!(frame, Message::Close(_)) {
                break;
            }
            continue;
        };
        match serde_json::from_str::<ToHelper>(&text) {
            Ok(ToHelper::Call { id, cap, op, args }) => {
                let (registry, tx) = (registry.clone(), tx.clone());
                tokio::spawn(async move {
                    let answer = match registry.call(&cap, &op, args).await {
                        Ok(data) => FromHelper::ok(id, data),
                        Err(message) => FromHelper::err(id, message),
                    };
                    let _ = tx.send(answer).await;
                });
            }
            Ok(ToHelper::Ping) => {
                let _ = tx.send(FromHelper::Pong).await;
            }
            Ok(ToHelper::Refused { .. }) => {
                refused = true;
                break;
            }
            Ok(ToHelper::Welcome { .. }) | Ok(ToHelper::Paired { .. }) | Err(_) => {}
        }
    }
    drop(tx);
    writer.abort();
    Ok(refused)
}

/// Stay connected until Relay refuses this helper: reconnect after 1 s, doubling to at most 30 s.
pub async fn run(config: Config, registry: Registry) -> Result<(), String> {
    let mut pause = Duration::from_secs(1);
    loop {
        match session(&config, &registry).await {
            Ok(true) => return Err("Relay refused this helper. Pair it again from Relay's admin page.".to_string()),
            Ok(false) => pause = Duration::from_secs(1),
            Err(_) => pause = (pause * 2).min(Duration::from_secs(30)),
        }
        tokio::time::sleep(pause).await;
    }
}

pub fn registry_for(config: &Config, opener: Arc<dyn crate::propresenter::Opener>) -> Registry {
    let mut r = Registry::new();
    let scratch = std::env::temp_dir().join("relay-helper");
    r.register(Arc::new(crate::propresenter::ProPresenter::new(config.propresenter.clone(), opener, scratch)));
    r
}
