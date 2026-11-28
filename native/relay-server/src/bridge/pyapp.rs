//! The server's calls into the Python app behind it, for what only a Python module knows (#657): which audio sources and outputs
//! modules registered, and, for each module that still owns gear, how to apply a route to it, read what it does, and rename on it.
//! The app answers under `/api/internal/` with the same `X-Relay-Front` token as `front.rs`, and only to a loopback peer.
//! Each module that is ported to Rust takes its provider out of this list; when the last is, so does this file.

use std::collections::HashMap;
use std::sync::{Arc, RwLock};
use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::{Method, Request, StatusCode};
use hyper_util::client::legacy::{connect::HttpConnector, Client};
use hyper_util::rt::TokioExecutor;
use relay_audio::channels::{End, Fut, Gear};
use relay_audio::routing::{Provider, ReadItem, Wanted};
use serde_json::{json, Value};

const CALL_TIMEOUT: Duration = Duration::from_secs(5);

pub struct PyApp {
    base: String,
    token: String,
    client: Client<HttpConnector, Full<Bytes>>,
}

impl PyApp {
    pub fn new(base: &str, token: &str) -> Arc<Self> {
        let client = Client::builder(TokioExecutor::new()).build(HttpConnector::new());
        Arc::new(Self { base: base.trim_end_matches('/').to_owned(), token: token.to_owned(), client })
    }

    async fn call(&self, method: Method, path: &str, body: Option<&Value>) -> Result<Value, &'static str> {
        let bytes = body.map(|b| serde_json::to_vec(b).unwrap_or_default()).unwrap_or_default();
        let req = Request::builder()
            .method(method)
            .uri(format!("{}{path}", self.base))
            .header("x-relay-front", &self.token)
            .header("content-type", "application/json")
            .body(Full::new(Bytes::from(bytes)))
            .map_err(|_| "bad request")?;
        let resp = tokio::time::timeout(CALL_TIMEOUT, self.client.request(req)).await.map_err(|_| "the app did not answer in time")?.map_err(|_| "the app is not reachable")?;
        if resp.status() != StatusCode::OK {
            return Err("the app refused the call");
        }
        let bytes = resp.into_body().collect().await.map_err(|_| "the app's answer was cut short")?.to_bytes();
        serde_json::from_slice(&bytes).map_err(|_| "the app's answer was not understood")
    }

    pub async fn get(&self, path: &str) -> Result<Value, &'static str> {
        self.call(Method::GET, path, None).await
    }

    pub async fn post(&self, path: &str, body: &Value) -> Result<Value, &'static str> {
        self.call(Method::POST, path, Some(body)).await
    }

    /// The audio sources and outputs modules registered: `{"sources": [{id, label}], "outputs": [{id, label}]}`.
    pub async fn audio_registry(&self) -> Value {
        match self.get("/api/internal/audio/registry").await {
            Ok(v) => v,
            Err(e) => {
                tracing::debug!("the app's audio registry could not be read: {e}");
                json!({"sources": [], "outputs": []})
            }
        }
    }
}

/// A module's destination, as the app says it can be applied to.
struct PyProvider {
    app: Arc<PyApp>,
    id: String,
    exclusive: bool,
    gain: bool,
    permission: Option<String>,
}

fn end_json(e: &End) -> Value {
    e.json()
}

impl Provider for PyProvider {
    fn exclusive(&self) -> bool {
        self.exclusive
    }
    fn gain(&self) -> bool {
        self.gain
    }
    fn permission(&self) -> Option<String> {
        self.permission.clone()
    }
    fn apply<'a>(&'a self, end: &'a End, wanted: &'a [Wanted]) -> Fut<'a, String> {
        Box::pin(async move {
            let wanted: Vec<Value> = wanted.iter().map(|w| json!({"ends": w.ends.iter().map(end_json).collect::<Vec<_>>(), "gain_db": w.gain_db})).collect();
            match self.app.post("/api/internal/routes/apply", &json!({"provider": self.id, "end": end_json(end), "wanted": wanted})).await {
                Ok(v) => v["problem"].as_str().unwrap_or("").to_owned(),
                Err(e) => {
                    tracing::warn!("routing: {} could not apply {}: {e}", self.id, end.key);
                    "The gear did not take that.".to_owned()
                }
            }
        })
    }
    fn read<'a>(&'a self, end: &'a End) -> Fut<'a, Option<Vec<ReadItem>>> {
        Box::pin(async move {
            let v = self.app.post("/api/internal/routes/read", &json!({"provider": self.id, "end": end_json(end)})).await.ok()?;
            let items = v["read"].as_array()?;
            Some(
                items
                    .iter()
                    .filter_map(|i| Some(ReadItem { provider: i["provider"].as_str().map(str::to_owned), key: i["key"].as_str()?.to_owned(), gain_db: i["gain_db"].as_f64() }))
                    .collect(),
            )
        })
    }
}

/// The modules that can rename a channel on their gear, as the app last said.
pub struct PyGear {
    app: Arc<PyApp>,
    providers: RwLock<Vec<String>>,
}

impl Gear for PyGear {
    fn can_rename(&self, provider: &str, _key: &str) -> bool {
        self.providers.read().unwrap_or_else(|e| e.into_inner()).iter().any(|p| p == provider)
    }
    fn rename<'a>(&'a self, provider: &'a str, key: &'a str, name: &'a str) -> Fut<'a, Result<(), ()>> {
        Box::pin(async move {
            match self.app.post("/api/internal/gear/rename", &json!({"provider": provider, "key": key, "name": name})).await {
                Ok(v) if v["ok"] == true => Ok(()),
                _ => Err(()),
            }
        })
    }
}

/// Read what the app's modules offer and hand it to routing and channels. Called at start and every few seconds, so a module
/// switched on or off shows up.
pub async fn refresh(app: &Arc<PyApp>, routing: &relay_audio::Routing, gear: &PyGear) {
    let Ok(v) = app.get("/api/internal/routes/providers").await else { return };
    let mut map: HashMap<String, Arc<dyn Provider>> = HashMap::new();
    let mut renamers = Vec::new();
    for (id, p) in v["providers"].as_object().into_iter().flatten() {
        map.insert(
            id.clone(),
            Arc::new(PyProvider { app: app.clone(), id: id.clone(), exclusive: p["exclusive"] == true, gain: p["gain"] == true, permission: p["permission"].as_str().map(str::to_owned) }),
        );
        if p["rename"] == true {
            renamers.push(id.clone());
        }
    }
    for id in v["gear"].as_array().into_iter().flatten().filter_map(Value::as_str) {
        renamers.push(id.to_owned());
    }
    routing.set_external(map);
    *gear.providers.write().unwrap_or_else(|e| e.into_inner()) = renamers;
}

pub fn gear(app: &Arc<PyApp>) -> Arc<PyGear> {
    Arc::new(PyGear { app: app.clone(), providers: RwLock::new(Vec::new()) })
}
