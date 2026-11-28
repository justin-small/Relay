//! What modules get from `Host` (#662): a gated route refuses until the server hands over its accounts and then asks them for
//! the named permission, and settings read and save through the store.

use std::sync::Arc;

use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{Request, StatusCode};
use axum::response::{IntoResponse, Response};
use axum::routing::get;
use axum::Router;
use relay_core::gate::gate;
use relay_core::future::BoxFuture;
use relay_core::{AppError, Authorizer, Config, ErrorCode, Host, Shutdown};
use serde_json::json;
use tower::ServiceExt;

/// Lets a caller through when it sends `x-role: <permission>` matching what the route needs.
struct Header;

impl Authorizer for Header {
    fn require<'a>(&'a self, request: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async move {
            match request.headers.get("x-can").and_then(|v| v.to_str().ok()) {
                Some(p) if p == permission => Ok(()),
                Some(_) => Err(AppError::new(ErrorCode::NotAllowed).into_response()),
                None => Err(AppError::new(ErrorCode::SignInRequired).into_response()),
            }
        })
    }
}

fn status(app: &Router, can: Option<&str>) -> StatusCode {
    let mut req = Request::builder().uri("/x");
    if let Some(p) = can {
        req = req.header("x-can", p);
    }
    let app = app.clone();
    let rt = tokio::runtime::Builder::new_current_thread().build().unwrap();
    rt.block_on(async move { app.oneshot(req.body(Body::empty()).unwrap()).await.unwrap().status() })
}

#[test]
fn a_gated_route_refuses_until_accounts_are_handed_over_then_asks_them() {
    let host = Host::new(Shutdown::new());
    let app = Router::new().route("/x", gate(&host, "demo.manage", get(|| async { "ok" })));
    assert_eq!(status(&app, Some("demo.manage")), StatusCode::UNAUTHORIZED, "no accounts yet: closed");
    host.set_authorizer(Arc::new(Header));
    assert_eq!(status(&app, None), StatusCode::UNAUTHORIZED);
    assert_eq!(status(&app, Some("demo.view")), StatusCode::FORBIDDEN);
    assert_eq!(status(&app, Some("demo.manage")), StatusCode::OK);
}

#[test]
fn settings_read_and_save_through_the_store() {
    let dir = std::env::temp_dir().join(format!("relay-core-store-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("config.json");
    std::fs::write(&path, json!({ "demo": { "a": 1 } }).to_string()).unwrap();
    let config = Arc::new(Config::load(&path).unwrap());
    let host = Host::new(Shutdown::new());
    let _registry = relay_core::Registry::new(Arc::clone(&config), host.clone());
    assert_eq!(host.settings("demo"), Some(json!({ "a": 1 })));
    host.save_settings("demo", &json!({ "b": 2 })).unwrap();
    assert_eq!(host.settings("demo"), Some(json!({ "a": 1, "b": 2 })));
    assert_eq!(host.settings("missing"), None);
    let _ = std::fs::remove_dir_all(dir);
}
