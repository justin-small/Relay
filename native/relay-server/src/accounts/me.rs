//! The signed-in user's own routes (`/api/admin/me*`) and the token lists.

use super::request::*;
use super::*;

impl Accounts {
    pub(super) async fn me_payload(acc: &Accounts, who: &Who, user: &User) -> Result<Value, Response> {
        let device = acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await.map_err(failure)?;
        let prefs = if user.id != 0 { acc.auth.all_prefs(user.id).await.map_err(failure)? } else { Value::Object(Map::new()) };
        Ok(json!({
            "user": {"id": user.id, "username": user.username, "name": user.name, "role": user.role, "must_change_password": user.must_change_password},
            "permissions": user.permissions().into_iter().collect::<Vec<_>>(),
            "pin_set": user.pin_set,
            "device": {"trusted": device.is_some(), "name": device.map(|d| d.name)},
            "prefs": prefs,
        }))
    }

    pub(super) async fn api_me(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { pending_ok: true, ..SIGNED });
        match Accounts::me_payload(&acc, &who, &user).await {
            Ok(v) => Json(v).into_response(),
            Err(r) => r,
        }
    }

    pub(super) async fn api_me_password(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { pending_ok: true, tokens: false, ..SIGNED });
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        let Some(new) = text_opt(&body, "new") else { return failure(Refusal::BadPassword.into()) };
        let Some(current) = text_opt(&body, "current") else { return failure(Refusal::WrongPassword.into()) };
        match acc.auth.change_own_password(&user, current, new, Some(&who.cookie(ADMIN_COOKIE))).await {
            Ok(()) => Json(json!({"ok": true})).into_response(),
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_me_pin_set(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { tokens: false, ..SIGNED });
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        let Some(pin) = text_opt(&body, "pin") else { return failure(Refusal::BadPin.into()) };
        let Some(password) = text_opt(&body, "password") else { return failure(Refusal::WrongPassword.into()) };
        match acc.auth.set_pin(&user, password, pin).await {
            Ok(()) => {
                acc.note(&who, &user, "account.pin_set", "Set their PIN", "", None).await;
                Json(json!({"ok": true, "pin_set": true})).into_response()
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_me_pin_clear(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { tokens: false, ..SIGNED });
        match acc.auth.clear_pin(user.id).await {
            Ok(()) => {
                acc.note(&who, &user, "account.pin_clear", "Removed their PIN", "", None).await;
                Json(json!({"ok": true, "pin_set": false})).into_response()
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_me_tokens(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { perm: Some("tokens.own"), tokens: false, ..SIGNED });
        match acc.auth.list_tokens(Some(user.id)).await {
            Ok(tokens) => Json(json!({
                "tokens": tokens,
                "expiry_days": relay_auth::TOKEN_EXPIRY_DAYS,
                "default_days": relay_auth::TOKEN_DEFAULT_DAYS,
            }))
            .into_response(),
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_me_token_create(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { perm: Some("tokens.own"), tokens: false, ..SIGNED });
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        // `days` is 30, 90, 365 or null for never; absent is the default, and anything else (a bool, a string) is refused.
        let days = match body.get("days") {
            None => Some(relay_auth::TOKEN_DEFAULT_DAYS),
            Some(Value::Null) => None,
            Some(Value::Number(n)) => match n.as_i64() {
                Some(d) => Some(d),
                None => return failure(Refusal::BadExpiry.into()),
            },
            Some(_) => return failure(Refusal::BadExpiry.into()),
        };
        match acc.auth.create_token(&user, text(&body, "name"), days).await {
            Ok((secret, row)) => {
                let expires = row.expires.clone().unwrap_or_else(|| "never".into());
                acc.note(&who, &user, "token.create", "Made an API token", &row.name, Some(json!({"expires": expires}))).await;
                no_store(Json(json!({"token": row, "secret": secret})).into_response())
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_me_token_revoke(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { tokens: false, ..SIGNED });
        let mine = match acc.auth.list_tokens(Some(user.id)).await {
            Ok(all) => all.into_iter().find(|t| t.id == id),
            Err(e) => return failure(e),
        };
        let Some(mine) = mine else { return failure(Refusal::NotFound.into()) };
        match acc.auth.revoke_token(id, &user).await {
            Ok(()) => {
                acc.note(&who, &user, "token.revoke", "Revoked one of their API tokens", &mine.name, None).await;
                Json(json!({"ok": true})).into_response()
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_tokens(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let _ = need!(acc, who, Need { perm: Some("tokens.manage"), ..SIGNED });
        match acc.auth.list_tokens(None).await {
            Ok(tokens) => Json(json!({"tokens": tokens})).into_response(),
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_token_revoke(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { perm: Some("tokens.manage"), ..SIGNED });
        let had = match acc.auth.list_tokens(None).await {
            Ok(all) => all.into_iter().find(|t| t.id == id),
            Err(e) => return failure(e),
        };
        if let Err(e) = acc.auth.revoke_token(id, &user).await {
            return failure(e);
        }
        if let Some(had) = had {
            let summary = format!("Revoked {}'s API token", had.user_name);
            acc.note(&who, &user, "token.revoke", &summary, &had.name, Some(json!({"owner": had.username}))).await;
        }
        Json(json!({"ok": true})).into_response()
    }
}
