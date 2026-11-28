//! Static files (#654): `/static/<file>` from `app/static/` and `/static/modules/<id>/<file>` from that module's `static/`
//! (`RevalidatedStaticFiles` in `app/main.py`). Same files for the pages that link them (`relay-ui.js`, `components.css`,
//! `tokens.css`), served whether the module is on or off, with the same caching: `Cache-Control: no-cache`, an `ETag` and a
//! `Last-Modified`, so an unchanged file is a cheap 304 and a changed one is never kept from before an update.

use std::path::{Path, PathBuf};
use std::sync::Arc;

use axum::body::Body;
use axum::extract::{Path as UrlPath, Request, State};
use axum::http::header::{CACHE_CONTROL, CONTENT_LENGTH, ETAG, IF_NONE_MATCH, LAST_MODIFIED};
use axum::http::{HeaderValue, StatusCode, Uri};
use axum::response::Response;
use axum::routing::get;
use axum::Router;
use sha2::{Digest, Sha256};
use tower::ServiceExt;
use tower_http::services::ServeDir;

use crate::pages::static_file;

/// `GET /static/{path}`; `app_dir` holds `static/` and `modules/<id>/static/`.
pub fn routes(app_dir: &Path) -> Router {
    Router::new().route("/static/{*path}", get(serve)).with_state(Arc::new(app_dir.to_path_buf()))
}

async fn serve(State(root): State<Arc<PathBuf>>, UrlPath(path): UrlPath<String>, req: Request) -> Response {
    let Some(file) = static_file(&root, &path) else { return nothing() };
    let (Some(dir), Some(name)) = (file.parent(), file.file_name().and_then(|n| n.to_str())) else { return nothing() };
    let if_none_match = req.headers().get(IF_NONE_MATCH).cloned();

    // ServeDir does the type, the ranges and `If-Modified-Since`; it is given only this one file's folder and name.
    let (mut parts, _) = req.into_parts();
    let Ok(uri) = format!("/{name}").parse::<Uri>() else { return nothing() };
    parts.uri = uri;
    let inner = Request::from_parts(parts, Body::empty());
    let Ok(res) = ServeDir::new(dir).append_index_html_on_directories(false).oneshot(inner).await;
    let mut res = res.map(Body::new);

    if !matches!(res.status(), StatusCode::OK | StatusCode::PARTIAL_CONTENT | StatusCode::NOT_MODIFIED) {
        return nothing();
    }
    let headers = res.headers_mut();
    headers.entry(CACHE_CONTROL).or_insert(HeaderValue::from_static("no-cache"));
    if let (Some(etag), true) = (etag(headers), !headers.contains_key(ETAG)) {
        if let Ok(v) = HeaderValue::from_str(&etag) {
            headers.insert(ETAG, v);
        }
    }
    if let (Some(sent), Some(mine)) = (if_none_match, headers.get(ETAG)) {
        if sent.as_bytes().split(|b| *b == b',').any(|t| t.trim_ascii() == mine.as_bytes()) {
            headers.remove(CONTENT_LENGTH);
            let mut not_modified = Response::new(Body::empty());
            *not_modified.status_mut() = StatusCode::NOT_MODIFIED;
            *not_modified.headers_mut() = headers.clone();
            return not_modified;
        }
    }
    res
}

/// An ETag from what changes when the file does: its modification time and size.
fn etag(headers: &axum::http::HeaderMap) -> Option<String> {
    let modified = headers.get(LAST_MODIFIED)?.to_str().ok()?;
    let length = headers.get(CONTENT_LENGTH).and_then(|v| v.to_str().ok()).unwrap_or("");
    let digest = Sha256::digest(format!("{modified}-{length}"));
    Some(format!("\"{}\"", digest.iter().take(16).map(|b| format!("{b:02x}")).collect::<String>()))
}

fn nothing() -> Response {
    let mut r = Response::new(Body::from(r#"{"detail":"Not found"}"#));
    *r.status_mut() = StatusCode::NOT_FOUND;
    r.headers_mut().insert("content-type", HeaderValue::from_static("application/json"));
    r
}
