//! The 32-lane audio bus on `relay-bus` directly (`app/audiobus.py`, #657): the saved setting, the core routes that say who feeds
//! which output channel, the engine, and the status the Audio bus page reads. There is no Python between the routes and the engine:
//! a route change reaches the mixer through this file, and the crosspoint matrix is cached in memory and written through to
//! `relay.db` (the routes are the stored truth; the cache is what the engine runs on).
//!
//! **The matrix cache** (#648). Reading the matrix the Python way meant a database query per channel on every page load. Here it is
//! three queries, kept in memory until something that can change it happens: a write through this process (a route, a saved
//! matrix, a channel report) invalidates it at once, and a write by the app still behind the server shows within [`MAX_AGE`].
//!
//! What stays outside: a module's network source or sender (NDI, Dante, AES67) is still driven by Python. It hands blocks to
//! [`AudioBus::push`], drains an output with [`AudioBus::pull`], and tells the bus how those streams are doing with
//! [`AudioBus::set_external`]; the server's internal routes carry those calls.

use std::collections::{BTreeMap, HashMap, HashSet};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::thread::JoinHandle;
use std::time::{Duration, Instant};

use relay_bus::fake::{signal, DeviceSpec, FakeBackend};
use relay_bus::{Backend, Engine, ProbeHub, StreamState, TapOptions};
use relay_bus_cpal::{list_devices as cpal_devices, ChangeSignal, CpalBackend, DeviceWatcher};
use serde_json::{json, Value};
use sqlx::Row;
use tokio::sync::watch;

use crate::channels::{Channels, Fut};
use crate::error::Result;
use crate::normalize::{engine_config, first_setup, needs_setup, normalize, Pick, DEFAULT_LATENCY_MS, LANES};
use crate::routing::{Provider, ReadItem, Routing, Wanted};

pub const PROVIDER: &str = "audiobus";
pub const DEST_PREFIX: &str = "dest:";
/// The channel-end provider of the sound cards: each channel of each card is a source channel (`audio.py` `_report_input_channels`).
pub const DEVICE_PROVIDER: &str = "portaudio";
pub const LANE_RATE: u32 = 48_000;
pub const DBFS_FLOOR: f64 = -120.0;
/// A change made by the app still behind the server shows within this long, at the latest.
pub const MAX_AGE: Duration = Duration::from_secs(2);
const SUPERVISE_EVERY: Duration = Duration::from_millis(500);
const FAKE_TICK: Duration = Duration::from_millis(5);
/// Destinations first setup patches into every channel of the first output.
pub const FIRST_PATCHED: [&str; 2] = ["alerts", "test"];
pub const CORE_DESTINATIONS: [(&str, &str); 2] = [("alerts", "Alert tones"), ("test", "Test tone")];
const MAX_CHANNELS_PER_DEVICE: usize = 32;

/// Where the saved setting lives (config.json, through `relay-config`; a map in a test).
pub trait Store: Send + Sync {
    /// The saved `audio_bus` block as stored.
    fn audio_bus(&self) -> Value;
    /// Replace the `audio_bus` block with `cfg` and say what was saved.
    fn save_audio_bus(&self, cfg: &Value) -> Value;
    /// The language codes Relay translates into (`targets`), for the spoken-translation destinations.
    fn targets(&self) -> Vec<String>;
}

/// Which sound system the engine opens.
pub enum Backing {
    /// Real devices through cpal.
    Cpal,
    /// The engine's own deterministic test devices on the wall clock.
    Fake(Arc<FakeBackend>),
}

impl Backing {
    /// The fake backend with a 2-channel "Fake Mic" and "Fake Speaker", as the Python wheel's `Bus("fake")` has.
    pub fn fake() -> Backing {
        let f = FakeBackend::new();
        for name in ["default", "Fake Mic"] {
            f.add_input(name, DeviceSpec::new(2, 48000, 480), signal::sine(1000.0, 48000, 0.25));
        }
        for name in ["default", "Fake Speaker"] {
            f.add_output(name, DeviceSpec::new(2, 48000, 480));
        }
        Backing::Fake(f)
    }
}

struct State {
    config: Value,
    running: bool,
    /// destination key -> [(output id, channel from 1, gain dB)]
    patches: HashMap<String, Vec<(String, i64, f64)>>,
    modules: BTreeMap<String, String>,
    external: Value,
}

struct Cache {
    key: u64,
    at: Option<Instant>,
    routes: Vec<Route>,
}

/// One route into an output channel: `source` is "lane<n>" or "dest:<destination>".
#[derive(Clone, Debug, PartialEq)]
pub struct Route {
    pub source: String,
    pub output: String,
    pub channel: i64,
    pub gain_db: f64,
}

pub struct AudioBus {
    pub engine: Arc<Engine>,
    probes: ProbeHub,
    fake: Option<Arc<FakeBackend>>,
    store: Arc<dyn Store>,
    pub channels: Arc<Channels>,
    pub routing: Arc<Routing>,
    state: Mutex<State>,
    cache: Mutex<Cache>,
    routes_gen: AtomicU64,
    changed: watch::Sender<u64>,
    stop: Arc<AtomicBool>,
    thread: Mutex<Option<JoinHandle<()>>>,
    watcher: Mutex<Option<DeviceWatcher>>,
}

fn supervisor(engine: Arc<Engine>, fake: Option<Arc<FakeBackend>>, signal: Arc<ChangeSignal>, stop: Arc<AtomicBool>) {
    let start = Instant::now();
    let mut seen = signal.count();
    let mut last = Instant::now();
    while !stop.load(Ordering::Relaxed) {
        let changed = signal.wait(&mut seen, if fake.is_some() { FAKE_TICK } else { SUPERVISE_EVERY });
        if let Some(f) = &fake {
            f.run_until(start.elapsed().as_secs_f64());
        }
        if changed {
            engine.retry_now();
        }
        if changed || last.elapsed() >= SUPERVISE_EVERY {
            last = Instant::now();
            engine.supervise();
        }
    }
}

/// The name the engine's device backend knows a saved device by. A saved selector is what the admin page picked from a list: a bare
/// name, or "<host API>: <name>" (what PortAudio labelled devices). The engine opens devices by name, so a selector is kept when the
/// engine lists a device of that name, or mapped to the part after the first ": " when that is one. Anything else is passed on
/// unchanged: the engine then reports the device as missing, as it does for any unplugged one.
pub fn engine_device(selector: &Value, names: &[String]) -> Value {
    let Some(sel) = selector.as_str().map(str::trim).filter(|s| !s.is_empty()) else { return selector.clone() };
    let known: HashMap<String, &String> = names.iter().map(|n| (n.to_lowercase(), n)).collect();
    let mut candidates = vec![sel.to_owned()];
    if let Some((_, rest)) = sel.split_once(": ") {
        candidates.push(rest.trim().to_owned());
    }
    for c in candidates {
        if let Some(n) = known.get(&c.to_lowercase()) {
            return json!(n);
        }
    }
    selector.clone()
}

fn dbfs(linear: f32) -> f64 {
    if linear <= 0.0 {
        DBFS_FLOOR
    } else {
        (20.0 * f64::from(linear.min(1.0)).log10()).max(DBFS_FLOOR)
    }
}

fn round1(x: f64) -> f64 {
    (x * 10.0).round() / 10.0
}

fn meter(m: &relay_bus::MeterReading) -> Value {
    json!({"rms_dbfs": round1(dbfs(m.rms)), "peak_dbfs": round1(dbfs(m.peak)), "clipping": m.clipping, "clipped_samples": m.clipped_samples})
}

fn state_name(s: StreamState) -> &'static str {
    match s {
        StreamState::Open => "open",
        StreamState::Waiting => "waiting",
        StreamState::Closed => "closed",
    }
}

/// `("<output id>", channel from 1)` for a destination end key "out:<id>:<n>".
pub fn split_out(key: &str) -> Option<(String, i64)> {
    let rest = key.strip_prefix("out:")?;
    let (oid, ch) = rest.rsplit_once(':')?;
    (!oid.is_empty() && !ch.is_empty() && ch.bytes().all(|b| b.is_ascii_digit())).then(|| ch.parse().ok().map(|c| (oid.to_owned(), c))).flatten()
}

impl AudioBus {
    /// Make the bus. The engine starts its device watcher and supervisor threads at once; nothing is opened until the first
    /// [`apply`](Self::apply), and with nothing patched nothing ever is.
    pub fn new(backing: Backing, store: Arc<dyn Store>, channels: Arc<Channels>, routing: Arc<Routing>) -> Arc<Self> {
        let signal = ChangeSignal::new();
        let (engine, fake, watcher): (Arc<Engine>, Option<Arc<FakeBackend>>, Option<DeviceWatcher>) = match backing {
            Backing::Cpal => {
                let b = Arc::new(CpalBackend::new());
                (Arc::new(Engine::new(b as Arc<dyn Backend>)), None, Some(DeviceWatcher::start(signal.clone())))
            }
            Backing::Fake(f) => (Arc::new(Engine::new(f.clone() as Arc<dyn Backend>)), Some(f), None),
        };
        let stop = Arc::new(AtomicBool::new(false));
        let thread = {
            let (e, f, s, st) = (engine.clone(), fake.clone(), signal, stop.clone());
            std::thread::Builder::new().name("relay-bus-supervisor".into()).spawn(move || supervisor(e, f, s, st)).ok()
        };
        let probes = ProbeHub::new(engine.clone());
        let (changed, _) = watch::channel(0);
        let bus = Arc::new(Self {
            engine,
            probes,
            fake,
            store,
            channels,
            routing: routing.clone(),
            state: Mutex::new(State { config: json!({"lanes": [], "outputs": [], "matrix": []}), running: false, patches: HashMap::new(), modules: BTreeMap::new(), external: json!({})}),
            cache: Mutex::new(Cache { key: u64::MAX, at: None, routes: Vec::new() }),
            routes_gen: AtomicU64::new(0),
            changed,
            stop,
            thread: Mutex::new(thread),
            watcher: Mutex::new(watcher),
        });
        routing.provide_core(PROVIDER, Arc::new(BusProvider { bus: Arc::downgrade(&bus) }));
        bus
    }

    pub fn fake(&self) -> Option<&Arc<FakeBackend>> {
        self.fake.as_ref()
    }

    pub fn probes(&self) -> &ProbeHub {
        &self.probes
    }

    fn state(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(|e| e.into_inner())
    }

    /// Wakes on every change of the running setting: a Python driver of a network source or sender waits on this.
    pub fn subscribe(&self) -> watch::Receiver<u64> {
        self.changed.subscribe()
    }

    pub fn running(&self) -> bool {
        self.state().running
    }

    /// The setting the bus is running, matrix included.
    pub fn config(&self) -> Value {
        self.state().config.clone()
    }

    pub fn lane_count() -> i64 {
        LANES
    }

    // -- destinations (#599) --------------------------------------------------------------------------------------
    /// A module's own destination ("talkback"): `key` is lower_snake_case, `label` what the operator sees.
    pub fn register_destination(&self, key: &str, label: &str) {
        self.state().modules.insert(key.to_owned(), label.to_owned());
    }

    pub fn unregister_destination(&self, key: &str) {
        self.state().modules.remove(key);
    }

    /// Every destination now: `[{key, label}]`. Spoken translation has one per target language, as the config lists them.
    pub fn destinations(&self) -> Vec<(String, String)> {
        let mut found: Vec<(String, String)> = CORE_DESTINATIONS.iter().map(|(k, v)| ((*k).to_owned(), (*v).to_owned())).collect();
        let mut targets = self.store.targets();
        targets.sort();
        for lang in targets {
            let low = lang.to_lowercase();
            let mut title = low.chars();
            let shown = title.next().map(|f| f.to_uppercase().collect::<String>() + title.as_str()).unwrap_or_default();
            found.push((format!("speech:{low}"), format!("Spoken translation: {shown}")));
        }
        found.extend(self.state().modules.iter().map(|(k, v)| (k.clone(), v.clone())));
        found
    }

    /// Tell core which audio channels the bus has (#542): each patched lane is a source and each channel of each output a destination,
    /// so the console, the intercom and a rundown can pick one by ref, and a route can feed one lane into one output channel.
    pub async fn report_channels(&self, cfg: &Value) -> Option<HashMap<String, String>> {
        let mut found: Vec<Value> = Vec::new();
        for l in cfg["lanes"].as_array().into_iter().flatten() {
            let n = l["lane"].as_i64().unwrap_or(0);
            let name = l["name"].as_str().unwrap_or("");
            found.push(json!({"key": format!("lane{n}"), "kind": "audio", "direction": "source", "number": n,
                              "gear_name": if name.is_empty() { format!("Lane {n}") } else { name.to_owned() }}));
        }
        for (key, label) in self.destinations() {
            found.push(json!({"key": format!("{DEST_PREFIX}{key}"), "kind": "audio", "direction": "source", "number": null, "gear_name": label}));
        }
        for o in cfg["outputs"].as_array().into_iter().flatten() {
            let id = o["id"].as_str().unwrap_or("");
            let shown = o["name"].as_str().filter(|n| !n.is_empty()).unwrap_or(id);
            let n = o["channels"].as_i64().unwrap_or(1);
            for ch in 1..=n {
                found.push(json!({"key": format!("out:{id}:{ch}"), "kind": "audio", "direction": "destination", "number": ch,
                                  "gear_name": if n == 1 { shown.to_owned() } else { format!("{shown} {ch}") }}));
            }
        }
        let got = self.channels.report(PROVIDER, &found).await;
        self.invalidate();
        got
    }

    // -- the matrix cache -----------------------------------------------------------------------------------------
    /// Something that can change the matrix just happened.
    pub fn invalidate(&self) {
        self.routes_gen.fetch_add(1, Ordering::AcqRel);
    }

    fn cache_key(&self) -> u64 {
        self.routes_gen
            .load(Ordering::Acquire)
            .wrapping_mul(1_000_003)
            .wrapping_add(self.channels.generation().wrapping_mul(7919))
            .wrapping_add(self.routing.generation())
    }

    /// Every audio route into one of the bus's output channels. Read from the cache while it is fresh.
    pub async fn routes(&self) -> Vec<Route> {
        let key = self.cache_key();
        {
            let c = self.cache.lock().unwrap_or_else(|e| e.into_inner());
            if c.key == key && c.at.is_some_and(|t| t.elapsed() < MAX_AGE) {
                return c.routes.clone();
            }
        }
        match read_routes(self.channels.pool()).await {
            Ok(routes) => {
                *self.cache.lock().unwrap_or_else(|e| e.into_inner()) = Cache { key, at: Some(Instant::now()), routes: routes.clone() };
                routes
            }
            Err(e) => {
                tracing::warn!("The audio bus routes could not be read: the database is not ready ({e:?})");
                Vec::new()
            }
        }
    }

    /// The crosspoints the bus should run, `[{in, out, ch, gain_db}]`, built from core routes.
    pub async fn matrix_from_routes(&self) -> Vec<Value> {
        self.routes()
            .await
            .into_iter()
            .filter_map(|r| r.source.strip_prefix("lane").and_then(|n| n.parse::<i64>().ok()).map(|n| json!({"in": n, "out": r.output, "ch": r.channel, "gain_db": r.gain_db})))
            .collect()
    }

    /// Where each destination is patched: `{destination key: [(output id, channel from 1, gain dB)]}`.
    pub async fn destination_patches(&self) -> HashMap<String, Vec<(String, i64, f64)>> {
        let mut out: HashMap<String, Vec<(String, i64, f64)>> = HashMap::new();
        for r in self.routes().await {
            if let Some(key) = r.source.strip_prefix(DEST_PREFIX) {
                out.entry(key.to_owned()).or_default().push((r.output, r.channel, r.gain_db));
            }
        }
        out
    }

    /// Make `cells` (what the page sends) the routes: every output channel takes exactly the lanes listed for it.
    pub async fn save_matrix(&self, cfg: &Value, cells: &[Value]) -> Result<()> {
        let refs = self.report_channels(cfg).await.unwrap_or_default();
        let mut per: HashMap<String, Vec<(Value, Value)>> = HashMap::new();
        for c in cells {
            let lane = c["in"].as_i64().unwrap_or(0);
            if let Some(r) = self.channels.ensure(PROVIDER, &format!("lane{lane}"), "audio", "source", &format!("Lane {lane}")).await? {
                per.entry(format!("out:{}:{}", c["out"].as_str().unwrap_or(""), c["ch"].as_i64().unwrap_or(0))).or_default().push((json!(r), c["gain_db"].clone()));
            }
        }
        for (key, r) in refs.iter().filter(|(k, _)| k.starts_with("out:")) {
            self.routing.replace(&json!(r), per.get(key).map(Vec::as_slice).unwrap_or(&[]), false).await?;
        }
        self.invalidate();
        Ok(())
    }

    /// Route the alert tones and the test tone into every channel of the output first setup made, so a stereo speaker plays them with
    /// nothing else set up. The output is new, so no channel of it has a route to keep.
    pub async fn patch_first_output(&self, cfg: &Value) -> Result<()> {
        let Some(out) = cfg["outputs"].as_array().and_then(|a| a.first()) else { return Ok(()) };
        let refs = self.report_channels(cfg).await.unwrap_or_default();
        let mut sources: Vec<Value> = Vec::new();
        for key in FIRST_PATCHED {
            let label = CORE_DESTINATIONS.iter().find(|(k, _)| *k == key).map(|x| x.1).unwrap_or(key);
            if let Some(r) = self.channels.ensure(PROVIDER, &format!("{DEST_PREFIX}{key}"), "audio", "source", label).await? {
                sources.push(json!(r));
            }
        }
        for ch in 1..=out["channels"].as_i64().unwrap_or(0) {
            if let Some(r) = refs.get(&format!("out:{}:{ch}", out["id"].as_str().unwrap_or(""))) {
                let wanted: Vec<(Value, Value)> = sources.iter().map(|s| (s.clone(), json!(0.0))).collect();
                self.routing.replace(&json!(r), &wanted, false).await?;
            }
        }
        self.invalidate();
        Ok(())
    }

    /// The bus setting with the matrix core routes hold. A matrix saved in config before #544 moves to routes first, once.
    pub async fn runtime_config(&self, raw: &Value) -> Value {
        let mut cfg = normalize(raw);
        let cells = cfg["matrix"].as_array().cloned().unwrap_or_default();
        if !cells.is_empty() {
            let moved = self.save_matrix(&cfg, &cells).await;
            match moved {
                Ok(()) => {
                    let mut bare = cfg.clone();
                    bare["matrix"] = json!([]);
                    self.store.save_audio_bus(&bare);
                }
                Err(_) => tracing::warn!("The audio bus matrix could not be moved: the database is down"),
            }
        } else {
            self.report_channels(&cfg).await;
        }
        cfg["matrix"] = json!(self.matrix_from_routes().await);
        cfg
    }

    // -- the engine -----------------------------------------------------------------------------------------------
    fn for_engine(&self, cfg: &Value) -> Value {
        if self.fake.is_some() {
            return cfg.clone(); // the fake backend's devices are named by the test that made them
        }
        let names: Vec<String> = cpal_devices().into_iter().map(|d| d.name).collect();
        let mut out = cfg.clone();
        for (list, kind) in [("lanes", "source"), ("outputs", "output")] {
            for item in out[list].as_array_mut().into_iter().flatten() {
                if item[kind].as_str() == Some("portaudio") {
                    let mapped = engine_device(&item["device"], &names);
                    item["device"] = mapped;
                }
            }
        }
        out
    }

    /// Make the bus match `cfg`, touching only what changed: a device or output that is the same as before keeps running.
    pub async fn apply(self: &Arc<Self>, cfg: &Value) {
        let cfg = normalize(cfg);
        self.report_channels(&cfg).await;
        let patches = self.destination_patches().await;
        let engine_cfg = engine_config(&self.for_engine(&cfg));
        let me = self.clone();
        let ok = tokio::task::spawn_blocking(move || me.engine.apply(&engine_cfg)).await;
        match ok {
            Ok(Ok(())) => {}
            Ok(Err(e)) => tracing::warn!("The audio bus did not take that setting: {e}"),
            Err(e) => tracing::error!("The audio bus apply task failed: {e}"),
        }
        {
            let mut st = self.state();
            st.config = cfg;
            st.patches = patches;
            st.running = true;
        }
        self.changed.send_modify(|n| *n += 1);
    }

    /// A route into an output channel changed (from any page): run the bus on the matrix the routes now say.
    pub async fn reapply(self: &Arc<Self>) -> String {
        let mut cfg = self.config();
        cfg["matrix"] = json!(self.matrix_from_routes().await);
        self.apply(&cfg).await;
        String::new()
    }

    pub async fn stop(self: &Arc<Self>) {
        let me = self.clone();
        let _ = tokio::task::spawn_blocking(move || me.engine.apply(&relay_bus::Config { enabled: false, ..Default::default() })).await;
        let mut st = self.state();
        st.running = false;
        st.config = json!({"lanes": [], "outputs": [], "matrix": []});
    }

    /// Stop everything the bus owns, for the server's shutdown. The bus is unusable afterwards.
    pub fn close(&self) {
        self.stop.store(true, Ordering::Relaxed);
        self.engine.shutdown();
        if let Some(t) = self.thread.lock().unwrap_or_else(|e| e.into_inner()).take() {
            let _ = t.join();
        }
        self.watcher.lock().unwrap_or_else(|e| e.into_inner()).take();
    }

    /// Start the bus the way Relay starts it: the saved setting with the routes' matrix. Nothing is opened when nothing is patched.
    pub async fn start(self: &Arc<Self>) {
        let cfg = self.runtime_config(&self.store.audio_bus()).await;
        self.apply(&cfg).await;
    }

    // -- first setup ----------------------------------------------------------------------------------------------
    /// First setup: the operator picks the first input and the first output; a part already set up is kept. Returns what the page
    /// shows next.
    pub async fn setup(self: &Arc<Self>, input: &Value, output: Option<&Value>, channels_of: impl Fn(&str) -> Option<i64>) -> Result<Value> {
        let chosen = Pick::of(output);
        let width = match &chosen {
            Pick::Device(d) => channels_of(d).unwrap_or(2),
            _ => 2,
        };
        let (cfg, made) = first_setup(&self.store.audio_bus(), &Pick::of(Some(input).filter(|v| !v.is_null())), &chosen, width.min(2));
        let mut bare = cfg.clone();
        bare["matrix"] = json!([]);
        let saved = self.store.save_audio_bus(&bare);
        if made {
            self.patch_first_output(&saved).await?;
        } else {
            self.report_channels(&saved).await;
        }
        let mut cfg = saved;
        cfg["matrix"] = json!(self.matrix_from_routes().await);
        self.apply(&cfg).await;
        Ok(json!({"config": cfg, "status": self.status(), "setup": needs_setup(&cfg)}))
    }

    /// Save the bus setting and apply it. Without a `matrix` key the routes are left alone (the Audio bus page edits them in the
    /// shared route editor); with one, every output channel takes exactly the lanes listed.
    pub async fn save(self: &Arc<Self>, payload: &Value) -> Result<Value> {
        let cfg = normalize(payload);
        let cells = cfg["matrix"].as_array().cloned().unwrap_or_default();
        let mut bare = cfg.clone();
        bare["matrix"] = json!([]);
        let saved = self.store.save_audio_bus(&bare);
        if payload.get("matrix").is_some() {
            self.save_matrix(&saved, &cells).await?;
        } else {
            self.report_channels(&saved).await;
        }
        let mut cfg = saved;
        cfg["matrix"] = json!(self.matrix_from_routes().await);
        self.apply(&cfg).await;
        Ok(json!({"config": cfg, "status": self.status()}))
    }

    /// What the admin page's GET answers besides the sources and outputs Python registers.
    pub async fn page(&self) -> Value {
        let mut cfg = normalize(&self.store.audio_bus());
        cfg["matrix"] = json!(self.matrix_from_routes().await);
        json!({"config": cfg, "status": self.status(), "lanes": LANES, "setup": needs_setup(&cfg)})
    }

    // -- the sound cards ------------------------------------------------------------------------------------------
    /// The sound cards that take input (`inputs: true`) or give output, as the Audio bus page lists them.
    pub fn devices(&self, inputs: bool) -> Vec<Value> {
        if let Some(f) = &self.fake {
            let _ = f;
            let names = if inputs { ["default", "Fake Mic"] } else { ["default", "Fake Speaker"] };
            return names.iter().enumerate().map(|(i, n)| json!({"index": i, "name": n, "label": n, "channels": 2, "default": i == 0})).collect();
        }
        cpal_devices()
            .into_iter()
            .filter(|d| if inputs { d.inputs > 0 } else { d.outputs > 0 })
            .enumerate()
            .map(|(i, d)| {
                let (channels, default) = if inputs { (d.inputs, d.is_default_input) } else { (d.outputs, d.is_default_output) };
                json!({"index": i, "name": d.name, "label": d.name, "channels": channels, "default": default, "rate": d.default_rate})
            })
            .collect()
    }

    /// Each input channel of each sound card is an audio source channel in core (#542). A card that is gone turns missing there, with
    /// any name an operator gave it.
    pub async fn report_input_channels(&self) {
        let found: Vec<Value> = self
            .devices(true)
            .iter()
            .flat_map(|d| {
                let label = d["label"].as_str().unwrap_or("").to_owned();
                let name = d["name"].as_str().unwrap_or("").to_owned();
                let n = (d["channels"].as_u64().unwrap_or(0) as usize).min(MAX_CHANNELS_PER_DEVICE);
                (1..=n).map(move |c| json!({"key": format!("{label}#{c}"), "kind": "audio", "direction": "source", "number": c, "gear_name": format!("{name} {c}")}))
            })
            .collect();
        self.channels.report(DEVICE_PROVIDER, &found).await;
    }

    // -- what the Python side drives --------------------------------------------------------------------------------
    /// How the network sources and senders Python drives are doing: `{"inputs": {id: {state, error, rate, channels, dropped, ...}},
    /// "outputs": {id: {state, error, underruns}}}`, shown in the status next to the engine's own.
    pub fn set_external(&self, state: Value) {
        self.state().external = state;
    }

    /// What `set_external` last said.
    pub fn external(&self) -> Value {
        self.state().external.clone()
    }

    /// Hand a block of a network source to the engine (`channels` interleaved).
    pub fn push(&self, input_id: &str, data: &[f32], channels: usize, rate: u32) -> bool {
        self.engine.push(input_id, data, channels, rate).is_ok()
    }

    /// Take `frames` of a network sender's mix.
    pub fn pull(&self, output_id: &str, frames: usize) -> Option<Vec<f32>> {
        self.engine.pull(output_id, frames).ok()
    }

    pub fn open_tap(&self, lane: usize) -> Option<u64> {
        self.engine.open_tap(lane, TapOptions::default()).ok()
    }

    pub fn read_tap(&self, tap: u64, max: usize, wait: Duration) -> Option<relay_bus::TapRead> {
        self.engine.read_tap(tap, max, wait).ok()
    }

    pub fn close_tap(&self, tap: u64) {
        let _ = self.engine.close_tap(tap);
    }

    /// Play mono `samples` at `rate` to the destination `key` ("alerts", "test", "speech:spanish"): the clip is mixed into the next
    /// block of every output channel it is patched to, at that patch's gain. Returns how many channels it reached (0: not patched, or
    /// the bus is not running). Never blocks and never opens a device.
    pub fn play_to(&self, key: &str, samples: &[f32], rate: u32) -> usize {
        let (patched, running) = {
            let st = self.state();
            (st.patches.get(key).cloned().unwrap_or_default(), st.running)
        };
        if patched.is_empty() || !running || samples.is_empty() {
            return 0;
        }
        let widths: HashMap<String, i64> = self.config()["outputs"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|o| (o["id"].as_str().unwrap_or("").to_owned(), o["channels"].as_i64().unwrap_or(0)))
            .collect();
        let mut reached = 0;
        for (oid, ch, gain_db) in patched {
            if !widths.get(&oid).is_some_and(|w| (1..=*w).contains(&ch)) {
                continue;
            }
            let g = relay_bus::db_to_gain(gain_db as f32);
            let clip: Vec<f32> = samples.iter().map(|s| s * g).collect();
            match self.engine.inject(&oid, &[(ch - 1) as usize], &clip, rate) {
                Ok(()) => reached += 1,
                Err(e) => tracing::warn!("Could not play {key} into {oid} channel {ch}: {e}"),
            }
        }
        reached
    }

    // -- status ---------------------------------------------------------------------------------------------------
    /// The live state of every lane, input and output, in the shape the Audio bus page and the status feed read.
    pub fn status(&self) -> Value {
        let (cfg, running, external) = {
            let st = self.state();
            (st.config.clone(), st.running, st.external.clone())
        };
        let eng = running.then(|| self.engine.status());
        let patch: HashMap<i64, &Value> = cfg["lanes"].as_array().into_iter().flatten().filter_map(|l| l["lane"].as_i64().map(|n| (n, l))).collect();
        let ext_in = |id: &str| external["inputs"].get(id).cloned();
        let none_meter = relay_bus::MeterReading { rms: 0.0, peak: 0.0, clipping: false, clipped_samples: 0 };
        let mut lanes = Vec::new();
        for n in 1..=LANES {
            let spec = patch.get(&n).copied();
            let e = eng.as_ref().and_then(|s| s.lanes.iter().find(|l| l.lane as i64 == n));
            let ext = spec.and_then(|s| ext_in(&format!("{}:{}", s["source"].as_str().unwrap_or("").to_lowercase(), s["device"].as_str().unwrap_or("").to_lowercase())));
            let (state, error) = match (&ext, spec) {
                (Some(x), _) => (x["state"].as_str().unwrap_or("closed").to_owned(), x["error"].clone()),
                (None, Some(_)) => (e.and_then(|l| l.state).map(|s| state_name(s).to_owned()).unwrap_or_else(|| "closed".into()), e.and_then(|l| l.error.clone()).map_or(Value::Null, Value::String)),
                (None, None) => ("unpatched".into(), Value::Null),
            };
            let m = meter(e.map(|l| &l.meter).unwrap_or(&none_meter));
            let mut lane = json!({"lane": n, "name": spec.map(|s| s["name"].clone()).unwrap_or_else(|| json!("")), "patched": spec.is_some(),
                "source": spec.map(|s| s["source"].clone()), "device": spec.map(|s| s["device"].clone()), "channel": spec.map(|s| s["channel"].clone()),
                "state": state, "error": error, "subscribers": e.map_or(0, |l| l.subscribers)});
            if let (Some(l), Some(m)) = (lane.as_object_mut(), m.as_object()) {
                l.extend(m.clone());
            }
            lanes.push(lane);
        }
        let mut inputs = Vec::new();
        for i in eng.iter().flat_map(|s| s.inputs.iter()) {
            inputs.push(match ext_in(&i.id) {
                Some(x) => json!({"source": i.source, "device": i.device, "label": x["label"].clone(), "state": x["state"].clone(), "error": x["error"].clone(),
                                  "rate": x["rate"].clone(), "channels": x["channels"].clone(), "lanes": i.lanes, "dropped": x["dropped"].clone()}),
                None => json!({"source": i.source, "device": i.device, "label": i.device.clone().unwrap_or_else(|| "default input".into()),
                               "state": state_name(i.state), "error": i.error, "rate": i.rate, "channels": i.channels, "lanes": i.lanes, "dropped": i.dropped}),
            });
        }
        let mut outputs = Vec::new();
        for spec in cfg["outputs"].as_array().into_iter().flatten().filter(|_| running) {
            let id = spec["id"].as_str().unwrap_or("");
            let e = eng.as_ref().and_then(|s| s.outputs.iter().find(|o| o.id == id));
            let ext = external["outputs"].get(id);
            let (state, error) = match ext {
                Some(x) => (x["state"].as_str().unwrap_or("closed").to_owned(), x["error"].clone()),
                None => (e.map(|o| state_name(o.state).to_owned()).unwrap_or_else(|| "closed".into()), e.and_then(|o| o.error.clone()).map_or(Value::Null, Value::String)),
            };
            let ext_under = ext.and_then(|x| x["underruns"].as_u64()).unwrap_or(0);
            outputs.push(json!({"id": id, "name": spec["name"], "output": spec["output"], "device": spec["device"], "channels": spec["channels"],
                "latency_ms": spec["latency_ms"], "state": state, "error": error,
                "underruns": e.map_or(0, |o| o.underruns) + ext_under, "resyncs": e.map_or(0, |o| o.resyncs),
                "queued_ms": e.map_or(0.0, |o| f64::from(o.queued_ms)), "meters": e.map(|o| o.meters.iter().map(meter).collect::<Vec<_>>()).unwrap_or_default(),
                "ratio": e.map_or(1.0, |o| f64::from(o.ratio)), "drift": e.map_or(0.0, |o| f64::from(o.drift))}));
        }
        json!({"running": running, "lane_rate": LANE_RATE, "lanes": lanes, "inputs": inputs, "outputs": outputs, "matrix": cfg["matrix"]})
    }

    /// The lanes something is patched to, as the Audio input page lists them: `[{lane, name}]`.
    pub fn patched_lanes(&self) -> Vec<Value> {
        self.config()["lanes"].as_array().into_iter().flatten().map(|l| json!({"lane": l["lane"], "name": l["name"]})).collect()
    }

    /// The default for an output's total buffering, for callers that build one.
    pub const DEFAULT_LATENCY_MS: i64 = DEFAULT_LATENCY_MS;
}

impl Drop for AudioBus {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
    }
}

// -- reading the routes: three queries -----------------------------------------------------------------------------------
async fn read_routes(pool: &sqlx::SqlitePool) -> Result<Vec<Route>> {
    struct End {
        provider: String,
        key: String,
        direction: String,
        missing: bool,
    }
    let merged: HashMap<i64, Option<i64>> = sqlx::query("select id, merged_into from channels").fetch_all(pool).await?.iter().map(|r| (r.get("id"), r.get("merged_into"))).collect();
    let kinds: BTreeMap<i64, String> = sqlx::query("select id, kind from channels where merged_into is null order by id").fetch_all(pool).await?.iter().map(|r| (r.get("id"), r.get("kind"))).collect();
    let mut ends: HashMap<i64, Vec<End>> = HashMap::new();
    for r in sqlx::query("select channel_id, provider, key, direction, missing from channel_ends where provider = ? order by position, id").bind(PROVIDER).fetch_all(pool).await? {
        ends.entry(r.get("channel_id")).or_default().push(End { provider: r.get("provider"), key: r.get("key"), direction: r.get("direction"), missing: r.get::<i64, _>("missing") != 0 });
    }
    let mut asked: HashMap<i64, Vec<(i64, Option<f64>)>> = HashMap::new();
    for r in sqlx::query("select destination, source, gain_db from routes order by id").fetch_all(pool).await? {
        asked.entry(r.get("destination")).or_default().push((r.get("source"), r.get("gain_db")));
    }
    let root = |mut id: i64| -> Option<i64> {
        let mut seen = HashSet::new();
        while seen.insert(id) {
            match merged.get(&id)? {
                None => return Some(id),
                Some(next) => id = *next,
            }
        }
        None
    };
    let mut found = Vec::new();
    for (cid, kind) in &kinds {
        if kind != "audio" {
            continue;
        }
        for e in ends.get(cid).into_iter().flatten() {
            let Some((oid, ch)) = (e.provider == PROVIDER && e.direction == "destination" && !e.missing).then(|| split_out(&e.key)).flatten() else { continue };
            let mut seen = HashSet::new();
            for (src, gain) in asked.get(cid).into_iter().flatten() {
                let Some(src) = root(*src).filter(|s| s != cid && seen.insert(*s) && kinds.contains_key(s)) else { continue };
                if let Some(x) = ends.get(&src).into_iter().flatten().find(|x| x.provider == PROVIDER && (x.key.starts_with("lane") || x.key.starts_with(DEST_PREFIX))) {
                    found.push(Route { source: x.key.clone(), output: oid.clone(), channel: ch, gain_db: gain.unwrap_or(0.0) });
                }
            }
        }
    }
    Ok(found)
}

/// The audio bus as a routing provider (`routing.provide_core("audiobus", ...)`): an output channel mixes several lanes, with a gain
/// each, and the bus itself is the truth about what it mixes.
struct BusProvider {
    bus: std::sync::Weak<AudioBus>,
}

impl Provider for BusProvider {
    fn exclusive(&self) -> bool {
        false
    }
    fn gain(&self) -> bool {
        true
    }
    fn permission(&self) -> Option<String> {
        Some("audio.manage".into())
    }
    fn apply<'a>(&'a self, _end: &'a crate::channels::End, _wanted: &'a [Wanted]) -> Fut<'a, String> {
        Box::pin(async move {
            match self.bus.upgrade() {
                Some(bus) => bus.reapply().await,
                None => "The audio bus did not take that route.".to_owned(),
            }
        })
    }
    fn read<'a>(&'a self, end: &'a crate::channels::End) -> Fut<'a, Option<Vec<ReadItem>>> {
        Box::pin(async move {
            let bus = self.bus.upgrade()?;
            let (oid, ch) = split_out(&end.key)?;
            if !bus.running() {
                return None;
            }
            Some(
                bus.config()["matrix"]
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter(|c| c["out"].as_str() == Some(oid.as_str()) && c["ch"].as_i64() == Some(ch))
                    .map(|c| ReadItem { provider: Some(PROVIDER.into()), key: format!("lane{}", c["in"]), gain_db: c["gain_db"].as_f64() })
                    .collect(),
            )
        })
    }
}
