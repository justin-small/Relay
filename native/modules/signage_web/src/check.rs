//! Checks the admin asks for (never at play time): does a site allow being framed, does YouTube allow a video to be embedded.
//! `app/modules/signage_web/links.py`, `check_*` and `_fetch`.
//!
//! A saved address is fetched from this machine, so it must not be a way to probe the network Relay runs on: every redirect hop goes only to
//! a host whose addresses are all public, and the connection is made to the address that was checked. It leaves through the `signage_web`
//! traffic class's network port, as Python's does (#348).

use std::net::{IpAddr, Ipv4Addr, Ipv6Addr};
use std::sync::Arc;
use std::time::Duration;

use bytes::Bytes;
use http_body_util::{BodyExt, Empty};
use hyper::{Method, Request};
use hyper_util::rt::TokioIo;
use relay_core::future::BoxFuture;
use relay_core::Net;
use rustls::pki_types::ServerName;
use rustls::{ClientConfig, RootCertStore};
use serde_json::Value;
use tokio::io::{AsyncRead, AsyncWrite};
use tokio_rustls::TlsConnector;
use url::Url;

use crate::links::frame_verdict;

/// The checks go out through this class's port.
pub const TRAFFIC: &str = "signage_web";
const TIMEOUT: Duration = Duration::from_secs(6);
const HOPS: usize = 3;
const BODY_LIMIT: usize = 200_000;

/// One answer: status, headers (names as sent) and at most [`BODY_LIMIT`] bytes of the body.
#[derive(Debug, Clone)]
pub struct Reply {
    pub status: u16,
    pub headers: Vec<(String, String)>,
    pub body: Bytes,
}

impl Reply {
    pub fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FetchError {
    /// The address (or a redirect's) is not a public one, so it is not fetched.
    Private,
    /// Anything else: no route, no answer, a bad certificate, too many redirects.
    Unreachable,
}

/// Where the checks get their answers. The server's is [`Web`]; a test hands its own.
pub trait Fetch: Send + Sync + 'static {
    fn get<'a>(&'a self, url: &'a str) -> BoxFuture<'a, Result<Reply, FetchError>>;
}

/// True when `ip` is a public address: not private, loopback, link-local, shared (carrier-grade NAT), documentation, benchmarking,
/// reserved or multicast (what Python's `ipaddress` calls "global").
pub fn is_public(ip: IpAddr) -> bool {
    match ip {
        IpAddr::V4(v4) => public_v4(v4),
        IpAddr::V6(v6) => match v6.to_ipv4_mapped() {
            Some(v4) => public_v4(v4),
            None => public_v6(v6),
        },
    }
}

fn public_v4(ip: Ipv4Addr) -> bool {
    let [a, b, c, _] = ip.octets();
    !(a == 0
        || a == 10
        || a == 127
        || (a == 100 && (64..128).contains(&b))
        || (a == 169 && b == 254)
        || (a == 172 && (16..32).contains(&b))
        || (a == 192 && b == 0 && c == 0)
        || (a == 192 && b == 0 && c == 2)
        || (a == 192 && b == 168)
        || (a == 198 && (b == 18 || b == 19))
        || (a == 198 && b == 51 && c == 100)
        || (a == 203 && b == 0 && c == 113)
        || a >= 224)
}

fn public_v6(ip: Ipv6Addr) -> bool {
    let s = ip.segments();
    !(ip.is_unspecified()
        || ip.is_loopback()
        || ip.is_multicast()
        || (s[0] & 0xfe00) == 0xfc00
        || (s[0] & 0xffc0) == 0xfe80
        || (s[0] == 0x2001 && s[1] == 0x0db8)
        || (s[0] == 0x0100 && s[1] == 0 && s[2] == 0 && s[3] == 0))
}

/// The Cloudflare and Google certificates the sites serve are checked against the bundled roots, as Python checks against certifi: the
/// frozen app has no system store it can rely on everywhere.
fn tls() -> TlsConnector {
    let roots = RootCertStore { roots: webpki_roots::TLS_SERVER_ROOTS.to_vec() };
    let config = ClientConfig::builder_with_provider(Arc::new(rustls::crypto::ring::default_provider()))
        .with_safe_default_protocol_versions()
        .map(|b| b.with_root_certificates(roots).with_no_client_auth());
    // The ring provider supports the default versions, so this cannot fail; an empty config would refuse every connection.
    TlsConnector::from(Arc::new(config.unwrap_or_else(|_| unreachable!("ring supports the default protocol versions"))))
}

/// One GET over an open stream: the status, the headers and the start of the body.
pub async fn exchange<S>(io: S, host: &str, target: &str) -> Option<Reply>
where
    S: AsyncRead + AsyncWrite + Unpin + Send + 'static,
{
    let request = Request::builder()
        .method(Method::GET)
        .uri(target)
        .header("Host", host)
        .header("User-Agent", "Relay-signage-check")
        .header("Accept", "*/*")
        .header("Connection", "close")
        .body(Empty::<Bytes>::new())
        .ok()?;
    let (mut sender, connection) = hyper::client::conn::http1::handshake(TokioIo::new(io)).await.ok()?;
    let driver = tokio::spawn(connection);
    let response = sender.send_request(request).await.ok()?;
    let status = response.status().as_u16();
    let headers = response.headers().iter().map(|(k, v)| (k.as_str().to_owned(), String::from_utf8_lossy(v.as_bytes()).into_owned())).collect();
    let mut body = response.into_body();
    let mut out: Vec<u8> = Vec::new();
    while let Some(frame) = body.frame().await {
        let Ok(frame) = frame else { break };
        if let Some(d) = frame.data_ref() {
            out.extend_from_slice(d);
            if out.len() >= BODY_LIMIT {
                out.truncate(BODY_LIMIT);
                break;
            }
        }
    }
    driver.abort();
    Some(Reply { status, headers, body: Bytes::from(out) })
}

/// The real fetcher: public hosts only, through the routed network.
pub struct Web {
    net: Net,
}

impl Web {
    pub fn new(net: Net) -> Self {
        Self { net }
    }

    async fn once(&self, url: &Url) -> Result<Reply, FetchError> {
        let host = url.host_str().ok_or(FetchError::Private)?;
        let https = url.scheme() == "https";
        let port = url.port_or_known_default().ok_or(FetchError::Private)?;
        let found = relay_core::net::resolve_all(host.trim_start_matches('[').trim_end_matches(']'), port).await.map_err(|_| FetchError::Private)?;
        if found.is_empty() || !found.iter().all(|a| is_public(*a)) {
            return Err(FetchError::Private);
        }
        // Relay's networks are IPv4: connect to the first IPv4 address that was just checked, not to a second lookup of the name.
        let ip = found.iter().find(|a| a.is_ipv4()).ok_or(FetchError::Unreachable)?;
        let tcp = self.net.connect_tcp(TRAFFIC, &ip.to_string(), port, TIMEOUT).await.map_err(|_| FetchError::Unreachable)?;
        let authority = match url.port() {
            Some(p) => format!("{host}:{p}"),
            None => host.to_owned(),
        };
        let target = match url.query() {
            Some(q) => format!("{}?{q}", url.path()),
            None => url.path().to_owned(),
        };
        let answered = if https {
            let name = ServerName::try_from(host.to_owned()).map_err(|_| FetchError::Unreachable)?;
            let stream = tls().connect(name, tcp).await.map_err(|_| FetchError::Unreachable)?;
            exchange(stream, &authority, &target).await
        } else {
            exchange(tcp, &authority, &target).await
        };
        answered.ok_or(FetchError::Unreachable)
    }
}

impl Fetch for Web {
    /// GET `url`, following up to three redirects, each to a public host.
    fn get<'a>(&'a self, url: &'a str) -> BoxFuture<'a, Result<Reply, FetchError>> {
        Box::pin(async move {
            let mut url = Url::parse(url).map_err(|_| FetchError::Private)?;
            for _ in 0..=HOPS {
                if !matches!(url.scheme(), "http" | "https") {
                    return Err(FetchError::Private);
                }
                let reply = tokio::time::timeout(TIMEOUT, self.once(&url)).await.map_err(|_| FetchError::Unreachable)??;
                match (reply.status, reply.header("location")) {
                    (301 | 302 | 303 | 307 | 308, Some(to)) => url = url.join(to).map_err(|_| FetchError::Unreachable)?,
                    _ => return Ok(reply),
                }
            }
            Err(FetchError::Unreachable)
        })
    }
}

/// What a check found: `state` is one of ok, blocked, unsure, gone, offline; `title` is YouTube's name for the video, when it gave one.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Verdict {
    pub state: &'static str,
    pub note: String,
    pub title: String,
}

fn verdict(state: &'static str, note: impl Into<String>) -> Verdict {
    Verdict { state, note: note.into(), title: String::new() }
}

pub async fn check_web(fetch: &dyn Fetch, url: &str) -> Verdict {
    match fetch.get(url).await {
        Ok(reply) if reply.status < 300 => {
            let (state, note) = frame_verdict(&reply.headers);
            verdict(state, note)
        }
        // A site that answers with an error can still allow framing.
        Ok(reply) => {
            let (state, note) = frame_verdict(&reply.headers);
            if state == "ok" { verdict(state, format!("The site answered with an error ({}), but allows framing.", reply.status)) } else { verdict(state, note) }
        }
        Err(FetchError::Private) => verdict("unsure", "This address is on a private network, so Relay doesn't check it. Add it to a playlist and look at a screen."),
        Err(FetchError::Unreachable) => verdict("offline", "Relay couldn't reach this address to check it."),
    }
}

/// oEmbed answers 200 for a public embeddable video or playlist, 401/403 when embedding is off, 404 if gone.
pub async fn check_youtube(fetch: &dyn Fetch, url: &str) -> Verdict {
    let encoded: String = url.bytes().map(|b| if b.is_ascii_alphanumeric() || matches!(b, b'-' | b'_' | b'.' | b'~') { (b as char).to_string() } else { format!("%{b:02X}") }).collect();
    let api = format!("https://www.youtube.com/oembed?format=json&url={encoded}");
    let offline = || verdict("offline", "Relay couldn't reach YouTube to check this.");
    match fetch.get(&api).await {
        Ok(reply) if reply.status < 300 => match serde_json::from_slice::<Value>(&reply.body) {
            Ok(data) => Verdict { state: "ok", note: "YouTube allows this to be embedded.".into(), title: data["title"].as_str().unwrap_or("").to_owned() },
            Err(_) => offline(),
        },
        Ok(reply) => match reply.status {
            401 | 403 => verdict("blocked", "The owner has turned embedding off, so screens can't play it."),
            400 | 404 => verdict("gone", "YouTube has no such video or playlist, or it is private."),
            code => verdict("unsure", format!("YouTube answered with an error ({code}).")),
        },
        Err(_) => offline(),
    }
}

/// Ask the site (or YouTube) now about the saved link `link`.
pub async fn check(fetch: &dyn Fetch, link: &Value) -> Verdict {
    let url = link["url"].as_str().unwrap_or("");
    if link["kind"] == "youtube" { check_youtube(fetch, url).await } else { check_web(fetch, url).await }
}
