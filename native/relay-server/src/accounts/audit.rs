//! The audit log route (`/api/admin/audit`).

use super::request::*;
use super::*;

#[derive(serde::Deserialize, Default)]
struct AuditQuery {
    #[serde(default)]
    who: String,
    #[serde(default)]
    kind: String,
    #[serde(default)]
    q: String,
    #[serde(default)]
    days: i64,
    #[serde(default)]
    before: i64,
}

impl Accounts {
    pub(super) async fn api_audit(State(acc): State<Arc<Accounts>>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let _ = need!(acc, who, Need { perm: Some("audit.view"), ..SIGNED });
        let q: AuditQuery = req.uri().query().and_then(|q| serde_urlencoded::from_str(q).ok()).unwrap_or_default();
        let actor = if q.who == "system" {
            Some(Actor::System)
        } else if q.who.is_ascii() && !q.who.is_empty() && q.who.len() < 10 && q.who.bytes().all(|b| b.is_ascii_digit()) {
            q.who.parse().ok().map(Actor::Id)
        } else {
            None
        };
        let kind: String = q.kind.chars().take(64).collect();
        let text: String = q.q.trim().chars().take(100).collect();
        let search = Search {
            actor,
            kind: Some(kind.as_str()).filter(|k| !k.is_empty()),
            q: Some(text.as_str()).filter(|t| !t.is_empty()),
            days: Some(q.days).filter(|d| (1..=3650).contains(d)),
            before: Some(q.before).filter(|b| *b > 0),
            limit: None,
        };
        let found = match acc.auth.search_audit(search).await {
            Ok(f) => f,
            Err(e) => return failure(e),
        };
        let (people, kinds) = match acc.auth.audit_filters().await {
            Ok(found) => found,
            Err(e) => return failure(e),
        };
        let people: Vec<Value> = people.into_iter().map(|(id, name, username)| json!({"id": id, "name": name, "username": username})).collect();
        Json(json!({"entries": found.entries, "more": found.more, "people": people, "kinds": kinds, "keep_days": relay_auth::KEEP_DAYS})).into_response()
    }
}
