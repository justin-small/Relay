//! The source watch (`watch.py`, #562): look at the sources the booth relies on and raise a problem only when it lasts.
//!
//! The deciding is `relay-ndi`'s `Watcher` (a fault must last the hold time before it is raised, and be gone for a few seconds before it is
//! cleared). This holds a low-bandwidth receiver for each watched source, asks the service what is wrong with each about every two seconds, and
//! keeps what is raised for the page. A problem is logged; the stage alert button the Python module flashed has no Rust capability yet (alerts
//! live in `relay-show`, which a module cannot depend on), so it is left until the modules that raise alerts are ported (#661).

use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

use relay_ndi::watch::{Event, Kind, Watcher};
use serde_json::{json, Value};

use crate::pool::Lease;

/// What the watch needs from the service.
pub trait Ctx {
    /// The saved watch settings (cleaned).
    fn settings(&self) -> Value;
    /// Sources the booth plainly relies on: those with a camera number and those in a multiview box.
    fn auto(&self) -> Vec<String>;
    /// What is wrong with a watched source now.
    fn problems(&self, source: &str, sound: bool) -> Vec<(Kind, String)>;
    /// A low-bandwidth receiver for a watched source, with its audio meter running.
    fn acquire(&self, source: &str) -> Option<Lease>;
    /// Whether the named schedule is open now.
    fn in_service(&self, schedule: &str) -> bool;
    fn changed(&self);
}

pub struct Watch {
    watcher: Mutex<Watcher>,
    leases: Mutex<HashMap<String, Lease>>,
    on_service: std::sync::atomic::AtomicBool,
}

impl Default for Watch {
    fn default() -> Self {
        Self::new()
    }
}

impl Watch {
    pub fn new() -> Watch {
        Watch { watcher: Mutex::new(Watcher::new(10.0)), leases: Mutex::default(), on_service: std::sync::atomic::AtomicBool::new(false) }
    }

    /// The sources watched: those saved, and (when `auto`) those the booth relies on.
    pub fn relied(cfg: &Value, auto: &[String]) -> Vec<String> {
        let mut names: Vec<String> = cfg["sources"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_owned)).collect();
        if cfg["auto"] == true {
            for n in auto {
                if !names.contains(n) {
                    names.push(n.clone());
                }
            }
        }
        names
    }

    /// Look at every watched source (about every two seconds, on a worker thread). `now` is monotonic seconds.
    pub fn tick(&self, now: f64, ctx: &dyn Ctx) {
        let cfg = ctx.settings();
        let live = cfg["on"] == true;
        let schedule = cfg["schedule"].as_str().unwrap_or("");
        let on_service = live && (schedule.is_empty() || ctx.in_service(schedule));
        self.on_service.store(on_service, std::sync::atomic::Ordering::SeqCst);
        let wanted = if on_service { Self::relied(&cfg, &ctx.auto()) } else { Vec::new() };
        let sound = cfg["sound"] == true;
        let mut events: Vec<Event> = Vec::new();
        let (Ok(mut w), Ok(mut leases)) = (self.watcher.lock(), self.leases.lock()) else { return };
        w.set_hold(cfg["hold"].as_f64().unwrap_or(10.0));
        let gone: Vec<String> = leases.keys().filter(|s| !wanted.contains(s)).cloned().collect();
        for g in gone {
            leases.remove(&g);
            events.extend(w.forget(&g));
        }
        for name in &wanted {
            if !leases.contains_key(name) {
                if let Some(lease) = ctx.acquire(name) {
                    leases.insert(name.clone(), lease);
                }
            }
            events.extend(w.feed(now, name, &ctx.problems(name, sound)));
        }
        let keep: HashSet<&String> = wanted.iter().collect();
        let stale: HashSet<String> = w.active().into_iter().map(|a| a.0).filter(|s| !keep.contains(s)).collect();
        for s in stale {
            events.extend(w.forget(&s));
        }
        drop((w, leases));
        if !events.is_empty() {
            Self::settle(&events);
            ctx.changed();
        }
    }

    fn settle(events: &[Event]) {
        for e in events {
            if let Event::Raised { source, why, .. } = e {
                tracing::warn!("NDI source {source}: {why}");
            }
        }
    }

    pub fn active(&self) -> Vec<Value> {
        self.watcher.lock().map(|w| w.active().into_iter().map(|(s, k, why)| json!({"source": s, "kind": k.name(), "text": why})).collect()).unwrap_or_default()
    }

    /// Release every receiver and clear what was raised (NDI is stopping, or the service is over).
    pub fn stop(&self) -> bool {
        let leases = self.leases.lock().map(|mut l| std::mem::take(&mut *l)).unwrap_or_default();
        let events: Vec<Event> = self
            .watcher
            .lock()
            .map(|mut w| {
                let sources: HashSet<String> = w.active().into_iter().map(|a| a.0).collect();
                sources.iter().flat_map(|s| w.forget(s)).collect()
            })
            .unwrap_or_default();
        drop(leases);
        self.on_service.store(false, std::sync::atomic::Ordering::SeqCst);
        !events.is_empty()
    }

    pub fn snapshot(&self, cfg: &Value, auto: &[String]) -> Value {
        let mut out = cfg.clone();
        out["relied"] = json!(Self::relied(cfg, auto));
        out["active"] = json!(self.active());
        out["in_service"] = json!(self.on_service.load(std::sync::atomic::Ordering::SeqCst));
        out
    }
}
