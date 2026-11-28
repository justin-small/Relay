//! A fake Dante device and a fake mDNS responder on loopback UDP sockets, and a rig that gives the module recording stand-ins for what core offers it
//! (alert buttons, the audit log, this computer's network ports, a database).
#![allow(dead_code)]

use std::collections::{BTreeMap, HashMap};
use std::net::SocketAddr;
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::Duration;

use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{Method, Request, StatusCode};
use axum::response::Response;
use http_body_util::BodyExt;
use relay_core::access::Peer;
use relay_core::caps::*;
use relay_core::future::BoxFuture;
use relay_core::{Authorizer, Host, SettingsStore, Shutdown};
use relay_module_dante::arc;
use relay_module_dante::mdns::Ask;
use relay_module_dante::service::Tuning;
use relay_module_dante::{migrate, Dante};
use serde_json::{json, Value};
use sqlx::sqlite::SqlitePoolOptions;
use sqlx::SqlitePool;
use tokio::net::UdpSocket;
use tower::ServiceExt;

// ------------------------------------------------------------------------------------------------------------------- a Dante device
pub struct DeviceState {
    pub locked: bool,
    pub leader: bool,
    pub errors: u16,
    /// Receive channel to (source, status low byte): 10 subscribed, 0 none, 4 does not resolve.
    pub subs: BTreeMap<u16, (String, u16)>,
    pub up: bool,
    pub refuse: bool,
    pub ignore_writes: bool,
    /// 0x3410 writes received.
    pub pages: u32,
    /// Whether it answers the channel counts (0x1000).
    pub answers_info: bool,
}

pub struct FakeDevice {
    pub port: u16,
    state: Mutex<DeviceState>,
    task: tokio::task::JoinHandle<()>,
}

impl Drop for FakeDevice {
    fn drop(&mut self) {
        self.task.abort();
    }
}

fn string_at(data: &[u8], at: usize) -> String {
    let end = data[at..].iter().position(|b| *b == 0).map_or(data.len(), |e| at + e);
    String::from_utf8_lossy(&data[at..end]).into_owned()
}

fn written(data: &[u8], chan_at: usize, dev_at: usize) -> String {
    if chan_at == 0 { String::new() } else { format!("{}@{}", string_at(data, chan_at), string_at(data, dev_at)) }
}

impl FakeDevice {
    pub async fn start(subs: &[(u16, &str)]) -> Arc<Self> {
        let sock = UdpSocket::bind("127.0.0.1:0").await.expect("bind");
        let port = sock.local_addr().expect("addr").port();
        let state = DeviceState {
            locked: true,
            leader: true,
            errors: 0,
            subs: subs.iter().map(|(c, s)| (*c, ((*s).to_owned(), if s.is_empty() { 0 } else { 10 }))).collect(),
            up: true,
            refuse: false,
            ignore_writes: false,
            pages: 0,
            answers_info: true,
        };
        // The task needs the state, and the device owns the task: the state is reached through a pointer filled in once the device exists.
        let cell: Arc<Mutex<Option<std::sync::Weak<FakeDevice>>>> = Arc::new(Mutex::new(None));
        let me = cell.clone();
        let task = tokio::spawn(async move {
            let mut buf = vec![0u8; 2048];
            loop {
                let Ok((n, from)) = sock.recv_from(&mut buf).await else { continue };
                let Some(dev) = me.lock().unwrap().as_ref().and_then(std::sync::Weak::upgrade) else { continue };
                if let Some(answer) = dev.answer(&buf[..n]) {
                    let _ = sock.send_to(&answer, from).await;
                }
            }
        });
        let dev = Arc::new(Self { port, state: Mutex::new(state), task });
        *cell.lock().unwrap() = Some(Arc::downgrade(&dev));
        dev
    }

    pub fn lock(&self) -> MutexGuard<'_, DeviceState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// Set what receive channel `ch` is subscribed to.
    pub fn subscribe(&self, ch: u16, source: &str) {
        self.lock().subs.insert(ch, (source.to_owned(), if source.is_empty() { 0 } else { 10 }));
    }

    /// Set a receive channel's source and the status the device reports for it.
    pub fn subscribe_as(&self, ch: u16, source: &str, status: u16) {
        self.lock().subs.insert(ch, (source.to_owned(), status));
    }

    pub fn source(&self, ch: u16) -> String {
        self.lock().subs.get(&ch).map(|s| s.0.clone()).unwrap_or_default()
    }

    fn answer(&self, data: &[u8]) -> Option<Vec<u8>> {
        let mut st = self.lock();
        if !st.up || data.len() < 10 {
            return None;
        }
        let proto = u16::from_be_bytes([data[0], data[1]]);
        let seq = u16::from_be_bytes([data[4], data[5]]);
        let cmd = u16::from_be_bytes([data[6], data[7]]);
        match cmd {
            arc::CMD_CLOCK => {
                let mut p = vec![u8::from(st.leader) | if st.locked { 2 } else { 0 }, 0];
                p.extend_from_slice(&st.errors.to_be_bytes());
                p.extend_from_slice(&1000u32.to_be_bytes());
                p.extend_from_slice(&[0, 0]);
                Some(arc::reply(cmd, seq, &p, 1, arc::PROTOCOL))
            }
            arc::CMD_SUBSCRIPTIONS => {
                let first = usize::from(u16::from_be_bytes([data[12], data[13]])).saturating_sub(1);
                let rows: Vec<(u16, String, u16)> = st.subs.iter().map(|(c, (s, status))| (*c, s.clone(), *status)).collect();
                let page: Vec<(u16, String, u16)> = rows.iter().skip(first).take(arc::PAGE).cloned().collect();
                let (payload, result) = arc::rx_page(&page, first + arc::PAGE < rows.len());
                Some(arc::reply(cmd, seq, &payload, result, arc::PROTOCOL))
            }
            arc::CMD_COUNTS if st.answers_info => {
                let mut p = 0x07F9u16.to_be_bytes().to_vec();
                p.extend_from_slice(&16u16.to_be_bytes());
                p.extend_from_slice(&8u16.to_be_bytes());
                p.extend_from_slice(&[0; 16]);
                Some(arc::reply(cmd, seq, &p, 1, arc::PROTOCOL))
            }
            arc::CMD_INFO if st.answers_info => {
                let mut p = vec![0u8; 80];
                for (at, v) in [(6usize, 10 + 20), (12, 10 + 40), (14, 10 + 60)] {
                    p[at..at + 2].copy_from_slice(&u16::try_from(v).unwrap().to_be_bytes());
                }
                p[20..33].copy_from_slice(b"Audinate DCM\0");
                p[40..46].copy_from_slice(b"Device");
                p[60..70].copy_from_slice(b"DT-123456\0");
                Some(arc::reply(cmd, seq, &p, 1, arc::PROTOCOL))
            }
            arc::CMD_RATES if st.answers_info => {
                let mut p = vec![0u8; 100];
                p[2..4].copy_from_slice(&0x0201u16.to_be_bytes());
                p[4..6].copy_from_slice(&1u16.to_be_bytes());
                p[6..8].copy_from_slice(&arc::RATE_TAG.to_be_bytes());
                p[8..10].copy_from_slice(&70u16.to_be_bytes());
                p[60..64].copy_from_slice(&48000u32.to_be_bytes());
                Some(arc::reply(cmd, seq, &p, 1, arc::PROTOCOL))
            }
            arc::CMD_SET_SUBSCRIPTION => {
                if st.refuse {
                    return Some(arc::reply(cmd, seq, &[], 7, proto));
                }
                let (ch, chan_at, dev_at) = (u16::from(data[13]), usize::from(u16::from_be_bytes([data[14], data[15]])), usize::from(u16::from_be_bytes([data[16], data[17]])));
                let source = written(data, chan_at, dev_at);
                if !st.ignore_writes {
                    let status = if source.is_empty() { 0 } else { 10 };
                    st.subs.insert(ch, (source, status));
                }
                Some(arc::reply(cmd, seq, &[], 1, proto))
            }
            arc::CMD_SET_SUBSCRIPTION_PAGE => {
                st.pages += 1;
                if st.refuse || proto != arc::PROTOCOL_MODERN {
                    return Some(arc::reply(cmd, seq, &[], 7, proto));
                }
                for i in 0..usize::from(data[19]) {
                    let at = 20 + 8 * i;
                    let (ch, chan_at, dev_at) = (u16::from(data[at + 1]), usize::from(u16::from_be_bytes([data[at + 4], data[at + 5]])), usize::from(u16::from_be_bytes([data[at + 6], data[at + 7]])));
                    let source = written(data, chan_at, dev_at);
                    if !st.ignore_writes {
                        let status = if source.is_empty() { 0 } else { 10 };
                        st.subs.insert(ch, (source, status));
                    }
                }
                Some(arc::reply(cmd, seq, &[], 1, proto))
            }
            _ => None,
        }
    }
}

// ------------------------------------------------------------------------------------------------------------------- a fake mDNS responder
fn name(out: &mut Vec<u8>, n: &str) {
    for label in n.trim_end_matches('.').split('.') {
        out.push(u8::try_from(label.len()).unwrap());
        out.extend_from_slice(label.as_bytes());
    }
    out.push(0);
}

fn dns_record(out: &mut Vec<u8>, owner: &str, rtype: u16, data: &[u8]) {
    name(out, owner);
    out.extend_from_slice(&rtype.to_be_bytes());
    out.extend_from_slice(&0x8001u16.to_be_bytes());
    out.extend_from_slice(&120u32.to_be_bytes());
    out.extend_from_slice(&u16::try_from(data.len()).unwrap().to_be_bytes());
    out.extend_from_slice(data);
}

/// An mDNS answer announcing a device: its ARC, control and channel instances with SRV and TXT, and the host's address.
pub fn announcement(device: &str, host: &str, arc_port: u16, ip: [u8; 4], channels: &[&str]) -> Vec<u8> {
    let mut records: Vec<u8> = Vec::new();
    let mut count = 0u16;
    let mut add = |owner: &str, rtype: u16, data: Vec<u8>| {
        dns_record(&mut records, owner, rtype, &data);
        count += 1;
    };
    let mut instance = |service: &str, label: &str, port: u16, txt: &[&str]| {
        let full = format!("{label}.{service}");
        let mut ptr = Vec::new();
        name(&mut ptr, &full);
        add(service, 12, ptr);
        let mut srv = vec![0, 0, 0, 0];
        srv.extend_from_slice(&port.to_be_bytes());
        name(&mut srv, &format!("{host}.local"));
        add(&full, 33, srv);
        let mut t = Vec::new();
        for item in txt {
            t.push(u8::try_from(item.len()).unwrap());
            t.extend_from_slice(item.as_bytes());
        }
        if t.is_empty() {
            t.push(0);
        }
        add(&full, 16, t);
    };
    instance("_netaudio-arc._udp.local", device, arc_port, &["arcp_vers=2.8.12", "model=DT168", "mf=Allen & Heath"]);
    instance("_netaudio-cmc._udp.local", device, 8800, &[]);
    for c in channels {
        instance("_netaudio-chan._udp.local", &format!("{c}@{device}"), 4455, &[]);
    }
    add(&format!("{host}.local"), 1, ip.to_vec());
    let mut out = vec![0, 0, 0x84, 0, 0, 0];
    out.extend_from_slice(&count.to_be_bytes());
    out.extend_from_slice(&[0, 0, 0, 0]);
    out.extend(records);
    out
}

/// A responder on loopback that answers any question with `answer`, straight back to whoever asked. Returns where it listens.
pub async fn responder(answer: Vec<u8>) -> (SocketAddr, tokio::task::JoinHandle<()>) {
    let sock = UdpSocket::bind("127.0.0.1:0").await.expect("bind");
    let addr = sock.local_addr().expect("addr");
    let task = tokio::spawn(async move {
        let mut buf = vec![0u8; 2048];
        loop {
            let Ok((_, from)) = sock.recv_from(&mut buf).await else { continue };
            let _ = sock.send_to(&answer, from).await;
        }
    });
    (addr, task)
}

// ------------------------------------------------------------------------------------------------------------------- stand-ins for core
#[derive(Default)]
pub struct Board {
    pub presses: Mutex<Vec<(String, String)>>,
    pub cancels: Mutex<Vec<String>>,
    pub on: Mutex<Vec<String>>,
    pub notes: Mutex<Vec<(String, String)>>,
    pub refuse: Mutex<Vec<&'static str>>,
    pub local: Mutex<Vec<LocalPort>>,
    pub buttons: Mutex<Vec<(String, String)>>,
}

pub struct Buttons(pub Arc<Board>);
impl AlertButtons for Buttons {
    fn buttons(&self) -> Vec<(String, String)> {
        self.0.buttons.lock().unwrap().clone()
    }
    fn exists(&self, id: &str) -> bool {
        self.0.buttons.lock().unwrap().iter().any(|b| b.0 == id)
    }
    fn is_on(&self, id: &str) -> bool {
        self.0.on.lock().unwrap().iter().any(|x| x == id)
    }
    fn press(&self, id: &str, sender: &str) -> bool {
        self.0.presses.lock().unwrap().push((id.to_owned(), sender.to_owned()));
        self.0.on.lock().unwrap().push(id.to_owned());
        true
    }
    fn cancel(&self, id: &str) -> bool {
        self.0.cancels.lock().unwrap().push(id.to_owned());
        self.0.on.lock().unwrap().retain(|x| x != id);
        true
    }
}

impl Authorizer for Board {
    fn require<'a>(&'a self, _r: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async move {
            if self.refuse.lock().unwrap().contains(&permission) {
                let mut r = Response::new(Body::empty());
                *r.status_mut() = StatusCode::FORBIDDEN;
                return Err(r);
            }
            Ok(())
        })
    }
    fn note<'a>(&'a self, _r: &'a Parts, action: &'a str, summary: &'a str, _t: &'a str, _d: Option<Value>) -> BoxFuture<'a, ()> {
        Box::pin(async move { self.notes.lock().unwrap().push((action.to_owned(), summary.to_owned())) })
    }
    fn who<'a>(&'a self, _r: &'a Parts) -> BoxFuture<'a, Option<String>> {
        Box::pin(async { Some("op1".to_owned()) })
    }
}

pub struct Store(pub Mutex<HashMap<String, Value>>);

impl SettingsStore for Store {
    fn read(&self, key: &str) -> Option<Value> {
        self.0.lock().unwrap().get(key).cloned()
    }
    fn save(&self, key: &str, changes: &Value) -> Result<(), relay_core::AppError> {
        let mut m = self.0.lock().unwrap();
        let mut cur = m.get(key).cloned().unwrap_or_else(|| json!({}));
        for (k, v) in changes.as_object().into_iter().flatten() {
            cur[k] = v.clone();
        }
        m.insert(key.to_owned(), cur);
        Ok(())
    }
    fn replace(&self, key: &str, value: &Value) -> Result<(), relay_core::AppError> {
        self.0.lock().unwrap().insert(key.to_owned(), value.clone());
        Ok(())
    }
    fn add_secret(&self, _key: &str, _field: &str) {}
}

/// Waits and ports turned down so a test runs in milliseconds and listens on loopback, not on the network.
pub fn quick() -> Tuning {
    Tuning {
        scan_every: Duration::from_secs(3600),
        meter_every: Duration::from_millis(50),
        probe_wait: Duration::from_millis(300),
        arc_wait: Duration::from_millis(300),
        write_wait: Duration::from_millis(500),
        mdns: Ask { group: "127.0.0.1:9".parse().unwrap(), seconds: 0.1 },
        heartbeat_port: 0,
        heartbeat_join: false,
        meter_port: 0,
        control_port: 8800,
        write_lock: Duration::from_millis(500),
    }
}

pub struct Rig {
    pub module: Arc<Dante>,
    pub host: Host,
    pub board: Arc<Board>,
    pub pool: SqlitePool,
}

pub async fn memory_db() -> SqlitePool {
    // One connection, so every query sees the same in-memory database.
    SqlitePoolOptions::new().max_connections(1).connect("sqlite::memory:").await.expect("database")
}

/// The module attached, with the database made and open but nothing started: a test records devices itself.
pub async fn rig() -> Rig {
    rig_with(quick()).await
}

pub async fn rig_with(tuning: Tuning) -> Rig {
    rig_on(tuning, relay_core::Net::unrouted()).await
}

/// As [`rig_with`], with every socket the module asks for coming from `net` (a test makes a Dante port that is not there).
pub async fn rig_on(tuning: Tuning, net: relay_core::Net) -> Rig {
    let pool = memory_db().await;
    let host = Host::new(Shutdown::new()).with_store(Arc::new(Store(Mutex::new(HashMap::new())))).with_net(net);
    let board = Arc::new(Board::default());
    *board.buttons.lock().unwrap() = vec![("dante".into(), "Dante".into()), ("other".into(), "Other".into())];
    *board.local.lock().unwrap() = vec![LocalPort { addresses: vec!["10.9.0.2".into()], mac: "00:11:22:33:44:55".into(), virtual_: false }];
    host.set_authorizer(board.clone());
    host.provide("core", ALERT_BUTTONS, Arc::new(AlertButtonsCap(Arc::new(Buttons(board.clone())))));
    let b = board.clone();
    host.provide("core", INTERFACES, Arc::new(InterfacesCap(Box::new(move || b.local.lock().unwrap().clone()))));
    host.provide("core", relay_core::screens::DB, Arc::new(pool.clone()));
    let module = Arc::new(Dante::new().with_tuning(tuning));
    relay_core::Module::attach(&*module, &host);
    migrate(&pool).await.expect("migrate");
    module.service().unwrap().monitor.use_database(Some(pool.clone()));
    Rig { module, host, board, pool }
}

impl Rig {
    pub fn service(&self) -> &Arc<relay_module_dante::service::Service> {
        self.module.service().expect("attached")
    }

    /// Who is calling: a viewer reads, an operator also changes routing, an admin also manages.
    pub fn role(&self, role: &str) {
        let mut refuse = self.board.refuse.lock().unwrap();
        refuse.clear();
        match role {
            "viewer" => refuse.extend(["dante.control", "dante.manage"]),
            "operator" => refuse.push("dante.manage"),
            _ => {}
        }
    }

    pub async fn send(&self, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value) {
        let mut req = Request::builder().method(method).uri(uri).header("content-type", "application/json").header("host", "relay.local:8443").body(body.map_or_else(Body::empty, |b| Body::from(b.to_string()))).unwrap();
        req.extensions_mut().insert(Peer([10, 0, 0, 9].into()));
        let res = relay_core::Module::routes(&*self.module).oneshot(req).await.unwrap();
        let status = res.status();
        let bytes = res.into_body().collect().await.unwrap().to_bytes();
        (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null))
    }

    pub async fn get(&self, uri: &str) -> (StatusCode, Value) {
        self.send(Method::GET, uri, None).await
    }

    pub async fn send_delete(&self, uri: &str) -> (StatusCode, Value) {
        self.send(Method::DELETE, uri, None).await
    }

    pub async fn post(&self, uri: &str, body: Value) -> (StatusCode, Value) {
        self.send(Method::POST, uri, Some(body)).await
    }
}

/// A device record as a scan makes it, read from a fake device over its socket.
pub async fn record(rig: &Rig, name: &str, dev: &FakeDevice, transmit: &[&str], reachable: bool) -> Value {
    let mut d = relay_module_dante::service::device(name, &["127.0.0.1"], Some(dev.port), transmit);
    d["reachable"] = json!(reachable);
    d["clock"] = Value::Null;
    d["subscriptions"] = Value::Null;
    if reachable {
        rig.service().read_state(&mut d, "127.0.0.1", dev.port).await;
    }
    d
}

/// Wait up to `secs` for `cond`; polls, never a fixed sleep.
pub async fn until(secs: f64, mut cond: impl FnMut() -> bool) -> bool {
    let end = tokio::time::Instant::now() + Duration::from_secs_f64(secs);
    while tokio::time::Instant::now() < end {
        if cond() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(20)).await;
    }
    cond()
}

/// A network with no Dante port: every socket the module asks for is refused with the sentence the page shows.
pub struct NoPort;

impl relay_core::Router for NoPort {
    fn connect_tcp<'a>(&'a self, _c: &'a str, _h: &'a str, _p: u16, _t: Duration) -> BoxFuture<'a, Result<tokio::net::TcpStream, relay_core::RouteError>> {
        Box::pin(async { Err(relay_core::RouteError::new("The Dante network port is not available.")) })
    }
    fn udp(&self, _c: &str, _o: &relay_core::UdpOptions) -> Result<UdpSocket, relay_core::RouteError> {
        Err(relay_core::RouteError::new("The Dante network port is not available."))
    }
}
