//! Signage: web pages and YouTube (#210; ported from `app/modules/signage_web` in #660). A web page shown full screen, or a YouTube video or
//! playlist, as items in a signage playlist. A module of its own because YouTube is another product; it needs Signage on to do anything.
//!
//! Items need the internet. The player skips one while its screen can't reach the site, and plays it again when it can.
//!
//! The module provides two signage sources, `signage.source.web_page` and `signage.source.youtube`, while it is on; signage asks for them
//! by name and never imports this crate. Not here yet: the "Web & YouTube" tab and its script (the Rust module trait has no admin pages;
//! the Python shell still lists the module and loads them).

// axum's `Response` is the error half of every route helper (the answer to send instead): the shape of the Python it ports.
#![allow(clippy::result_large_err)]

pub mod check;
pub mod links;

use std::sync::{Arc, OnceLock};

use axum::body::Body;
use axum::extract::{Path, Request, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post, put};
use axum::{Json, Router};
use http_body_util::BodyExt;
use relay_core::future::BoxFuture;
use relay_core::gate::gate;
use relay_core::screens::{SignageSource, SIGNAGE_SOURCE};
use relay_core::{AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::{json, Value};

use crate::check::{Fetch, Web};

pub const KEY: &str = "signage_web";
pub const WEB_KIND: &str = "web_page";
pub const YOUTUBE_KIND: &str = "youtube";

pub const MANIFEST: Manifest = Manifest {
    id: "signage_web",
    name: "Signage: web pages and YouTube",
    version: "1.1.0",
    summary: "Adds web pages and YouTube videos or playlists to signage playlists. They need the internet and are skipped while a screen can't reach it.",
    settings_key: KEY,
    guide: "https://github.com/justin-small/relay#signage-web-pages-and-youtube",
    uses: &["signage"],
    route_prefixes: &["/api/admin/signage-web"],
    renamed_from: "",
    section: "Screens",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "signage_web.view", label: "See the saved web pages and YouTube links", group: "Signage web", min_role: "viewer" },
    Permission { id: "signage_web.manage", label: "Add, change and remove saved web pages and YouTube links", group: "Signage web", min_role: "admin" },
];

pub struct SignageWeb {
    host: OnceLock<Host>,
    fetch: OnceLock<Arc<dyn Fetch>>,
}

impl Default for SignageWeb {
    fn default() -> Self {
        Self::new()
    }
}

impl SignageWeb {
    pub fn new() -> Self {
        Self { host: OnceLock::new(), fetch: OnceLock::new() }
    }

    /// The module with the answers its checks get chosen by the caller: a test's, in place of the network.
    pub fn with_fetcher(fetch: Arc<dyn Fetch>) -> Self {
        let me = Self::new();
        let _ = me.fetch.set(fetch);
        me
    }
}

#[derive(Clone)]
struct Ctx {
    host: Host,
    fetch: Arc<dyn Fetch>,
}

/// The saved links, cleaned as Python's `clean` does whatever the file holds.
fn saved(host: &Host) -> Vec<Value> {
    let raw = host.settings(KEY).unwrap_or_else(|| json!({}));
    links::clean_stored(&raw)["links"].as_array().cloned().unwrap_or_default()
}

fn store(host: &Host, items: &[Value]) -> Result<(), Response> {
    host.save_settings(KEY, &json!({"links": items})).map_err(|_| error(503, "The settings could not be saved right now. Try again in a moment."))
}

fn error(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"error": text}))).into_response()
}

fn detail(status: u16, text: &str) -> Response {
    (StatusCode::from_u16(status).unwrap_or(StatusCode::BAD_REQUEST), Json(json!({"detail": text}))).into_response()
}

async fn object(body: Body) -> Result<Value, Response> {
    let mut out = Vec::new();
    let mut body = body;
    while let Some(frame) = body.frame().await {
        let Ok(frame) = frame else { return Err(detail(400, "Bad request")) };
        if let Some(d) = frame.data_ref() {
            out.extend_from_slice(d);
            if out.len() > 1 << 20 {
                return Err(detail(413, "request too large"));
            }
        }
    }
    match serde_json::from_slice::<Value>(&out) {
        Ok(v @ Value::Object(_)) => Ok(v),
        _ => Err(detail(422, "That request is not valid.")),
    }
}

fn find(items: &[Value], id: &str) -> Result<Value, Response> {
    items.iter().find(|l| l["id"] == id).cloned().ok_or_else(|| detail(404, "Not Found"))
}

/// Ask the site (or YouTube) now; fill in the name from YouTube's title when none was typed.
async fn checked(c: &Ctx, mut link: Value) -> Value {
    let v = check::check(c.fetch.as_ref(), &link).await;
    if !v.title.is_empty() && (link["name"].as_str().is_none_or(str::is_empty) || link["name"] == link["url"]) {
        link["name"] = json!(v.title.chars().take(100).collect::<String>());
    }
    let at = relay_core::timewin::iso(std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map_or(0, |d| i64::try_from(d.as_secs()).unwrap_or(0)));
    link["check"] = json!({"state": v.state, "note": v.note, "at": at});
    link
}

fn name_or_url(link: &mut Value) {
    if link["name"].as_str().is_none_or(str::is_empty) {
        link["name"] = link["url"].clone();
    }
}

async fn admin_list(State(c): State<Ctx>) -> Response {
    Json(json!({"links": saved(&c.host)})).into_response()
}

async fn admin_add(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let items = saved(&c.host);
    if items.len() >= links::MAX_LINKS {
        return error(400, &format!("At most {} links can be saved.", links::MAX_LINKS));
    }
    let taken = items.iter().filter_map(|l| l["id"].as_str().map(str::to_owned)).collect();
    let link = match links::clean_link(&json!({"url": payload.get("url"), "name": payload.get("name")}), &taken) {
        Ok(l) => l,
        Err(e) => return error(400, e.0),
    };
    let mut link = checked(&c, link).await;
    name_or_url(&mut link);
    let mut all = items;
    all.push(link.clone());
    if let Err(r) = store(&c.host, &all) {
        return r;
    }
    c.host.note(&parts, "signage_web.add", "Added a signage web page or YouTube link", link["name"].as_str().unwrap_or(""), Some(json!({"kind": link["kind"]}))).await;
    Json(link).into_response()
}

async fn admin_edit(State(c): State<Ctx>, Path(lid): Path<String>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let old = match find(&saved(&c.host), &lid) {
        Ok(o) => o,
        Err(r) => return r,
    };
    let raw = json!({"id": lid, "url": payload.get("url").unwrap_or(&old["url"]), "name": payload.get("name").unwrap_or(&old["name"])});
    let mut link = match links::clean_link(&raw, &Default::default()) {
        Ok(l) => l,
        Err(e) => return error(400, e.0),
    };
    if link["url"] == old["url"] {
        link["check"] = old["check"].clone();
    } else {
        link = checked(&c, link).await;
    }
    name_or_url(&mut link);
    let all: Vec<Value> = saved(&c.host).into_iter().map(|l| if l["id"] == lid.as_str() { link.clone() } else { l }).collect();
    if let Err(r) = store(&c.host, &all) {
        return r;
    }
    c.host.note(&parts, "signage_web.edit", "Changed a signage web page or YouTube link", link["name"].as_str().unwrap_or(""), None).await;
    Json(link).into_response()
}

async fn admin_delete(State(c): State<Ctx>, Path(lid): Path<String>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let items = saved(&c.host);
    let old = match find(&items, &lid) {
        Ok(o) => o,
        Err(r) => return r,
    };
    let kept: Vec<Value> = items.into_iter().filter(|l| l["id"] != lid.as_str()).collect();
    if let Err(r) = store(&c.host, &kept) {
        return r;
    }
    c.host.note(&parts, "signage_web.delete", "Removed a signage web page or YouTube link", old["name"].as_str().unwrap_or(""), None).await;
    Json(json!({"ok": true})).into_response()
}

async fn admin_check(State(c): State<Ctx>, Path(lid): Path<String>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let old = match find(&saved(&c.host), &lid) {
        Ok(o) => o,
        Err(r) => return r,
    };
    let link = checked(&c, old).await;
    let all: Vec<Value> = saved(&c.host).into_iter().map(|l| if l["id"] == lid.as_str() { link.clone() } else { l }).collect();
    if let Err(r) = store(&c.host, &all) {
        return r;
    }
    c.host.note(&parts, "signage_web.check", "Re-checked a signage web page or YouTube link", link["name"].as_str().unwrap_or(""), Some(json!({"result": link["check"]["state"]}))).await;
    Json(link).into_response()
}

/// The source signage plays one kind of saved link from: resolved, described and offered by id.
fn source(host: &Host, kind: &'static str, label: &str) -> SignageSource {
    let (h1, h2, h3) = (host.clone(), host.clone(), host.clone());
    let pick = move |host: &Host, id: &str| saved(host).into_iter().find(|l| l["id"] == id && links::of_kind(&json!({"links": [l]}), kind).len() == 1);
    SignageSource {
        label: label.to_owned(),
        resolve: Box::new(move |id| pick(&h1, id).map(|l| links::resolve(&l))),
        describe: Box::new(move |id| match pick(&h2, id) {
            Some(l) => json!({"name": l["name"], "thumb": ""}),
            None => json!({"name": id, "thumb": "", "missing": true}),
        }),
        choices: Box::new(move || {
            // A single video plays to its end by default (duration 0); everything else gets a number of seconds.
            let all = json!({"links": saved(&h3)});
            links::of_kind(&all, kind)
                .into_iter()
                .map(|l| {
                    let yt = links::parse_youtube(l["url"].as_str().unwrap_or(""));
                    let video = yt.is_some_and(|y| !y.video.is_empty() && y.list.is_empty());
                    json!({"ref": l["id"], "name": l["name"], "thumb": "", "kind": if video { "video" } else { "" }})
                })
                .collect()
        }),
    }
}

impl Module for SignageWeb {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    fn defaults(&self) -> Value {
        json!({"links": []})
    }

    fn clean(&self, raw: &Value) -> Value {
        links::clean_stored(raw)
    }

    fn routes(&self) -> Router {
        let (Some(host), Some(fetch)) = (self.host.get(), self.fetch.get()) else { return Router::new() };
        let view = |r| gate(host, "signage_web.view", r);
        let manage = |r| gate(host, "signage_web.manage", r);
        Router::new()
            .route("/api/admin/signage-web", view(get(admin_list)))
            .route("/api/admin/signage-web/links", manage(post(admin_add)))
            .route("/api/admin/signage-web/links/{lid}", manage(put(admin_edit).delete(admin_delete)))
            .route("/api/admin/signage-web/links/{lid}/check", manage(post(admin_check)))
            .with_state(Ctx { host: host.clone(), fetch: Arc::clone(fetch) })
    }

    fn attach(&self, host: &Host) {
        let _ = self.host.set(host.clone());
        let _ = self.fetch.set(Arc::new(Web::new(host.net().clone())));
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            for (kind, label) in [(WEB_KIND, "Web page"), (YOUTUBE_KIND, "YouTube video or playlist")] {
                host.provide(MANIFEST.id, &format!("{SIGNAGE_SOURCE}{kind}"), Arc::new(source(&host, kind, label)));
            }
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            if let Some(host) = self.host.get() {
                host.withdraw_all(MANIFEST.id);
            }
        })
    }

    fn health(&self) -> Health {
        if self.host.get().is_none() {
            return Health::idle();
        }
        Health::new(HealthState::Ok, "Saved links are offered to signage playlists")
    }
}
