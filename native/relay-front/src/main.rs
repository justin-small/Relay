//! Spike for #651: a front door that forwards every request to the running FastAPI app, which stays untouched.
//! Server-sent events stream through (the response body is handed on frame by frame, never collected) and websocket
//! upgrades become a byte pipe once both ends have switched protocols.
//!
//!   relay-front --listen 127.0.0.1:19080 --upstream http://127.0.0.1:18080 \
//!               [--tls-listen 127.0.0.1:19443 --tls-upstream https://127.0.0.1:18443]
//!
//! The HTTPS listener serves a throwaway self-signed certificate; the HTTPS upstream is not verified, because it is a
//! loopback connection to Relay's own self-signed panel port (the real server would not forward over TLS at all).
use std::net::SocketAddr;
use std::sync::Arc;

use bytes::Bytes;
use http_body_util::{combinators::BoxBody, BodyExt, Full};
use hyper::body::Incoming;
use hyper::header::{HeaderName, HeaderValue, CONNECTION, UPGRADE};
use hyper::service::service_fn;
use hyper::{Request, Response, StatusCode, Uri};
use hyper_rustls::HttpsConnectorBuilder;
use hyper_util::client::legacy::{connect::HttpConnector, Client};
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto;
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use tokio::net::TcpListener;
use tokio_rustls::TlsAcceptor;

type Body = BoxBody<Bytes, hyper::Error>;
type Upstream = Client<hyper_rustls::HttpsConnector<HttpConnector>, Incoming>;

#[derive(Debug)]
struct NoVerify;
impl ServerCertVerifier for NoVerify {
    fn verify_server_cert(&self, _: &CertificateDer<'_>, _: &[CertificateDer<'_>], _: &ServerName<'_>, _: &[u8], _: UnixTime)
        -> Result<ServerCertVerified, rustls::Error> { Ok(ServerCertVerified::assertion()) }
    fn verify_tls12_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct)
        -> Result<HandshakeSignatureValid, rustls::Error> { Ok(HandshakeSignatureValid::assertion()) }
    fn verify_tls13_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct)
        -> Result<HandshakeSignatureValid, rustls::Error> { Ok(HandshakeSignatureValid::assertion()) }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        rustls::crypto::ring::default_provider().signature_verification_algorithms.supported_schemes()
    }
}

fn arg(name: &str) -> Option<String> {
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        if a == name {
            return it.next();
        }
    }
    None
}

fn failure(status: StatusCode, why: &str) -> Response<Body> {
    let mut r = Response::new(Full::new(Bytes::from(why.to_owned())).map_err(|never| match never {}).boxed());
    *r.status_mut() = status;
    r
}

fn is_upgrade(req: &Request<Incoming>) -> bool {
    req.headers().get(UPGRADE).is_some()
        && req.headers().get(CONNECTION).and_then(|v| v.to_str().ok())
            .is_some_and(|v| v.to_ascii_lowercase().contains("upgrade"))
}

async fn forward(mut req: Request<Incoming>, peer: SocketAddr, base: Arc<String>, tls: bool, client: Upstream)
    -> Result<Response<Body>, std::convert::Infallible> {
    let path = req.uri().path_and_query().map(|p| p.as_str()).unwrap_or("/");
    let uri: Uri = match format!("{base}{path}").parse() {
        Ok(u) => u,
        Err(_) => return Ok(failure(StatusCode::BAD_REQUEST, "bad request target")),
    };
    *req.uri_mut() = uri;
    // What Relay would need to know the real client; the design issue (#652) decides how it trusts these.
    let h = req.headers_mut();
    h.insert(HeaderName::from_static("x-forwarded-for"), HeaderValue::from_str(&peer.ip().to_string()).unwrap());
    h.insert(HeaderName::from_static("x-forwarded-proto"), HeaderValue::from_static(if tls { "https" } else { "http" }));

    let upgrading = is_upgrade(&req);
    let client_side = upgrading.then(|| hyper::upgrade::on(&mut req));
    let mut resp = match client.request(req).await {
        Ok(r) => r,
        Err(e) => return Ok(failure(StatusCode::BAD_GATEWAY, &format!("Relay did not answer: {e}"))),
    };
    if resp.status() == StatusCode::SWITCHING_PROTOCOLS {
        if let Some(client_side) = client_side {
            let upstream_side = hyper::upgrade::on(&mut resp);
            tokio::spawn(async move {
                if let (Ok(c), Ok(u)) = (client_side.await, upstream_side.await) {
                    let (mut c, mut u) = (TokioIo::new(c), TokioIo::new(u));
                    let _ = tokio::io::copy_bidirectional(&mut c, &mut u).await;
                }
            });
        }
    }
    Ok(resp.map(|b| b.boxed()))
}

fn upstream_client() -> Upstream {
    let cfg = rustls::ClientConfig::builder()
        .dangerous().with_custom_certificate_verifier(Arc::new(NoVerify)).with_no_client_auth();
    let https = HttpsConnectorBuilder::new().with_tls_config(cfg).https_or_http().enable_http1().build();
    Client::builder(TokioExecutor::new()).pool_max_idle_per_host(512).build(https)
}

async fn serve(listener: TcpListener, base: String, tls: Option<TlsAcceptor>) {
    let base = Arc::new(base.trim_end_matches('/').to_owned());
    let client = upstream_client();
    loop {
        let Ok((sock, peer)) = listener.accept().await else { continue };
        let _ = sock.set_nodelay(true);
        let (base, client, tls_acceptor) = (base.clone(), client.clone(), tls.clone());
        tokio::spawn(async move {
            let is_tls = tls_acceptor.is_some();
            let svc = service_fn(move |req| forward(req, peer, base.clone(), is_tls, client.clone()));
            let builder = auto::Builder::new(TokioExecutor::new());
            match tls_acceptor {
                Some(a) => {
                    if let Ok(s) = a.accept(sock).await {
                        let _ = builder.serve_connection_with_upgrades(TokioIo::new(s), svc).await;
                    }
                }
                None => { let _ = builder.serve_connection_with_upgrades(TokioIo::new(sock), svc).await; }
            }
        });
    }
}

fn acceptor() -> TlsAcceptor {
    let cert = rcgen::generate_simple_self_signed(vec!["localhost".into(), "127.0.0.1".into()]).expect("certificate");
    let chain = vec![CertificateDer::from(cert.cert.der().to_vec())];
    let key = PrivateKeyDer::try_from(cert.key_pair.serialize_der()).expect("key");
    let mut cfg = rustls::ServerConfig::builder().with_no_client_auth().with_single_cert(chain, key).expect("tls");
    cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
    TlsAcceptor::from(Arc::new(cfg))
}

#[tokio::main]
async fn main() {
    rustls::crypto::ring::default_provider().install_default().expect("crypto provider");
    let listen = arg("--listen").unwrap_or("127.0.0.1:19080".into());
    let upstream = arg("--upstream").unwrap_or("http://127.0.0.1:18080".into());
    eprintln!("relay-front http  {listen} -> {upstream}");
    let mut tasks = vec![tokio::spawn(serve(TcpListener::bind(&listen).await.expect("--listen"), upstream, None))];
    if let (Some(l), Some(u)) = (arg("--tls-listen"), arg("--tls-upstream")) {
        eprintln!("relay-front https {l} -> {u}");
        tasks.push(tokio::spawn(serve(TcpListener::bind(&l).await.expect("--tls-listen"), u, Some(acceptor()))));
    }
    let _ = tokio::signal::ctrl_c().await;
}
