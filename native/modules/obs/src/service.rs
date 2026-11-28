//! The OBS computers together (`service.py`'s `Service`): one [`Machine`] each, restarted when the settings change, and what the rest of Relay asks of all
//! of them: switch a scene on every computer that has it, follow Start and Stop, hand core the channels, and the caption sink.

use std::sync::{Arc, Mutex, PoisonError, RwLock};

use relay_core::caps::{AlertButtonsCap, HubCap, ALERT_BUTTONS, HUB};
use relay_core::languages;
use relay_core::routes::{RoutesCap, ROUTES};
use relay_core::Host;
use serde_json::{json, Value};
use subtle::ConstantTimeEq;

use crate::machine::{Machine, Reporter, Timing};
use crate::proto::ObsError;
use crate::settings::{clean_settings, passwords, public, text, SOURCE_LANGUAGE};
use crate::KEY;

/// The name the caption sink is added to the hub under.
const SINK: &str = "obs";

pub struct Service {
    host: Host,
    timing: Timing,
    machines: RwLock<Vec<Arc<Machine>>>,
    running: Mutex<bool>,
    me: std::sync::Weak<Service>,
}

impl Service {
    pub fn new(host: Host, timing: Timing) -> Arc<Self> {
        Arc::new_cyclic(|me| Self { host, timing, machines: RwLock::new(Vec::new()), running: Mutex::new(false), me: me.clone() })
    }

    /// The saved settings, cleaned.
    pub fn settings(&self) -> Value {
        clean_settings(&self.host.settings(KEY).unwrap_or(Value::Null))
    }

    pub fn machines(&self) -> Vec<Arc<Machine>> {
        self.machines.read().unwrap_or_else(PoisonError::into_inner).clone()
    }

    pub fn machine(&self, id: &str) -> Option<Arc<Machine>> {
        self.machines().into_iter().find(|m| m.id() == id)
    }

    /// The machine whose scene-screen key is `key`, compared in constant time, only while its screen is on.
    pub fn screen_machine(&self, id: &str, key: &str) -> Option<Arc<Machine>> {
        let m = self.machine(id)?;
        let (on, own) = m.screen();
        (on && !own.is_empty() && bool::from(own.as_bytes().ct_eq(key.as_bytes()))).then_some(m)
    }

    pub async fn start(&self) {
        *self.running.lock().unwrap_or_else(PoisonError::into_inner) = true;
        self.add_sink();
        self.restart().await;
    }

    pub async fn stop(&self) {
        *self.running.lock().unwrap_or_else(PoisonError::into_inner) = false;
        if let Some(h) = self.host.using::<HubCap>(HUB) {
            h.0.remove_line_sink(SINK);
        }
        self.close_all().await;
    }

    async fn close_all(&self) {
        let old: Vec<Arc<Machine>> = std::mem::take(&mut *self.machines.write().unwrap_or_else(PoisonError::into_inner));
        for m in old {
            m.stop().await;
        }
    }

    pub async fn restart(&self) {
        self.close_all().await;
        if !*self.running.lock().unwrap_or_else(PoisonError::into_inner) {
            return;
        }
        let s = self.settings();
        let pw = passwords(&s["passwords"]);
        let report: Reporter = {
            let me = self.me.clone();
            Arc::new(move || {
                if let Some(s) = me.upgrade() {
                    s.report_channels();
                }
            })
        };
        let made: Vec<Arc<Machine>> = s["machines"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|cfg| Machine::new(self.host.clone(), cfg, pw.get(cfg["id"].as_str().unwrap_or("")).and_then(Value::as_str).unwrap_or("").to_owned(), self.timing.clone(), Arc::clone(&report)))
            .collect();
        *self.machines.write().unwrap_or_else(PoisonError::into_inner) = made.clone();
        for m in made {
            m.start();
        }
    }

    /// Hand core the caption lines the hub commits. It may run off the event loop, so each caption goes to the runtime the module started on.
    fn add_sink(&self) {
        let Some(hub) = self.host.using::<HubCap>(HUB) else { return };
        let (me, handle) = (self.me.clone(), tokio::runtime::Handle::current());
        hub.0.add_line_sink(
            SINK,
            Arc::new(move |e: &relay_core::hub::LineEvent| {
                let Some(s) = me.upgrade() else { return };
                if e.text.is_empty() {
                    return;
                }
                let _in = handle.enter();
                for m in s.machines().into_iter().filter(|m| m.wants(&e.stream, &e.lang)) {
                    m.caption_line(e.text.clone());
                }
            }),
        );
    }

    /// Tell core the scenes of every computer that answered in this run; one that is off says nothing new, so its scenes do not turn missing.
    pub fn report_channels(&self) {
        let Some(routes) = self.host.using::<RoutesCap>(ROUTES) else { return };
        let all: Vec<Value> = self.machines().iter().flat_map(|m| m.channels()).collect();
        if let Ok(rt) = tokio::runtime::Handle::try_current() {
            rt.spawn(async move { routes.0.report("obs", all).await });
        }
    }

    /// The hook behind a slide tag's `obs=`: every connected computer that has the scene switches to it.
    pub async fn switch_scene(&self, name: &str, source: &str) -> Result<(), String> {
        if name.trim().is_empty() {
            return Err("Name the scene.".to_owned());
        }
        let found: Vec<(Arc<Machine>, String)> = self.machines().into_iter().filter(|m| m.has_connection()).filter_map(|m| Some((Arc::clone(&m), Some(m.scene_named(name)).filter(|s| !s.is_empty())?))).collect();
        if found.is_empty() {
            return Err(format!("No connected OBS computer has a scene called \"{}\".", text(&json!(name), 60)));
        }
        let mut failed = Vec::new();
        for (m, scene) in found {
            match m.set_scene(&scene).await {
                Ok(()) => {
                    let from = if source.is_empty() { String::new() } else { format!(" (from {source})") };
                    self.host.record("obs.scene", &format!("OBS ({}): switched to {}{from}", m.cfg.name, scene.chars().take(60).collect::<String>()), m.id()).await;
                }
                Err(ObsError(_)) => failed.push(m.cfg.name.clone()),
            }
        }
        if failed.is_empty() { Ok(()) } else { Err(format!("OBS did not switch on {}.", failed.join(", "))) }
    }

    /// Relay's Start: each computer begins what it is set to begin with it.
    pub fn stream_started(&self) {
        for m in self.machines() {
            tokio::spawn(async move { m.follow_start().await });
        }
    }

    pub fn stream_stopped(&self) {
        for m in self.machines() {
            tokio::spawn(async move { m.follow_stop().await });
        }
    }

    /// The worst health of any computer.
    pub fn health(&self) -> (&'static str, &'static str) {
        let machines = self.machines();
        if machines.is_empty() {
            return ("warn", "Not set up: add an OBS computer");
        }
        let order = |h: &str| ["error", "warn", "ok", "idle"].iter().position(|x| *x == h).unwrap_or(3);
        let worst = machines.iter().map(|m| m.health().0).min_by_key(|h| order(h)).unwrap_or("idle");
        (worst, match worst {
            "error" => "An OBS computer needs attention",
            "warn" => "An OBS computer has a warning",
            "ok" => "Streaming or recording",
            _ => "Connected, standing by",
        })
    }

    pub fn snapshot(&self) -> Value {
        let mut out = public(&self.settings());
        let mut langs = vec![json!({"id": SOURCE_LANGUAGE, "label": "Source language (the captions)"})];
        langs.extend(languages::target_names(languages::SOURCE.name).into_iter().filter_map(languages::target).map(|t| json!({"id": t.name, "label": t.label})));
        out["languages"] = Value::Array(langs);
        out["alert_buttons"] = self.host.using::<AlertButtonsCap>(ALERT_BUTTONS).map_or_else(|| json!([]), |b| Value::Array(b.0.buttons().into_iter().map(|(id, name)| json!({"id": id, "name": name})).collect()));
        let live: Vec<(String, Value)> = self.machines().iter().map(|m| (m.id().to_owned(), m.snapshot())).collect();
        for m in out["machines"].as_array_mut().into_iter().flatten() {
            let id = m["id"].as_str().unwrap_or("").to_owned();
            m["live"] = live.iter().find(|l| l.0 == id).map_or(Value::Null, |l| l.1.clone());
        }
        out
    }
}
