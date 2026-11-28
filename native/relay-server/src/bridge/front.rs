//! The server's two calls to the app behind it (`app/front.py`), while the app is still Python (#650):
//!
//! * `GET /api/internal/front`: what only the app's modules know: each module listener that is on (its port, its prefixes, the
//!   traffic class it binds to), and every prefix a module reserves for a listener or for the HTTPS panel.
//! * `POST /api/internal/front`: what only this side knows: the ports really bound, the notices about them, the panel
//!   certificate and where each listener binds, so the app's links, admin page and Network page say what is true.
//!
//! Both carry `X-Relay-Front: <token>` (`RELAY_FRONT_TOKEN`, the same value for both programs). Without a token the app
//! answers 404 and this side serves with the listeners it knows itself.

use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Method, Request, StatusCode};
use hyper_util::client::legacy::{connect::HttpConnector, Client};
use hyper_util::rt::TokioExecutor;
use serde::{Deserialize, Serialize};

const PATH: &str = "/api/internal/front";
const ADMIN_PATH: &str = "/api/internal/front/admin";
const CALL_TIMEOUT: Duration = Duration::from_secs(5);

/// One listener of a module that is on, as the app reports it.
#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct ListenerPlan {
    pub key: String,
    /// "Signage (screens)": what to call it in a sentence.
    pub what: String,
    pub traffic: String,
    pub traffic_label: String,
    /// "strict" for a class that carries audio or video, else "fallback".
    pub policy: String,
    pub port: u16,
    pub prefixes: Vec<String>,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Eq)]
pub struct AppPlan {
    #[serde(default)]
    pub listeners: Vec<ListenerPlan>,
    /// Every listener prefix, of modules that are off too.
    #[serde(default)]
    pub listener_prefixes: Vec<String>,
    #[serde(default)]
    pub panel_prefixes: Vec<String>,
    /// What the app's modules add to the role table (#719), so a permission of a module only the app has is known here.
    #[serde(default)]
    pub permissions: Vec<PermissionPlan>,
}

#[derive(Clone, Debug, Deserialize, PartialEq, Eq)]
pub struct PermissionPlan {
    pub id: String,
    pub label: String,
    pub group: String,
    pub min_role: String,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct GroupReport {
    pub key: String,
    pub label: String,
    pub traffic: Option<String>,
    pub configured: String,
    pub port: u16,
    pub secure: bool,
    pub hosts: Vec<String>,
    pub notes: Vec<String>,
    /// The paths that alone answer on it, for a module's port; empty for the viewer and panel sockets.
    pub prefixes: Vec<String>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct CertReport {
    pub fingerprint: String,
    pub names: Vec<String>,
    pub expires: String,
    pub changed: Option<ChangedReport>,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct ChangedReport {
    pub from: String,
    pub to: String,
    pub at: String,
}

#[derive(Clone, Debug, Serialize, PartialEq, Eq)]
pub struct Report {
    pub viewer_port: u16,
    pub admin_port: u16,
    pub notices: Vec<String>,
    pub groups: Vec<GroupReport>,
    pub certificate: CertReport,
}

#[derive(Clone)]
pub struct Front {
    base: String,
    token: String,
    client: Client<HttpConnector, Full<Bytes>>,
}

impl Front {
    pub fn new(base: &str, token: &str) -> Self {
        let client = Client::builder(TokioExecutor::new()).build(HttpConnector::new());
        Self { base: base.trim_end_matches('/').to_owned(), token: token.to_owned(), client }
    }

    async fn call(&self, method: Method, path: &str, body: Option<Vec<u8>>) -> Result<Bytes, &'static str> {
        let req = Request::builder()
            .method(method)
            .uri(format!("{}{path}", self.base))
            .header("x-relay-front", &self.token)
            .header("content-type", "application/json")
            .body(Full::new(Bytes::from(body.unwrap_or_default())))
            .map_err(|_| "bad request")?;
        let resp = tokio::time::timeout(CALL_TIMEOUT, self.client.request(req))
            .await
            .map_err(|_| "the app did not answer in time")?
            .map_err(|_| "the app is not reachable")?;
        if resp.status() != StatusCode::OK {
            return Err("the app refused the call");
        }
        let bytes = resp.into_body().collect().await.map_err(|_| "the app's answer was cut short")?.to_bytes();
        Ok(bytes)
    }

    /// The app's listeners and reserved prefixes.
    pub async fn plan(&self) -> Result<AppPlan, &'static str> {
        let bytes = self.call(Method::GET, PATH, None).await?;
        serde_json::from_slice(&bytes).map_err(|_| "the app's plan was not understood")
    }

    /// The modules the app has that are on, with their admin pages, scripts and styles (`GET /api/internal/front/admin`, #719).
    pub async fn admin(&self) -> Result<Vec<serde_json::Value>, &'static str> {
        let bytes = self.call(Method::GET, ADMIN_PATH, None).await?;
        serde_json::from_slice(&bytes).map_err(|_| "the app's module pages were not understood")
    }

    /// Tell the app what is really bound.
    pub async fn report(&self, report: &Report) -> Result<(), &'static str> {
        let body = serde_json::to_vec(report).map_err(|_| "report")?;
        self.call(Method::POST, PATH, Some(body)).await.map(|_| ())
    }
}
