//! The sign-in, PIN, password and sign-out forms (`/admin/login`, `/admin/login/pin`, `/admin/password`, `/admin/logout`).

use super::request::*;
use super::*;

fn signed_in(who: &Who, session: &relay_auth::Session, to: &str) -> Response {
    let mut resp = see_other(to);
    set_cookie(&mut resp, who, ADMIN_COOKIE, &session.token, session.max_age);
    resp
}

/// The audit sentence for each refused sign-in, by refusal.
fn signin_refused(r: Refusal) -> &'static str {
    match r {
        Refusal::BadLogin => "Failed sign-in: wrong username or password",
        Refusal::Disabled => "Sign-in refused: the account is turned off",
        Refusal::WrongPin => "Failed sign-in: wrong PIN",
        Refusal::PinCleared => "PIN cleared after too many wrong tries",
        Refusal::NoDevice => "PIN sign-in refused: not a trusted device",
        _ => "Failed sign-in",
    }
}


impl Accounts {
    pub(super) fn render(acc: &Accounts, template: &str, ctx: Value, status: u16) -> Response {
        match acc.pages.render(template, &ctx) {
            Ok(html) => no_store((StatusCode::from_u16(status).unwrap_or(StatusCode::OK), Html(html)).into_response()),
            Err(e) => {
                tracing::error!("page {template} did not render: {e}");
                detail(500, "Something went wrong inside Relay")
            }
        }
    }

    /// The sign-in form again, with a sentence. On a trusted device with PIN users it is the PIN picker unless `pw`.
    pub(super) async fn login_page(acc: &Accounts, who: &Who, error: &str, status: u16, username: &str, pw: bool, pin_user: Option<i64>) -> Response {
        let (device, pins) = acc.auth.pin_picker(&who.cookie(DEVICE_COOKIE)).await.unwrap_or_default();
        let mode = if !pins.is_empty() && !pw { "pin" } else { "signin" };
        let pin_users: Vec<Value> = pins.iter().map(|(id, name)| json!({"id": id, "name": name})).collect();
        Accounts::render(
            acc,
            "login.html",
            json!({"mode": mode, "error": error, "username": username, "pin_users": pin_users, "pin_user": pin_user,
                   "device_name": device.map(|d| d.name).unwrap_or_default()}),
            status,
        )
    }

    pub(super) fn password_page(acc: &Accounts, error: &str, status: u16, voluntary: bool) -> Response {
        Accounts::render(acc, "password_change.html", json!({"error": error, "voluntary": voluntary}), status)
    }

    /// A refused sign-in, against the account it named when there is one. What was typed as a username is never written: it is
    /// sometimes a password typed in the wrong box.
    pub(super) async fn audit_failed(acc: &Accounts, who: &Who, r: Refusal, named: Option<&User>, way: &str, device: &str) {
        let mut d = json!({"with": way, "reason": r.key()});
        if !device.is_empty() {
            d["device"] = json!(device);
        }
        let target = named.map_or("(unknown username)", |u| u.username.as_str());
        acc.auth.record("auth.failed", signin_refused(r), Record { target, detail: Some(&d), ip: &who.ip, actor: None }).await;
    }

    pub(super) async fn admin_login(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let body = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
        let f = form(&body);
        let username = field(&f, "username", 100);
        let password = f.get("password").cloned().unwrap_or_default();
        if let Some(left) = acc.lockout.locked(&who.ip) {
            tracing::warn!("Admin login from {} refused, locked for {}s", who.ip, left.as_secs());
            return Accounts::login_page(&acc, &who, &lockout::sentence(left), 429, &username, true, None).await;
        }
        match acc.auth.count_users().await {
            Ok(0) => return see_other("/admin"), // no accounts yet: the first-run form is Python's
            Ok(_) => {}
            Err(e) => return failure(e),
        }
        let user = match acc.auth.authenticate(&username, &password).await {
            Ok(u) => u,
            Err(Error::Refused(r)) => {
                let named = acc.auth.get_by_username(&username).await.ok().flatten();
                Accounts::audit_failed(&acc, &who, r, named.as_ref(), "password", "").await;
                if r == Refusal::BadLogin {
                    acc.fail(&who.ip).await;
                }
                return Accounts::login_page(&acc, &who, r.message(), r.status(), &username, true, None).await;
            }
            Err(e) => return failure(e),
        };
        acc.auth.record("auth.password", "Signed in with their password", Record { actor: Some(&user), ip: &who.ip, ..Default::default() }).await;
        match acc.auth.start_session(&user, "password", None, who.user_agent()).await {
            Ok(s) => {
                acc.lockout.clear(&who.ip);
                signed_in(&who, &s, home_for(&user))
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn admin_login_pin(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let body = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
        let f = form(&body);
        let raw = f.get("user_id").map(String::as_str).unwrap_or("");
        let uid: i64 = if raw.is_ascii() && !raw.is_empty() && raw.len() < 10 && raw.bytes().all(|b| b.is_ascii_digit()) { raw.parse().unwrap_or(-1) } else { -1 };
        if let Some(left) = acc.lockout.locked(&who.ip) {
            return Accounts::login_page(&acc, &who, &lockout::sentence(left), 429, "", false, Some(uid)).await;
        }
        let device = match acc.auth.device_for(&who.cookie(DEVICE_COOKIE)).await {
            Ok(d) => d,
            Err(e) => return failure(e),
        };
        let pin = field(&f, "pin", 16);
        let user = match acc.auth.authenticate_pin(uid, &pin, device.as_ref().map(|d| d.id)).await {
            Ok(u) => u,
            Err(Error::Refused(r)) => {
                let named = if uid > 0 { acc.auth.get_user(uid).await.ok().flatten() } else { None };
                Accounts::audit_failed(&acc, &who, r, named.as_ref(), "PIN", device.as_ref().map_or("", |d| d.name.as_str())).await;
                if matches!(r, Refusal::WrongPin | Refusal::PinCleared) {
                    acc.fail(&who.ip).await;
                }
                // Without a trusted device there is no picker to go back to: the password form, with the sentence.
                return Accounts::login_page(&acc, &who, r.message(), r.status(), "", r == Refusal::NoDevice, Some(uid)).await;
            }
            Err(e) => return failure(e),
        };
        let Some(device) = device else { return detail(401, Refusal::NoDevice.message()) };
        acc.auth
            .record("auth.pin", "Signed in with their PIN", Record { actor: Some(&user), detail: Some(&json!({"device": device.name})), ip: &who.ip, ..Default::default() })
            .await;
        match acc.auth.start_session(&user, "pin", Some(device.id), who.user_agent()).await {
            Ok(s) => {
                acc.lockout.clear(&who.ip);
                signed_in(&who, &s, home_for(&user))
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn admin_password(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let body = axum::body::to_bytes(req.into_body(), 1 << 16).await.unwrap_or_default();
        let f = form(&body);
        let again = f.get("voluntary").is_some_and(|v| v == "1");
        let user = match acc.auth.session_user(&who.cookie(ADMIN_COOKIE)).await {
            Ok(Some(u)) => u,
            Ok(None) => return see_other("/admin"),
            Err(e) => return failure(e),
        };
        if let Some(left) = acc.lockout.locked(&who.ip) {
            return Accounts::password_page(&acc, &lockout::sentence(left), 429, again);
        }
        let (current, new, confirm) = (f.get("current").cloned().unwrap_or_default(), f.get("new").cloned().unwrap_or_default(), f.get("confirm").cloned().unwrap_or_default());
        if new != confirm {
            return Accounts::password_page(&acc, MISMATCH, 400, again);
        }
        match acc.auth.change_own_password(&user, &current, &new, Some(&who.cookie(ADMIN_COOKIE))).await {
            Ok(()) => {
                acc.auth.record("account.password", "Changed their own password", Record { actor: Some(&user), ip: &who.ip, ..Default::default() }).await;
                see_other(home_for(&user))
            }
            Err(Error::Refused(r)) => {
                if r == Refusal::WrongPassword {
                    acc.fail(&who.ip).await;
                }
                Accounts::password_page(&acc, r.message(), r.status(), again)
            }
            Err(e) => failure(e),
        }
    }

    pub(super) async fn admin_logout(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let token = who.cookie(ADMIN_COOKIE);
        if !token.is_empty() {
            // Ends it server-side, so a copied cookie is dead too. If the database is down the browser still forgets its cookie.
            if let Ok(Some(user)) = acc.auth.session_user(&token).await {
                let _ = acc.auth.end_session(&token).await;
                acc.auth.record("auth.signout", "Signed out", Record { actor: Some(&user), ip: &who.ip, ..Default::default() }).await;
            } else {
                let _ = acc.auth.end_session(&token).await;
            }
        }
        let mut resp = see_other("/admin");
        if let Ok(v) = HeaderValue::from_str(&format!("{ADMIN_COOKIE}=; Max-Age=0; Path=/; SameSite=lax; expires=Thu, 01 Jan 1970 00:00:00 GMT")) {
            resp.headers_mut().append(SET_COOKIE, v);
        }
        resp
    }
}
