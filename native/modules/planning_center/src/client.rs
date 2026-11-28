//! The one small client every call to Planning Center goes through (`planningcenter.py`'s `Client`).
//!
//! Relay signs in to one church's account with a Personal Access Token (an application ID and a secret). Every call runs as the person who
//! made the token and only within their permissions. The secret is used for the Authorization header and nowhere else: it is not logged, not
//! put in an error's text, and the settings routes withhold it from the browser.
//!
//! Rate limits are read from the response (`X-PCO-API-Request-Rate-*`), never hard-coded. A 429 is waited out for the `Retry-After` it names
//! and retried; if it keeps coming the caller gets [`Kind::Rate`]. Every connection goes out through the `planning_center` traffic class's
//! network port (`Host::net()`), as Python's `netsock` calls did (#348).

use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine;
use bytes::Bytes;
use http_body_util::{BodyExt, Full};
use hyper::Request;
use hyper_util::rt::TokioIo;
use relay_core::future::BoxFuture;
use relay_core::Net;
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, RootCertStore};
use serde_json::{json, Value};
use tokio::io::{AsyncRead, AsyncWrite};
use tokio::sync::mpsc;
use tokio_rustls::TlsConnector;
use url::Url;

use crate::plan::{self, attrs, data, f, find, i, id_of, included, item_time, live as live_doc, parse_time, plan_of, rel_id, rel_ids, s, text};

pub const TRAFFIC: &str = "planning_center";
pub const BASE_URL: &str = "https://api.planningcenteronline.com";
/// Sent as `X-PCO-API-Version` on every Services request.
pub const API_VERSION: &str = "2018-11-01";
/// And this one on Check-Ins requests (parent paging, #220).
pub const CHECK_INS_VERSION: &str = "2025-05-28";
pub const TIMEOUT: Duration = Duration::from_secs(15);
pub const ATTEMPTS: u32 = 3;
pub const MAX_WAIT_S: f64 = 60.0;
pub const DEFAULT_WAIT_S: f64 = 20.0;
pub const PAGE_SIZE: usize = 100;
pub const MAX_PAGES: usize = 20;
pub const PLANS_PER_CHECK: usize = 25;
/// A JSON answer larger than this is not read.
const DOC_LIMIT: usize = 32 * 1024 * 1024;

pub const LIVE_INCLUDE: &str = "current_item_time,next_item_time,controller";
pub const LIVE_ACTIONS: [&str; 3] = ["go_to_next_item", "go_to_previous_item", "toggle_control"];

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    Auth,
    Rate,
    Network,
    Http,
}

/// A call that did not work. The text is for the log; the admin page gets [`PcError::operator_message`].
#[derive(Debug, Clone)]
pub struct PcError {
    pub kind: Kind,
    pub retry_after: Option<f64>,
    pub status: Option<u16>,
    detail: String,
}

impl PcError {
    pub fn new(kind: Kind, detail: impl Into<String>) -> Self {
        Self { kind, retry_after: None, status: None, detail: detail.into() }
    }

    pub fn with_status(mut self, status: u16) -> Self {
        self.status = Some(status);
        self
    }

    /// For the log only: it can carry Planning Center's reply.
    pub fn detail(&self) -> &str {
        &self.detail
    }

    /// A fixed sentence for the admin page, chosen by the error's kind and status.
    pub fn operator_message(&self) -> &'static str {
        match self.kind {
            Kind::Auth if self.status == Some(403) => "Planning Center accepted the token, but the person who made it does not have access to this in Services.",
            Kind::Auth => "Planning Center did not accept that application ID and secret.",
            Kind::Rate => "Planning Center is rate limiting this account. Try again in a minute.",
            Kind::Network => "Could not reach Planning Center. Check this machine's internet connection.",
            Kind::Http if self.status.is_some() => match self.status {
                Some(400) => "Planning Center refused the request (HTTP 400).",
                Some(404) => "Planning Center refused the request (HTTP 404).",
                Some(409) => "Planning Center refused the request (HTTP 409).",
                Some(422) => "Planning Center refused the request (HTTP 422).",
                Some(500) => "Planning Center refused the request (HTTP 500).",
                Some(502) => "Planning Center refused the request (HTTP 502).",
                Some(503) => "Planning Center refused the request (HTTP 503).",
                _ => "Planning Center refused the request.",
            },
            Kind::Http => "Planning Center sent something Relay could not read.",
        }
    }

    /// The HTTP status the admin page's test route answers with (`{"auth": 400, "rate": 429, "network": 502}`, else 502).
    pub fn route_status(&self) -> u16 {
        match self.kind {
            Kind::Auth => 400,
            Kind::Rate => 429,
            _ => 502,
        }
    }
}

impl std::fmt::Display for PcError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.detail)
    }
}

impl std::error::Error for PcError {}

/// What Planning Center said about its limits last: a number or nothing yet.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct Rate {
    pub count: Option<u64>,
    pub limit: Option<u64>,
    pub period: Option<u64>,
}

impl Rate {
    pub fn json(&self) -> Value {
        json!({"count": self.count, "limit": self.limit, "period": self.period})
    }
}

/// Everything a caller can ask of Planning Center, as one value so a test can answer from a table. The answers are JSON shaped as
/// Python's client returned them (lists of dicts), so a fake and the real client are read the same way.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Op {
    ServiceTypes,
    TimeZone,
    /// Plans from the first with a time after this epoch second.
    PlansFrom { st: String, after: i64 },
    Plan { st: String, plan: String },
    Live { st: String, plan: String },
    Me,
    LiveAction { st: String, plan: String, action: &'static str },
    Items { st: String, plan: String },
    PlanAttachments { st: String, plan: String },
    ItemAttachments { st: String, plan: String, item: String },
    TeamMembers { st: String, plan: String },
    NeededPositions { st: String, plan: String },
    Teams { st: String },
}

/// A downloaded file arriving in chunks; the last item is an error when the file is too large or the connection dropped.
pub type Download = mpsc::Receiver<Result<Bytes, PcError>>;

/// What the follower, the LIVE buttons and the signage sync need of Planning Center. The real one is [`Client`].
pub trait Services: Send + Sync + 'static {
    fn call(&self, op: Op) -> BoxFuture<'_, Result<Value, PcError>>;
    /// The last rate-limit reading.
    fn rate(&self) -> Rate;
    /// The bytes of an attachment, never more than `limit`.
    fn download<'a>(&'a self, url: &'a str, limit: u64) -> BoxFuture<'a, Result<Download, PcError>>;
}

/// `Relay/<version> (church caption relay)`: requests without a User-Agent can get 403.
pub fn user_agent() -> String {
    let version: String = std::env::var("RELAY_VERSION").unwrap_or_default().chars().filter(|c| c.is_alphanumeric() || matches!(c, '_' | '.' | '+' | '-')).collect();
    format!("Relay/{} (church caption relay)", if version.is_empty() { "dev" } else { &version })
}

fn leading_number(raw: &str) -> Option<u64> {
    let digits: String = raw.trim_start().chars().take_while(char::is_ascii_digit).collect();
    digits.parse().ok()
}

fn retry_after(headers: &[(String, String)], period: Option<u64>) -> f64 {
    let raw = header(headers, "Retry-After").unwrap_or("").trim();
    let wait = raw.parse::<f64>().ok().filter(|w| w.is_finite()).unwrap_or(period.map_or(DEFAULT_WAIT_S, |p| p as f64));
    wait.clamp(0.0, MAX_WAIT_S)
}

fn header<'a>(headers: &'a [(String, String)], name: &str) -> Option<&'a str> {
    headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
}

/// A Planning Center id goes into a path: digits only, so nothing the browser sends can change the address.
pub fn pid(raw: &str) -> Result<&str, PcError> {
    if plan::is_id(raw) { Ok(raw) } else { Err(PcError::new(Kind::Http, "That is not a Planning Center id.")) }
}

/// How a wait is made. Real time by default; a test hands one that records the wait and returns at once.
pub type Sleeper = Arc<dyn Fn(f64) -> BoxFuture<'static, ()> + Send + Sync>;

fn real_sleep() -> Sleeper {
    Arc::new(|secs| Box::pin(tokio::time::sleep(Duration::from_secs_f64(secs))))
}

fn tls() -> TlsConnector {
    let roots = RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
    let config = ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map(|b| b.with_root_certificates(roots).with_no_client_auth());
    // The ring provider supports the default versions, so this cannot fail; an empty config would refuse every connection.
    TlsConnector::from(Arc::new(config.unwrap_or_else(|_| unreachable!("ring supports the default protocol versions"))))
}

struct Reply {
    status: u16,
    headers: Vec<(String, String)>,
    body: hyper::body::Incoming,
}

pub struct Client {
    app_id: String,
    secret: String,
    base: String,
    net: Net,
    rate: Mutex<Rate>,
    sleep: Sleeper,
}

impl Client {
    pub fn new(net: Net, app_id: &str, secret: &str) -> Self {
        Self { app_id: app_id.to_owned(), secret: secret.to_owned(), base: BASE_URL.to_owned(), net, rate: Mutex::new(Rate::default()), sleep: real_sleep() }
    }

    /// Talk to `base` (no trailing slash needed) instead of Planning Center: a test's server.
    pub fn with_base(mut self, base: &str) -> Self {
        self.base = base.trim_end_matches('/').to_owned();
        self
    }

    pub fn with_sleeper(mut self, sleep: Sleeper) -> Self {
        self.sleep = sleep;
        self
    }

    /// The saved token, or `None` while the integration is off.
    pub fn from_settings(net: &Net, settings: &Value) -> Option<Self> {
        let (id, secret) = (s(settings, "app_id"), s(settings, "secret"));
        (!id.is_empty() && !secret.is_empty()).then(|| Self::new(net.clone(), &id, &secret))
    }

    pub fn rate(&self) -> Rate {
        self.rate.lock().map(|r| *r).unwrap_or_default()
    }

    fn set_rate(&self, update: impl FnOnce(&mut Rate)) {
        if let Ok(mut r) = self.rate.lock() {
            update(&mut r);
        }
    }

    fn headers(&self, path: &str) -> Vec<(String, String)> {
        let token = base64::engine::general_purpose::STANDARD.encode(format!("{}:{}", self.app_id, self.secret));
        vec![
            ("Authorization".into(), format!("Basic {token}")),
            ("X-PCO-API-Version".into(), if path.starts_with("/check-ins/") { CHECK_INS_VERSION } else { API_VERSION }.into()),
            ("User-Agent".into(), user_agent()),
            ("Accept".into(), "application/json".into()),
        ]
    }

    fn note_rate(&self, headers: &[(String, String)]) {
        let read = |name: &str| header(headers, &format!("X-PCO-API-Request-Rate-{name}")).and_then(leading_number);
        let (count, limit, period) = (read("Count"), read("Limit"), read("Period"));
        self.set_rate(|r| {
            r.count = count.or(r.count);
            r.limit = limit.or(r.limit);
            r.period = period.or(r.period);
        });
    }

    /// The text with the secret taken out, for a log line.
    fn clean(&self, text: &str) -> String {
        if self.secret.is_empty() { text.to_owned() } else { text.replace(&self.secret, "…") }
    }

    /// One request, headers and body not yet read. A refusal to connect or a timeout is [`Kind::Network`].
    async fn send(&self, method: &str, url: &Url, headers: Vec<(String, String)>, body: Option<Vec<u8>>) -> Result<Reply, PcError> {
        let net_err = |why: &str| PcError::new(Kind::Network, format!("Could not reach Planning Center: {why}"));
        let host = url.host_str().ok_or_else(|| net_err("no host"))?;
        let https = url.scheme() == "https";
        let port = url.port_or_known_default().ok_or_else(|| net_err("no port"))?;
        let work = async {
            let tcp = self.net.connect_tcp(TRAFFIC, host.trim_start_matches('[').trim_end_matches(']'), port, TIMEOUT).await.map_err(|e| e.message)?;
            let authority = match url.port() {
                Some(p) => format!("{host}:{p}"),
                None => host.to_owned(),
            };
            let target = match url.query() {
                Some(q) => format!("{}?{q}", url.path()),
                None => url.path().to_owned(),
            };
            if https {
                let name = ServerName::try_from(host.to_owned()).map_err(|_| "bad name".to_owned())?;
                let stream = tls().connect(name, tcp).await.map_err(|_| "TLS failed".to_owned())?;
                exchange(stream, method, &authority, &target, headers, body).await
            } else {
                exchange(tcp, method, &authority, &target, headers, body).await
            }
        };
        match tokio::time::timeout(TIMEOUT, work).await {
            Ok(r) => r.map_err(|e| net_err(&e)),
            Err(_) => Err(net_err("timed out")),
        }
    }

    async fn request(&self, method: &str, path: &str, params: &[(&str, String)]) -> Result<Value, PcError> {
        let mut url = Url::parse(&format!("{}{path}", self.base)).map_err(|_| PcError::new(Kind::Http, "That is not a Planning Center address."))?;
        if !params.is_empty() {
            url.query_pairs_mut().extend_pairs(params.iter().map(|(k, v)| (*k, v.as_str())));
        }
        for attempt in 1..=ATTEMPTS {
            // Already at the ceiling for this window: wait for it to roll over rather than spend a request on a certain 429.
            let r = self.rate();
            if let (Some(count), Some(limit)) = (r.count, r.limit) {
                if count >= limit {
                    (self.sleep)(r.period.map_or(DEFAULT_WAIT_S, |p| p as f64).clamp(0.0, MAX_WAIT_S)).await;
                    self.set_rate(|r| r.count = None);
                }
            }
            let body = (method == "POST").then(Vec::new);
            let reply = self.send(method, &url, self.headers(path), body).await?;
            self.note_rate(&reply.headers);
            let status = reply.status;
            if status == 429 {
                let wait = retry_after(&reply.headers, self.rate().period);
                if attempt < ATTEMPTS {
                    tracing::info!("Planning Center is rate limiting; waiting {wait:.0} s");
                    (self.sleep)(wait).await;
                    self.set_rate(|r| r.count = None);
                    continue;
                }
                let mut e = PcError::new(Kind::Rate, format!("Planning Center is rate limiting this account. Try again in {wait:.0} seconds."));
                e.retry_after = Some(wait);
                return Err(e);
            }
            let bytes = read_body(reply.body, DOC_LIMIT).await.unwrap_or_default();
            return match status {
                200..=299 => serde_json::from_slice(&bytes).map_err(|_| PcError::new(Kind::Http, "Planning Center sent something that was not JSON.")),
                401 => Err(PcError::new(Kind::Auth, "Planning Center did not accept that application ID and secret.").with_status(401)),
                403 => Err(PcError::new(Kind::Auth, "Planning Center accepted the token, but the person who made it does not have access to this in Services.").with_status(403)),
                code => {
                    let reply: String = String::from_utf8_lossy(&bytes).chars().take(200).collect();
                    Err(PcError::new(Kind::Http, format!("Planning Center answered HTTP {code}: {}", self.clean(&reply))).with_status(code))
                }
            };
        }
        Err(PcError::new(Kind::Rate, "Planning Center is rate limiting this account."))
    }

    pub async fn get(&self, path: &str, params: &[(&str, String)]) -> Result<Value, PcError> {
        self.request("GET", path, params).await
    }

    /// An action with no body (the LIVE buttons). A 429 is retried, since Planning Center did not carry it out; a dropped connection is not,
    /// since it may have.
    pub async fn post(&self, path: &str, params: &[(&str, String)]) -> Result<Value, PcError> {
        self.request("POST", path, params).await
    }

    // -- calls ----------------------------------------------------------------------------------------------------------------------
    /// Every service type on the account as `{"id", "name"}`, by name. Archived and deleted ones are left out: there is nothing live to follow in them.
    pub async fn service_types(&self) -> Result<Value, PcError> {
        let mut out: Vec<Value> = Vec::new();
        let mut offset = 0usize;
        for _ in 0..MAX_PAGES {
            let page = self.get("/services/v2/service_types", &[("per_page", PAGE_SIZE.to_string()), ("offset", offset.to_string())]).await?;
            let rows = data(&page);
            for row in rows {
                let a = attrs(Some(row));
                let name = text(&a["name"]).trim().to_owned();
                if truthy(&a["archived_at"]) || truthy(&a["deleted_at"]) {
                    continue;
                }
                if row.get("id").is_some_and(|v| !v.is_null()) {
                    out.push(json!({"id": id_of(row), "name": if name.is_empty() { format!("Service type {}", id_of(row)) } else { name }}));
                }
            }
            // A short page is the last one. per_page tops out at 100.
            if rows.len() < PAGE_SIZE {
                break;
            }
            offset += PAGE_SIZE;
        }
        out.sort_by_key(|t| s(t, "name").to_lowercase());
        Ok(Value::Array(out))
    }

    /// The organisation's time zone as Planning Center names it (`""` if absent).
    pub async fn time_zone(&self) -> Result<Value, PcError> {
        let doc = self.get("/services/v2", &[]).await?;
        Ok(json!(text(&attrs(doc.get("data"))["time_zone"]).trim()))
    }

    /// The service type's next plans in date order, each with its times, starting from the first plan with a time after `after`.
    pub async fn plans_from(&self, st: &str, after: i64) -> Result<Value, PcError> {
        let st = pid(st)?;
        let base = format!("/services/v2/service_types/{st}/plans");
        let stamp = jiff::Timestamp::from_second(after).map_or_else(|_| "1970-01-01T00:00:00Z".to_owned(), |t| t.strftime("%Y-%m-%dT%H:%M:%SZ").to_string());
        let doc = self
            .get(&base, &[("filter", "after".into()), ("after", stamp), ("order", "sort_date".into()), ("include", "plan_times".into()), ("per_page", PLANS_PER_CHECK.to_string())])
            .await?;
        let inc = included(&doc);
        let rows = data(&doc);
        // The docs do not list plan_times among a Plan's relationships. If the include comes back unlinked, ask each plan near the day for its own.
        let linked = rows.iter().any(|r| r.get("relationships").and_then(|x| x.get("plan_times")).is_some());
        let mut out = Vec::new();
        for row in rows {
            let times: Vec<Value> = if linked {
                rel_ids(row, "plan_times").iter().filter_map(|i| find(&inc, "PlanTime", Some(i)).cloned()).collect()
            } else {
                let sort = parse_time(&attrs(Some(row))["sort_date"]);
                let near = sort.is_some_and(|t| (t - after as f64).abs() < 3.0 * 86400.0);
                if near { data(&self.get(&format!("{base}/{}/plan_times", id_of(row)), &[("per_page", PAGE_SIZE.to_string())]).await?).to_vec() } else { Vec::new() }
            };
            out.push(plan_of(row, &times.iter().collect::<Vec<_>>()));
        }
        Ok(Value::Array(out))
    }

    /// One plan with its times: for the service's `live_starts_at` once it is running.
    pub async fn plan(&self, st: &str, plan_id: &str) -> Result<Value, PcError> {
        let (st, plan_id) = (pid(st)?, pid(plan_id)?);
        let doc = self.get(&format!("/services/v2/service_types/{st}/plans/{plan_id}"), &[("include", "plan_times".into())]).await?;
        let times: Vec<&Value> = doc.get("included").and_then(Value::as_array).into_iter().flatten().filter(|r| r["type"] == "PlanTime").collect();
        Ok(plan_of(doc.get("data").filter(|d| d.is_object()).unwrap_or(&json!({})), &times))
    }

    /// The plan's LIVE state. Nobody controlling, or LIVE never opened for the plan, is an ordinary answer (controller `null`), not an error.
    pub async fn live(&self, st: &str, plan_id: &str) -> Result<Value, PcError> {
        let (st, plan_id) = (pid(st)?, pid(plan_id)?);
        match self.get(&format!("/services/v2/service_types/{st}/plans/{plan_id}/live"), &[("include", LIVE_INCLUDE.into())]).await {
            Ok(doc) => Ok(live_doc(&doc)),
            Err(e) if e.status == Some(404) => Ok(json!({"id": null, "controller": null, "current": null, "next": null})),
            Err(e) => Err(e),
        }
    }

    /// The Planning Center person id of whoever made the token: LIVE's controller is compared with it, because `toggle_control` releases
    /// control when that person already has it.
    pub async fn me(&self) -> Result<Value, PcError> {
        let doc = self.get("/services/v2/me", &[]).await?;
        Ok(json!(doc.get("data").filter(|d| !d.is_null()).map(id_of).unwrap_or_default()))
    }

    /// Press a LIVE button as the token's owner: `go_to_next_item`, `go_to_previous_item` or `toggle_control`. The path has no LIVE id.
    /// Planning Center answers 201 with the new LIVE state. Next and Previous answer 403 unless the token's owner has control.
    pub async fn live_action(&self, st: &str, plan_id: &str, action: &str) -> Result<Value, PcError> {
        if !LIVE_ACTIONS.contains(&action) {
            return Err(PcError::new(Kind::Http, "That is not a LIVE action."));
        }
        let (st, plan_id) = (pid(st)?, pid(plan_id)?);
        Ok(live_doc(&self.post(&format!("/services/v2/service_types/{st}/plans/{plan_id}/live/{action}"), &[("include", LIVE_INCLUDE.into())]).await?))
    }

    /// The plan's items in order, with key, arrangement, notes and per-service times.
    pub async fn items(&self, st: &str, plan_id: &str) -> Result<Value, PcError> {
        let (st, plan_id) = (pid(st)?, pid(plan_id)?);
        let mut out: Vec<Value> = Vec::new();
        let mut offset = 0usize;
        for _ in 0..MAX_PAGES {
            let doc = self
                .get(
                    &format!("/services/v2/service_types/{st}/plans/{plan_id}/items"),
                    &[("include", "item_times,key,arrangement,item_notes".into()), ("per_page", PAGE_SIZE.to_string()), ("offset", offset.to_string())],
                )
                .await?;
            let rows = data(&doc);
            let inc = included(&doc);
            for row in rows {
                let a = attrs(Some(row));
                let iid = id_of(row);
                let arr = attrs(find(&inc, "Arrangement", rel_id(row, "arrangement").as_deref()));
                let key = attrs(find(&inc, "Key", rel_id(row, "key").as_deref()));
                let mine = |kind: &str| -> Vec<&Value> { inc.iter().filter(|((k, _), r)| k == kind && rel_id(r, "item").as_deref() == Some(iid.as_str())).map(|(_, r)| *r).collect() };
                let key_name = [text(&a["key_name"]), text(&key["name"])].into_iter().find(|t| !t.is_empty()).unwrap_or_default();
                let seq_src = [&a["custom_arrangement_sequence"], &arr["sequence"]].into_iter().find(|v| v.as_array().is_some_and(|x| !x.is_empty())).cloned().unwrap_or(json!([]));
                out.push(json!({
                    "id": iid,
                    "title": text(&a["title"]),
                    "type": or_text(text(&a["item_type"]), "item"),
                    "length": i(&a, "length"),
                    "position": or_text(text(&a["service_position"]), "during"),
                    "sequence": i(&a, "sequence"),
                    "key": key_name,
                    "arrangement": text(&arr["name"]),
                    "arrangement_sequence": seq_src.as_array().map(|x| x.iter().map(|v| json!(text(v))).collect::<Vec<_>>()).unwrap_or_default(),
                    "notes": mine("ItemNote").iter().map(|n| { let na = attrs(Some(n)); json!({"category": text(&na["category_name"]), "content": text(&na["content"])}) }).collect::<Vec<_>>(),
                    "times": mine("ItemTime").iter().map(|t| item_time(Some(t))).collect::<Vec<_>>(),
                }));
            }
            if rows.len() < PAGE_SIZE {
                break;
            }
            offset += PAGE_SIZE;
        }
        out.sort_by_key(|x| i(x, "sequence"));
        Ok(Value::Array(out))
    }

    async fn attachments(&self, path: &str) -> Result<Value, PcError> {
        let mut out: Vec<Value> = Vec::new();
        let mut offset = 0usize;
        for _ in 0..MAX_PAGES {
            let doc = self.get(path, &[("per_page", PAGE_SIZE.to_string()), ("offset", offset.to_string())]).await?;
            let rows = data(&doc);
            for row in rows {
                let a = attrs(Some(row));
                out.push(json!({
                    "id": id_of(row),
                    "name": or_text(text(&a["display_name"]), &text(&a["filename"])),
                    "filename": text(&a["filename"]),
                    "content_type": text(&a["content_type"]).to_lowercase(),
                    "size": i(&a, "file_size"),
                    "url": text(&a["url"]),
                    "linked": truthy(&a["linked_url"]),
                    "updated_at": text(&a["updated_at"]),
                }));
            }
            if rows.len() < PAGE_SIZE {
                break;
            }
            offset += PAGE_SIZE;
        }
        Ok(Value::Array(out))
    }

    /// Who is scheduled on the plan (#148): one row per person per position, with the team, the answer (confirmed, pending, declined), the
    /// thumbnail Planning Center hosts, and the plan times they serve (`null` when Planning Center does not say, which means every time).
    pub async fn team_members(&self, st: &str, plan_id: &str) -> Result<Value, PcError> {
        let (st, plan_id) = (pid(st)?, pid(plan_id)?);
        let mut out: Vec<Value> = Vec::new();
        let mut offset = 0usize;
        for _ in 0..MAX_PAGES {
            let doc = self
                .get(&format!("/services/v2/service_types/{st}/plans/{plan_id}/team_members"), &[("include", "team".into()), ("per_page", PAGE_SIZE.to_string()), ("offset", offset.to_string())])
                .await?;
            let rows = data(&doc);
            let inc = included(&doc);
            for row in rows {
                let a = attrs(Some(row));
                let team = attrs(find(&inc, "Team", rel_id(row, "team").as_deref()));
                let times = if plan::has_rel_data(row, "times") { json!(rel_ids(row, "times")) } else { Value::Null };
                let photo = text(&a["photo_thumbnail"]);
                let name = text(&a["name"]).trim().to_owned();
                out.push(json!({
                    "id": id_of(row),
                    "person": rel_id(row, "person").unwrap_or_default(),
                    "name": if name.is_empty() { "Someone".to_owned() } else { name },
                    "status": member_status(&text(&a["status"])),
                    "position": text(&a["team_position_name"]).trim(),
                    "team": text(&team["name"]).trim(),
                    "team_id": rel_id(row, "team").unwrap_or_default(),
                    // Only a picture Planning Center hosts, over https: the screen shows it as an <img>.
                    "photo": if photo.starts_with("https://") { photo } else { String::new() },
                    "times": times,
                }));
            }
            if rows.len() < PAGE_SIZE {
                break;
            }
            offset += PAGE_SIZE;
        }
        Ok(Value::Array(out))
    }

    /// Positions still to fill on the plan (#148): team, position, how many, and the plan time (`null`: all).
    pub async fn needed_positions(&self, st: &str, plan_id: &str) -> Result<Value, PcError> {
        let (st, plan_id) = (pid(st)?, pid(plan_id)?);
        let doc = self.get(&format!("/services/v2/service_types/{st}/plans/{plan_id}/needed_positions"), &[("include", "team".into()), ("per_page", PAGE_SIZE.to_string())]).await?;
        let inc = included(&doc);
        Ok(Value::Array(
            data(&doc)
                .iter()
                .map(|row| {
                    let a = attrs(Some(row));
                    let team = attrs(find(&inc, "Team", rel_id(row, "team").as_deref()));
                    json!({"team": text(&team["name"]).trim(), "team_id": rel_id(row, "team").unwrap_or_default(),
                           "position": text(&a["team_position_name"]).trim(), "quantity": quantity(&a),
                           "time": rel_id(row, "time")})
                })
                .collect(),
        ))
    }

    /// Every team of the service type with its positions, by Planning Center's ids (#525).
    pub async fn teams(&self, st: &str) -> Result<Value, PcError> {
        let st = pid(st)?;
        let mut out: Vec<Value> = Vec::new();
        let mut offset = 0usize;
        for _ in 0..MAX_PAGES {
            let doc = self
                .get(&format!("/services/v2/service_types/{st}/teams"), &[("include", "team_positions".into()), ("per_page", PAGE_SIZE.to_string()), ("offset", offset.to_string())])
                .await?;
            let rows = data(&doc);
            let inc = included(&doc);
            for row in rows {
                let mut positions = Vec::new();
                for p in rel_ids(row, "team_positions") {
                    let pos = attrs(find(&inc, "TeamPosition", Some(&p)));
                    let name = text(&pos["name"]).trim().to_owned();
                    if !name.is_empty() {
                        positions.push(json!({"id": p, "name": name}));
                    }
                }
                let name = text(&attrs(Some(row))["name"]).trim().to_owned();
                if row.get("id").is_some_and(|v| !v.is_null()) && !name.is_empty() {
                    out.push(json!({"id": id_of(row), "name": name, "positions": positions}));
                }
            }
            if rows.len() < PAGE_SIZE {
                break;
            }
            offset += PAGE_SIZE;
        }
        Ok(Value::Array(out))
    }

    /// The bytes of an attachment, in chunks, never more than `limit`. The address comes from Planning Center's own answer, so only https to
    /// a named host is followed; the token goes only to Planning Center's hosts and is dropped when a redirect leaves them (the file itself is
    /// usually on a signed storage address).
    pub async fn stream_file(&self, url: &str, limit: u64) -> Result<Download, PcError> {
        let mut url = url.to_owned();
        for _ in 0..5 {
            let parsed = Url::parse(&url).ok();
            let refuse = || PcError::new(Kind::Http, "Planning Center gave an address Relay will not fetch.");
            let parsed = parsed.ok_or_else(refuse)?;
            let host = parsed.host_str().unwrap_or("").to_lowercase();
            let literal = host.is_empty() || host == "localhost" || host.starts_with('[') || host.bytes().all(|b| b.is_ascii_digit() || b == b'.' || b == b':');
            let plain_ok = self.allows_plain(&parsed);
            if !plain_ok && (parsed.scheme() != "https" || literal) {
                return Err(refuse());
            }
            let ours = host == "planningcenteronline.com" || host.ends_with(".planningcenteronline.com") || plain_ok;
            let mut headers = if ours { self.headers("") } else { vec![("User-Agent".into(), user_agent())] };
            headers.retain(|(k, _)| !k.eq_ignore_ascii_case("accept"));
            headers.push(("Accept".into(), "*/*".into()));
            let reply = self.send("GET", &parsed, headers, None).await.map_err(|e| if e.kind == Kind::Network { PcError::new(Kind::Network, "Could not download a file from Planning Center.") } else { e })?;
            if matches!(reply.status, 301 | 302 | 303 | 307 | 308) {
                if let Some(to) = header(&reply.headers, "location") {
                    url = parsed.join(to).map_err(|_| refuse())?.to_string();
                    continue;
                }
            }
            if !(200..300).contains(&reply.status) {
                return Err(PcError::new(Kind::Http, format!("Planning Center answered HTTP {} for a file.", reply.status)).with_status(reply.status));
            }
            let (tx, rx) = mpsc::channel(8);
            let mut body = reply.body;
            tokio::spawn(async move {
                let mut sent = 0u64;
                while let Some(frame) = body.frame().await {
                    let Ok(frame) = frame else {
                        let _ = tx.send(Err(PcError::new(Kind::Network, "Could not download a file from Planning Center."))).await;
                        return;
                    };
                    let Some(chunk) = frame.data_ref() else { continue };
                    sent += chunk.len() as u64;
                    if sent > limit {
                        let _ = tx.send(Err(PcError::new(Kind::Http, "A Planning Center file is larger than signage keeps."))).await;
                        return;
                    }
                    if tx.send(Ok(chunk.clone())).await.is_err() {
                        return;
                    }
                }
            });
            return Ok(rx);
        }
        Err(PcError::new(Kind::Http, "Planning Center sent a file address that kept redirecting."))
    }

    /// A client built for a test's server (`with_base`) may fetch files from that same plain address; the real one never does.
    fn allows_plain(&self, url: &Url) -> bool {
        Url::parse(&self.base).is_ok_and(|b| b.scheme() == "http" && b.host_str() == url.host_str() && b.port_or_known_default() == url.port_or_known_default())
    }
}

/// `text`, or `fallback` when it is empty (Python's `a or b`).
fn or_text(text: String, fallback: &str) -> String {
    if text.is_empty() { fallback.to_owned() } else { text }
}

/// `max(1, min(20, int(quantity or 1)))`.
fn quantity(a: &Value) -> i64 {
    let q = i(a, "quantity");
    (if q == 0 { 1 } else { q }).clamp(1, 20)
}

fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::String(t) => !t.is_empty(),
        Value::Number(n) => n.as_f64().is_some_and(|x| x != 0.0),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// PlanPerson.status is C, U or D (or spelled out: Confirmed, Unconfirmed, Declined).
fn member_status(raw: &str) -> &'static str {
    match raw.chars().next().map(|c| c.to_ascii_uppercase()) {
        Some('C') => "confirmed",
        Some('D') => "declined",
        _ => "pending",
    }
}

async fn exchange<S>(io: S, method: &str, authority: &str, target: &str, headers: Vec<(String, String)>, body: Option<Vec<u8>>) -> Result<Reply, String>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let mut builder = Request::builder().method(method).uri(target).header("Host", authority).header("Connection", "close");
    for (k, v) in &headers {
        builder = builder.header(k, v);
    }
    let bytes = Bytes::from(body.unwrap_or_default());
    if method == "POST" {
        builder = builder.header("Content-Length", bytes.len());
    }
    let request = builder.body(Full::new(bytes)).map_err(|_| "bad request".to_owned())?;
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(io)).await.map_err(|e| e.to_string())?;
    tokio::spawn(connection);
    let response = sender.send_request(request).await.map_err(|e| e.to_string())?;
    Ok(reply_of(response))
}

fn reply_of(response: hyper::Response<hyper::body::Incoming>) -> Reply {
    let status = response.status().as_u16();
    let headers = response.headers().iter().map(|(k, v)| (k.as_str().to_owned(), String::from_utf8_lossy(v.as_bytes()).into_owned())).collect();
    Reply { status, headers, body: response.into_body() }
}

async fn read_body(mut body: hyper::body::Incoming, limit: usize) -> Option<Vec<u8>> {
    let mut out = Vec::new();
    while let Some(frame) = body.frame().await {
        let frame = frame.ok()?;
        if let Some(d) = frame.data_ref() {
            out.extend_from_slice(d);
            if out.len() > limit {
                return None;
            }
        }
    }
    Some(out)
}

impl Services for Client {
    fn call(&self, op: Op) -> BoxFuture<'_, Result<Value, PcError>> {
        Box::pin(async move {
            match op {
                Op::ServiceTypes => self.service_types().await,
                Op::TimeZone => self.time_zone().await,
                Op::PlansFrom { st, after } => self.plans_from(&st, after).await,
                Op::Plan { st, plan } => self.plan(&st, &plan).await,
                Op::Live { st, plan } => self.live(&st, &plan).await,
                Op::Me => self.me().await,
                Op::LiveAction { st, plan, action } => self.live_action(&st, &plan, action).await,
                Op::Items { st, plan } => self.items(&st, &plan).await,
                Op::PlanAttachments { st, plan } => self.attachments(&format!("/services/v2/service_types/{}/plans/{}/attachments", pid(&st)?, pid(&plan)?)).await,
                Op::ItemAttachments { st, plan, item } => self.attachments(&format!("/services/v2/service_types/{}/plans/{}/items/{}/attachments", pid(&st)?, pid(&plan)?, pid(&item)?)).await,
                Op::TeamMembers { st, plan } => self.team_members(&st, &plan).await,
                Op::NeededPositions { st, plan } => self.needed_positions(&st, &plan).await,
                Op::Teams { st } => self.teams(&st).await,
            }
        })
    }

    fn rate(&self) -> Rate {
        Client::rate(self)
    }

    fn download<'a>(&'a self, url: &'a str, limit: u64) -> BoxFuture<'a, Result<Download, PcError>> {
        Box::pin(self.stream_file(url, limit))
    }
}

/// `f(v, key)` as a whole number of seconds.
pub fn secs(v: &Value, key: &str) -> i64 {
    f(v, key).map_or(0, |x| x as i64)
}
