//! Dante admin routes (`routes.py`). Mounted by the registry, so each answers 404 while the module is off. A refusal is `{"detail": sentence}`, as
//! FastAPI wrote it, with the same status.

use std::sync::Arc;

use axum::extract::{Path, RawQuery, Request, State};
use axum::response::{IntoResponse, Response};
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use relay_core::gate::gate;
use relay_core::Host;
use serde_json::{json, Value};

use crate::routing::Refused;
use crate::service::Service;
use crate::web::{detail, lenient, query_param};

#[derive(Clone)]
pub struct Ctx {
    pub host: Host,
    pub service: Arc<Service>,
}

fn text(v: &Value, n: usize) -> Option<String> {
    v.as_str().filter(|s| s.chars().count() <= n).map(str::to_owned)
}

fn channel(v: &Value) -> Option<i64> {
    v.as_i64().filter(|c| *c > 0 && *c <= 4096)
}

/// Routing changes only after the operator confirms.
fn confirmed(body: &Value) -> Result<(), Response> {
    if body["confirm"] == json!(true) {
        Ok(())
    } else {
        Err(detail(400, "Relay changes routing only after the operator confirms: send confirm true."))
    }
}

fn preset_id(raw: &str) -> Result<i64, Response> {
    raw.parse::<i64>().map_err(|_| detail(422, "Input should be a valid integer"))
}

async fn who(c: &Ctx, parts: &axum::http::request::Parts) -> String {
    c.host.who(parts).await.unwrap_or_else(|| "an operator".to_owned())
}

fn pairs(set: impl IntoIterator<Item = (String, impl Into<Value>)>) -> Value {
    Value::Array(set.into_iter().map(|(d, c)| json!([d, c.into()])).collect())
}

async fn admin_state(State(c): State<Ctx>) -> Response {
    Json(c.service.snapshot()).into_response()
}

async fn admin_scan(State(c): State<Ctx>, req: Request) -> Response {
    c.service.rescan().await;
    let (parts, _) = req.into_parts();
    c.host.note(&parts, "dante.scan", "Looked for Dante devices again", "", None).await;
    Json(c.service.snapshot()).into_response()
}

async fn admin_history(State(c): State<Ctx>, RawQuery(query): RawQuery) -> Response {
    let limit = match query_param(query.as_deref(), "limit").as_str() {
        "" => 100,
        n => match n.parse::<i64>() {
            Ok(n) => n,
            Err(_) => return detail(422, "Input should be a valid integer"),
        },
    };
    Json(json!({"history": c.service.monitor.history(limit).await})).into_response()
}

/// Star or unstar a transmit channel for live levels: `{"device", "channel", "on"}`.
async fn admin_star(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body = lenient(body).await;
    let (device, channel_name, on) = (text(&body["device"], 80).filter(|s| !s.is_empty()), text(&body["channel"], 80).filter(|s| !s.is_empty()), body["on"].as_bool());
    let (Some(device), Some(channel_name), Some(on)) = (device, channel_name, on) else { return detail(400, "Send the device, the channel and on true or false.") };
    let known = c.service.devices_copy().iter().any(|d| d["name"] == json!(device) && d["transmit"].as_array().is_some_and(|t| t.iter().any(|x| x == &json!(channel_name))));
    if !known {
        return detail(404, "Relay has not seen that channel.");
    }
    if c.service.monitor.star(&device, &channel_name, on).await.is_err() {
        return detail(500, "The star could not be saved.");
    }
    c.host.note(&parts, "dante.star", &format!("{} {channel_name} on {device} for live levels", if on { "Starred" } else { "Unstarred" }), "", None).await;
    Json(json!({"starred": pairs(c.service.monitor.starred().await)})).into_response()
}

async fn routing_state(State(c): State<Ctx>) -> Response {
    let devices: Vec<Value> = c.service.devices_copy().iter().map(|d| json!({"name": d["name"], "reachable": d["reachable"], "transmit": d["transmit"], "receive": d["subscriptions"]})).collect();
    let locks = pairs(c.service.routing.locks(&c.service).await);
    Json(json!({"devices": devices, "locks": locks, "undo": c.service.routing.undo_info(), "presets": c.service.routing.presets(&c.service).await})).into_response()
}

/// Subscribe a receive channel to a source, or clear it: `{"device", "channel", "source" ("" clears), "confirm": true}`.
async fn routing_subscribe(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body = lenient(body).await;
    let (device, ch, source) = (text(&body["device"], 80).filter(|s| !s.is_empty()), channel(&body["channel"]), text(&body["source"], 160));
    let (Some(device), Some(ch), Some(source)) = (device, ch, source) else { return detail(400, "Send the device, the receive channel number and the source (empty to clear it).") };
    if let Err(r) = confirmed(&body) {
        return r;
    }
    let who = who(&c, &parts).await;
    let row = match c.service.routing.subscribe(&c.service, &device, ch, &source, &who).await {
        Ok(r) => r,
        Err(Refused(why)) => return detail(409, &why),
    };
    c.host.note(&parts, "dante.subscribe", &format!("{device} channel {ch}: {} ({})", if source.is_empty() { "cleared" } else { &source }, row["result"].as_str().unwrap_or("")), &device, None).await;
    Json(json!({"result": row, "undo": c.service.routing.undo_info()})).into_response()
}

async fn routing_undo(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body = lenient(body).await;
    if let Err(r) = confirmed(&body) {
        return r;
    }
    let who = who(&c, &parts).await;
    let results = match c.service.routing.undo(&c.service, &who).await {
        Ok(r) => r,
        Err(Refused(why)) => return detail(409, &why),
    };
    c.host.note(&parts, "dante.undo", &format!("Undid the last Dante routing change ({} subscriptions)", results.len()), "", None).await;
    Json(json!({"results": results, "undo": c.service.routing.undo_info()})).into_response()
}

/// Lock or unlock a receive channel so Relay never changes it: `{"device", "channel", "on"}`.
async fn routing_lock(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body = lenient(body).await;
    let (device, ch, on) = (text(&body["device"], 80).filter(|s| !s.is_empty()), channel(&body["channel"]), body["on"].as_bool());
    let (Some(device), Some(ch), Some(on)) = (device, ch, on) else { return detail(400, "Send the device, the receive channel number and on true or false.") };
    if !c.service.devices_copy().iter().any(|d| d["name"] == json!(device)) {
        return detail(404, "Relay has not seen that device.");
    }
    if c.service.routing.set_lock(&c.service, &device, ch, on).await.is_err() {
        return detail(500, "The lock could not be saved.");
    }
    c.host.note(&parts, "dante.lock", &format!("{} {device} receive channel {ch}", if on { "Locked" } else { "Unlocked" }), &device, None).await;
    Json(json!({"locks": pairs(c.service.routing.locks(&c.service).await)})).into_response()
}

/// Save the current routing as a preset: `{"name", "devices": [] (empty for all)}`. Saving a name again replaces it.
async fn preset_save(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let body = lenient(body).await;
    let name = text(&body["name"], 80).filter(|s| !s.is_empty());
    let scope: Option<Vec<String>> = match body.get("devices") {
        None => Some(Vec::new()),
        Some(Value::Array(a)) => a.iter().map(|x| text(x, 80)).collect(),
        Some(_) => None,
    };
    let (Some(name), Some(scope)) = (name, scope) else { return detail(400, "Send a name and, optionally, the device names to cover.") };
    let who = who(&c, &parts).await;
    let saved = match c.service.routing.save_preset(&c.service, &name, &scope, &who).await {
        Ok(s) => s,
        Err(Refused(why)) => return detail(409, &why),
    };
    c.host.note(&parts, "dante.preset_save", &format!("Saved Dante routing preset {} ({} receive channels)", saved["name"].as_str().unwrap_or(""), saved["count"]), "", None).await;
    Json(json!({"saved": saved, "presets": c.service.routing.presets(&c.service).await})).into_response()
}

async fn preset_delete(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let id = match preset_id(&id) {
        Ok(i) => i,
        Err(r) => return r,
    };
    let (parts, _) = req.into_parts();
    let name = match c.service.routing.delete_preset(&c.service, id).await {
        Ok(n) => n,
        Err(Refused(why)) => return detail(404, &why),
    };
    c.host.note(&parts, "dante.preset_delete", &format!("Deleted Dante routing preset {name}"), "", None).await;
    Json(json!({"presets": c.service.routing.presets(&c.service).await})).into_response()
}

/// The preset against the live network: what would change, what is locked, what is missing.
async fn preset_compare(State(c): State<Ctx>, Path(id): Path<String>) -> Response {
    let id = match preset_id(&id) {
        Ok(i) => i,
        Err(r) => return r,
    };
    match c.service.routing.compare(&c.service, id).await {
        Ok(v) => Json(v).into_response(),
        Err(Refused(why)) => detail(404, &why),
    }
}

async fn preset_apply(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let id = match preset_id(&id) {
        Ok(i) => i,
        Err(r) => return r,
    };
    let (parts, body) = req.into_parts();
    let body = lenient(body).await;
    if let Err(r) = confirmed(&body) {
        return r;
    }
    let who = who(&c, &parts).await;
    let out = match c.service.routing.apply_preset(&c.service, id, &who).await {
        Ok(o) => o,
        Err(Refused(why)) => return detail(409, &why),
    };
    let n = out["results"].as_array().map_or(0, Vec::len);
    c.host.note(&parts, "dante.preset_apply", &format!("Applied Dante routing preset {} ({n} subscriptions, {} failed)", out["preset"]["name"].as_str().unwrap_or(""), out["failed"]), "", None).await;
    let mut out = out;
    out["undo"] = c.service.routing.undo_info();
    Json(out).into_response()
}

pub fn router(c: Ctx) -> Router {
    let h = c.host.clone();
    let view = |r| gate(&h, "dante.view", r);
    let control = |r| gate(&h, "dante.control", r);
    let manage = |r| gate(&h, "dante.manage", r);
    let base = "/api/admin/dante";
    Router::new()
        .route(base, view(get(admin_state)))
        .route(&format!("{base}/scan"), manage(post(admin_scan)))
        .route(&format!("{base}/history"), view(get(admin_history)))
        .route(&format!("{base}/star"), manage(post(admin_star)))
        .route(&format!("{base}/routing"), view(get(routing_state)))
        .route(&format!("{base}/routing/subscribe"), control(post(routing_subscribe)))
        .route(&format!("{base}/routing/undo"), control(post(routing_undo)))
        .route(&format!("{base}/routing/lock"), manage(post(routing_lock)))
        .route(&format!("{base}/presets"), manage(post(preset_save)))
        .route(&format!("{base}/presets/{{id}}"), manage(delete(preset_delete)))
        .route(&format!("{base}/presets/{{id}}/compare"), view(get(preset_compare)))
        .route(&format!("{base}/presets/{{id}}/apply"), control(post(preset_apply)))
        .with_state(c)
}
