//! The NDI router (`router.py`, #298): named outputs the booth points at any source, like a video matrix.
//!
//! A **routed output** is an NDI source "Relay - <name>" that the NDI library itself points at another source (no decoding, no
//! re-encoding), so a TV or decoder set to receive "Relay - Lobby TV" once can be switched from here without touching it.
//!
//! The routes are core routes (`relay_core::routes`): each output is a video channel (an NDI destination end, key `route:<id>`) and what
//! it shows is a route to the channel of an NDI source (key `src:<full NDI name>`), so the same output can be routed from the Blackmagic
//! page or a board button. This module keeps only the outputs it creates (`router_outputs`), applies a route when core asks
//! (`RouteProvider`), and says what each output really shows. A **salvo** is a core preset with scope "ndi".
//!
//! Left in Python: the one-off move of routes saved before #544 into core (`router.migrate`); an install that has run any Python since
//! has done it.

use std::collections::HashMap;
use std::sync::{Arc, Mutex};

use relay_core::module::BoxFuture;
use relay_core::routes::{ReadSource, RouteError, RouteProvider, Routes, WantedSource};
use serde_json::{json, Value};

use crate::settings::{same_name, squeeze};

pub const MAX_OUTPUTS: usize = 16;
pub const MAX_SALVOS: usize = 24;
pub const SCOPE: &str = "ndi";
pub const KEY: &str = "ndi";
pub const OUT: &str = "route:";
pub const SRC: &str = "src:";
pub const DOWN: &str = "The database is down, so routes cannot be changed right now.";
const NO_CHANNEL: &str = "That channel does not exist.";

/// "Relay - <name>": how an output shows up on the network.
pub fn ndi_name(name: &str) -> String {
    format!("Relay - {}", squeeze(name).chars().take(70).collect::<String>())
}

fn is_self(source: &str, output_name: &str) -> bool {
    let me = ndi_name(output_name);
    source == me || source.ends_with(&format!("({me})"))
}

// ------------------------------------------------------------------------------------------------- the NDI side
/// One NDI routing instance: point it at a source, or at nothing.
pub trait Instance: Send + Sync {
    fn change(&self, source: &str) -> bool;
    fn clear(&self) -> bool;
    fn close(&self);
}

/// What the router needs of the NDI runtime. The real one is `relay-ndi`; a test gives its own.
pub trait Backend: Send + Sync {
    fn open_router(&self, name: &str) -> Result<Box<dyn Instance>, String>;
    /// A source with the picture of `video` and the sound of `audio` (either may be empty).
    fn open_combiner(&self, name: &str, video: &str, audio: &str) -> Result<Box<dyn Closer>, String>;
}

pub trait Closer: Send + Sync {
    fn close(&self);
}

pub struct Runtime;

impl Instance for relay_ndi::route::Router {
    fn change(&self, source: &str) -> bool {
        relay_ndi::route::Router::change(self, source, "")
    }
    fn clear(&self) -> bool {
        relay_ndi::route::Router::clear(self)
    }
    fn close(&self) {
        relay_ndi::route::Router::close(self);
    }
}

impl Closer for relay_ndi::sink::Combiner {
    fn close(&self) {
        relay_ndi::sink::Combiner::close(self);
    }
}

const LOCATE_SECONDS: f64 = 3.0;

impl Backend for Runtime {
    fn open_router(&self, name: &str) -> Result<Box<dyn Instance>, String> {
        Ok(Box::new(relay_ndi::route::Router::open(name)?))
    }
    fn open_combiner(&self, name: &str, video: &str, audio: &str) -> Result<Box<dyn Closer>, String> {
        Ok(Box::new(relay_ndi::sink::Combiner::open(name, video, audio, LOCATE_SECONDS)?))
    }
}

/// A routed output whose sound comes from another source than its picture (#301). The NDI routing call switches a whole source, so this
/// output is a combiner instead, restarted with the picture each time the route changes.
struct Combined {
    backend: Arc<dyn Backend>,
    name: String,
    audio: String,
    inst: Mutex<Option<Box<dyn Closer>>>,
}

impl Combined {
    fn new(backend: Arc<dyn Backend>, name: String, audio: String) -> Result<Combined, String> {
        let c = Combined { backend, name, audio, inst: Mutex::new(None) };
        c.swap("")?; // the sound alone until a picture is chosen
        Ok(c)
    }

    fn swap(&self, source: &str) -> Result<(), String> {
        let inst = self.backend.open_combiner(&self.name, source, &self.audio)?;
        let old = self.inst.lock().ok().and_then(|mut i| i.replace(inst));
        if let Some(o) = old {
            o.close();
        }
        Ok(())
    }
}

impl Instance for Combined {
    fn change(&self, source: &str) -> bool {
        self.swap(source).is_ok()
    }
    fn clear(&self) -> bool {
        self.swap("").is_ok()
    }
    fn close(&self) {
        if let Some(i) = self.inst.lock().ok().and_then(|mut i| i.take()) {
            i.close();
        }
    }
}

// ------------------------------------------------------------------------------------------------- the router
pub struct Deps {
    pub routes: Arc<dyn Routes>,
    pub backend: Arc<dyn Backend>,
    pub settings: Arc<dyn Fn() -> Value + Send + Sync>,
    /// Save changes to the module's settings.
    pub save: Arc<dyn Fn(Value) + Send + Sync>,
    /// Something changed that the page should hear about.
    pub notify: Arc<dyn Fn() + Send + Sync>,
    pub running: Arc<dyn Fn() -> bool + Send + Sync>,
    /// Tell core the channels NDI has now (`Service::gear_list`).
    pub report: Arc<dyn Fn() -> BoxFuture<'static, ()> + Send + Sync>,
}

pub struct Inner {
    d: Deps,
    live: Mutex<HashMap<String, Arc<dyn Instance>>>,
    target: Mutex<HashMap<String, String>>,
    err: Mutex<HashMap<String, String>>,
}

#[derive(Clone)]
pub struct Router(pub Arc<Inner>);

async fn blocking<T: Send + 'static>(f: impl FnOnce() -> T + Send + 'static) -> Option<T> {
    tokio::task::spawn_blocking(f).await.ok()
}

fn sentence(e: &RouteError) -> String {
    match e {
        RouteError::Refused(s) => (*s).to_owned(),
        RouteError::Unavailable => DOWN.to_owned(),
    }
}

impl Router {
    pub fn new(d: Deps) -> Router {
        Router(Arc::new(Inner { d, live: Mutex::default(), target: Mutex::default(), err: Mutex::default() }))
    }

    pub fn provider(&self) -> Arc<dyn RouteProvider> {
        Arc::new(Provider(Arc::clone(&self.0)))
    }

    /// Create every routed output, then have core point each at the source asked for.
    pub async fn start(&self) {
        for r in self.0.outputs() {
            self.open(&r).await;
        }
        (self.0.d.report)().await;
        match self.0.d.routes.reapply(KEY).await {
            Ok(problems) => problems.iter().for_each(|p| tracing::info!("NDI route: {p}")),
            Err(_) => tracing::info!("NDI routes were not applied: the database is down"),
        }
    }

    pub fn stop(&self) {
        let live: Vec<Arc<dyn Instance>> = self.0.live.lock().map(|mut l| l.drain().map(|(_, v)| v).collect()).unwrap_or_default();
        if let Ok(mut t) = self.0.target.lock() {
            t.clear();
        }
        for i in live {
            i.close();
        }
    }

    async fn open(&self, r: &Value) -> bool {
        let inner = Arc::clone(&self.0);
        let r = r.clone();
        blocking(move || inner.open(&r)).await.unwrap_or(false)
    }

    // ---------------------------------------------------------------------------------------- editing
    /// The new output, or the sentence saying why not.
    pub async fn add_output(&self, name: &str) -> Result<Value, String> {
        let s = (self.0.d.settings)();
        let name: String = squeeze(name).chars().take(60).collect();
        let outputs = self.0.outputs();
        if name.is_empty() {
            return Err("Give the output a name.".into());
        }
        if outputs.len() >= MAX_OUTPUTS {
            return Err(format!("At most {MAX_OUTPUTS} routed outputs."));
        }
        if outputs.iter().any(|r| r["name"].as_str().unwrap_or("").to_lowercase() == name.to_lowercase()) {
            return Err("There is already an output with that name.".into());
        }
        let _ = s;
        let r = json!({"id": new_id(), "name": name, "audio": ""});
        let mut all = outputs;
        all.push(r.clone());
        (self.0.d.save)(json!({"router_outputs": all}));
        self.open(&r).await;
        (self.0.d.report)().await;
        (self.0.d.notify)();
        Ok(r)
    }

    pub async fn remove_output(&self, oid: &str) -> bool {
        let outputs = self.0.outputs();
        if !outputs.iter().any(|r| r["id"] == oid) {
            return false;
        }
        let inst = self.0.live.lock().ok().and_then(|mut l| l.remove(oid));
        if let Ok(mut t) = self.0.target.lock() {
            t.remove(oid);
        }
        if let Some(i) = inst {
            blocking(move || i.close()).await;
        }
        if let Ok(mut e) = self.0.err.lock() {
            e.remove(oid);
        }
        (self.0.d.save)(json!({"router_outputs": outputs.iter().filter(|r| r["id"] != oid).collect::<Vec<_>>()}));
        (self.0.d.report)().await; // it is missing now, so core lets it go with its routes
        if let Some(chan) = self.0.chans().await.get(oid) {
            let _ = self.0.d.routes.forget(chan["ref"].as_str().unwrap_or("")).await;
        }
        (self.0.d.notify)();
        true
    }

    /// Take this output's sound from `source` ("" puts it back with the picture). "" on success.
    pub async fn set_audio(&self, oid: &str, source: &str) -> String {
        let outputs = self.0.outputs();
        let Some(r) = outputs.iter().find(|r| r["id"] == oid).cloned() else { return "That output does not exist.".into() };
        let source: String = source.trim().chars().take(160).collect();
        if is_self(&source, r["name"].as_str().unwrap_or("")) {
            return "An output cannot take sound from itself.".into();
        }
        let shown = self.0.target.lock().ok().and_then(|t| t.get(oid).cloned()).unwrap_or_default();
        let old = self.0.live.lock().ok().and_then(|mut l| l.remove(oid));
        if let Some(o) = old {
            blocking(move || o.close()).await;
        }
        let mut r = r;
        r["audio"] = json!(source);
        let updated: Vec<Value> = outputs.iter().map(|x| if x["id"] == oid { r.clone() } else { x.clone() }).collect();
        (self.0.d.save)(json!({"router_outputs": updated}));
        if (self.0.d.running)() && self.open(&r).await && !shown.is_empty() {
            let inst = self.0.live.lock().ok().and_then(|l| l.get(oid).cloned());
            let ok = match inst {
                Some(i) => blocking(move || i.change(&shown)).await.unwrap_or(false),
                None => false,
            };
            if !ok {
                (self.0.d.notify)();
                return "NDI would not switch that output.".into();
            }
        }
        (self.0.d.notify)();
        self.0.err.lock().ok().and_then(|e| e.get(oid).cloned()).unwrap_or_default()
    }

    /// "" on success, else the sentence saying why not.
    pub async fn route(&self, oid: &str, source: &str) -> String {
        let Some(r) = self.0.outputs().into_iter().find(|x| x["id"] == oid) else { return "That output does not exist.".into() };
        let source: String = source.trim().chars().take(160).collect();
        if is_self(&source, r["name"].as_str().unwrap_or("")) {
            return "An output cannot show itself.".into();
        }
        let mut dest = self.0.chans().await.get(oid).cloned();
        if dest.is_none() {
            (self.0.d.report)().await;
            dest = self.0.chans().await.get(oid).cloned();
        }
        let Some(dest) = dest else { return "NDI has not listed that output yet.".into() };
        let dref = dest["ref"].as_str().unwrap_or("").to_owned();
        let got = if source.is_empty() {
            self.0.d.routes.clear(&dref).await
        } else {
            match self.0.d.routes.ensure(KEY, &format!("{SRC}{source}"), "video", "source", &source).await {
                Ok(Some(src)) => self.0.d.routes.set(&dref, &src).await,
                Ok(None) => Err(RouteError::Refused(NO_CHANNEL)),
                Err(e) => Err(e),
            }
        };
        (self.0.d.notify)();
        match got {
            Ok(problems) => problems.join("; "),
            Err(e) => sentence(&e),
        }
    }

    // ---------------------------------------------------------------------------------------- salvos
    /// Save what every output shows now as a preset; with `sid` an existing one is replaced.
    pub async fn save_salvo(&self, name: &str, sid: &str) -> Result<Value, String> {
        let held = self.0.d.routes.salvos(SCOPE).await.map_err(|e| sentence(&e))?;
        let id = sid.parse::<i64>().ok().filter(|_| sid.bytes().all(|b| b.is_ascii_digit()));
        if id.is_none() && held.len() >= MAX_SALVOS {
            return Err(format!("At most {MAX_SALVOS} presets."));
        }
        let dests: Vec<String> = self.0.chans().await.values().filter_map(|c| c["ref"].as_str().map(str::to_owned)).collect();
        let got = self.0.d.routes.save_salvo(name, dests, SCOPE, id).await.map_err(|e| sentence(&e))?;
        (self.0.d.notify)();
        Ok(json!({"id": got["id"].to_string(), "name": got["name"]}))
    }

    pub async fn delete_salvo(&self, sid: &str) -> bool {
        let Ok(id) = sid.parse::<i64>() else { return false };
        let ok = self.0.d.routes.delete_salvo(id).await.is_ok();
        if ok {
            (self.0.d.notify)();
        }
        ok
    }

    /// Apply a preset: every output it names is pointed at its saved source. "" on success.
    pub async fn take(&self, sid: &str) -> String {
        let Ok(id) = sid.parse::<i64>() else { return "That preset does not exist.".into() };
        match self.0.d.routes.take_salvo(id).await {
            Ok(problems) => {
                (self.0.d.notify)();
                problems.join("; ")
            }
            Err(RouteError::Refused(_)) => "That preset does not exist.".into(),
            Err(RouteError::Unavailable) => DOWN.into(),
        }
    }

    // ---------------------------------------------------------------------------------------- state
    pub async fn snapshot(&self) -> Value {
        let world = self.0.d.routes.channels().await.unwrap_or_default();
        let views: Vec<Value> = self.0.d.routes.views(KEY).await.unwrap_or_default();
        let held = self.0.d.routes.salvos(SCOPE).await.unwrap_or_default();
        let chans = Inner::chans_of(&world);
        let seen: HashMap<&str, &Value> = views.iter().filter_map(|v| Some((v["destination"].as_str()?, v))).collect();
        let names: HashMap<&str, String> = world.iter().filter_map(|c| Some((c["ref"].as_str()?, source_name(c).unwrap_or_else(|| c["shown"].as_str().unwrap_or("").to_owned())))).collect();
        let first = |v: &Value, k: &str| v[k].as_array().and_then(|a| a.first()).and_then(|x| x["ref"].as_str()).and_then(|r| names.get(r).cloned()).unwrap_or_default();
        let live = self.0.live.lock().map(|l| l.keys().cloned().collect::<Vec<_>>()).unwrap_or_default();
        let routes: Vec<Value> = self
            .0
            .outputs()
            .iter()
            .map(|r| {
                let id = r["id"].as_str().unwrap_or("");
                let c = chans.get(id);
                let v = c.and_then(|c| seen.get(c["ref"].as_str().unwrap_or("")));
                let mut o = r.clone();
                o["audio"] = json!(r["audio"].as_str().unwrap_or(""));
                o["ndi"] = json!(ndi_name(r["name"].as_str().unwrap_or("")));
                o["live"] = json!(live.iter().any(|l| l == id));
                o["error"] = json!(self.0.err.lock().ok().and_then(|e| e.get(id).cloned()).unwrap_or_default());
                o["source"] = json!(v.map(|v| first(v, "sources")).unwrap_or_default());
                o["asked"] = json!(v.map(|v| first(v, "wanted")).unwrap_or_default());
                o["state"] = v.map_or(json!("none"), |v| v["state"].clone());
                o["ref"] = json!(c.map_or("", |c| c["ref"].as_str().unwrap_or("")));
                o
            })
            .collect();
        let by_ref: HashMap<&str, &str> = chans.iter().filter_map(|(oid, c)| Some((c["ref"].as_str()?, oid.as_str()))).collect();
        let salvos: Vec<Value> = held
            .iter()
            .map(|sv| {
                let mut m = serde_json::Map::new();
                for x in sv["routes"].as_array().into_iter().flatten() {
                    if let Some(oid) = x["destination"].as_str().and_then(|d| by_ref.get(d)) {
                        let src = x["source"].as_str().filter(|s| !s.is_empty()).and_then(|s| names.get(s).cloned()).unwrap_or_default();
                        m.insert((*oid).to_owned(), json!(src));
                    }
                }
                json!({"id": sv["id"].to_string(), "name": sv["name"], "routes": m})
            })
            .collect();
        json!({"routes": routes, "salvos": salvos})
    }

    pub fn live_count(&self) -> usize {
        self.0.live.lock().map_or(0, |l| l.len())
    }
}

fn new_id() -> String {
    crate::settings::new_id()
}

/// The full NDI name of the source a channel stands for ("" when it has no NDI source end).
fn source_name(chan: &Value) -> Option<String> {
    chan["ends"].as_array()?.iter().find(|e| e["provider"] == KEY && e["key"].as_str().is_some_and(|k| k.starts_with(SRC))).and_then(|e| e["key"].as_str()).map(|k| k[SRC.len()..].to_owned())
}

impl Inner {
    fn outputs(&self) -> Vec<Value> {
        (self.d.settings)()["router_outputs"].as_array().cloned().unwrap_or_default()
    }

    fn open(&self, r: &Value) -> bool {
        let id = r["id"].as_str().unwrap_or("");
        if !(self.d.running)() {
            return false;
        }
        if self.live.lock().is_ok_and(|l| l.contains_key(id)) {
            return true;
        }
        let name = ndi_name(r["name"].as_str().unwrap_or(""));
        let audio = r["audio"].as_str().unwrap_or("");
        let made: Result<Arc<dyn Instance>, String> = if audio.is_empty() {
            self.d.backend.open_router(&name).map(Arc::from)
        } else {
            Combined::new(Arc::clone(&self.d.backend), name, audio.to_owned()).map(|c| Arc::new(c) as Arc<dyn Instance>)
        };
        match made {
            Ok(inst) => {
                if let Ok(mut e) = self.err.lock() {
                    e.remove(id);
                }
                if let Ok(mut l) = self.live.lock() {
                    l.insert(id.to_owned(), inst);
                }
                true
            }
            Err(why) => {
                tracing::warn!("NDI routed output {} could not open: {why}", r["name"].as_str().unwrap_or(""));
                if let Ok(mut e) = self.err.lock() {
                    e.insert(id.to_owned(), "NDI would not create this output.".to_owned());
                }
                false
            }
        }
    }

    fn chans_of(world: &[Value]) -> HashMap<String, Value> {
        world
            .iter()
            .filter_map(|c| {
                let e = c["ends"].as_array()?.iter().find(|e| e["provider"] == KEY && e["key"].as_str().is_some_and(|k| k.starts_with(OUT)))?;
                Some((e["key"].as_str()?[OUT.len()..].to_owned(), c.clone()))
            })
            .collect()
    }

    /// `{output id: its channel}` for the outputs core knows.
    async fn chans(&self) -> HashMap<String, Value> {
        Self::chans_of(&self.d.routes.channels().await.unwrap_or_default())
    }

    /// What core asks of this module: point the destination `end` at what is `wanted`.
    async fn core_apply(self: &Arc<Self>, end: &Value, wanted: &[WantedSource]) -> String {
        let Some(oid) = end["key"].as_str().and_then(|k| k.strip_prefix(OUT)) else { return "That output does not exist.".into() };
        let Some(r) = self.outputs().into_iter().find(|x| x["id"] == oid) else { return "That output does not exist.".into() };
        let names: Vec<String> = wanted
            .iter()
            .flat_map(|w| w.ends.iter())
            .filter(|e| e["provider"] == KEY)
            .filter_map(|e| e["key"].as_str().and_then(|k| k.strip_prefix(SRC)).map(str::to_owned))
            .collect();
        if !wanted.is_empty() && names.is_empty() {
            return "NDI can only show an NDI source.".into();
        }
        let source = names.first().cloned().unwrap_or_default();
        if is_self(&source, r["name"].as_str().unwrap_or("")) {
            return "An output cannot show itself.".into();
        }
        if !(self.d.running)() {
            return String::new(); // asked for; core applies it when NDI is on
        }
        let oid = oid.to_owned();
        let me = Arc::clone(self);
        let done = blocking(move || {
            if !me.live.lock().is_ok_and(|l| l.contains_key(&oid)) && !me.open(&r) {
                return me.err.lock().ok().and_then(|e| e.get(&oid).cloned()).unwrap_or_else(|| "NDI is not running.".into());
            }
            let Some(inst) = me.live.lock().ok().and_then(|l| l.get(&oid).cloned()) else { return "NDI is not running.".into() };
            let ok = if source.is_empty() { inst.clear() } else { inst.change(&source) };
            if !ok {
                return "NDI would not switch that output.".into();
            }
            if let Ok(mut t) = me.target.lock() {
                t.insert(oid, source);
            }
            String::new()
        })
        .await;
        done.unwrap_or_else(|| "NDI would not switch that output.".into())
    }

    fn core_read(&self, end: &Value) -> Option<Vec<ReadSource>> {
        let oid = end["key"].as_str()?.strip_prefix(OUT)?;
        if !self.live.lock().ok()?.contains_key(oid) {
            return None;
        }
        let t = self.target.lock().ok()?.get(oid).cloned().unwrap_or_default();
        Some(if t.is_empty() { vec![] } else { vec![ReadSource { provider: Some(KEY.to_owned()), key: format!("{SRC}{t}"), gain_db: None }] })
    }
}

struct Provider(Arc<Inner>);

impl RouteProvider for Provider {
    fn exclusive(&self) -> bool {
        true
    }
    fn gain(&self) -> bool {
        false
    }
    fn permission(&self) -> Option<String> {
        Some("ndi.manage".to_owned())
    }
    fn apply<'a>(&'a self, end: &'a Value, wanted: &'a [WantedSource]) -> BoxFuture<'a, String> {
        Box::pin(async move { self.0.core_apply(end, wanted).await })
    }
    fn read<'a>(&'a self, end: &'a Value) -> BoxFuture<'a, Option<Vec<ReadSource>>> {
        Box::pin(async move { self.0.core_read(end) })
    }
}

/// Whether two output names would be the same output.
pub fn same_output(a: &str, b: &str) -> bool {
    same_name(a, b)
}
