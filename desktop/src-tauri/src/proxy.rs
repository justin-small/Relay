//! The pinning proxy: how the app window reaches the HTTPS panel without turning certificate checks off.
//!
//! The system webviews (WKWebView, WebView2, WebKitGTK) cannot be told to trust one self-signed
//! certificate, and each has its own private hook for it. So the window loads this loopback listener
//! instead; it forwards every request to the panel over TLS, and that TLS leg is verified by accepting
//! exactly one thing: a certificate whose SHA-256 fingerprint is the one the backend reported over the
//! sidecar channel (app/sidecar.py). Any other certificate fails the handshake.
//!
//! The webview sees plain HTTP on 127.0.0.1, so `Secure` is dropped from cookies the panel sets (the panel
//! sets it because its own socket speaks TLS). Requests whose Host is not this listener are refused, which
//! stops a web page from reaching the panel through it by DNS rebinding.

use std::{convert::Infallible, net::SocketAddr, sync::Arc};

use bytes::Bytes;
use futures_util::TryStreamExt;
use http_body_util::{combinators::BoxBody, BodyExt, StreamBody};
use hyper::{
    body::{Frame, Incoming},
    header::{HeaderName, HeaderValue, HOST, SET_COOKIE},
    service::service_fn,
    Request, Response, StatusCode,
};
use hyper_util::rt::TokioIo;
use rustls::{
    client::danger::{HandshakeSignatureValid, ServerCertVerifier},
    crypto::{ring, verify_tls12_signature, verify_tls13_signature, CryptoProvider},
    pki_types::{CertificateDer, ServerName, UnixTime},
    DigitallySignedStruct, SignatureScheme,
};
use sha2::{Digest, Sha256};
use tauri::AppHandle;
use tokio::net::TcpListener;

use crate::backend::Pins;

type Body = BoxBody<Bytes, std::io::Error>;

/// Upper-case hex with no separators, so `AB:CD`, `abcd` and `AB CD` compare equal.
pub fn normalise(fingerprint: &str) -> String {
    fingerprint.chars().filter(|c| c.is_ascii_hexdigit()).collect::<String>().to_ascii_uppercase()
}

pub fn fingerprint_of(cert: &[u8]) -> String {
    Sha256::digest(cert).iter().map(|b| format!("{b:02X}")).collect()
}

/// Accepts a server certificate only if its fingerprint is the pinned one. The name is not checked: the
/// fingerprint already names exactly one certificate. Signatures are still verified.
#[derive(Debug)]
pub struct PinVerifier {
    pins: Arc<Pins>,
    provider: Arc<CryptoProvider>,
}

impl PinVerifier {
    pub fn new(pins: Arc<Pins>) -> Self {
        PinVerifier { pins, provider: Arc::new(ring::default_provider()) }
    }
}

impl ServerCertVerifier for PinVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _name: &ServerName<'_>,
        _ocsp: &[u8],
        _now: UnixTime,
    ) -> Result<rustls::client::danger::ServerCertVerified, rustls::Error> {
        let pinned = normalise(&self.pins.fingerprint.read().unwrap());
        if !pinned.is_empty() && fingerprint_of(end_entity.as_ref()) == pinned {
            Ok(rustls::client::danger::ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General("certificate does not match the pinned fingerprint".into()))
        }
    }

    fn verify_tls12_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls12_signature(m, c, d, &self.provider.signature_verification_algorithms)
    }

    fn verify_tls13_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(m, c, d, &self.provider.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider.signature_verification_algorithms.supported_schemes()
    }
}

pub fn client(pins: Arc<Pins>) -> reqwest::Client {
    let config = rustls::ClientConfig::builder_with_provider(Arc::new(ring::default_provider()))
        .with_safe_default_protocol_versions()
        .expect("TLS versions")
        .dangerous() // only to install PinVerifier, which accepts one fingerprint and nothing else
        .with_custom_certificate_verifier(Arc::new(PinVerifier::new(pins)))
        .with_no_client_auth();
    reqwest::Client::builder()
        .use_preconfigured_tls(config)
        .redirect(reqwest::redirect::Policy::none())
        .no_proxy()
        .build()
        .expect("pinned client")
}

const HOP_BY_HOP: [&str; 8] =
    ["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer", "transfer-encoding", "upgrade"];

fn error_page(status: StatusCode, text: &str) -> Response<Body> {
    let body = http_body_util::Full::new(Bytes::from(text.to_string())).map_err(|never| match never {}).boxed();
    let mut r = Response::new(body);
    *r.status_mut() = status;
    r.headers_mut().insert("content-type", HeaderValue::from_static("text/plain; charset=utf-8"));
    r
}

/// Drop `Secure` from a Set-Cookie value: the webview talks plain HTTP to this listener.
pub fn plain_cookie(value: &str) -> String {
    value
        .split(';')
        .filter(|part| !part.trim().eq_ignore_ascii_case("secure"))
        .collect::<Vec<_>>()
        .join(";")
}

async fn forward(
    req: Request<Incoming>,
    port: u16,
    pins: Arc<Pins>,
    client: reqwest::Client,
    app: AppHandle,
) -> Result<Response<Body>, Infallible> {
    // DNS rebinding: only this listener's own name is served.
    let host_ok = req.headers().get(HOST).and_then(|h| h.to_str().ok()) == Some(format!("127.0.0.1:{port}").as_str());
    if !host_ok {
        return Ok(error_page(StatusCode::MISDIRECTED_REQUEST, "Not available."));
    }
    // The shell's own routes (the update banner's install button). They never reach the panel, and the
    // webview that holds the per-run token is the only caller that can use them.
    if req.uri().path().starts_with("/__shell/") {
        let token = req.headers().get("x-relay-shell").and_then(|h| h.to_str().ok());
        let (code, text) = crate::updater::http(&app, req.method().as_str(), req.uri().path(), token);
        return Ok(error_page(StatusCode::from_u16(code).unwrap_or(StatusCode::BAD_REQUEST), text));
    }
    let admin = pins.admin_port.load(std::sync::atomic::Ordering::SeqCst);
    if admin == 0 {
        return Ok(error_page(StatusCode::SERVICE_UNAVAILABLE, "Relay is not running."));
    }
    let (parts, body) = req.into_parts();
    let path = parts.uri.path_and_query().map(|p| p.as_str()).unwrap_or("/");
    let mut out = client.request(parts.method.clone(), format!("https://127.0.0.1:{admin}{path}"));
    for (name, value) in parts.headers.iter() {
        if !HOP_BY_HOP.contains(&name.as_str()) {
            out = out.header(name, value);
        }
    }
    let stream = body.into_data_stream().map_err(std::io::Error::other);
    out = out.body(reqwest::Body::wrap_stream(stream));
    let resp = match out.send().await {
        Ok(r) => r,
        Err(_) => return Ok(error_page(StatusCode::BAD_GATEWAY, "Relay's panel could not be reached, or its certificate did not match.")),
    };
    let mut builder = Response::builder().status(resp.status());
    for (name, value) in resp.headers().iter() {
        if HOP_BY_HOP.contains(&name.as_str()) {
            continue;
        }
        if name == SET_COOKIE {
            if let Ok(v) = value.to_str() {
                if let Ok(v) = HeaderValue::from_str(&plain_cookie(v)) {
                    builder = builder.header(SET_COOKIE, v);
                    continue;
                }
            }
        }
        builder = builder.header(HeaderName::from(name.clone()), value.clone());
    }
    let stream = resp.bytes_stream().map_ok(Frame::data).map_err(std::io::Error::other);
    Ok(builder.body(StreamBody::new(stream).boxed()).unwrap_or_else(|_| error_page(StatusCode::BAD_GATEWAY, "Bad response.")))
}

/// Start the proxy on a free loopback port; returns the port.
pub async fn start(pins: Arc<Pins>, app: AppHandle) -> std::io::Result<u16> {
    let listener = TcpListener::bind(SocketAddr::from(([127, 0, 0, 1], 0))).await?;
    let port = listener.local_addr()?.port();
    let client = client(pins.clone());
    tokio::spawn(async move {
        loop {
            let Ok((stream, _)) = listener.accept().await else { continue };
            let (pins, client, app) = (pins.clone(), client.clone(), app.clone());
            tokio::spawn(async move {
                let svc = service_fn(move |req| forward(req, port, pins.clone(), client.clone(), app.clone()));
                let _ = hyper::server::conn::http1::Builder::new().serve_connection(TokioIo::new(stream), svc).await;
            });
        }
    });
    Ok(port)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn fingerprints_compare_without_separators_or_case() {
        assert_eq!(normalise("ab:CD:01"), "ABCD01");
        assert_eq!(normalise("ab cd 01"), "ABCD01");
    }

    #[test]
    fn only_the_pinned_certificate_is_accepted() {
        let pins = Arc::new(Pins::default());
        let verifier = PinVerifier::new(pins.clone());
        let cert = CertificateDer::from(vec![1u8, 2, 3, 4]);
        let name = ServerName::try_from("127.0.0.1").unwrap();
        let check = |v: &PinVerifier| v.verify_server_cert(&cert, &[], &name, &[], UnixTime::now());
        assert!(check(&verifier).is_err(), "nothing pinned yet: refuse");
        *pins.fingerprint.write().unwrap() = fingerprint_of(&[9, 9, 9]);
        assert!(check(&verifier).is_err(), "a different certificate: refuse");
        let formatted = fingerprint_of(&[1, 2, 3, 4]).as_bytes().chunks(2).map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(":");
        *pins.fingerprint.write().unwrap() = formatted;
        assert!(check(&verifier).is_ok(), "the pinned certificate, however the fingerprint is written");
    }

    #[test]
    fn sha256_of_nothing() {
        assert_eq!(fingerprint_of(b""), "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855");
    }

    #[test]
    fn cookies_lose_only_secure() {
        assert_eq!(plain_cookie("relay_admin=abc; HttpOnly; SameSite=strict; Secure; Max-Age=5"),
                   "relay_admin=abc; HttpOnly; SameSite=strict; Max-Age=5");
        assert_eq!(plain_cookie("a=Secure"), "a=Secure");
    }
}
