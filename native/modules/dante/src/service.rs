//! Dante discovery and reachability (#257): which Dante devices answer on Relay's Dante network, and whether Relay can reach their control port
//! (`service.py`).
//!
//! Discovery is multicast DNS through the `dante` traffic class's network port (`mdns.rs`); this folds its answers into one record per Dante device and
//! asks each device's control port (ARC) whether it answers, then reads its clock and subscriptions. A device is a JSON object, as the admin page reads it.

use std::collections::{BTreeMap, BTreeSet};
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use std::time::{Duration, Instant};

use relay_core::caps::{InterfacesCap, LocalPort, INTERFACES};
use relay_core::{Host, RouteError, UdpOptions};
use serde_json::{json, Map, Value};
use tokio::sync::Notify;
use tokio::task::JoinHandle;

use crate::arc::{self, CLASS_ID};
use crate::clock;
use crate::heartbeat::{self, Heartbeats};
use crate::inventory::{inventory, DEFAULT_ARC, SERVICES};
use crate::mdns::{self, Ask};
use crate::metering::{self, Meters};
use crate::monitor::{self, Monitor};
use crate::routing::Routing;

/// Between looks at the network.
pub const SCAN_EVERY: Duration = Duration::from_secs(30);
pub const METER_EVERY: Duration = Duration::from_secs(1);
pub const PROBE_WAIT: Duration = Duration::from_millis(600);

/// Everything a test turns down: waits, and where the module listens and asks.
#[derive(Clone, Debug)]
pub struct Tuning {
    pub scan_every: Duration,
    pub meter_every: Duration,
    pub probe_wait: Duration,
    pub arc_wait: Duration,
    pub write_wait: Duration,
    pub mdns: Ask,
    /// The port the heartbeat is heard on (8708), and whether to join the multicast group on it.
    pub heartbeat_port: u16,
    pub heartbeat_join: bool,
    /// The port a device is told to push its meters to (8751), and the control port it is asked on (8800).
    pub meter_port: u16,
    pub control_port: u16,
    /// The most a routing change waits for one still being written.
    pub write_lock: Duration,
}

impl Default for Tuning {
    fn default() -> Self {
        Self {
            scan_every: SCAN_EVERY,
            meter_every: METER_EVERY,
            probe_wait: PROBE_WAIT,
            arc_wait: arc::WAIT,
            write_wait: arc::WRITE_WAIT,
            mdns: Ask::default(),
            heartbeat_port: heartbeat::PORT,
            heartbeat_join: true,
            meter_port: metering::PORT,
            control_port: metering::CONTROL_PORT,
            write_lock: Duration::from_secs(10),
        }
    }
}

/// The 10-byte ARC header alone (protocol 0x27ff, length, sequence, command 0x1000, no result). Checked on 2026-11-20 against real DT168 stage boxes, a
/// Mac running Dante Virtual Soundcard and Dante Via (port 24440), from the Dante network and from the main LAN across the router.
pub fn probe_packet(sequence: u16) -> Vec<u8> {
    let mut out = Vec::new();
    for v in [arc::PROTOCOL, 10, sequence, arc::CMD_COUNTS, 0] {
        out.extend_from_slice(&v.to_be_bytes());
    }
    out
}

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}

#[derive(Default)]
struct State {
    devices: Vec<Value>,
    error: String,
    /// `(device key, {"device", "channels"})`, in the order the devices were checked.
    levels: Vec<(String, Value)>,
    scanned: f64,
}

pub struct Service {
    pub host: Host,
    pub tuning: Tuning,
    state: Mutex<State>,
    pub monitor: Monitor,
    pub routing: Routing,
    pub heartbeats: Arc<Heartbeats>,
    pub meters: Arc<Meters>,
    tasks: Mutex<Vec<JoinHandle<()>>>,
    wake: Notify,
    running: AtomicBool,
}

impl Service {
    pub fn new(host: Host, tuning: Tuning) -> Arc<Self> {
        let service = Arc::new(Self {
            host: host.clone(),
            tuning,
            state: Mutex::new(State::default()),
            monitor: Monitor::new(),
            routing: Routing::new(),
            heartbeats: Heartbeats::new(),
            meters: Meters::new(),
            tasks: Mutex::new(Vec::new()),
            wake: Notify::new(),
            running: AtomicBool::new(false),
        });
        service.monitor.set_stage(Some(Arc::new(move |_why: &str, on: bool| monitor::stage_alert(&host, on))));
        service
    }

    fn state(&self) -> MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    pub fn running(&self) -> bool {
        self.running.load(Ordering::SeqCst)
    }

    // ---------------------------------------------------------------------------------------------- this computer
    /// This computer's network ports, as core reports them (none when core offers no list).
    pub fn local_ports(&self) -> Vec<LocalPort> {
        self.host.using::<InterfacesCap>(INTERFACES).map(|c| (c.0)()).unwrap_or_default()
    }

    /// Every address this computer has.
    pub fn local_addresses(&self) -> BTreeSet<String> {
        self.local_ports().into_iter().flat_map(|p| p.addresses).collect()
    }

    /// The local addresses mDNS questions leave from: none when a Dante port is chosen (the class's own port is used), else every real port.
    fn scan_addresses(&self) -> Vec<Ipv4Addr> {
        if self.host.net().routed_address(CLASS_ID).is_some() {
            return Vec::new();
        }
        self.local_ports().into_iter().filter(|p| !p.virtual_).flat_map(|p| p.addresses).filter_map(|a| a.parse::<Ipv4Addr>().ok()).collect()
    }

    // ---------------------------------------------------------------------------------------------- scanning
    /// Whether something at `ip:port` sent a datagram back. Errors only when the Dante network is not there.
    pub async fn probe(&self, ip: &str, port: u16, sequence: u16, wait: Duration) -> Result<bool, RouteError> {
        let Ok(addr) = ip.parse::<IpAddr>() else { return Ok(false) };
        let sock = self.host.net().udp(CLASS_ID, &UdpOptions::default())?;
        if sock.send_to(&probe_packet(sequence), SocketAddr::new(addr, port)).await.is_err() {
            return Err(RouteError::new("The Dante network could not be reached."));
        }
        let end = tokio::time::Instant::now() + wait;
        let mut buf = vec![0u8; 2048];
        loop {
            let Ok(got) = tokio::time::timeout_at(end, sock.recv_from(&mut buf)).await else { return Ok(false) };
            if let Ok((n, from)) = got {
                if from.ip() == addr && n >= 2 {
                    return Ok(true);
                }
            }
        }
    }

    /// Listen, fold, probe each device's control port, and keep the answer.
    pub async fn scan_once(&self) {
        if let Err(e) = self.host.net().udp(CLASS_ID, &UdpOptions::default()) {
            self.state().error = e.message;
            return;
        }
        let found = mdns::scan(self.host.net(), &self.scan_addresses(), &SERVICES, &self.tuning.mdns).await;
        let mut devices = inventory(&found);
        for (i, d) in devices.iter_mut().enumerate() {
            let seq = u16::try_from(i + 1).unwrap_or(u16::MAX);
            d["reachable"] = Value::Null;
            let ip = d["ips"][0].as_str().unwrap_or("").to_owned();
            let port = d["ports"]["arc"].as_u64().and_then(|p| u16::try_from(p).ok()).unwrap_or(DEFAULT_ARC);
            d["arc_port"] = json!(port);
            if ip.is_empty() {
                continue;
            }
            d["reachable"] = match self.probe(&ip, port, seq, self.tuning.probe_wait).await {
                Ok(b) => json!(b),
                Err(_) => Value::Null,
            };
            d["clock"] = Value::Null;
            d["subscriptions"] = Value::Null;
            d["info"] = json!({});
            let ips = ips_of(d);
            let beat = self.heartbeats.seen(&ips, Instant::now());
            d["heartbeat"] = json!(beat);
            d["online"] = json!(d["reachable"] == json!(true) || beat);
            if d["reachable"] == json!(true) {
                self.read_state(d, &ip, port).await;
            }
        }
        self.record(devices.clone());
        self.monitor.observe(&devices).await;
    }

    /// The clock and the subscriptions of one device that answered. A device that answers in a way Relay cannot read keeps `null`, which is "no reading",
    /// never a made-up one.
    pub async fn read_state(&self, d: &mut Value, ip: &str, port: u16) {
        let net = self.host.net();
        let wait = self.tuning.arc_wait;
        let Ok(info) = arc::read_info(net, ip, port, 10, wait).await else { return };
        d["info"] = Value::Object(info);
        let Ok(clock) = arc::ask(net, ip, port, arc::CMD_CLOCK, 2, wait).await else { return };
        d["clock"] = arc::parse_clock(clock.as_deref()).unwrap_or(Value::Null);
        let Ok(subs) = arc::read_subscriptions(net, ip, port, 3, wait).await else { return };
        d["subscriptions"] = subs.map_or(Value::Null, Value::Array);
    }

    /// Keep what a scan found.
    pub fn record(&self, devices: Vec<Value>) {
        let mut s = self.state();
        s.devices = devices;
        s.error.clear();
        s.scanned = clock::epoch();
    }

    // ---------------------------------------------------------------------------------------------- levels
    /// Per device key, the channel numbers worth metering: Relay's own computer's channels and the starred ones.
    pub async fn meter_targets(&self) -> BTreeMap<String, BTreeSet<usize>> {
        let local = self.local_addresses();
        let starred = self.monitor.starred().await;
        let devices = self.devices_copy();
        let mut out = BTreeMap::new();
        for d in &devices {
            let names: Vec<&str> = d["transmit"].as_array().into_iter().flatten().map(text).collect();
            let own = ips_of(d).iter().any(|ip| local.contains(ip));
            let want: BTreeSet<usize> = names.iter().enumerate().filter(|(_, c)| own || starred.contains(&(text(&d["name"]).to_owned(), (**c).to_owned()))).map(|(i, _)| i).collect();
            if !want.is_empty() && d["ips"].as_array().is_some_and(|a| !a.is_empty()) {
                out.insert(text(&d["key"]).to_owned(), want);
            }
        }
        out
    }

    /// One meter reading of every device with a channel worth watching.
    pub async fn read_levels(&self) {
        let targets = self.meter_targets().await;
        if targets.is_empty() {
            self.state().levels.clear();
            return;
        }
        let devices: BTreeMap<String, Value> = self.devices_copy().into_iter().map(|d| (text(&d["key"]).to_owned(), d)).collect();
        let mut out: Vec<(String, Value)> = Vec::new();
        for (key, want) in targets {
            let Some(d) = devices.get(&key) else { continue };
            let ips = ips_of(d);
            let now = Instant::now();
            let mut got = self.heartbeats.levels(&ips, now);
            if got.is_none() {
                // No signal presence in its heartbeat (a hardware box): ask it for meters.
                self.meters.ask(text(&d["name"]), &ips, now, self.tuning.control_port).await;
                got = self.meters.levels(&ips, Instant::now());
            }
            let Some(got) = got else { continue };
            let names: Vec<&str> = d["transmit"].as_array().into_iter().flatten().map(text).collect();
            let named = got.len() == names.len(); // a count that disagrees means the order cannot be trusted
            let channels: Vec<Value> = want
                .iter()
                .filter(|i| **i < got.len())
                .filter_map(|i| got[*i].map(|level| json!({"channel": if named { names[*i].to_owned() } else { format!("Channel {}", i + 1) }, "level": level})))
                .collect();
            out.push((key, json!({"device": text(&d["name"]), "channels": channels})));
        }
        self.state().levels = out;
    }

    /// The last meter reading of one device, by its key.
    pub fn levels_of(&self, key: &str) -> Option<Value> {
        self.state().levels.iter().find(|(k, _)| k == key).map(|(_, v)| v.clone())
    }

    // ---------------------------------------------------------------------------------------------- loops
    async fn scan_loop(self: Arc<Self>) {
        loop {
            self.scan_once().await;
            tokio::select! {
                () = self.host.shutdown.wait() => return,
                () = self.wake.notified() => {}
                () = tokio::time::sleep(self.tuning.scan_every) => {}
            }
        }
    }

    async fn meter_loop(self: Arc<Self>) {
        loop {
            self.read_levels().await;
            tokio::select! {
                () = self.host.shutdown.wait() => return,
                () = tokio::time::sleep(self.tuning.meter_every) => {}
            }
        }
    }

    /// Begin scanning and listening. Every listener is a task, so a stop ends them at once.
    pub fn start(self: &Arc<Self>) {
        let mut tasks = self.tasks.lock().unwrap_or_else(PoisonError::into_inner);
        if !tasks.is_empty() {
            return;
        }
        self.running.store(true, Ordering::SeqCst);
        tasks.push(tokio::spawn(Arc::clone(self).scan_loop()));
        tasks.push(tokio::spawn(Arc::clone(self).meter_loop()));
        drop(tasks);
        match self.host.net().udp(CLASS_ID, &UdpOptions { port: self.tuning.heartbeat_port, reuse: true, ..UdpOptions::default() }) {
            Ok(sock) => {
                if self.tuning.heartbeat_join {
                    let iface = match self.host.net().routed_address(CLASS_ID) {
                        Some(IpAddr::V4(a)) => a,
                        _ => Ipv4Addr::UNSPECIFIED,
                    };
                    if sock.join_multicast_v4(Ipv4Addr::from(heartbeat::GROUP), iface).is_err() {
                        tracing::warn!("dante: cannot listen for heartbeats, so no levels");
                        return;
                    }
                }
                self.heartbeats.listen(sock);
            }
            Err(e) => tracing::warn!("dante: cannot listen for heartbeats, so no levels: {e}"),
        }
        match self.host.net().udp(CLASS_ID, &UdpOptions { port: self.tuning.meter_port, reuse: true, ..UdpOptions::default() }) {
            Ok(sock) => {
                let mac = sock.local_addr().ok().map_or([0; 6], |a| self.mac_of(&a.ip().to_string()));
                self.meters.listen(sock, mac);
            }
            Err(e) => tracing::warn!("dante: cannot listen for device meters, so no levels from hardware boxes: {e}"),
        }
    }

    /// The hardware address of the port that has `address`, or zeros.
    fn mac_of(&self, address: &str) -> [u8; 6] {
        let ports = self.local_ports();
        let hex: String = ports.iter().find(|p| p.addresses.iter().any(|a| a == address) && !p.mac.is_empty()).map(|p| p.mac.replace([':', '-'], "")).unwrap_or_default();
        let mut out = [0u8; 6];
        if hex.len() >= 12 {
            for (i, b) in out.iter_mut().enumerate() {
                *b = u8::from_str_radix(&hex[i * 2..i * 2 + 2], 16).unwrap_or(0);
            }
        }
        out
    }

    pub async fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
        // Devices asked for meters are told to stop, then every listener ends: tasks, so none of it waits on a thread.
        self.meters.release(self.tuning.control_port).await;
        self.meters.stop();
        self.heartbeats.stop();
        let tasks: Vec<JoinHandle<()>> = std::mem::take(&mut *self.tasks.lock().unwrap_or_else(PoisonError::into_inner));
        for t in tasks {
            t.abort();
            let _ = t.await;
        }
    }

    /// Scan again now. With the loop running it wakes the loop; without it, scans here.
    pub async fn rescan(&self) {
        if self.running() {
            self.wake.notify_one();
        } else {
            self.scan_once().await;
        }
    }

    // ---------------------------------------------------------------------------------------------- what is shown
    pub fn snapshot(&self) -> Value {
        let (devices, error, scanned, levels) = {
            let s = self.state();
            (s.devices.clone(), s.error.clone(), s.scanned, s.levels.iter().map(|(_, v)| v.clone()).collect::<Vec<_>>())
        };
        let count = |f: &dyn Fn(&Value) -> bool| devices.iter().filter(|d| f(d)).count();
        json!({
            "devices": devices,
            "error": error,
            "scanned": scanned,
            "running": self.running(),
            "reachable": count(&|d| d["reachable"] == json!(true)),
            "online": count(&|d| d["online"] == json!(true)),
            "unreachable": count(&|d| d["reachable"] == json!(false)),
            "problems": self.monitor.current(),
            "levels": levels,
            "leader": monitor::leader_of(&devices),
        })
    }

    pub fn devices_copy(&self) -> Vec<Value> {
        self.state().devices.clone()
    }

    /// Routing wrote and read back a device: keep what it now shows, so the page does not wait for the next scan.
    pub fn keep_subscriptions(&self, key: &str, subs: &Value) {
        if let Some(d) = self.state().devices.iter_mut().find(|d| d["key"] == json!(key)) {
            d["subscriptions"] = subs.clone();
        }
    }

    /// Boxes for the network map and each device's transmit channels as core channels. `None` until the first scan has run, so nothing turns missing
    /// before Relay has looked. Core's gear list is not in Rust, so no group names a gear id.
    pub fn gear_list(&self) -> Option<Value> {
        let devices = {
            let s = self.state();
            if s.scanned == 0.0 {
                return None;
            }
            s.devices.clone()
        };
        let mut out_devices = Vec::new();
        let mut groups = Vec::new();
        for d in &devices {
            let via = d["via"] == json!(true);
            out_devices.push(json!({"ips": d["ips"], "host": d["host"], "name": d["name"], "kind": "av", "dante": true, "tags": if via { json!(["Dante", "Dante Via"]) } else { json!(["Dante"]) }}));
            let key = text(&d["key"]);
            let items: Vec<Value> = d["transmit"].as_array().into_iter().flatten().map(|c| json!({"key": format!("{key}:tx:{}", text(c)), "kind": "audio", "direction": "source", "gear_name": c})).collect();
            groups.push(json!({"gear": Value::Null, "within": format!("{key}:"), "items": items}));
        }
        Some(json!({"devices": out_devices, "channels": groups}))
    }
}

/// A device's addresses.
pub fn ips_of(d: &Value) -> Vec<String> {
    d["ips"].as_array().into_iter().flatten().filter_map(Value::as_str).map(str::to_owned).collect()
}

/// The fields of a device a plain object holds, for a test that builds one.
pub fn device(name: &str, ips: &[&str], arc_port: Option<u16>, transmit: &[&str]) -> Value {
    let mut ports = Map::new();
    if let Some(p) = arc_port {
        ports.insert("arc".into(), json!(p));
    }
    json!({"name": name, "key": name.to_lowercase(), "host": "", "ips": ips, "ports": ports, "transmit": transmit, "via": false})
}
