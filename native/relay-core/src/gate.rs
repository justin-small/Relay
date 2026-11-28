//! Puts one permission in front of a route (#662). It uses [`Host`] to reach the server's [`Authorizer`](crate::access::Authorizer),
//! so it sits above both `module` and `access`.

use axum::extract::{Request, State};
use axum::middleware::{self, Next};
use axum::response::{IntoResponse, Response};
use axum::routing::MethodRouter;

use crate::error::{AppError, ErrorCode};
use crate::module::Host;

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
