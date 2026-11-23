//! The helper against a fake Relay (a WebSocket server) and a fake ProPresenter API (a tiny HTTP server): pairing,
//! hello, calls answered, refusals, the file and path rules, and reconnecting.

use std::io::{Read, Write};
use std::net::TcpListener;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use base64::Engine;
use futures_util::{SinkExt, StreamExt};
use relay_helper::capability::Registry;
use relay_helper::client;
use relay_helper::config::{Config, ProPresenterConfig};
use relay_helper::{autostart, tls};
use relay_helper::propresenter::{check_api_path, safe_name, Opener, ProPresenter};
use relay_helper::protocol::{FromHelper, ToHelper};
use serde_json::{json, Value};
use tokio::net::TcpListener as AsyncListener;
use tokio_tungstenite::tungstenite::Message;

struct Recorder(Mutex<Vec<PathBuf>>);

impl Opener for Recorder {
    fn open(&self, files: &[PathBuf]) -> Result<(), String> {
        // The file must exist when it is handed over.
        for f in files {
            if !f.exists() {
                return Err("missing".into());
            }
        }
        self.0.lock().unwrap().extend_from_slice(files);
        Ok(())
    }
}

/// A one-route HTTP server standing in for ProPresenter: GET /v1/version and an echo of anything else.
fn fake_propresenter() -> u16 {
    let listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let port = listener.local_addr().unwrap().port();
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(mut s) = stream else { continue };
            // A request can arrive in pieces (headers, then body): read until the whole body is here.
            let mut raw: Vec<u8> = Vec::new();
            let mut buf = [0u8; 4096];
            loop {
                let n = s.read(&mut buf).unwrap_or(0);
                if n == 0 {
                    break;
                }
                raw.extend_from_slice(&buf[..n]);
                let text = String::from_utf8_lossy(&raw).to_string();
                if let Some(split) = text.find("\r\n\r\n") {
                    let want: usize = text[..split]
                        .lines()
                        .find_map(|l| l.to_ascii_lowercase().strip_prefix("content-length:").map(|v| v.trim().parse().unwrap_or(0)))
                        .unwrap_or(0);
                    if raw.len() >= split + 4 + want {
                        break;
                    }
                }
            }
            let req = String::from_utf8_lossy(&raw).to_string();
            let first = req.lines().next().unwrap_or("").to_string();
            let body = if first.starts_with("GET /version") {
                r#"{"host_description":"ProPresenter 22","api_version":"v1"}"#.to_string()
            } else {
                let payload = req.split("\r\n\r\n").nth(1).unwrap_or("");
                json!({"saw": first, "payload": payload}).to_string()
            };
            let _ = write!(s, "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}", body.len(), body);
        }
    });
    port
}

async fn relay_listener() -> (AsyncListener, String) {
    let l = AsyncListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("ws://{}", l.local_addr().unwrap());
    (l, url)
}

fn registry(port: u16, rec: Arc<Recorder>) -> Registry {
    let mut r = Registry::new();
    let scratch = std::env::temp_dir().join(format!("relay-helper-test-{}", uuid_like()));
    r.register(Arc::new(ProPresenter::new(ProPresenterConfig { api_host: "127.0.0.1".into(), api_port: port }, rec, scratch)));
    r
}

fn uuid_like() -> String {
    format!("{}-{:?}", std::process::id(), std::time::Instant::now())
        .chars()
        .filter(|c| c.is_ascii_alphanumeric())
        .collect()
}

async fn next_text<S>(ws: &mut S) -> Value
where
    S: StreamExt<Item = Result<Message, tokio_tungstenite::tungstenite::Error>> + Unpin,
{
    loop {
        match tokio::time::timeout(Duration::from_secs(5), ws.next()).await.expect("a frame within 5 s") {
            Some(Ok(Message::Text(t))) => return serde_json::from_str(&t).unwrap(),
            Some(Ok(_)) => continue,
            other => panic!("connection ended: {other:?}"),
        }
    }
}

#[tokio::test]
async fn pairing_trades_a_code_for_a_token() {
    let (listener, url) = relay_listener().await;
    let server = tokio::spawn(async move {
        let (s, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
        let first = next_text(&mut ws).await;
        assert_eq!(first["type"], "pair");
        assert_eq!(first["code"], "ABC-123");
        let reply = ToHelper::Paired { helper_id: "h1".into(), token: "secret-token".into() };
        ws.send(Message::text(serde_json::to_string(&reply).unwrap())).await.unwrap();
    });
    let cfg = Config { relay_url: url, ..Config::default() };
    let paired = client::pair(cfg, " ABC-123 ").await.unwrap();
    assert_eq!((paired.helper_id.as_str(), paired.token.as_str()), ("h1", "secret-token"));
    server.await.unwrap();

    let (listener, url) = relay_listener().await;
    tokio::spawn(async move {
        let (s, _) = listener.accept().await.unwrap();
        let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
        let _ = next_text(&mut ws).await;
        let refuse = ToHelper::Refused { reason: "That code was already used.".into() };
        ws.send(Message::text(serde_json::to_string(&refuse).unwrap())).await.unwrap();
    });
    let err = client::pair(Config { relay_url: url, ..Config::default() }, "x").await.unwrap_err();
    assert_eq!(err, "That code was already used.");
}

#[tokio::test]
async fn hello_calls_and_results() {
    let port = fake_propresenter();
    let rec = Arc::new(Recorder(Mutex::new(vec![])));
    let (listener, url) = relay_listener().await;
    let cfg = Config { relay_url: url, helper_id: "h1".into(), token: "tok".into(), name: "FOH Mac".into(), ..Config::default() };
    let reg = registry(port, rec.clone());
    let helper = tokio::spawn(async move { client::run(cfg, reg).await });

    let (s, _) = listener.accept().await.unwrap();
    let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
    let hello = next_text(&mut ws).await;
    assert_eq!(hello["type"], "hello");
    assert_eq!(hello["token"], "tok");
    assert_eq!(hello["name"], "FOH Mac");
    assert_eq!(hello["capabilities"][0]["id"], "propresenter");
    assert_eq!(hello["capabilities"][0]["ops"], json!(["status", "open_files", "api"]));

    let ask = |id: u64, op: &str, args: Value| {
        serde_json::to_string(&ToHelper::Call { id, cap: "propresenter".into(), op: op.into(), args }).unwrap()
    };

    ws.send(Message::text(ask(1, "status", Value::Null))).await.unwrap();
    let r = next_text(&mut ws).await;
    assert_eq!((r["id"].as_u64(), r["ok"].as_bool()), (Some(1), Some(true)));
    assert_eq!(r["data"]["api_reachable"], true);
    assert_eq!(r["data"]["version"]["host_description"], "ProPresenter 22");

    ws.send(Message::text(ask(2, "api", json!({"method": "POST", "path": "/v1/looks", "body": {"a": 1}})))).await.unwrap();
    let r = next_text(&mut ws).await;
    assert_eq!(r["ok"], true);
    assert_eq!(r["data"]["status"], 200);
    assert!(r["data"]["body"]["saw"].as_str().unwrap().starts_with("POST /v1/looks"));
    assert_eq!(r["data"]["body"]["payload"], "{\"a\":1}");

    ws.send(Message::text(ask(3, "api", json!({"method": "GET", "path": "/etc/passwd"})))).await.unwrap();
    let r = next_text(&mut ws).await;
    assert_eq!((r["ok"].as_bool(), r["id"].as_u64()), (Some(false), Some(3)));
    assert!(r["error"].as_str().unwrap().contains("/v1/"));

    let data = base64::engine::general_purpose::STANDARD.encode(b"song bytes");
    ws.send(Message::text(ask(4, "open_files", json!({"files": [{"name": "Song A.pro", "data_b64": data}]})))).await.unwrap();
    let r = next_text(&mut ws).await;
    assert_eq!(r["ok"], true, "{r}");
    assert_eq!(r["data"]["opened"], 1);
    {
        let opened = rec.0.lock().unwrap();
        assert_eq!(opened.len(), 1);
        assert_eq!(std::fs::read(&opened[0]).unwrap(), b"song bytes");
    }

    ws.send(Message::text(ask(5, "open_files", json!({"files": [{"name": "../evil.pro", "data_b64": ""}]})))).await.unwrap();
    assert_eq!(next_text(&mut ws).await["ok"], false);
    ws.send(Message::text(ask(6, "nope", Value::Null))).await.unwrap();
    assert!(next_text(&mut ws).await["error"].as_str().unwrap().contains("no 'nope' operation"));
    let other = serde_json::to_string(&ToHelper::Call { id: 7, cap: "obs".into(), op: "x".into(), args: Value::Null }).unwrap();
    ws.send(Message::text(other)).await.unwrap();
    assert!(next_text(&mut ws).await["error"].as_str().unwrap().contains("no 'obs' capability"));

    ws.send(Message::text(serde_json::to_string(&ToHelper::Ping).unwrap())).await.unwrap();
    assert_eq!(next_text(&mut ws).await["type"], "pong");

    // Dropping the connection makes the helper dial again, and say hello again.
    drop(ws);
    let (s, _) = tokio::time::timeout(Duration::from_secs(8), listener.accept()).await.expect("helper reconnects").unwrap();
    let mut ws = tokio_tungstenite::accept_async(s).await.unwrap();
    assert_eq!(next_text(&mut ws).await["type"], "hello");

    // A refusal stops the helper for good.
    ws.send(Message::text(serde_json::to_string(&ToHelper::Refused { reason: "removed".into() }).unwrap())).await.unwrap();
    let ended = tokio::time::timeout(Duration::from_secs(5), helper).await.expect("helper stops").unwrap();
    assert!(ended.unwrap_err().contains("refused"));
}

#[tokio::test]
async fn api_port_must_be_set() {
    let rec = Arc::new(Recorder(Mutex::new(vec![])));
    let reg = registry(0, rec);
    let status = reg.call("propresenter", "status", Value::Null).await.unwrap();
    assert_eq!(status["api_configured"], false);
    let err = reg.call("propresenter", "api", json!({"path": "/v1/looks"})).await.unwrap_err();
    assert!(err.contains("network port"));
}

#[test]
fn file_names_and_api_paths() {
    for ok in ["Song.pro", "Amazing Grace (G).PRO", "é.pro"] {
        assert!(safe_name(ok).is_ok(), "{ok}");
    }
    for bad in ["", ".hidden.pro", "a/b.pro", "a\\b.pro", "C:evil.pro", "song.exe", "song", "a\0.pro", &"x".repeat(250)] {
        assert!(safe_name(bad).is_err(), "{bad:?}");
    }
    for ok in ["/v1/looks", "/v1/look/ABC-123/trigger", "/v1/presentation/active?x=1"] {
        assert!(check_api_path(ok).is_ok(), "{ok}");
    }
    for bad in ["", "v1/looks", "/v2/looks", "/v1/../etc", "/v1//x", "/v1/a b", "/v1/a#b", "/v1/@host", "/v1/x\r\nHost: evil"] {
        assert!(check_api_path(bad).is_err(), "{bad:?}");
    }
}

#[test]
fn config_round_trips_and_is_private() {
    let dir = std::env::temp_dir().join(format!("relay-helper-cfg-{}", uuid_like()));
    let path = dir.join("nested").join("helper.json");
    assert_eq!(Config::load(&path).unwrap(), Config::default());
    let cfg = Config { relay_url: "ws://x".into(), helper_id: "h".into(), token: "t".into(), name: "n".into(), tls_pin: "AB".repeat(32),
                       propresenter: ProPresenterConfig { api_host: "127.0.0.1".into(), api_port: 50402 } };
    cfg.save(&path).unwrap();
    assert_eq!(Config::load(&path).unwrap(), cfg);
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
    }
    std::fs::write(&path, "{ not json").unwrap();
    assert!(Config::load(&path).is_err());
    let _ = std::fs::remove_dir_all(dir);
}

#[test]
fn frames_have_stable_shapes() {
    let call: ToHelper = serde_json::from_str(r#"{"type":"call","id":9,"cap":"propresenter","op":"status"}"#).unwrap();
    assert_eq!(call, ToHelper::Call { id: 9, cap: "propresenter".into(), op: "status".into(), args: Value::Null });
    let ok = serde_json::to_value(FromHelper::ok(1, json!({"a": 1}))).unwrap();
    assert_eq!(ok, json!({"type": "result", "id": 1, "ok": true, "data": {"a": 1}}));
    let err = serde_json::to_value(FromHelper::err(2, "no")).unwrap();
    assert_eq!(err, json!({"type": "result", "id": 2, "ok": false, "error": "no"}));
}

/// A Relay with its own certificate: a TLS listener whose certificate the test makes, and that certificate's fingerprint.
async fn tls_relay() -> (AsyncListener, String, String) {
    let key = rcgen::generate_simple_self_signed(vec!["relay.test".to_string()]).unwrap();
    let der = key.cert.der().to_vec();
    let fp = tls::fingerprint_of(&der);
    let acceptor = tokio_rustls::TlsAcceptor::from(Arc::new(
        tokio_rustls::rustls::ServerConfig::builder_with_provider(Arc::new(tokio_rustls::rustls::crypto::ring::default_provider()))
            .with_safe_default_protocol_versions()
            .unwrap()
            .with_no_client_auth()
            .with_single_cert(vec![key.cert.der().clone()], tokio_rustls::rustls::pki_types::PrivateKeyDer::Pkcs8(key.signing_key.serialize_der().into()))
            .unwrap(),
    ));
    let inner = AsyncListener::bind("127.0.0.1:0").await.unwrap();
    let addr = inner.local_addr().unwrap();
    // The helper dials this port; each connection is wrapped in TLS, then handed to a plain WebSocket accept on a pipe.
    let front = AsyncListener::bind("127.0.0.1:0").await.unwrap();
    let url = format!("wss://127.0.0.1:{}", front.local_addr().unwrap().port());
    tokio::spawn(async move {
        loop {
            let Ok((tcp, _)) = front.accept().await else { return };
            let acceptor = acceptor.clone();
            let target = addr;
            tokio::spawn(async move {
                let Ok(mut tls_stream) = acceptor.accept(tcp).await else { return };
                let Ok(mut plain) = tokio::net::TcpStream::connect(target).await else { return };
                let _ = tokio::io::copy_bidirectional(&mut tls_stream, &mut plain).await;
            });
        }
    });
    (inner, url, fp)
}

#[tokio::test]
async fn a_pinned_certificate_is_the_only_one_trusted() {
    let (listener, url, fp) = tls_relay().await;
    tokio::spawn(async move {
        loop {
            let Ok((s, _)) = listener.accept().await else { return };
            tokio::spawn(async move {
                let Ok(mut ws) = tokio_tungstenite::accept_async(s).await else { return };
                let _ = next_text(&mut ws).await;
                let reply = ToHelper::Paired { helper_id: "h1".into(), token: "tok".into() };
                let _ = ws.send(Message::text(serde_json::to_string(&reply).unwrap())).await;
            });
        }
    });
    // The right fingerprint, written the way Relay's admin page shows it (colons), connects.
    let colons = fp.as_bytes().chunks(2).map(|c| std::str::from_utf8(c).unwrap()).collect::<Vec<_>>().join(":");
    let good = Config { relay_url: url.clone(), tls_pin: tls::clean_pin(&colons).unwrap(), ..Config::default() };
    assert_eq!(client::pair(good, "x").await.unwrap().token, "tok");
    // Another certificate's fingerprint is refused before anything is sent.
    let wrong = Config { relay_url: url.clone(), tls_pin: "00".repeat(32), ..Config::default() };
    assert!(client::pair(wrong, "x").await.unwrap_err().starts_with("Cannot reach Relay"));
    // No pin: a self-signed certificate is not one the public roots vouch for.
    let none = Config { relay_url: url, ..Config::default() };
    assert!(client::pair(none, "x").await.is_err());
}

#[test]
fn fingerprints_are_cleaned() {
    let fp = "AB".repeat(32);
    assert_eq!(tls::clean_pin(&fp.to_lowercase()).unwrap(), fp);
    assert!(tls::clean_pin(&"ab:".repeat(31)).is_err(), "62 digits is not a SHA-256");
    assert!(tls::clean_pin("not a fingerprint").is_err());
    assert!(tls::clean_pin(&"G".repeat(64)).is_err());
    assert_eq!(tls::fingerprint_of(b""), "E3B0C44298FC1C149AFBF4C8996FB92427AE41E4649B934CA495991B7852B855");
}

#[tokio::test]
async fn a_connection_that_goes_quiet_is_dropped_so_run_dials_again() {
    let (listener, url) = relay_listener().await;
    let cfg = Config { relay_url: url, helper_id: "h1".into(), token: "tok".into(), name: "n".into(), ..Config::default() };
    let reg = registry(1, Arc::new(Recorder(Mutex::new(vec![]))));
    let keep = client::Keepalive { ping_every: Duration::from_millis(100), dead_after: Duration::from_millis(400) };
    let helper = tokio::spawn(async move { client::session_with(&cfg, &reg, keep).await });
    // The "Relay" accepts the socket at the TCP level and then reads and answers nothing: what a sleeping laptop's
    // half-open connection looks like from the other side.
    let (s, _) = listener.accept().await.unwrap();
    let ws = tokio_tungstenite::accept_async(s).await.unwrap();
    let (_sink, _stream) = ws.split();
    let ended = tokio::time::timeout(Duration::from_secs(4), helper).await.expect("the helper gives up on silence").unwrap();
    assert_eq!(ended, Ok(false));
}

#[test]
fn the_login_entries_name_the_program() {
    let exe = std::path::Path::new("/Applications/Relay Helper/relay-helper");
    let log = std::path::Path::new("/Users/a&b/Library/Logs/Relay Helper.log");
    let plist = autostart::plist(exe, Some(std::path::Path::new("/tmp/x<y>.json")), log);
    assert!(plist.contains("<string>org.relay.helper</string>"));
    assert!(plist.contains("<string>/Applications/Relay Helper/relay-helper</string>\n    <string>run</string>\n    <string>--background</string>"));
    assert!(plist.contains("<key>RunAtLoad</key>\n  <true/>") && plist.contains("<key>KeepAlive</key>\n  <true/>"));
    assert!(plist.contains("/tmp/x&lt;y&gt;.json") && plist.contains("/Users/a&amp;b/"));
    assert!(!autostart::plist(exe, None, log).contains("EnvironmentVariables"));
    assert_eq!(
        autostart::launch_agent_path(std::path::Path::new("/Users/a")),
        std::path::PathBuf::from("/Users/a/Library/LaunchAgents/org.relay.helper.plist")
    );
    assert_eq!(autostart::run_value(std::path::Path::new(r"C:\Program Files\Relay Helper\relay-helper.exe")),
               r#""C:\Program Files\Relay Helper\relay-helper.exe" run --background"#);
}
