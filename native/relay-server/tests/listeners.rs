//! The listeners, TLS and port rules of #653, driven the way a browser would: real sockets, real TLS handshakes, real HTTP.
//! Nothing here touches the machine's interfaces or ports 80 and 443; every port is the operating system's pick.

use std::net::{SocketAddr, TcpListener};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU32, Ordering};
use std::sync::Arc;

use axum::extract::Request;
use axum::http::HeaderMap;
use axum::Router;
use relay_server::forward::Upstream;
use relay_server::gate::{Kind, Prefixes};
use relay_server::listen;
use relay_server::netinfo::Port;
use relay_server::netroute::{self, Policy};
use relay_server::serve::{Net, Server, Spec};
use relay_server::tls::{self, Names, Panel};
use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpStream;
use tokio_rustls::TlsConnector;

fn install_provider() {
    let _ = rustls::crypto::ring::default_provider().install_default();
}

/// A folder that is removed when the test ends.
struct Scratch(PathBuf);

impl Scratch {
    fn new() -> Self {
        static N: AtomicU32 = AtomicU32::new(0);
        let dir = std::env::temp_dir().join(format!("relay-server-test-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
        std::fs::create_dir_all(&dir).expect("scratch folder");
        Self(dir)
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn names(ips: &[&str]) -> Names {
    Names { dns: vec!["localhost".into(), "relay-test.local".into()], ips: ips.iter().map(|s| (*s).to_owned()).collect() }
}

fn port(id: &str, name: &str, ip: &str, up: bool) -> Port {
    Port {
        id: id.to_owned(),
        name: name.to_owned(),
        friendly_name: format!("{name} adapter"),
        mac: None,
        ipv4: if ip.is_empty() { Vec::new() } else { vec![format!("{ip}/24")] },
        default_route: false,
        up: Some(up),
        virtual_: false,
        vpn: false,
        loopback: false,
        link_local_only: false,
    }
}

// ------------------------------------------------------------------------------------------------ routes

#[test]
fn route_keys_accept_both_saved_forms_and_refuse_junk() {
    assert_eq!(netroute::route_key(&json!("mac:aa:bb:cc:dd:ee:ff")).as_deref(), Some("mac:aa:bb:cc:dd:ee:ff"));
    assert_eq!(netroute::route_key(&json!({"port": "port:relay:5", "key": "name:en5"})).as_deref(), Some("name:en5"));
    assert_eq!(netroute::route_key(&json!("en5")), None);
    assert_eq!(netroute::route_key(&json!("mac:")), None);
    assert_eq!(netroute::route_key(&json!(5)), None);
    assert_eq!(netroute::route_key(&json!("name:bad\nname")), None);
}

#[test]
fn a_class_with_no_route_follows_the_default_network() {
    let ports = [Port { default_route: true, ..port("name:en0", "en0", "192.168.1.20", true) }];
    let r = netroute::resolve("viewers", "Viewer pages", Policy::Fallback, &json!({"routes": {}}), &ports);
    assert!(!r.routed() && r.available && !r.fallback);
    assert_eq!(r.address.as_deref(), Some("192.168.1.20"));
}

#[test]
fn a_routed_class_uses_its_port_while_the_port_is_up() {
    let ports = [port("name:en5", "en5", "10.0.0.5", true), Port { default_route: true, ..port("name:en0", "en0", "192.168.1.20", true) }];
    let net = json!({"routes": {"crew": {"port": "port:relay:5", "key": "name:en5"}}});
    let r = netroute::resolve("crew", "Crew pages", Policy::Fallback, &net, &ports);
    assert!(r.routed() && r.available && !r.fallback);
    assert_eq!(r.address.as_deref(), Some("10.0.0.5"));
    assert_eq!(netroute::link_addresses(&r, &ports), vec!["10.0.0.5".to_owned()]);
}

#[test]
fn a_fallback_class_uses_the_default_network_when_its_port_is_down_and_says_so() {
    let ports = [port("name:en5", "en5", "10.0.0.5", false), Port { default_route: true, ..port("name:en0", "en0", "192.168.1.20", true) }];
    let net = json!({"routes": {"crew": "name:en5"}});
    let r = netroute::resolve("crew", "Crew pages", Policy::Fallback, &net, &ports);
    assert!(r.routed() && r.fallback && r.available);
    assert_eq!(r.address.as_deref(), Some("192.168.1.20"));
    assert!(r.reason.starts_with("Crew pages: en5 adapter is down (no cable or no link)."), "{}", r.reason);
    assert!(r.reason.ends_with("Using the system's default network instead."));
}

#[test]
fn a_strict_class_is_not_run_when_its_port_is_missing() {
    let ports = [Port { default_route: true, ..port("name:en0", "en0", "192.168.1.20", true) }];
    let net = json!({"routes": {"dante": "mac:aa:bb:cc:dd:ee:ff"}, "known_ports": {"mac:aa:bb:cc:dd:ee:ff": {"friendly_name": "USB LAN"}}});
    let r = netroute::resolve("dante", "Dante audio", Policy::Strict, &net, &ports);
    assert!(r.routed() && !r.available && !r.fallback && r.address.is_none());
    assert_eq!(r.reason, "Dante audio: USB LAN is not connected to this computer.");
    assert!(netroute::link_addresses(&r, &ports).is_empty());
}

#[test]
fn a_saved_policy_beats_the_class_default() {
    let ports = [port("name:en5", "en5", "", true)];
    let net = json!({"routes": {"crew": "name:en5"}, "policy": {"crew": "strict"}});
    let r = netroute::resolve("crew", "Crew pages", Policy::Fallback, &net, &ports);
    assert_eq!(r.policy, Policy::Strict);
    assert_eq!(r.reason, "Crew pages: en5 adapter has no network address.");
}

// ------------------------------------------------------------------------------------------------ plans and ports

#[test]
fn an_unrouted_listener_binds_the_configured_host() {
    let plan = listen::plan(None, false, "0.0.0.0", 80);
    assert_eq!(plan.hosts, vec!["0.0.0.0".to_owned()]);
    assert!(plan.notes.is_empty());
}

#[test]
fn a_routed_listener_binds_its_address_and_loopback_only() {
    let ports = [port("name:en5", "en5", "10.0.0.5", true)];
    let r = netroute::resolve("viewers", "Viewer pages", Policy::Fallback, &json!({"routes": {"viewers": "name:en5"}}), &ports);
    let plan = listen::plan(Some(&r), false, "0.0.0.0", 80);
    assert_eq!(plan.hosts, vec!["10.0.0.5".to_owned(), "127.0.0.1".to_owned()]);
}

#[test]
fn a_strict_route_with_its_port_missing_leaves_only_this_computer_and_the_panel_says_how_to_get_back_in() {
    let r = netroute::resolve("crew", "Crew pages", Policy::Strict, &json!({"routes": {"crew": "name:en9"}}), &[]);
    let plan = listen::plan(Some(&r), true, "0.0.0.0", 443);
    assert_eq!(plan.hosts, vec!["127.0.0.1".to_owned()]);
    assert_eq!(plan.notes.len(), 2);
    assert!(plan.notes[0].ends_with("Until it is back, only this computer can reach it."));
    assert!(plan.notes[1].contains("https://127.0.0.1/admin"), "{}", plan.notes[1]);
    assert!(plan.notes[1].contains("remove \"crew\" from \"routes\""));
}

#[test]
fn links_leave_out_the_port_of_the_scheme_itself() {
    assert_eq!(listen::link("https", "relay.local", 443, "/admin"), "https://relay.local/admin");
    assert_eq!(listen::link("https", "relay.local", 8443, "/admin"), "https://relay.local:8443/admin");
    assert_eq!(listen::link("http", "10.0.0.5", 80, "/"), "http://10.0.0.5/");
    assert_eq!(listen::link("http", "10.0.0.5", 8080, "/"), "http://10.0.0.5:8080/");
}

#[test]
fn only_the_defaults_fall_back() {
    assert_eq!((listen::fallback(80), listen::fallback(443), listen::fallback(8090)), (Some(8080), Some(8443), None));
}

fn taken() -> (TcpListener, u16) {
    let l = TcpListener::bind("127.0.0.1:0").expect("a port");
    let p = l.local_addr().expect("address").port();
    (l, p)
}

#[test]
fn a_port_in_use_falls_back_and_says_so() {
    let (_held, busy) = taken();
    let (free, spare) = taken();
    drop(free);
    let opened = listen::listen_via("127.0.0.1", busy, "viewer pages", Some(spare)).expect("falls back");
    assert_eq!(opened.port, spare);
    assert_eq!(opened.note, Some(format!("The viewer pages could not use port {busy} (it is already in use), so it is on port {spare}.")));
}

#[test]
fn a_chosen_port_in_use_is_an_error_not_a_silent_substitution() {
    let (_held, busy) = taken();
    let err = listen::listen_via("127.0.0.1", busy, "Signage (screens)", None).err().expect("refused");
    assert_eq!(err, format!("Cannot listen on 127.0.0.1:{busy} for the Signage (screens): it is already in use."));
}

#[test]
fn when_the_fallback_is_taken_too_both_are_named() {
    let (_a, busy) = taken();
    let (_b, also) = taken();
    let err = listen::listen_via("127.0.0.1", busy, "operator panel", Some(also)).err().expect("refused");
    assert_eq!(err, format!("Cannot listen on 127.0.0.1:{busy} for the operator panel (it is already in use) or on {also} (it is already in use)."));
}

#[test]
fn a_second_address_joins_the_port_the_first_was_given() {
    let hosts = vec!["127.0.0.1".to_owned(), "127.0.0.1".to_owned()];
    let opening = listen::open_all(&hosts, 0, "viewer pages", Vec::new()).expect("opens");
    assert_eq!(opening.sockets.len(), 1, "the same address twice cannot be bound twice");
    assert_eq!(opening.notes.len(), 1);
    assert!(opening.notes[0].starts_with("The viewer pages could not also listen on 127.0.0.1:"), "{}", opening.notes[0]);
}

// ------------------------------------------------------------------------------------------------ the certificate

fn read_cert_fingerprint(dir: &std::path::Path) -> String {
    let pem = std::fs::read_to_string(dir.join("admin.crt")).expect("certificate file");
    let body: String = pem.lines().filter(|l| !l.starts_with("-----")).collect();
    let der = decode_base64(&body);
    tls::fingerprint(&der)
}

fn decode_base64(text: &str) -> Vec<u8> {
    const ALPHABET: &[u8] = b"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
    let (mut out, mut acc, mut bits) = (Vec::new(), 0u32, 0u32);
    for b in text.bytes().filter(|b| *b != b'=') {
        let v = ALPHABET.iter().position(|a| *a == b).expect("base64") as u32;
        acc = (acc << 6) | v;
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    out
}

#[test]
fn the_certificate_is_minted_once_and_kept_across_restarts() {
    install_provider();
    let dir = Scratch::new();
    let first = Panel::start(&dir.0, "", &names(&["192.168.1.20", "127.0.0.1"])).expect("mints");
    assert!(!first.info.reused && first.changed.is_none());
    assert_eq!(first.info.fingerprint, read_cert_fingerprint(&dir.0));
    let again = Panel::start(&dir.0, "", &names(&["192.168.1.20", "127.0.0.1"])).expect("reads");
    assert!(again.info.reused, "a restart keeps what an operator already trusted");
    assert_eq!(again.info.fingerprint, first.info.fingerprint);
    assert!(again.info.dns.contains(&"localhost".to_owned()) && again.info.ips.contains(&"192.168.1.20".to_owned()));
}

#[cfg(unix)]
#[test]
fn the_private_key_is_owner_only() {
    use std::os::unix::fs::PermissionsExt;
    install_provider();
    let dir = Scratch::new();
    Panel::start(&dir.0, "", &names(&["127.0.0.1"])).expect("mints");
    let mode = std::fs::metadata(dir.0.join("admin.key")).expect("key file").permissions().mode() & 0o777;
    assert_eq!(mode, 0o600);
}

#[test]
fn a_new_address_mints_again_and_keeps_the_old_names_so_a_returning_name_does_not_mint_twice() {
    install_provider();
    let dir = Scratch::new();
    let mut panel = Panel::start(&dir.0, "", &names(&["192.168.1.20", "127.0.0.1"])).expect("mints");
    let before = panel.info.fingerprint.clone();

    assert!(!panel.refresh(&names(&["192.168.1.20", "127.0.0.1"])).expect("same names"), "nothing new, nothing minted");
    assert!(panel.refresh(&names(&["10.1.1.9", "127.0.0.1"])).expect("a new network"), "a laptop on a new Wi-Fi network");
    assert_ne!(panel.info.fingerprint, before);
    let changed = panel.changed.clone().expect("the move is recorded");
    assert_eq!((changed.from.as_str(), changed.to.as_str()), (before.as_str(), panel.info.fingerprint.as_str()));
    assert!(panel.info.ips.contains(&"10.1.1.9".to_owned()) && panel.info.ips.contains(&"192.168.1.20".to_owned()));

    let minted = panel.info.fingerprint.clone();
    assert!(!panel.refresh(&names(&["192.168.1.20", "127.0.0.1"])).expect("back home"), "the old address is still named");
    assert_eq!(panel.info.fingerprint, minted);
}

#[test]
fn a_certificate_without_its_key_is_replaced_not_diagnosed() {
    install_provider();
    let dir = Scratch::new();
    let first = Panel::start(&dir.0, "", &names(&["127.0.0.1"])).expect("mints");
    std::fs::remove_file(dir.0.join("admin.key")).expect("remove key");
    let second = Panel::start(&dir.0, "", &names(&["127.0.0.1"])).expect("mints again");
    assert_ne!(first.info.fingerprint, second.info.fingerprint);
}

#[test]
fn names_put_the_fqdn_first_and_always_include_loopback() {
    let n = tls::names("relay.example.org.", &["10.0.0.5".to_owned()], &["10.0.0.5".to_owned(), "10.0.0.6".to_owned()]);
    assert_eq!(n.dns.first().map(String::as_str), Some("relay.example.org"));
    assert!(n.dns.contains(&"localhost".to_owned()));
    assert_eq!(n.ips, vec!["10.0.0.5".to_owned(), "10.0.0.6".to_owned(), "127.0.0.1".to_owned()]);
}

// ------------------------------------------------------------------------------------------------ a real server

#[derive(Debug)]
struct AcceptAnyCertificate;

impl ServerCertVerifier for AcceptAnyCertificate {
    fn verify_server_cert(&self, _: &CertificateDer<'_>, _: &[CertificateDer<'_>], _: &ServerName<'_>, _: &[u8], _: UnixTime)
        -> Result<ServerCertVerified, rustls::Error> {
        Ok(ServerCertVerified::assertion())
    }
    fn verify_tls12_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct)
        -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }
    fn verify_tls13_signature(&self, _: &[u8], _: &CertificateDer<'_>, _: &DigitallySignedStruct)
        -> Result<HandshakeSignatureValid, rustls::Error> {
        Ok(HandshakeSignatureValid::assertion())
    }
    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        rustls::crypto::ring::default_provider().signature_verification_algorithms.supported_schemes()
    }
}

fn client_config() -> Arc<rustls::ClientConfig> {
    Arc::new(
        rustls::ClientConfig::builder()
            .dangerous()
            .with_custom_certificate_verifier(Arc::new(AcceptAnyCertificate))
            .with_no_client_auth(),
    )
}

struct Reply {
    status: u16,
    headers: Vec<(String, String)>,
    body: String,
    /// SHA-256 of the certificate the server showed, over TLS.
    seen_fingerprint: Option<String>,
}

impl Reply {
    fn header(&self, name: &str) -> Option<&str> {
        self.headers.iter().find(|(k, _)| k.eq_ignore_ascii_case(name)).map(|(_, v)| v.as_str())
    }
    fn json(&self) -> Value {
        serde_json::from_str(&self.body).unwrap_or(Value::Null)
    }
}

async fn get(addr: SocketAddr, secure: bool, path: &str, extra: &[(&str, &str)]) -> Reply {
    let mut req = format!("GET {path} HTTP/1.1\r\nHost: relay.test\r\nConnection: close\r\n");
    for (k, v) in extra {
        req.push_str(&format!("{k}: {v}\r\n"));
    }
    req.push_str("\r\n");
    let tcp = TcpStream::connect(addr).await.expect("connect");
    let mut raw = Vec::new();
    let mut seen = None;
    if secure {
        let connector = TlsConnector::from(client_config());
        let mut stream = connector.connect(ServerName::try_from("localhost").expect("name"), tcp).await.expect("TLS handshake");
        seen = stream.get_ref().1.peer_certificates().and_then(|c| c.first()).map(|c| tls::fingerprint(c.as_ref()));
        stream.write_all(req.as_bytes()).await.expect("write");
        let _ = stream.read_to_end(&mut raw).await;
    } else {
        let mut stream = tcp;
        stream.write_all(req.as_bytes()).await.expect("write");
        let _ = stream.read_to_end(&mut raw).await;
    }
    let text = String::from_utf8_lossy(&raw).into_owned();
    let (head, body) = text.split_once("\r\n\r\n").unwrap_or((text.as_str(), ""));
    let mut lines = head.lines();
    let status = lines.next().and_then(|l| l.split(' ').nth(1)).and_then(|s| s.parse().ok()).unwrap_or(0);
    let headers = lines.filter_map(|l| l.split_once(": ")).map(|(k, v)| (k.to_owned(), v.to_owned())).collect();
    Reply { status, headers, body: decode_chunked_if_needed(body), seen_fingerprint: seen }
}

/// The test replies are small; a chunked body is `<hex>\r\n<data>\r\n0\r\n\r\n`.
fn decode_chunked_if_needed(body: &str) -> String {
    let Some((size, rest)) = body.split_once("\r\n") else { return body.to_owned() };
    match usize::from_str_radix(size.trim(), 16) {
        Ok(n) if n <= rest.len() && body.ends_with("0\r\n\r\n") => rest[..n].to_owned(),
        _ => body.to_owned(),
    }
}

fn spec(key: &str, secure: bool, kind: Kind, traffic: Option<&str>) -> Spec {
    Spec {
        key: key.to_owned(),
        what: key.to_owned(),
        traffic: traffic.map(str::to_owned),
        traffic_label: key.to_owned(),
        policy: Policy::Fallback,
        configured: "127.0.0.1".to_owned(),
        port: 0,
        secure,
        kind,
    }
}

fn routes() -> Router {
    Router::new()
        .route("/hello", axum::routing::get(|| async { "hello" }))
        .route("/admin/page", axum::routing::get(|| async { "the panel" }))
        .route("/api/admin/users", axum::routing::get(|| async { "users" }))
        .route("/home", axum::routing::get(|| async { "home" }))
        .route("/crew", axum::routing::get(|| async { "crew" }))
        .route("/signage/screen", axum::routing::get(|| async { "a screen" }))
        .route("/admin/flash", axum::routing::get(|| async { "flasher" }))
}

fn prefixes() -> Prefixes {
    Prefixes { listener: vec!["/signage".to_owned()], panel: vec!["/crew".to_owned()] }
}

fn net() -> Net {
    Net { network: json!({"routes": {}}), ports: Vec::new() }
}

struct Running {
    server: Server,
    viewer: SocketAddr,
    panel: SocketAddr,
    module: SocketAddr,
    fingerprint: String,
    _dir: Scratch,
}

fn addr_of(server: &Server, key: &str) -> SocketAddr {
    let g = server.groups().into_iter().find(|g| g.spec.key == key).expect("group");
    SocketAddr::new("127.0.0.1".parse().expect("ip"), g.port)
}

async fn start(upstream: Option<Arc<Upstream>>) -> Running {
    install_provider();
    let dir = Scratch::new();
    let panel = Panel::start(&dir.0, "", &names(&["127.0.0.1"])).expect("certificate");
    let server = Server::new(routes(), upstream);
    server.set_prefixes(prefixes());
    server.set_tls(panel.server_config());
    server.open(spec("viewers", false, Kind::Viewer, None), &net()).expect("viewer port");
    server.open(spec("panel", true, Kind::Panel, None), &net()).expect("panel port");
    let own = Arc::new(vec!["/signage".to_owned()]);
    server.open(spec("signage.screens", false, Kind::Module(own), None), &net()).expect("module port");
    Running {
        viewer: addr_of(&server, "viewers"),
        panel: addr_of(&server, "panel"),
        module: addr_of(&server, "signage.screens"),
        fingerprint: panel.info.fingerprint.clone(),
        server,
        _dir: dir,
    }
}

#[tokio::test]
async fn the_viewer_port_serves_viewer_pages_and_never_the_panel() {
    let r = start(None).await;
    let ok = get(r.viewer, false, "/hello", &[]).await;
    assert_eq!((ok.status, ok.body.as_str()), (200, "hello"));
    assert_eq!(ok.header("permissions-policy"), Some("camera=(), microphone=(), geolocation=(), payment=(), usb=(), serial=()"));
    assert_eq!(ok.header("strict-transport-security"), None, "HSTS is only honoured over HTTPS, so only the panel sends it");

    for path in ["/admin/page", "/admin", "/api/admin/users", "/home", "/crew", "/signage/screen"] {
        let refused = get(r.viewer, false, path, &[]).await;
        assert_eq!(refused.status, 404, "{path} must not answer on the viewer port");
        assert_eq!(refused.json(), json!({"error": "Not found"}), "{path}");
    }
    r.server.close();
}

#[tokio::test]
async fn a_percent_encoded_path_is_read_the_way_the_app_reads_it() {
    let r = start(None).await;
    for path in ["/%61dmin/page", "/api/%61dmin/users", "/%68ome", "/admin%2Fpage", "/%73ignage/screen"] {
        let refused = get(r.viewer, false, path, &[]).await;
        assert_eq!(refused.status, 404, "{path}");
    }
    r.server.close();
}

#[tokio::test]
async fn the_panel_is_https_with_its_security_headers_and_the_fingerprint_that_was_printed() {
    let r = start(None).await;
    let ok = get(r.panel, true, "/admin/page", &[]).await;
    assert_eq!((ok.status, ok.body.as_str()), (200, "the panel"));
    assert_eq!(ok.seen_fingerprint.as_deref(), Some(r.fingerprint.as_str()), "the browser sees the fingerprint the operator was shown");
    assert_eq!(ok.header("x-content-type-options"), Some("nosniff"));
    assert_eq!(ok.header("x-frame-options"), Some("SAMEORIGIN"));
    assert_eq!(ok.header("referrer-policy"), Some("no-referrer"));
    assert_eq!(ok.header("strict-transport-security"), Some("max-age=34300800"));
    assert!(get(r.panel, true, "/crew", &[]).await.status == 200, "a panel prefix answers on the panel");
    assert_eq!(get(r.panel, true, "/hello", &[]).await.status, 200, "viewer pages answer there too");
    assert_eq!(get(r.panel, true, "/signage/screen", &[]).await.status, 404, "a module's own pages answer only on its own port");
    r.server.close();
}

#[tokio::test]
async fn the_panel_port_does_not_speak_plain_http() {
    let r = start(None).await;
    let tcp = TcpStream::connect(r.panel).await.expect("connect");
    let mut stream = tcp;
    stream.write_all(b"GET /admin/page HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n").await.expect("write");
    let mut raw = Vec::new();
    let _ = stream.read_to_end(&mut raw).await;
    assert!(!String::from_utf8_lossy(&raw).contains("the panel"), "the panel is never served in cleartext");
    r.server.close();
}

#[tokio::test]
async fn the_flasher_page_alone_may_use_web_serial() {
    let r = start(None).await;
    let flash = get(r.panel, true, "/admin/flash", &[]).await;
    assert!(flash.header("permissions-policy").is_some_and(|p| p.ends_with("serial=(self)")));
    let other = get(r.panel, true, "/admin/page", &[]).await;
    assert!(other.header("permissions-policy").is_some_and(|p| p.ends_with("serial=()")));
    r.server.close();
}

#[tokio::test]
async fn a_module_port_answers_only_that_modules_routes() {
    let r = start(None).await;
    let ok = get(r.module, false, "/signage/screen", &[]).await;
    assert_eq!((ok.status, ok.body.as_str()), (200, "a screen"));
    for path in ["/hello", "/admin/page", "/", "/signageX/screen"] {
        assert_eq!(get(r.module, false, path, &[]).await.status, 404, "{path} must not answer on a module's port");
    }
    r.server.close();
}

#[tokio::test]
async fn relays_own_calls_to_its_app_are_never_answered_to_a_client() {
    let r = start(None).await;
    for addr in [r.viewer, r.module] {
        assert_eq!(get(addr, false, "/api/internal/front", &[("X-Relay-Front", "guess")]).await.status, 404);
    }
    assert_eq!(get(r.panel, true, "/api/internal/front", &[]).await.status, 404);
    r.server.close();
}

// ------------------------------------------------------------------------------------------------ forwarding

/// A stand-in for the app: it says what it was told.
async fn app_that_echoes() -> SocketAddr {
    let router = Router::new()
        .route(
            "/who",
            axum::routing::get(|headers: HeaderMap| async move {
                let h = |n: &str| headers.get(n).and_then(|v| v.to_str().ok()).unwrap_or("-").to_owned();
                axum::Json(json!({
                    "xff": h("x-forwarded-for"), "proto": h("x-forwarded-proto"), "port": h("x-relay-port"), "front": h("x-relay-front"),
                }))
            }),
        )
        .fallback(|req: Request| async move { format!("app:{}", req.uri().path()) });
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.expect("app port");
    let addr = listener.local_addr().expect("address");
    tokio::spawn(async move {
        let _ = axum::serve(listener, router).await;
    });
    addr
}

#[tokio::test]
async fn what_the_server_does_not_serve_goes_to_the_app_with_the_clients_real_details() {
    let app = app_that_echoes().await;
    let r = start(Some(Upstream::new(&format!("http://{app}")))).await;

    let hello = get(r.viewer, false, "/hello", &[]).await;
    assert_eq!(hello.body, "hello", "a route the server has is not forwarded");
    let forwarded = get(r.viewer, false, "/some/page", &[]).await;
    assert_eq!(forwarded.body, "app:/some/page");

    let who = get(r.viewer, false, "/who", &[("X-Forwarded-For", "6.6.6.6"), ("X-Relay-Port", "443"), ("X-Relay-Front", "token")]).await.json();
    assert_eq!(who["xff"], "127.0.0.1", "a forwarded address the client sent is replaced, never believed");
    assert_eq!(who["proto"], "http");
    assert_eq!(who["port"], r.viewer.port().to_string(), "the port the client really reached, not the one it claimed");
    assert_eq!(who["front"], "-", "the client cannot send the app the front door's token");

    let who = get(r.panel, true, "/who", &[]).await.json();
    assert_eq!((who["proto"].as_str(), who["port"].as_str()), (Some("https"), Some(r.panel.port().to_string().as_str())));
    r.server.close();
}

#[tokio::test]
async fn the_port_rules_hold_for_what_is_forwarded_too() {
    let app = app_that_echoes().await;
    let r = start(Some(Upstream::new(&format!("http://{app}")))).await;
    assert_eq!(get(r.viewer, false, "/admin/anything-else", &[]).await.status, 404, "the app never sees an admin path from a viewer");
    assert_eq!(get(r.module, false, "/other", &[]).await.status, 404);
    let ok = get(r.panel, true, "/admin/anything-else", &[]).await;
    assert_eq!((ok.status, ok.body.as_str()), (200, "app:/admin/anything-else"));
    assert_eq!(ok.header("strict-transport-security"), Some("max-age=34300800"), "the panel's headers are on forwarded answers too");
    r.server.close();
}

#[tokio::test]
async fn an_app_that_is_down_is_a_502_and_the_listener_keeps_serving() {
    let (free, dead) = taken();
    drop(free);
    let r = start(Some(Upstream::new(&format!("http://127.0.0.1:{dead}")))).await;
    let down = get(r.viewer, false, "/some/page", &[]).await;
    assert_eq!(down.status, 502);
    assert_eq!(down.json(), json!({"detail": "Relay's app did not answer"}), "a fixed sentence, never the transport's text");
    assert_eq!(get(r.viewer, false, "/hello", &[]).await.status, 200);
    r.server.close();
}

#[tokio::test]
async fn without_an_app_a_path_nothing_serves_is_a_404() {
    let r = start(None).await;
    let nothing = get(r.viewer, false, "/some/page", &[]).await;
    assert_eq!((nothing.status, nothing.json()), (404, json!({"detail": "Not found"})));
    r.server.close();
}

// ------------------------------------------------------------------------------------------------ moving a listener

#[tokio::test]
async fn a_listener_moves_when_its_routes_address_changes_and_keeps_its_port() {
    install_provider();
    let dir = Scratch::new();
    let panel = Panel::start(&dir.0, "", &names(&["127.0.0.1"])).expect("certificate");
    let server = Server::new(routes(), None);
    server.set_tls(panel.server_config());
    let mut viewers = spec("viewers", false, Kind::Viewer, Some("viewers"));
    viewers.configured = "0.0.0.0".to_owned();
    server.open(viewers, &net()).expect("opens on every interface");
    let port = server.groups()[0].port;
    assert_eq!(server.groups()[0].hosts, vec!["0.0.0.0".to_owned()]);

    // The Network page routes viewers to a port whose address is this computer's own.
    let ports = [port_with("name:lo-test", "127.0.0.1")];
    let routed = Net { network: json!({"routes": {"viewers": "name:lo-test"}}), ports: ports.to_vec() };
    assert_eq!(server.rebind(&routed).await, vec!["viewers".to_owned()]);
    assert_eq!(server.groups()[0].hosts, vec!["127.0.0.1".to_owned()]);
    assert_eq!(server.groups()[0].port, port, "it moves on its port; links do not change");
    let reply = get(SocketAddr::new("127.0.0.1".parse().expect("ip"), port), false, "/hello", &[]).await;
    assert_eq!(reply.status, 200);

    assert!(server.rebind(&routed).await.is_empty(), "nothing moved, so nothing is reopened");
    // The route is taken off again.
    assert_eq!(server.rebind(&net()).await, vec!["viewers".to_owned()]);
    assert_eq!(server.groups()[0].hosts, vec!["0.0.0.0".to_owned()]);
    server.close();
}

fn port_with(id: &str, ip: &str) -> Port {
    port(id, "lo-test", ip, true)
}

#[tokio::test]
async fn a_routed_port_going_missing_keeps_this_computer_reaching_the_listener_and_says_why() {
    install_provider();
    let server = Server::new(routes(), None);
    let mut dante = spec("dante.ctl", false, Kind::Viewer, Some("dante"));
    dante.policy = Policy::Strict;
    dante.traffic_label = "Dante audio".to_owned();
    let down = Net { network: json!({"routes": {"dante": "name:usb0"}}), ports: Vec::new() };
    server.open(dante, &down).expect("opens on loopback only");
    let g = &server.groups()[0];
    assert_eq!(g.hosts, vec!["127.0.0.1".to_owned()]);
    assert!(g.notes.iter().any(|n| n.contains("Dante audio") && n.ends_with("only this computer can reach it.")), "{:?}", g.notes);
    server.close();
}
