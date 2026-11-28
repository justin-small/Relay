//! Remote access (#50, ported from `app/modules/remote_access` in #662): viewers off the venue's network open the caption and
//! translation links over the internet.
//!
//! Switched on, this install enrolls once with an enrollment code and gets its own address,
//! `https://<random>.cloud.relaysystems.dev`, made by Relay's cloud (relay-cloud). cloudflared then keeps an outbound-only tunnel
//! from this machine to Cloudflare: no port forwarding, no router changes. The tunnel reaches the viewer port only; the operator
//! panel is never on it. Switched off, cloudflared stops and the address is kept for next time.
//!
//! Not in the Rust module trait yet, so still served by the Python app while both run: the admin page and its script, the
//! `test` button, the status line that lists the viewer links, and the restart when the routed network port changes.

pub mod cloud;
pub mod service;
pub mod tunnel;

use std::sync::{Arc, Mutex, OnceLock};

use axum::extract::State;
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use relay_core::gate::gate;
use relay_core::future::BoxFuture;
use relay_core::{AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::{json, Map, Value};

use crate::service::{enrolled, url, Service, KEY};
use crate::tunnel::State as TunnelState;

pub const MANIFEST: Manifest = Manifest {
    id: "remote_access",
    name: "Remote access",
    version: "1.1.0",
    summary: "Gives the viewer links an internet address, so people off the venue's Wi-Fi can follow along.",
    settings_key: KEY,
    guide: "https://github.com/justin-small/relay#remote-access",
    uses: &[],
    route_prefixes: &["/api/admin/remote-access"],
    renamed_from: "",
    section: "Building",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission {
        id: "remote_access.view",
        label: "See the remote viewer link and whether it is connected",
        group: "Remote access",
        min_role: "viewer",
    },
    Permission {
        id: "remote_access.manage",
        label: "Enroll this install or release its internet address",
        group: "Remote access",
        min_role: "admin",
    },
];

const FIELDS: [&str; 6] = ["cloud_url", "install_id", "hostname", "secret", "tunnel_token", "pending_secret"];

/// What the page says about the tunnel, by state. Fixed sentences: nothing from cloudflared's own output.
fn tunnel_text(state: TunnelState) -> &'static str {
    match state {
        TunnelState::Off => "The tunnel is not running.",
        TunnelState::Missing => "cloudflared is missing from this install of Relay, so the tunnel can't run. Reinstall Relay.",
        TunnelState::Connecting => "Connecting to Cloudflare…",
        TunnelState::Connected => "Connected. Viewers anywhere can open the link.",
        TunnelState::Retrying => "Lost the connection to Cloudflare. Trying again…",
    }
}

type Slot = Arc<Mutex<Option<Service>>>;

pub struct RemoteAccess {
    host: OnceLock<Host>,
    service: Slot,
}

impl Default for RemoteAccess {
    fn default() -> Self {
        Self::new()
    }
}

impl RemoteAccess {
    pub fn new() -> Self {
        Self { host: OnceLock::new(), service: Slot::default() }
    }

    /// The service a test builds itself (a fake cloud and a quick back-off), in place of the one `start` makes.
    pub fn with_service(service: Service) -> Self {
        let me = Self::new();
        let _ = me.host.set(service.host.clone());
        if let Ok(mut s) = me.service.lock() {
            *s = Some(service);
        }
        me
    }
}

fn service_of(slot: &Slot) -> Option<Service> {
    slot.lock().ok().and_then(|s| s.clone())
}

fn error(status: StatusCode, message: &str) -> Response {
    (status, Json(json!({ "error": message }))).into_response()
}

fn view(service: &Service) -> Value {
    let s = service.settings();
    let state = service.tunnel_state();
    let cloud_error = service.cloud_error();
    json!({
        "enrolled": enrolled(&s),
        "url": url(&s),
        "state": state.name(),
        "connections": service.tunnel_connections(),
        "detail": tunnel_text(state),
        "cloud_error": if cloud_error.is_empty() { "" } else { cloud::message_for(&cloud_error) },
        "cloudflared": tunnel::find_binary().is_some(),
    })
}

async fn show(State(slot): State<Slot>) -> Response {
    match service_of(&slot) {
        Some(service) => Json(view(&service)).into_response(),
        None => error(StatusCode::SERVICE_UNAVAILABLE, "Remote access is starting."),
    }
}

async fn enroll(State(slot): State<Slot>, Json(payload): Json<Value>) -> Response {
    let Some(service) = service_of(&slot) else { return error(StatusCode::SERVICE_UNAVAILABLE, "Remote access is starting.") };
    let code = payload.get("code").map(|c| c.as_str().map_or_else(|| c.to_string(), str::to_owned)).unwrap_or_default();
    let code = code.trim();
    if code.is_empty() || code.chars().count() > 40 {
        return error(StatusCode::BAD_REQUEST, "Enter the enrollment code you were given.");
    }
    if enrolled(&service.settings()) {
        return error(StatusCode::CONFLICT, "This install already has an address. Release it first to enroll again.");
    }
    match service.enroll(code).await {
        Ok(()) => Json(view(&service)).into_response(),
        Err(e) => {
            tracing::warn!("Remote access: enroll refused ({})", e.kind);
            error(if matches!(e.status, 403 | 409) { StatusCode::BAD_REQUEST } else { StatusCode::BAD_GATEWAY }, &e.message())
        }
    }
}

async fn release(State(slot): State<Slot>) -> Response {
    let Some(service) = service_of(&slot) else { return error(StatusCode::SERVICE_UNAVAILABLE, "Remote access is starting.") };
    match service.release().await {
        Ok(()) => Json(view(&service)).into_response(),
        Err(e) => {
            tracing::warn!("Remote access: release failed ({})", e.kind);
            error(StatusCode::BAD_GATEWAY, &e.message())
        }
    }
}

impl Module for RemoteAccess {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    fn secret_settings(&self) -> &'static [&'static str] {
        &["secret", "tunnel_token", "pending_secret"]
    }

    fn defaults(&self) -> Value {
        let mut m: Map<String, Value> = FIELDS.iter().map(|k| ((*k).to_owned(), json!(""))).collect();
        m.insert("cloud_url".into(), json!(""));
        Value::Object(m)
    }

    fn clean(&self, raw: &Value) -> Value {
        let text = |k: &str| raw.get(k).and_then(Value::as_str).map_or_else(String::new, |s| s.trim().to_owned());
        let mut out: Map<String, Value> = FIELDS.iter().map(|k| ((*k).to_owned(), json!(text(k)))).collect();
        if text("cloud_url").is_empty() {
            out.insert("cloud_url".into(), json!(cloud::DEFAULT_URL));
        }
        Value::Object(out)
    }

    fn routes(&self) -> Router {
        let Some(host) = self.host.get() else { return Router::new() };
        Router::new()
            .route("/api/admin/remote-access", gate(host, "remote_access.view", get(show)))
            .route("/api/admin/remote-access/enroll", gate(host, "remote_access.manage", post(enroll)))
            .route("/api/admin/remote-access/release", gate(host, "remote_access.manage", post(release)))
            .with_state(Arc::clone(&self.service))
    }

    fn attach(&self, host: &Host) {
        let _ = self.host.set(host.clone());
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            let service = {
                let mut slot = self.service.lock().map_err(|_| AppError::new(relay_core::ErrorCode::Internal))?;
                slot.get_or_insert_with(|| Service::new(host.clone(), tunnel::Tunnel::new(host.net().clone()))).clone()
            };
            service.begin().await;
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            if let Some(service) = service_of(&self.service) {
                service.end().await;
            }
        })
    }

    fn health(&self) -> Health {
        let Some(service) = service_of(&self.service) else { return Health::idle() };
        let settings = service.settings();
        if !enrolled(&settings) {
            return Health::new(HealthState::Warn, "Not set up: enter an enrollment code on the Remote access page");
        }
        if service.tunnel_state() == TunnelState::Missing {
            return Health::new(HealthState::Error, "cloudflared is missing from this install");
        }
        if service.cloud_error() == "unauthorised" {
            return Health::new(HealthState::Error, "The address was released");
        }
        match service.tunnel_state() {
            TunnelState::Connected => Health::new(HealthState::Ok, "Live: viewers can open the remote link"),
            TunnelState::Retrying => Health::new(HealthState::Error, "Can't reach Cloudflare"),
            _ => Health::new(HealthState::Warn, "Connecting"),
        }
    }
}
