//! Accounts over HTTP (#655): the sign-in, PIN, password and sign-out forms of `app/main.py`, the signed-in user's own routes
//! (`/api/admin/me*`), users, trusted devices, API tokens and the audit log. The rules are in `relay-auth`; this file is what
//! `require(...)` and the route functions of Python do: find who is asking, check the permission, answer with the fixed
//! sentence of the refusal, and write the audit entry once the change succeeded.
//!
//! The session cookie and the API token are the same ones the Python app reads (`relay_admin`, sha256 in `sessions`), so a
//! route still forwarded to Python sees the user this server signed in. Python keeps writing the audit entry for the routes it
//! still serves; this file writes it for these. First-run setup (`/admin/setup`, `/admin/import`) stays with Python until #656:
//! it stores the OpenAI key, which needs the sealed secret store.

// axum's `Response` is the error half of every helper here (the answer to send instead): that is the idiom, and each is built once.
#![allow(clippy::result_large_err)]

use std::path::PathBuf;
use std::sync::Arc;

use axum::extract::{Path, Request, State};
use axum::http::header::{HeaderMap, HeaderValue, SET_COOKIE};
use axum::http::StatusCode;
use axum::response::{Html, IntoResponse, Response};
use axum::routing::{delete, get, post, put};
use axum::{Json, Router};
use relay_auth::lockout::{self, Lockout, FAIL_DELAY};
use relay_auth::{Actor, Auth, Error, NewUser, Record, Refusal, Search, User, UserChanges};
use relay_core::Config;
use serde_json::{json, Map, Value};
use subtle::ConstantTimeEq;

use crate::forward::Conn;
use crate::pages::Pages;

macro_rules! need {
    ($acc:expr, $who:expr, $need:expr) => {
        match $acc.user_for(&$who, &$need).await {
            Ok(u) => u,
            Err(resp) => return resp,
        }
    };
}

mod admin;
mod audit;
mod me;
mod request;
mod signin;

pub(crate) use request::detail;
use request::{failure, Need, Who};

pub const ADMIN_COOKIE: &str = "relay_admin";
pub const DEVICE_COOKIE: &str = "relay_device";
const DEVICE_COOKIE_MAX_AGE: i64 = 2 * 365 * 24 * 3600;
pub const HOME_PATH: &str = "/home";
const DB_DOWN: &str = "The user database is not available. Relay is still captioning; try again in a moment.";
const SIGNIN_REQUIRED: &str = "Sign in required";
const NOT_ALLOWED: &str = "Not allowed";
const BAD_REQUEST: &str = "That request is not valid.";
const MISMATCH: &str = "The two passwords do not match.";
const BAD_API_TOKEN: &str = "That API token is not valid, has expired, or its user is turned off.";

/// What the account routes share.
pub struct Accounts {
    auth: Auth,
    lockout: Lockout,
    pages: Pages,
    config_path: PathBuf,
    /// Waits this long after a failed sign-in (`lockout::FAIL_DELAY`); the tests set it to nothing.
    fail_delay: std::time::Duration,
}

pub type Shared = Arc<Accounts>;

/// Who asked, for a route outside this file: the user and the address the audit entry names.
pub struct Caller {
    pub user: User,
    ip: String,
}

impl Accounts {
    pub fn new(auth: Auth, pages: Pages, config_path: PathBuf) -> Arc<Accounts> {
        Self::with_fail_delay(auth, pages, config_path, FAIL_DELAY)
    }

    /// As [`Accounts::new`], waiting `fail_delay` after a failed sign-in instead of `lockout::FAIL_DELAY`; the tests pass nothing.
    pub fn with_fail_delay(auth: Auth, pages: Pages, config_path: PathBuf, fail_delay: std::time::Duration) -> Arc<Accounts> {
        Arc::new(Self { auth, lockout: Lockout::new(), pages, config_path, fail_delay })
    }

    /// How long sign-in from `ip` stays locked, if it is.
    pub fn locked(&self, ip: &str) -> Option<std::time::Duration> {
        self.lockout.locked(ip)
    }
}

impl Accounts {
    async fn any_users(&self) -> Result<bool, Error> {
        Ok(self.auth.count_users().await? > 0)
    }

    async fn user_for(&self, who: &Who, need: &Need) -> Result<User, Response> {
        let user = match who.bearer() {
            Some(cred) => {
                let Some(user) = self.auth.token_user(&cred).await.map_err(failure)? else { return Err(detail(401, BAD_API_TOKEN)) };
                if !need.tokens {
                    return Err(detail(403, NOT_ALLOWED));
                }
                Some(user)
            }
            None => self.auth.session_user(&who.cookie(ADMIN_COOKIE)).await.map_err(failure)?,
        };
        let user = match user {
            Some(u) => Some(u),
            None => self.setup_owner(who).await.map_err(failure)?,
        };
        let Some(user) = user else { return Err(detail(401, SIGNIN_REQUIRED)) };
        if user.must_change_password && !need.pending_ok {
            return Err(detail(403, "password_change_required"));
        }
        if need.perm.is_some_and(|p| !user.can(p)) {
            return Err(detail(403, NOT_ALLOWED));
        }
        Ok(user)
    }

    /// For a route that lives outside this file (the admin status stream, #656): is the caller signed in, with `perm`? The refusal is
    /// the response to send (`require(perm)` in `app/users.py`).
    pub async fn require(&self, headers: &HeaderMap, conn: Option<Conn>, perm: &'static str) -> Result<(), Response> {
        let who = Who::of(headers, conn);
        self.user_for(&who, &Need { perm: Some(perm), pending_ok: false, tokens: true }).await.map(|_| ())
    }

    /// The signed-in user behind a request, for a route that lives outside this file (the audio routes, #657): `perm` None is "just
    /// signed in". The refusal is the response to send. Keep the [`Caller`] to write the audit entry once the change succeeded.
    pub async fn caller(&self, headers: &HeaderMap, conn: Option<Conn>, perm: Option<&'static str>) -> Result<Caller, Response> {
        let who = Who::of(headers, conn);
        let user = self.user_for(&who, &Need { perm, pending_ok: false, tokens: true }).await?;
        Ok(Caller { user, ip: who.ip })
    }

    /// Write one audit entry for `caller` (never fails the request).
    pub async fn record(&self, caller: &Caller, action: &str, summary: &str, target: &str, detail: Option<Value>) {
        self.auth.record(action, summary, Record { actor: Some(&caller.user), target, detail: detail.as_ref(), ip: &caller.ip }).await;
    }

    /// `x-admin-token` is honoured only while no account exists, and then acts as an Owner with no row (id 0).
    async fn setup_owner(&self, who: &Who) -> Result<Option<User>, Error> {
        let supplied = who.headers.get("x-admin-token").and_then(|v| v.to_str().ok()).unwrap_or("");
        if supplied.is_empty() || self.any_users().await? {
            return Ok(None);
        }
        let real = Config::load(&self.config_path)
            .ok()
            .and_then(|c| c.get("admin_token"))
            .and_then(|v| v.as_str().map(|s| s.trim().to_owned()))
            .unwrap_or_default();
        if real.is_empty() || !bool::from(supplied.as_bytes().ct_eq(real.as_bytes())) {
            return Ok(None);
        }
        Ok(Some(User {
            id: 0,
            username: "setup".into(),
            name: "Setup token".into(),
            role: "owner".into(),
            disabled: false,
            must_change_password: false,
            pin_set: false,
            token_id: 0,
        }))
    }

    async fn note(&self, who: &Who, user: &User, action: &str, summary: &str, target: &str, detail: Option<Value>) {
        self.auth.record(action, summary, Record { actor: Some(user), target, detail: detail.as_ref(), ip: &who.ip }).await;
    }

    /// Count a failed attempt and make it cost the fixed delay; the one that starts a lock-out is written to the audit log.
    async fn fail(&self, ip: &str) {
        tokio::time::sleep(self.fail_delay).await;
        if self.lockout.failed(ip) {
            let summary = format!(
                "Sign-in from this address locked for {} min after {} failed attempts",
                lockout::LOCKOUT.as_secs() / 60,
                lockout::MAX_FAILS
            );
            self.auth.record("auth.locked", &summary, Record { ip, ..Default::default() }).await;
        }
    }
}


// ---------------------------------------------------------------- the routes

/// Every route this file answers. Anything else on /admin or /api/admin is forwarded to the Python app.
pub fn routes(acc: Shared) -> Router {
    Router::new()
        .route("/admin/login", post(Accounts::admin_login))
        .route("/admin/login/pin", post(Accounts::admin_login_pin))
        .route("/admin/password", post(Accounts::admin_password))
        .route("/admin/logout", post(Accounts::admin_logout))
        .route("/api/admin/me", get(Accounts::api_me))
        .route("/api/admin/me/password", post(Accounts::api_me_password))
        .route("/api/admin/me/pin", post(Accounts::api_me_pin_set).delete(Accounts::api_me_pin_clear))
        .route("/api/admin/me/tokens", get(Accounts::api_me_tokens).post(Accounts::api_me_token_create))
        .route("/api/admin/me/tokens/{id}", delete(Accounts::api_me_token_revoke))
        .route("/api/admin/tokens", get(Accounts::api_tokens))
        .route("/api/admin/tokens/{id}", delete(Accounts::api_token_revoke))
        .route("/api/admin/users", get(Accounts::api_users).post(Accounts::api_user_create))
        .route("/api/admin/users/{id}", put(Accounts::api_user_update).delete(Accounts::api_user_delete))
        .route("/api/admin/users/{id}/password", post(Accounts::api_user_password))
        .route("/api/admin/users/{id}/signout", post(Accounts::api_user_signout))
        .route("/api/admin/trusted-devices", get(Accounts::api_devices).post(Accounts::api_device_trust))
        .route("/api/admin/trusted-devices/{id}", delete(Accounts::api_device_revoke))
        .route("/api/admin/audit", get(Accounts::api_audit))
        .with_state(acc)
}
