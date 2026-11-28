//! Users and trusted devices (`/api/admin/users*`, `/api/admin/trusted-devices*`).

use super::request::*;
use super::*;
use super::DEVICE_COOKIE;

fn roles() -> Value {
    Value::Array(relay_auth::perms::ROLES.iter().enumerate().map(|(i, r)| json!({"id": r, "label": title(r), "rank": i + 1})).collect())
}

/// What changed about a user, from and to: the role change is the one an audit is most often asked for.
fn user_change(before: Option<&User>, after: &User) -> (String, Value) {
    let mut changes = Map::new();
    let mut words: Vec<String> = Vec::new();
    if let Some(b) = before {
        if b.role != after.role {
            changes.insert("role".into(), json!([b.role, after.role]));
            words.push(format!("moved {} from {} to {}", after.name, title(&b.role), title(&after.role)));
        }
        if b.disabled != after.disabled {
            changes.insert("disabled".into(), json!([b.disabled, after.disabled]));
            words.push(format!("turned {} {}", after.name, if after.disabled { "off" } else { "on" }));
        }
        if b.name != after.name {
            changes.insert("name".into(), json!([b.name, after.name]));
            words.push(format!("renamed {} to {}", b.name, after.name));
        }
    }
    let summary = if words.is_empty() { format!("saved {} unchanged", after.name) } else { words.join("; ") };
    let mut c = summary.chars();
    let summary = c.next().map(|f| f.to_uppercase().collect::<String>() + c.as_str()).unwrap_or_default();
    (summary, Value::Object(changes))
}


impl Accounts {
    pub(super) async fn api_users(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let _ = need!(acc, who, Need { perm: Some("users.view"), ..SIGNED });
        match acc.auth.list_users().await {
            Ok(users) => Json(json!({"users": users, "roles": roles(), "permissions": relay_auth::perms::catalogue()})).into_response(),
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_user_create(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        let new = NewUser { username: text(&body, "username"), name: text(&body, "name"), role: text(&body, "role"), password: text(&body, "password"), temporary: true };
        match acc.auth.create_user(new, Some(&actor)).await {
            Ok(made) => {
                let summary = format!("Added {} as {}", made.name, title(&made.role));
                acc.note(&who, &actor, "user.create", &summary, &made.username, Some(json!({"role": made.role}))).await;
                Json(json!({"user": made})).into_response()
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_user_update(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        let disabled = match body.get("disabled") {
            None => None,
            Some(Value::Bool(b)) => Some(*b),
            Some(_) => return detail(400, BAD_REQUEST),
        };
        let changes = UserChanges { name: text_opt(&body, "name"), role: text_opt(&body, "role"), disabled };
        let before = acc.auth.get_user(id).await.ok().flatten();
        match acc.auth.update_user(id, changes, Some(&actor)).await {
            Ok(after) => {
                let (summary, changes) = user_change(before.as_ref(), &after);
                acc.note(&who, &actor, "user.update", &summary, &after.username, Some(json!({"changes": changes}))).await;
                Json(json!({"user": after})).into_response()
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_user_password(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        if let Err(e) = acc.auth.set_password(id, text(&body, "password"), true, Some(&actor)).await {
            return failure(e);
        }
        if let Ok(Some(target)) = acc.auth.get_user(id).await {
            let summary = format!("Reset {}'s password", target.name);
            acc.note(&who, &actor, "user.password_reset", &summary, &target.username, None).await;
        }
        Json(json!({"ok": true})).into_response()
    }

    pub(super) async fn api_user_signout(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        let target = match acc.auth.get_user(id).await {
            Ok(Some(t)) => t,
            Ok(None) => return failure(Refusal::NotFound.into()),
            Err(e) => return failure(e),
        };
        if relay_auth::perms::rank(&target.role) > relay_auth::perms::rank(&actor.role) {
            return failure(Refusal::AccountAboveYou.into());
        }
        if let Err(e) = acc.auth.end_sessions(id, None).await {
            return failure(e);
        }
        let summary = format!("Signed {} out everywhere", target.name);
        acc.note(&who, &actor, "user.signout", &summary, &target.username, None).await;
        Json(json!({"ok": true})).into_response()
    }

    pub(super) async fn api_user_delete(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let actor = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        let target = acc.auth.get_user(id).await.ok().flatten();
        if let Err(e) = acc.auth.delete_user(id, Some(&actor)).await {
            return failure(e);
        }
        if let Some(t) = target {
            let summary = format!("Deleted {} ({})", t.name, title(&t.role));
            acc.note(&who, &actor, "user.delete", &summary, &t.username, Some(json!({"role": t.role}))).await;
        }
        Json(json!({"ok": true})).into_response()
    }

    pub(super) async fn api_devices(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let _ = need!(acc, who, Need { perm: Some("users.view"), ..SIGNED });
        let here = match acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await {
            Ok(d) => d.map(|d| d.id),
            Err(e) => return failure(e),
        };
        match acc.auth.list_devices().await {
            Ok(devices) => Json(json!({"devices": devices, "this": here})).into_response(),
            Err(e) => failure(e),
        }
    }

    /// Trust THIS browser: it gets the relay_device cookie, and from then on may sign in with a PIN.
    pub(super) async fn api_device_trust(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        if user.id <= 0 {
            return detail(403, NOT_ALLOWED); // the setup token has no account to record it against
        }
        let body = match body_object(&axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default()) {
            Ok(b) => b,
            Err(r) => return r,
        };
        match acc.auth.trust_device(text_opt(&body, "name").unwrap_or(""), &user, who.user_agent()).await {
            Ok((token, row)) => {
                acc.note(&who, &user, "device.trust", "Trusted a browser for PIN sign-in", &row.name, None).await;
                let mut resp = Json(json!({"device": row, "this": row.id})).into_response();
                set_cookie(&mut resp, &who, DEVICE_COOKIE, &token, DEVICE_COOKIE_MAX_AGE);
                resp
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn api_device_revoke(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, Path(id): Path<i64>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let user = need!(acc, who, Need { perm: Some("users.manage"), ..SIGNED });
        let had = acc.auth.list_devices().await.ok().and_then(|all| all.into_iter().find(|d| d.id == id));
        if let Err(e) = acc.auth.revoke_device(id).await {
            return failure(e);
        }
        if let Some(had) = had {
            acc.note(&who, &user, "device.revoke", "Forgot a trusted device", &had.name, None).await;
        }
        Json(json!({"ok": true})).into_response()
    }
}
