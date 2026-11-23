//! One error and response convention (#652).
//!
//! A response never carries an exception's text. The body is `{"detail": "<sentence>"}`, the shape Python's `HTTPException`
//! gives today, so the operator pages and the API tokens' scripts see no change. The sentence comes from [`ErrorCode`]'s
//! table, or from a module's own table of `&'static str` (the type is the rule: a `String` built from an error cannot be
//! passed). What went wrong, with the cause, goes to the log through `tracing` and nowhere else. CodeQL fails a pull
//! request that lets an exception's text reach a response, even a fixed sentence passed through `to_string()`.

use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use axum::Json;
use serde_json::json;

/// What kind of failure, as the caller sees it. Each code has one status and one sentence.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ErrorCode {
    /// 400: the request is malformed or a value is out of range.
    BadRequest,
    /// 401: nobody is signed in.
    SignInRequired,
    /// 403: signed in, but the role or permission does not allow it.
    NotAllowed,
    /// 404: there is no such thing, or the module that owns the route is off.
    NotFound,
    /// 409: it clashes with what is already there.
    Conflict,
    /// 429: too many requests, or too many failed sign-ins.
    TooMany,
    /// 502: the product a module talks to answered badly or not at all.
    Upstream,
    /// 503: the database is down or still starting.
    Unavailable,
    /// 500: a bug. The cause is in the log.
    Internal,
}

impl ErrorCode {
    pub const fn status(self) -> StatusCode {
        match self {
            Self::BadRequest => StatusCode::BAD_REQUEST,
            Self::SignInRequired => StatusCode::UNAUTHORIZED,
            Self::NotAllowed => StatusCode::FORBIDDEN,
            Self::NotFound => StatusCode::NOT_FOUND,
            Self::Conflict => StatusCode::CONFLICT,
            Self::TooMany => StatusCode::TOO_MANY_REQUESTS,
            Self::Upstream => StatusCode::BAD_GATEWAY,
            Self::Unavailable => StatusCode::SERVICE_UNAVAILABLE,
            Self::Internal => StatusCode::INTERNAL_SERVER_ERROR,
        }
    }

    /// The fixed sentence. The wording of the first three is Python's (`WEB_MESSAGES` in `app/main.py`), so a client that
    /// matches on it keeps working through the port.
    pub const fn sentence(self) -> &'static str {
        match self {
            Self::BadRequest => "That request is not valid",
            Self::SignInRequired => "Sign in required",
            Self::NotAllowed => "Your role is not allowed to do that",
            Self::NotFound => "Not found",
            Self::Conflict => "That clashes with something already set up",
            Self::TooMany => "Too many attempts; wait a moment and try again",
            Self::Upstream => "The other product did not answer as expected",
            Self::Unavailable => "Relay's database is not available right now",
            Self::Internal => "Something went wrong inside Relay",
        }
    }
}

/// A failure on its way to becoming a response. Build it with [`AppError::new`], add the cause with [`AppError::because`].
#[derive(Debug)]
pub struct AppError {
    code: ErrorCode,
    sentence: &'static str,
    cause: Option<String>,
}

impl AppError {
    pub const fn new(code: ErrorCode) -> Self {
        Self { code, sentence: code.sentence(), cause: None }
    }

    /// The code's status with a sentence of the caller's own. Only a `&'static str` is accepted, so a sentence is always a
    /// literal in the source, never text from an error.
    pub const fn with_sentence(code: ErrorCode, sentence: &'static str) -> Self {
        Self { code, sentence, cause: None }
    }

    /// Record why, for the log. The cause is never sent to the caller.
    #[must_use]
    pub fn because(mut self, cause: impl std::fmt::Display) -> Self {
        self.cause = Some(cause.to_string());
        self
    }

    pub const fn code(&self) -> ErrorCode {
        self.code
    }

    pub const fn sentence(&self) -> &'static str {
        self.sentence
    }

    pub fn cause(&self) -> Option<&str> {
        self.cause.as_deref()
    }
}

impl std::fmt::Display for AppError {
    /// For the log. A response is built by [`IntoResponse`], which never calls this.
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        match &self.cause {
            Some(cause) => write!(f, "{}: {cause}", self.sentence),
            None => f.write_str(self.sentence),
        }
    }
}

impl std::error::Error for AppError {}

impl IntoResponse for AppError {
    fn into_response(self) -> Response {
        if self.code == ErrorCode::Internal || self.code == ErrorCode::Unavailable || self.code == ErrorCode::Upstream {
            tracing::error!(status = self.code.status().as_u16(), "{self}");
        } else {
            tracing::debug!(status = self.code.status().as_u16(), "{self}");
        }
        (self.code.status(), Json(json!({ "detail": self.sentence }))).into_response()
    }
}

impl From<ErrorCode> for AppError {
    fn from(code: ErrorCode) -> Self {
        Self::new(code)
    }
}
