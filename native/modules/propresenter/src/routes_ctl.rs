//! The control, lobby and slide-view routes (`routes.py`): reads need `propresenter.view`, live control `propresenter.control`; every write goes through
//! the one client, which refuses it in view-only mode (409). Errors answer through [`pp_error`] or a fixed sentence, never an exception's text.
//! The same slide view is open without the admin login only while `view.public` is on (off by default: slide images are licensed content).

#![allow(clippy::result_large_err)]

use axum::body::Body;
use axum::extract::{Path, RawQuery, Request, State};
use axum::http::{header, HeaderValue};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use axum::{Json, Router};
use relay_core::gate::gate;
use serde_json::{json, Value};

use crate::control::{ControlError, Done, THUMB_QUALITY};
use crate::lobby::LobbyError;
use crate::pclink::{message, LinkFail};
use crate::routes::{audit, pp_error, Ctx};
use crate::settings::configured;
use crate::songs::{self, SendError};
use crate::web::{detail, error, object};

/// A query value (`?v=...`), percent-decoded.
pub fn query_param(raw: &Option<String>, key: &str) -> String {
    for pair in raw.as_deref().unwrap_or("").split('&') {
        let (k, v) = pair.split_once('=').unwrap_or((pair, ""));
        if decode(k) == key {
            return decode(v);
        }
    }
    String::new()
}

fn decode(s: &str) -> String {
    let b = s.as_bytes();
    let (mut out, mut i) = (Vec::new(), 0);
    while i < b.len() {
        match b[i] {
            b'+' => out.push(b' '),
            b'%' if b.get(i + 1..i + 3).is_some_and(|h| h.iter().all(u8::is_ascii_hexdigit)) => {
                out.push(u8::from_str_radix(std::str::from_utf8(&b[i + 1..i + 3]).unwrap_or("0"), 16).unwrap_or(0));
                i += 2;
            }
            c => out.push(c),
        }
        i += 1;
    }
    String::from_utf8_lossy(&out).into_owned()
}

/// `_pp_run`: what a control call answers.
pub fn run(done: Done) -> Response {
    match done {
        Ok(Some(v)) => Json(v).into_response(),
        Ok(None) => Json(json!({"ok": true})).into_response(),
        Err(ControlError::Forbidden) => error(403, "That is not allowed by the operator-safe selection."),
        Err(ControlError::NoPlaylist) => error(404, "No playlist is active or focused in ProPresenter. Trigger an item first."),
        Err(ControlError::Invalid) => error(400, "That request was not valid."),
        Err(ControlError::Pro(e)) => pp_error(&e),
    }
}

fn run_value(done: Done<Value>) -> Response {
    run(done.map(Some))
}

/// A write that went well is audited, as Python's gate did for a module route that noted nothing itself.
async fn audited(c: &Ctx, parts: &axum::http::request::Parts, res: Response) -> Response {
    if res.status().is_success() {
        audit(c, parts).await;
    }
    res
}

fn image_response(ctype: &str, data: Vec<u8>, cache: &'static str) -> Response {
    let mut r = Response::new(Body::from(data));
    let h = r.headers_mut();
    h.insert(header::CONTENT_TYPE, HeaderValue::from_str(ctype).unwrap_or(HeaderValue::from_static("application/octet-stream")));
    h.insert(header::CACHE_CONTROL, HeaderValue::from_static(cache));
    r
}

fn int_path(v: &str) -> Result<i64, Response> {
    v.trim().parse::<i64>().map_err(|_| detail(422, "That request is not valid."))
}

// ------------------------------------------------------------------------------------------------------------------ control
async fn slides(State(c): State<Ctx>, RawQuery(q): RawQuery) -> Response {
    let uuid = query_param(&q, "uuid");
    run_value(c.control.slides(Some(&uuid)).await)
}

async fn thumbnail(State(c): State<Ctx>, Path((uuid, index)): Path<(String, String)>, RawQuery(q): RawQuery) -> Response {
    let Ok(n) = int_path(&index) else { return detail(422, "That request is not valid.") };
    match c.control.thumbnail(&uuid, &json!(n), &query_param(&q, "v"), THUMB_QUALITY).await {
        Ok((ctype, data)) => image_response(&ctype, data, "private, max-age=3600"),
        Err(ControlError::Pro(e)) => pp_error(&e),
        Err(_) => error(400, "That request was not valid."),
    }
}

async fn step(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.step(payload["direction"].as_str().unwrap_or("")).await);
    audited(&c, &parts, res).await
}

async fn next_presentation(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = run(c.control.next_presentation().await);
    audited(&c, &parts, res).await
}

/// The next or previous playlist item (Shift+arrow keys on the control page).
async fn item(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.step_item(payload["direction"].as_str().unwrap_or("")).await);
    audited(&c, &parts, res).await
}

/// Audience or stage screens on or off. A write, so view-only refuses it like every other.
async fn screens(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.set_screens(&payload["kind"], &payload["on"]).await);
    audited(&c, &parts, res).await
}

async fn slide(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.trigger_slide(&payload["uuid"], &payload["index"]).await);
    audited(&c, &parts, res).await
}

async fn clear(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.clear(payload["layer"].as_str().unwrap_or("")).await);
    audited(&c, &parts, res).await
}

async fn playlists(State(c): State<Ctx>) -> Response {
    run_value(c.control.playlists().await)
}

async fn playlist(State(c): State<Ctx>, Path(id): Path<String>) -> Response {
    run_value(c.control.playlist_items(&id).await)
}

async fn playlist_diagnostics(State(c): State<Ctx>, Path(id): Path<String>) -> Response {
    run_value(c.control.playlist_diagnostics(&id).await)
}

async fn playlist_cues(State(c): State<Ctx>, Path((id, index)): Path<(String, String)>) -> Response {
    let Ok(n) = int_path(&index) else { return detail(422, "That request is not valid.") };
    run_value(c.control.playlist_item_cues(&id, &json!(n)).await)
}

/// A playlist item's cue thumbnail, proxied and cached like the presentation ones (browsers never reach ProPresenter).
async fn playlist_thumbnail(State(c): State<Ctx>, Path((id, index, cue)): Path<(String, String, String)>, RawQuery(q): RawQuery) -> Response {
    let (Ok(i), Ok(k)) = (int_path(&index), int_path(&cue)) else { return detail(422, "That request is not valid.") };
    match c.control.playlist_thumbnail(&id, &json!(i), &json!(k), &query_param(&q, "v"), THUMB_QUALITY).await {
        Ok((ctype, data)) => image_response(&ctype, data, "private, max-age=3600"),
        Err(ControlError::Pro(e)) => pp_error(&e),
        Err(_) => error(400, "That request was not valid."),
    }
}

async fn playlist_trigger(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.trigger_playlist_item(&id, &payload["index"]).await);
    audited(&c, &parts, res).await
}

async fn macros(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    // The operator-safe selection is what an operator sees; every macro (the picker's list) is for whoever manages it.
    let all = query_param(&parts.uri.query().map(str::to_owned), "all").to_lowercase();
    let all = matches!(all.as_str(), "true" | "1" | "yes" | "on");
    if all {
        let Some(a) = c.host.authorizer() else { return detail(401, "Sign in required") };
        if a.require(&parts, "propresenter.manage").await.is_err() {
            return detail(403, "Not allowed");
        }
    }
    run_value(c.control.macros(all).await)
}

async fn macro_trigger(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = run(c.control.trigger_macro(&id).await);
    audited(&c, &parts, res).await
}

async fn looks(State(c): State<Ctx>) -> Response {
    run_value(c.control.looks().await)
}

async fn look_trigger(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = run(c.control.trigger_look(&id).await);
    audited(&c, &parts, res).await
}

async fn timers(State(c): State<Ctx>) -> Response {
    run_value(c.control.timers().await)
}

async fn timer(State(c): State<Ctx>, Path((id, op)): Path<(String, String)>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = run(c.control.timer(&id, &op).await);
    audited(&c, &parts, res).await
}

async fn messages(State(c): State<Ctx>) -> Response {
    run_value(c.control.messages().await)
}

async fn message_trigger(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.trigger_message(&id, &payload["tokens"]).await);
    audited(&c, &parts, res).await
}

async fn message_clear(State(c): State<Ctx>, Path(id): Path<String>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = run(c.control.clear_message(&id).await);
    audited(&c, &parts, res).await
}

async fn transport(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = run(c.control.go_to_time(&payload["seconds"]).await);
    audited(&c, &parts, res).await
}

// -------------------------------------------------------------------------------------------------------------------- lobby
fn lobby_run(done: Result<Value, LobbyError>) -> Response {
    match done {
        Ok(v) => Json(v).into_response(),
        // A fixed sentence by key, never the exception's own text.
        Err(LobbyError::Refused(r)) => error(409, r.message()),
        Err(LobbyError::Control(ControlError::Invalid)) => error(400, "That request was not valid."),
        Err(LobbyError::Control(ControlError::Pro(e))) => pp_error(&e),
        Err(LobbyError::Control(_)) => error(409, "The lobby rotation could not do that."),
    }
}

async fn lobby_get(State(c): State<Ctx>) -> Response {
    Json(c.lobby.state()).into_response()
}

async fn lobby_start(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = lobby_run(c.lobby.start_rotation().await);
    audited(&c, &parts, res).await
}

async fn lobby_resume(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = lobby_run(c.lobby.resume().await);
    audited(&c, &parts, res).await
}

/// Stop stepping; with `clear` true, also clear the announcements layer.
async fn lobby_stop(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let res = lobby_run(c.lobby.stop_rotation(payload["clear"] == Value::Bool(true)).await);
    audited(&c, &parts, res).await
}

// ----------------------------------------------------------------------------------------------------------- the slide view
async fn view_image(c: &Ctx, which: &str, v: &str) -> Response {
    match c.view.image(which, v).await {
        Ok(Some((ctype, data))) => image_response(&ctype, data, "private, max-age=300"),
        Ok(None) => error(404, "That slide is not up any more."),
        Err(ControlError::Pro(e)) => pp_error(&e),
        Err(_) => error(400, "That request was not valid."),
    }
}

async fn live(State(c): State<Ctx>) -> Response {
    Json(c.view.snapshot("/api/admin/propresenter/live")).into_response()
}

async fn live_image(State(c): State<Ctx>, Path(which): Path<String>, RawQuery(q): RawQuery) -> Response {
    view_image(&c, &which, &query_param(&q, "v")).await
}

/// The same view without the admin login, only while `view.public` is on (off by default: slide images are licensed content). Off, every one of these
/// is a 404, as though the route did not exist.
fn stage_public(c: &Ctx) -> bool {
    let s = c.follower.settings();
    s["view"]["public"] == Value::Bool(true) && configured(&s)
}

const STAGE_PAGE: &str = "<!doctype html>\n<html lang=\"en\"><head>\n<meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">\n<meta name=\"robots\" content=\"noindex\">\n<title>Slide view</title><link rel=\"icon\" href=\"/static/brand/favicon.ico\" sizes=\"32x32\"><link rel=\"icon\" href=\"/static/brand/favicon.svg\" type=\"image/svg+xml\"><link rel=\"apple-touch-icon\" href=\"/static/brand/apple-touch-icon.png\"><link rel=\"manifest\" href=\"/static/brand/site.webmanifest\">\n<link rel=\"stylesheet\" href=\"/static/tokens.css\"><link rel=\"stylesheet\" href=\"/static/viewer.css\">\n<link rel=\"stylesheet\" href=\"/static/modules/propresenter/slideview.css\">\n<style>body { display:block; padding:16px; margin:0; } #sv { max-width:1400px; margin:0 auto; }</style>\n</head><body>\n<div id=\"sv\"></div>\n<script src=\"/static/relay-ui.js\"></script>\n<script src=\"/static/modules/propresenter/slideview.js\"></script>\n<script>RelaySlideView.create(document.getElementById('sv'), { base: '/api/stage/live' });</script>\n</body></html>\n";

async fn page_stage(State(c): State<Ctx>) -> Response {
    if !stage_public(&c) {
        return detail(404, "Not found");
    }
    let mut r = Response::new(Body::from(STAGE_PAGE));
    r.headers_mut().insert(header::CONTENT_TYPE, HeaderValue::from_static("text/html; charset=utf-8"));
    r
}

async fn stage_live(State(c): State<Ctx>) -> Response {
    if !stage_public(&c) {
        return detail(404, "Not found");
    }
    Json(c.view.snapshot("/api/stage/live")).into_response()
}

async fn stage_live_image(State(c): State<Ctx>, Path(which): Path<String>, RawQuery(q): RawQuery) -> Response {
    if !stage_public(&c) {
        return detail(404, "Not found");
    }
    view_image(&c, &which, &query_param(&q, "v")).await
}

// ------------------------------------------------------------------------------------------------------ the Planning Center link
fn link_run(done: Result<Value, LinkFail>) -> Response {
    match done {
        Ok(v) => Json(v).into_response(),
        // A fixed sentence by key, never the exception's own text.
        Err(LinkFail::Told(key)) => error(409, message(key)),
        Err(LinkFail::Invalid) => error(400, "That request was not valid."),
        Err(LinkFail::Pro(e)) => pp_error(&e),
    }
}

async fn link_get(State(c): State<Ctx>) -> Response {
    Json(c.link.state()).into_response()
}

/// The presentations the last read found, for the operator's pairing picker (names and UUIDs only).
async fn link_presentations(State(c): State<Ctx>) -> Response {
    let rows: Vec<Value> = c.link.pool().iter().map(|p| json!({"uuid": p["uuid"], "name": p["name"]})).collect();
    Json(json!({"presentations": rows})).into_response()
}

/// Read ProPresenter's playlists and libraries again and match the plan. Reads only.
async fn link_refresh(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = match c.link.refresh().await {
        Ok(()) => link_run(Ok(c.link.state())),
        Err(e) => link_run(Err(LinkFail::Pro(e))),
    };
    audited(&c, &parts, res).await
}

/// Pair a plan item title with a presentation, or clear the pairing with an empty `uuid`.
async fn link_mapping(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    let uuid = match &payload["uuid"] {
        Value::Null => String::new(),
        Value::String(t) => t.clone(),
        other => other.to_string(),
    };
    let res = if !uuid.is_empty() && !crate::settings::id_ok(&uuid) {
        link_run(Err(LinkFail::Invalid))
    } else {
        let text_of = |k: &str| payload[k].as_str().unwrap_or("").to_owned();
        match c.link.set_mapping(&text_of("item"), &uuid, &text_of("name")) {
            Ok(_) => link_run(Ok(c.link.state())),
            Err(e) => link_run(Err(e)),
        }
    };
    audited(&c, &parts, res).await
}

/// Build (or rebuild) the Relay-owned playlist from the matched plan. On the operator's click only.
async fn link_playlist(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, _) = req.into_parts();
    let res = match c.link.build_playlist().await {
        Ok(mut done) => {
            done["state"] = c.link.state();
            link_run(Ok(done))
        }
        Err(e) => link_run(Err(e)),
    };
    audited(&c, &parts, res).await
}

// ------------------------------------------------------------------------------------------------------------------------- songs
/// ProPresenter's API cannot create a song, so Relay builds the file and the Relay Helper on the ProPresenter computer opens it. Fixed sentences only, never
/// an exception's text.
async fn songs_helper(State(c): State<Ctx>) -> Response {
    Json(songs::helper_state(&c.host)).into_response()
}

/// Build songs (name, sections of slides, arrangements, CCLI and the rest) and have the helper import them.
async fn songs_send(State(c): State<Ctx>, req: Request) -> Response {
    let (parts, body) = req.into_parts();
    let payload = match object(body).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    match songs::send(&c.host, &payload["songs"]).await {
        Ok(done) => {
            let names: Vec<Value> = done["files"].as_array().into_iter().flatten().take(20).cloned().collect();
            c.host.note(&parts, "propresenter.songs", &format!("Sent {} song(s) to ProPresenter", done["sent"]), "", Some(json!({"songs": names}))).await;
            Json(done).into_response()
        }
        Err(SendError::Song(e)) => detail(400, &e.message),
        Err(SendError::Helper(why)) => detail(409, &why),
    }
}

fn song_answer(done: Result<Value, songs::SongError>) -> Response {
    match done {
        Ok(v) => Json(v).into_response(),
        Err(e) => detail(400, &e.message),
    }
}

/// The lyrics, sections, arrangements and CCLI inside a `.pro` file (sent as base64 in `data_b64`).
async fn songs_read(State(c): State<Ctx>, req: Request) -> Response {
    let _ = &c;
    let payload = match object(req.into_body()).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    song_answer(songs::read_song(&payload["data_b64"], payload["inline"] == Value::Bool(true)))
}

/// A pasted lyric sheet (`text`) or a PDF (`pdf_b64`) read into sections of slides. Reads only; nothing is kept.
async fn songs_parse(State(c): State<Ctx>, req: Request) -> Response {
    let _ = &c;
    let payload = match object(req.into_body()).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    // Reading a PDF can take a while: it runs off the request's thread.
    let done = tokio::task::spawn_blocking(move || songs::parse_text(&payload)).await;
    song_answer(done.unwrap_or_else(|_| Err(songs::SongError { message: "That PDF could not be read.".into() })))
}

/// The given sections cut again into slides of `lines` lines. Reads only; nothing is kept.
async fn songs_split(State(c): State<Ctx>, req: Request) -> Response {
    let _ = &c;
    let payload = match object(req.into_body()).await {
        Ok(p) => p,
        Err(r) => return r,
    };
    song_answer(songs::split_song(&payload))
}

pub fn router(c: &Ctx) -> Router<Ctx> {
    let h = c.host.clone();
    let view = |r| gate(&h, "propresenter.view", r);
    let control = |r| gate(&h, "propresenter.control", r);
    let manage = |r| gate(&h, "propresenter.manage", r);
    let b = "/api/admin/propresenter";
    Router::new()
        .route(&format!("{b}/songs/helper"), view(get(songs_helper)))
        .route(&format!("{b}/songs/send"), manage(post(songs_send)))
        .route(&format!("{b}/songs/read"), view(post(songs_read)))
        .route(&format!("{b}/songs/parse"), view(post(songs_parse)))
        .route(&format!("{b}/songs/split"), view(post(songs_split)))
        .route(&format!("{b}/link"), view(get(link_get)))
        .route(&format!("{b}/link/presentations"), view(get(link_presentations)))
        .route(&format!("{b}/link/refresh"), manage(post(link_refresh)))
        .route(&format!("{b}/link/mapping"), manage(post(link_mapping)))
        .route(&format!("{b}/link/playlist"), manage(post(link_playlist)))
        .route(&format!("{b}/slides"), view(get(slides)))
        .route(&format!("{b}/thumbnail/{{uuid}}/{{index}}"), view(get(thumbnail)))
        .route(&format!("{b}/step"), control(post(step)))
        .route(&format!("{b}/next-presentation"), control(post(next_presentation)))
        .route(&format!("{b}/item"), control(post(item)))
        .route(&format!("{b}/screens"), control(post(screens)))
        .route(&format!("{b}/slide"), control(post(slide)))
        .route(&format!("{b}/clear"), control(post(clear)))
        .route(&format!("{b}/playlists"), view(get(playlists)))
        .route(&format!("{b}/playlists/{{playlist}}"), view(get(playlist)))
        .route(&format!("{b}/playlists/{{playlist}}/diagnostics"), view(get(playlist_diagnostics)))
        .route(&format!("{b}/playlists/{{playlist}}/items/{{index}}/cues"), view(get(playlist_cues)))
        .route(&format!("{b}/playlists/{{playlist}}/thumbnail/{{index}}/{{cue}}"), view(get(playlist_thumbnail)))
        .route(&format!("{b}/playlists/{{playlist}}/trigger"), control(post(playlist_trigger)))
        .route(&format!("{b}/macros"), view(get(macros)))
        .route(&format!("{b}/macros/{{macro}}/trigger"), control(post(macro_trigger)))
        .route(&format!("{b}/looks"), view(get(looks)))
        .route(&format!("{b}/looks/{{look}}/trigger"), control(post(look_trigger)))
        .route(&format!("{b}/timers"), view(get(timers)))
        .route(&format!("{b}/timers/{{timer}}/{{op}}"), control(post(timer)))
        .route(&format!("{b}/messages"), view(get(messages)))
        .route(&format!("{b}/messages/{{message}}/trigger"), control(post(message_trigger)))
        .route(&format!("{b}/messages/{{message}}/clear"), control(post(message_clear)))
        .route(&format!("{b}/transport"), control(post(transport)))
        .route(&format!("{b}/lobby"), view(get(lobby_get)))
        .route(&format!("{b}/lobby/start"), control(post(lobby_start)))
        .route(&format!("{b}/lobby/resume"), control(post(lobby_resume)))
        .route(&format!("{b}/lobby/stop"), control(post(lobby_stop)))
        .route(&format!("{b}/live"), view(get(live)))
        .route(&format!("{b}/live/image/{{which}}"), view(get(live_image)))
        .route("/stage", get(page_stage))
        .route("/api/stage/live", get(stage_live))
        .route("/api/stage/live/image/{which}", get(stage_live_image))
}
