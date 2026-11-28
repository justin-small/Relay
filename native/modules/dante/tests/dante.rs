//! The Dante module (`tests/test_dante.py` ported): the inventory folded from mDNS answers (as the field survey found them), the control-port check against
//! a real UDP socket on loopback, what a device says about itself, the gear it offers, a whole scan against a fake responder and a fake device, and the
//! module switched on and off at run time.

mod fake;

use std::sync::Arc;
use std::time::Instant;

use axum::http::{Method, StatusCode};
use fake::{announcement, rig, rig_with, quick, responder, until, FakeDevice};
use relay_core::caps::{DeviceList, GearFeed, InputLabels, NoSoundSteps, DANTE_ROUTING, GEAR_DANTE, INPUT_LABELS, NOSOUND_DANTE};
use relay_core::{Config, HealthState, Host, Module, Registry, Shutdown};
use relay_module_dante::arc;
use relay_module_dante::heartbeat::{self, Heartbeats};
use relay_module_dante::inventory::{inventory, split_channel, ARC, CHAN, CMC};
use relay_module_dante::mdns::Found;
use relay_module_dante::service::probe_packet;
use serde_json::{json, Value};
use tokio::net::UdpSocket;

fn found(service: &str, label: &str, host: &str, port: u16, ips: &[&str], txt: &[(&str, &str)]) -> Found {
    let mut f = Found::new(service, &format!("{label}.{service}"));
    f.host = host.to_owned();
    f.port = Some(port);
    f.ips = ips.iter().map(|s| (*s).to_owned()).collect();
    f.txt = txt.iter().map(|(k, v)| ((*k).to_owned(), (*v).to_owned())).collect();
    f
}

fn survey() -> Vec<Value> {
    inventory(&[
        found(ARC, "DT168-0a1b2c", "dt168-0a1b2c.local", 4440, &["10.9.0.21"], &[("arcp_vers", "2.8.12")]),
        found(CMC, "DT168-0a1b2c", "dt168-0a1b2c.local", 8800, &["10.9.0.21"], &[]),
        found(CHAN, "Lead Vox@DT168-0a1b2c", "dt168-0a1b2c.local", 4455, &["10.9.0.21"], &[]),
        found(CHAN, "Pastor @ Pulpit@DT168-0a1b2c", "dt168-0a1b2c.local", 4455, &["10.9.0.21"], &[]),
        found(ARC, "STAGE-MAC", "stage-mac.local", 4440, &["10.9.0.40"], &[("model", "DvsMacOs")]),
        found(ARC, "stage-mac", "stage-mac-lan.local", 4440, &["192.168.1.40", "fe80::1"], &[("model", "DvsMacOs")]),
        found(ARC, "BOOTH-PC", "booth-pc.local", 24440, &["10.9.0.50"], &[("model", "ViaWin"), ("router_info", "Dante Via")]),
        found("_ndi._tcp.local", "CAM 1", "cam1.local", 5961, &["10.0.0.30"], &[]),
    ])
}

#[test]
fn the_inventory_is_keyed_by_the_dante_name() {
    let inv = survey();
    let names: Vec<&str> = inv.iter().map(|d| d["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["BOOTH-PC", "DT168-0a1b2c", "STAGE-MAC"], "one record per Dante name, NDI left out");
    let stage = &inv[1];
    assert_eq!(stage["ports"], json!({"arc": 4440, "cmc": 8800, "chan": 4455}), "ports come from SRV");
    assert_eq!(stage["transmit"], json!(["Lead Vox", "Pastor @ Pulpit"]), "channels, with an @ in a name kept");
    assert_eq!(stage["arc_version"], json!("2.8.12"));
    assert_eq!(inv[2]["ips"], json!(["10.9.0.40", "192.168.1.40"]), "a computer on two ports is one device (case ignored), IPv4 only");
    assert_eq!((inv[0]["ports"]["arc"].clone(), inv[0]["via"].clone()), (json!(24440), json!(true)), "Via uses its own control port and is marked");
    assert_eq!(stage["via"], json!(false), "a stage box is not Via");
    assert_eq!(split_channel("A@B@Box"), ("A@B".to_owned(), "Box".to_owned()));
    assert_eq!(split_channel("Box"), (String::new(), "Box".to_owned()));
}

#[tokio::test]
async fn the_control_port_check_talks_to_a_real_socket() {
    assert_eq!(probe_packet(7), [0x27, 0xFF, 0, 10, 0, 7, 0x10, 0, 0, 0], "the probe packet is the ARC header");
    let r = rig().await;
    let answering = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let silent = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let port = answering.local_addr().unwrap().port();
    let heard = tokio::spawn(async move {
        let mut buf = vec![0u8; 2048];
        let (n, from) = answering.recv_from(&mut buf).await.unwrap();
        let data = buf[..n].to_vec();
        let mut back = data[..8].to_vec();
        back.extend_from_slice(&[0x10, 0, 0, 0]);
        answering.send_to(&back, from).await.unwrap();
        data
    });
    assert!(r.service().probe("127.0.0.1", port, 3, std::time::Duration::from_secs(2)).await.unwrap(), "an answering device is reachable");
    assert_eq!(heard.await.unwrap(), probe_packet(3), "it was sent the probe");
    assert!(!r.service().probe("127.0.0.1", silent.local_addr().unwrap().port(), 4, std::time::Duration::from_millis(300)).await.unwrap(), "a silent device is not");
}

#[tokio::test]
async fn what_a_device_says_about_itself() {
    // Layouts read from a real DT168 on 2026-11-20, names made up.
    let mut counts = 0x07F9u16.to_be_bytes().to_vec();
    counts.extend_from_slice(&[0, 16, 0, 8]);
    counts.extend_from_slice(&[0; 16]);
    assert_eq!(arc::parse_counts(Some(&counts)), Some(json!({"tx": 16, "rx": 8})), "0x1000 gives transmit and receive counts");
    assert_eq!(arc::parse_counts(Some(&[0, 0])), None, "a short answer is no reading");
    assert_eq!(arc::parse_counts(Some(&[0, 0, 0, 0, 0xEA, 0x60, 0, 8])), None, "an absurd count is no reading");
    assert_eq!(arc::parse_name(Some(b"Test-Box\0")).as_deref(), Some("Test-Box"), "0x1002 is the Dante name");

    let mut info = vec![0u8; 80];
    for (at, v) in [(6usize, 10 + 20), (12, 10 + 40), (14, 10 + 60)] {
        info[at..at + 2].copy_from_slice(&u16::try_from(v).unwrap().to_be_bytes()); // pointers are offsets in the whole packet
    }
    info[20..33].copy_from_slice(b"Audinate DCM\0");
    info[40..49].copy_from_slice(b"Test-Box\0");
    info[60..70].copy_from_slice(b"TB-123456\0");
    assert_eq!(arc::parse_info(Some(&info)), Some(json!({"board": "Audinate DCM", "name": "Test-Box", "hostname": "TB-123456"})), "0x1003 gives board, name and hostname");

    let mut rates = vec![0u8; 100];
    rates[2..6].copy_from_slice(&[0x02, 0x01, 0, 1]);
    rates[6..8].copy_from_slice(&arc::RATE_TAG.to_be_bytes());
    rates[8..10].copy_from_slice(&70u16.to_be_bytes());
    rates[60..64].copy_from_slice(&48000u32.to_be_bytes());
    assert_eq!(arc::parse_sample_rate(Some(&rates)), Some(48000), "0x1100 gives the sample rate");
    rates[60..64].copy_from_slice(&1234u32.to_be_bytes());
    assert_eq!(arc::parse_sample_rate(Some(&rates)), None, "a rate Dante does not run at is no reading");
    assert_eq!(arc::parse_sample_rate(Some(&[0u8; 20])), None, "no table is no reading");

    // read_info against a real socket: the device answers all three requests.
    let r = rig().await;
    let sock = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    let port = sock.local_addr().unwrap().port();
    rates[60..64].copy_from_slice(&96000u32.to_be_bytes());
    let device = tokio::spawn(async move {
        let mut buf = vec![0u8; 2048];
        for _ in 0..3 {
            let (n, from) = sock.recv_from(&mut buf).await.unwrap();
            let (seq, cmd) = (u16::from_be_bytes([buf[4], buf[5]]), u16::from_be_bytes([buf[6], buf[7]]));
            assert!(n >= 10);
            let payload: &[u8] = match cmd {
                arc::CMD_COUNTS => &counts,
                arc::CMD_INFO => &info,
                _ => &rates,
            };
            sock.send_to(&arc::reply(cmd, seq, payload, 1, arc::PROTOCOL), from).await.unwrap();
        }
    });
    let got = arc::read_info(r.host.net(), "127.0.0.1", port, 10, std::time::Duration::from_secs(1)).await.unwrap();
    device.await.unwrap();
    assert_eq!(Value::Object(got), json!({"tx": 16, "rx": 8, "board": "Audinate DCM", "name": "Test-Box", "hostname": "TB-123456", "sample_rate": 96000}), "read_info asks a device and keeps what it can read");
}

#[test]
fn the_heartbeat_says_a_device_is_alive_without_asking_it() {
    let hb = Heartbeats::new();
    let base = Instant::now();
    let ip = |s: &str| vec![s.to_owned()];
    assert!(hb.feed(&heartbeat::packet(&[1; 8], None, None, None, 0, 0), "192.168.1.9", base), "a heartbeat Relay can read is kept");
    let later = |s: u64| base + std::time::Duration::from_secs(s);
    assert!(hb.seen(&ip("192.168.1.9"), later(1)), "alive just after it");
    assert!(!hb.seen(&ip("192.168.1.9"), base + heartbeat::MAX_AGE + std::time::Duration::from_secs(1)), "not alive once it has gone quiet");
    assert!(!hb.seen(&ip("192.168.1.10"), later(1)), "another address is not it");
    assert!(hb.seen(&["10.0.0.1".to_owned(), "192.168.1.9".to_owned()], later(1)), "a computer on two ports is alive by either address");
}

#[tokio::test]
async fn the_gear_it_offers_and_its_status() {
    let r = rig().await;
    let s = r.service();
    assert_eq!(s.gear_list(), None, "nothing before the first scan");
    assert_eq!(Module::health(&*r.module).state, HealthState::Warn, "it says something before a scan");
    s.record(survey());
    let offer = s.gear_list().unwrap();
    let names: Vec<&str> = offer["devices"].as_array().unwrap().iter().map(|d| d["name"].as_str().unwrap()).collect();
    assert_eq!(names, ["BOOTH-PC", "DT168-0a1b2c", "STAGE-MAC"], "a box per device");
    assert!(offer["devices"][0]["tags"].as_array().unwrap().contains(&json!("Dante Via")), "Via is tagged");
    let g = &offer["channels"][1];
    let items: Vec<(String, String, String)> = g["items"].as_array().unwrap().iter().map(|c| (c["key"].as_str().unwrap().to_owned(), c["kind"].as_str().unwrap().to_owned(), c["direction"].as_str().unwrap().to_owned())).collect();
    assert_eq!(items, [("dt168-0a1b2c:tx:Lead Vox".into(), "audio".into(), "source".into()), ("dt168-0a1b2c:tx:Pastor @ Pulpit".into(), "audio".into(), "source".into())], "channels are audio sources keyed under the device");
    assert_eq!(g["within"], json!("dt168-0a1b2c:"), "limited to that device's keys");
    assert_eq!(Module::health(&*r.module).state, HealthState::Ok);
    s.record(Vec::new());
    let snap = s.snapshot();
    assert_eq!((snap["devices"].as_array().unwrap().len(), snap["problems"].as_array().unwrap().len()), (0, 0), "empty before a scan");
}

#[tokio::test]
async fn a_whole_scan_finds_a_device_probes_it_and_reads_it() {
    let device = FakeDevice::start(&[(1, "Lead Vox@STAGE"), (2, "")]).await;
    // The fake announces itself the way the survey found a stage box: ARC on the port the fake listens on, its channels, and one address.
    let (group, _task) = responder(announcement("STAGE", "stage", device.port, [127, 0, 0, 1], &["Lead Vox", "Pastor"])).await;
    let mut tuning = quick();
    tuning.mdns.group = group;
    tuning.mdns.seconds = 0.4;
    let r = rig_with(tuning).await;
    r.board.local.lock().unwrap().clear(); // ask through the class's own port, not an address of this machine
    r.service().scan_once().await;
    let snap = r.service().snapshot();
    assert_eq!(snap["error"], json!(""));
    let d = &snap["devices"][0];
    assert_eq!((d["name"].clone(), d["transmit"].clone(), d["ips"].clone()), (json!("STAGE"), json!(["Lead Vox", "Pastor"]), json!(["127.0.0.1"])));
    assert_eq!((d["ports"]["arc"].clone(), d["model"].clone(), d["arc_version"].clone()), (json!(device.port), json!("DT168"), json!("2.8.12")), "the port and what it says about itself come from mDNS");
    assert_eq!((d["reachable"].clone(), d["online"].clone()), (json!(true), json!(true)), "its control port answered");
    assert_eq!(d["clock"]["locked"], json!(true), "its clock was read");
    assert_eq!(d["subscriptions"][0], json!({"channel": 1, "source": "Lead Vox@STAGE", "status": "ok"}), "and its subscriptions");
    assert_eq!((snap["reachable"].clone(), snap["unreachable"].clone(), snap["leader"].clone()), (json!(1), json!(0), json!("STAGE")));
    assert!(snap["scanned"].as_f64().unwrap() > 0.0);

    device.lock().up = false;
    r.service().scan_once().await;
    assert_eq!(r.service().snapshot()["devices"][0]["reachable"], json!(false), "a device that stops answering is unreachable");
    assert_eq!(Module::health(&*r.module).state, HealthState::Error, "and that is a problem, with its card");
}

#[tokio::test]
async fn a_scan_with_no_dante_network_port_says_so() {
    let r = fake::rig_on(quick(), relay_core::Net::new(Arc::new(fake::NoPort))).await;
    r.service().scan_once().await;
    let snap = r.service().snapshot();
    assert_eq!(snap["error"], json!("The Dante network port is not available."), "the page shows why nothing was found");
    assert_eq!(Module::health(&*r.module).state, HealthState::Error);
    // Probing a device through it is an error, not "does not answer".
    assert!(r.service().probe("10.9.0.21", 4440, 1, std::time::Duration::from_millis(50)).await.is_err());
}

#[tokio::test]
async fn switched_on_it_offers_what_core_asks_for_and_switched_off_it_leaves_nothing() {
    let r = rig().await;
    let host: &Host = &r.host;
    assert!(host.using::<GearFeed>(GEAR_DANTE).is_none() && host.using::<InputLabels>(INPUT_LABELS).is_none(), "off: nothing is offered");
    r.module.start(host.clone(), json!({})).await.unwrap();
    assert!(r.service().running());
    assert!(host.using::<GearFeed>(GEAR_DANTE).is_some() && host.using::<DeviceList>(DANTE_ROUTING).is_some(), "on: the gear and the devices are offered");
    let steps = host.using::<NoSoundSteps>(NOSOUND_DANTE).expect("the walk's Dante steps");
    assert_eq!(steps.order, 10, "the Dante steps are nearest Relay");
    assert!((steps.steps)(&json!({"device": "Built-in Mic"})).is_empty(), "nothing is added for an input that is not Dante");
    // Heartbeats are heard on the port the module opened.
    let addr = std::net::SocketAddr::from(([127, 0, 0, 1], until_listening(&r).await.port())); // it listens on every address; the device is on loopback
    let tx = UdpSocket::bind("127.0.0.1:0").await.unwrap();
    tx.send_to(b"not a heartbeat", addr).await.unwrap();
    tx.send_to(&heartbeat::packet(&[1; 8], Some(&[4, 6]), None, None, 0, 0), addr).await.unwrap();
    let ips = vec!["127.0.0.1".to_owned()];
    assert!(until(3.0, || r.service().heartbeats.levels(&ips, Instant::now()).is_some()).await, "the heartbeat arrives");
    assert_eq!(r.service().heartbeats.levels(&ips, Instant::now()), Some(vec![Some(-2), Some(-3)]), "read off the wire");
    let begun = Instant::now();
    r.module.stop().await;
    assert!(begun.elapsed() < std::time::Duration::from_secs(2), "stopping joins nothing: it ends tasks");
    assert!(!r.service().running() && host.using::<GearFeed>(GEAR_DANTE).is_none(), "off again: nothing left offered");
    assert!(r.service().heartbeats.levels(&ips, Instant::now()).is_none(), "and what was heard is forgotten");
}

async fn until_listening(r: &fake::Rig) -> std::net::SocketAddr {
    assert!(until(3.0, || r.service().heartbeats.local().is_some()).await, "the module listens for heartbeats");
    r.service().heartbeats.local().unwrap()
}

#[tokio::test]
async fn the_admin_routes_with_the_module_on_and_off() {
    let dir = std::env::temp_dir().join(format!("relay-dante-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.json"), "{}").unwrap();
    let config = Arc::new(Config::load(&dir.join("config.json")).unwrap());
    let r = rig().await; // for its authorizer and database
    let mut registry = Registry::new(config, r.host.clone());
    let module: Arc<dyn Module> = Arc::new(relay_module_dante::Dante::new().with_tuning(quick()));
    registry.register(module).unwrap();
    let registry = Arc::new(registry);
    let send = |method: Method, uri: &'static str| {
        let router = registry.routes();
        async move {
            use tower::ServiceExt;
            let mut req = axum::http::Request::builder().method(method).uri(uri).header("host", "relay.local:8443").body(axum::body::Body::empty()).unwrap();
            req.extensions_mut().insert(relay_core::access::Peer([10, 0, 0, 9].into()));
            router.oneshot(req).await.unwrap().status()
        }
    };
    assert_eq!(send(Method::GET, "/api/admin/dante").await, StatusCode::NOT_FOUND, "it answers 404 when off");
    registry.set_enabled("dante", true).await.unwrap();
    assert_eq!(send(Method::GET, "/api/admin/dante").await, StatusCode::OK, "it reads when on");
    r.role("viewer");
    assert_eq!(send(Method::GET, "/api/admin/dante").await, StatusCode::OK, "a viewer can look");
    assert_eq!(send(Method::POST, "/api/admin/dante/scan").await, StatusCode::FORBIDDEN, "a viewer cannot scan");
    r.role("admin");
    assert_eq!(send(Method::POST, "/api/admin/dante/scan").await, StatusCode::OK, "an admin can");
    registry.set_enabled("dante", false).await.unwrap();
    assert_eq!(send(Method::GET, "/api/admin/dante").await, StatusCode::NOT_FOUND, "it answers 404 when off again");
    registry.stop_all().await;
    let _ = std::fs::remove_dir_all(&dir);
    let _ = Shutdown::new();
}

#[tokio::test]
async fn the_state_route_reads_what_the_page_reads() {
    let r = rig().await;
    r.service().record(survey());
    let (status, body) = r.get("/api/admin/dante").await;
    assert_eq!(status, StatusCode::OK);
    let mut keys: Vec<&str> = body.as_object().unwrap().keys().map(String::as_str).collect();
    keys.sort_unstable();
    assert_eq!(keys, ["devices", "error", "leader", "levels", "online", "problems", "reachable", "running", "scanned", "unreachable"]);
    assert_eq!(body["devices"].as_array().unwrap().len(), 3);
    r.role("viewer");
    assert_eq!(r.post("/api/admin/dante/scan", json!({})).await.0, StatusCode::FORBIDDEN, "a viewer cannot scan");
    r.role("admin");
    let (status, _) = r.post("/api/admin/dante/scan", json!({})).await;
    assert_eq!(status, StatusCode::OK, "a scan with the loop off answers");
    let notes = r.board.notes.lock().unwrap().clone();
    assert!(notes.iter().any(|n| n.0 == "dante.scan"), "and is written to the audit log: {notes:?}");
}
