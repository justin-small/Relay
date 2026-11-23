//! Who may call a module's routes (#662): the module names the permission a route needs, and the server's accounts decide.
//!
//! Core knows no account. It defines [`Authorizer`], which the server implements over its accounts (`relay-server`'s
//! `Accounts::require`), and the helper [`gate`] that puts one permission in front of a route. A module declares the
//! permissions it adds ([`Permission`], Python's `Module.permissions`) and the server registers them with the role table.
//!
//! Until the server hands its accounts over, a gated route refuses everything (401): a module never runs open.

use axum::extract::{Request, State};
use axum::http::request::Parts;
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::MethodRouter;

use crate::error::{AppError, ErrorCode};
use crate::module::{BoxFuture, Host};

/// One permission a module adds: `<area>.<action>`, the sentence the Users page shows, its group, and the lowest role that holds it
/// (`member`, `viewer`, `operator`, `admin` or `owner`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Permission {
    pub id: &'static str,
    pub label: &'static str,
    pub group: &'static str,
    pub min_role: &'static str,
}

pub trait Authorizer: Send + Sync + 'static {
    /// Is the caller of this request signed in (or holding an API token) with `permission`? `Err` is the response to send.
    fn require<'a>(&'a self, request: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>>;
}

async fn check(State((host, permission)): State<(Host, &'static str)>, request: Request, next: Next) -> Response {
    let Some(authorizer) = host.authorizer() else {
        return AppError::new(ErrorCode::SignInRequired).into_response();
    };
    let (parts, body) = request.into_parts();
    if let Err(refusal) = authorizer.require(&parts, permission).await {
        return refusal;
    }
    next.run(Request::from_parts(parts, body)).await
}

/// `route` answers only a caller who holds `permission`.
pub fn gate<S: Clone + Send + Sync + 'static>(host: &Host, permission: &'static str, route: MethodRouter<S>) -> MethodRouter<S> {
    route.layer(middleware::from_fn_with_state((host.clone(), permission), check))
}
