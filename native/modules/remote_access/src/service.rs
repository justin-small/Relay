//! Remote access as a whole: enrolling with Relay's cloud, checking in at start, running the tunnel, releasing.
//!
//! Settings live under "remote_access" in config.json. The secret and the tunnel token never reach the browser. Switching the
//! module off stops cloudflared but keeps the address: switching it on again brings the same link back. Only "Release this
//! address" gives it up.

use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;
use relay_core::Host;
use serde_json::{json, Value};
use tokio::task::JoinHandle;

use crate::cloud::{Cloud, CloudError};
use crate::tunnel::{State, Tunnel};

pub const KEY: &str = "remote_access";
/// Relay's cloud releases an address that hasn't checked in for 90 days, so a box left running checks in this often.
pub const CHECK_IN_EVERY: Duration = Duration::from_secs(12 * 3600);

fn text(settings: &Value, key: &str) -> String {
    settings.get(key).and_then(Value::as_str).unwrap_or("").to_owned()
}

pub fn enrolled(settings: &Value) -> bool {
    ["secret", "hostname", "tunnel_token"].iter().all(|k| !text(settings, k).is_empty())
}

pub fn url(settings: &Value) -> String {
    if enrolled(settings) { format!("https://{}", text(settings, "hostname")) } else { String::new() }
}

fn version() -> String {
    std::env::var("RELAY_VERSION").ok().filter(|v| !v.is_empty()).unwrap_or_else(|| "dev".to_owned())
}

/// A fresh secret for this install: 32 random bytes, URL-safe, like Python's `secrets.token_urlsafe(32)`.
fn new_secret() -> String {
    let mut bytes = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut bytes);
    URL_SAFE_NO_PAD.encode(bytes)
}

#[derive(Clone)]
pub struct Service {
    pub host: Host,
    pub cloud: Cloud,
    tunnel: Tunnel,
    /// The kind of the last failed call to the cloud, "" when the last one worked.
    cloud_error: Arc<Mutex<String>>,
    lock: Arc<tokio::sync::Mutex<()>>,
    beat: Arc<Mutex<Option<JoinHandle<()>>>>,
    every: Duration,
}

impl Service {
    pub fn new(host: Host, tunnel: Tunnel) -> Self {
        let cloud = Cloud::new(host.net().clone());
        Self::with(host, cloud, tunnel, CHECK_IN_EVERY)
    }

    pub fn with(host: Host, cloud: Cloud, tunnel: Tunnel, every: Duration) -> Self {
        Self { host, cloud, tunnel, cloud_error: Arc::default(), lock: Arc::default(), beat: Arc::default(), every }
    }

    pub fn settings(&self) -> Value {
        let mut s = self.host.settings(KEY).unwrap_or_else(|| json!({}));
        if text(&s, "cloud_url").is_empty() {
            s["cloud_url"] = json!(crate::cloud::DEFAULT_URL);
        }
        s
    }

    pub fn cloud_error(&self) -> String {
        self.cloud_error.lock().map(|e| e.clone()).unwrap_or_default()
    }

    fn set_error(&self, kind: &str) {
        if let Ok(mut e) = self.cloud_error.lock() {
            *e = kind.to_owned();
        }
    }

    fn save(&self, changes: &Value) {
        if let Err(e) = self.host.save_settings(KEY, changes) {
            tracing::error!("Remote access: settings could not be saved: {e}");
        }
    }

    /// At start (and after enrolling): check in, then run the tunnel with the token the cloud holds.
    pub async fn begin(&self) {
        {
            let _guard = self.lock.lock().await;
            let s = self.settings();
            if !enrolled(&s) {
                self.tunnel.stop().await;
                return;
            }
            let mut token = text(&s, "tunnel_token");
            match self.cloud.check_in(&text(&s, "cloud_url"), &text(&s, "secret"), self.host.viewer_port(), &version()).await {
                Err(e) => {
                    self.set_error(e.kind);
                    tracing::warn!("Remote access: check-in failed ({})", e.kind);
                    if e.kind == "unauthorised" {
                        // Released from the cloud side: the token no longer works, so don't keep trying it.
                        self.tunnel.stop().await;
                        return;
                    }
                    // The cloud being unreachable doesn't stop the tunnel: cloudflared talks to Cloudflare, not to us.
                }
                Ok(got) => {
                    self.set_error("");
                    if got.tunnel_token != token || got.hostname != text(&s, "hostname") {
                        self.save(&json!({ "tunnel_token": got.tunnel_token, "hostname": got.hostname }));
                        token = got.tunnel_token;
                    }
                }
            }
            self.tunnel.start(&token);
        }
        self.ensure_heartbeat();
    }

    fn ensure_heartbeat(&self) {
        let Ok(mut beat) = self.beat.lock() else { return };
        if beat.as_ref().is_some_and(|b| !b.is_finished()) {
            return;
        }
        let me = self.clone();
        *beat = Some(tokio::spawn(async move {
            loop {
                tokio::time::sleep(me.every).await;
                me.check_in_once().await;
            }
        }));
    }

    /// The heartbeat's check-in: the same as `begin` without starting another heartbeat.
    async fn check_in_once(&self) {
        let _guard = self.lock.lock().await;
        let s = self.settings();
        if !enrolled(&s) {
            return;
        }
        match self.cloud.check_in(&text(&s, "cloud_url"), &text(&s, "secret"), self.host.viewer_port(), &version()).await {
            Ok(got) => {
                self.set_error("");
                if got.tunnel_token != text(&s, "tunnel_token") || got.hostname != text(&s, "hostname") {
                    self.save(&json!({ "tunnel_token": got.tunnel_token, "hostname": got.hostname }));
                }
                self.tunnel.start(&got.tunnel_token);
            }
            Err(e) => {
                self.set_error(e.kind);
                tracing::warn!("Remote access: check-in failed ({})", e.kind);
                if e.kind == "unauthorised" {
                    self.tunnel.stop().await;
                }
            }
        }
    }

    /// Spend an enrollment code.
    pub async fn enroll(&self, code: &str) -> Result<(), CloudError> {
        {
            let _guard = self.lock.lock().await;
            let s = self.settings();
            // The secret is saved before the call, so a retry after a lost reply sends the same one and the cloud answers with
            // the same install instead of refusing a code it already spent.
            let mut secret = text(&s, "pending_secret");
            if secret.is_empty() {
                secret = new_secret();
                self.save(&json!({ "pending_secret": secret }));
            }
            let got = self.cloud.enroll(&text(&s, "cloud_url"), code, &secret, self.host.viewer_port(), &version()).await?;
            self.save(&json!({
                "secret": secret, "pending_secret": "", "install_id": got.install_id,
                "hostname": got.hostname, "tunnel_token": got.tunnel_token,
            }));
            self.set_error("");
            tracing::info!("Remote access: enrolled as {}", got.hostname);
        }
        self.begin().await;
        Ok(())
    }

    /// Give the address up for good. Fails unless the cloud has already let it go.
    pub async fn release(&self) -> Result<(), CloudError> {
        let _guard = self.lock.lock().await;
        let s = self.settings();
        if !text(&s, "secret").is_empty() {
            if let Err(e) = self.cloud.release(&text(&s, "cloud_url"), &text(&s, "secret")).await {
                if e.kind != "unauthorised" {
                    return Err(e);
                }
            }
        }
        self.tunnel.stop().await;
        self.save(&json!({ "secret": "", "pending_secret": "", "install_id": "", "hostname": "", "tunnel_token": "" }));
        self.set_error("");
        tracing::info!("Remote access: address released");
        Ok(())
    }

    pub async fn end(&self) {
        let beat = self.beat.lock().ok().and_then(|mut b| b.take());
        if let Some(beat) = beat {
            beat.abort();
        }
        self.tunnel.stop().await;
    }

    pub fn tunnel_state(&self) -> State {
        self.tunnel.state()
    }

    /// How many connections cloudflared holds open to Cloudflare right now.
    pub fn tunnel_connections(&self) -> u32 {
        self.tunnel.connections()
    }
}
