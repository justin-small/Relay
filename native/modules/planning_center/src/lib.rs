//! Planning Center Services (#80, #81, #122; ported from `app/modules/planning_center` in #666): today's plan and its LIVE state on panels,
//! panels that move LIVE on, and the rundown on /screen. A module (#157): switched on and off at run time; while off it contacts Planning
//! Center never and adds no route, page or feed.
//!
//! Signed in with a Personal Access Token saved on its admin page. The token is stored whole (sealed) in config.json under `planning_center`
//! but only whether one is saved ever reaches the browser. While on it provides [`relay_core::caps::PLAN`] and the signage source `pc_text`.

// axum's `Response` is the error half of route helpers: the shape of the Python it ports.
#![allow(clippy::result_large_err)]

pub const KEY: &str = "planning_center";

pub mod client;
pub mod control;
pub mod feed;
pub mod follower;
pub mod plan;
pub mod routes;
pub mod rundown;
pub mod settings;
pub mod signagesync;

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, OnceLock};

use axum::http::HeaderMap;
use axum::response::Response;
use axum::Router;
use relay_core::caps::{PlanCap, PLAN};
use relay_core::future::BoxFuture;
use relay_core::screens::SIGNAGE_SOURCE;
use relay_core::{AdminPage, AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::{json, Value};

use crate::client::{Client, Services};
use crate::control::Control;
use crate::feed::ServiceFeed;
use crate::follower::{Follower, MakeClient};
use crate::routes::Ctx;
use crate::signagesync::{Memory, PlanSync};

pub const SERVICE_STREAM: &str = "service";
/// The rundown for /screen (#148): names, mics and notes, so only while the operator has it on, and never through the remote access tunnel.
pub const RUNDOWN_STREAM: &str = "rundown";

pub const MANIFEST: Manifest = Manifest {
    id: "planning_center",
    name: "Planning Center",
    version: "1.4.0",
    summary: "Shows today's service plan and its LIVE item on caption panels, lets chosen panels move Services LIVE on, and puts the full rundown (clock times, teams, song leaders and mics) on /screen.",
    settings_key: KEY,
    guide: "https://github.com/justin-small/relay#planning-center-services-live",
    uses: &["signage"],
    route_prefixes: &["/api/admin/planning-center", "/api/panels"],
    renamed_from: "",
    section: "Show",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "planning_center.view", label: "See the Planning Center setup and plan", group: "Planning Center", min_role: "viewer" },
    Permission { id: "planning_center.control", label: "Choose which plan Relay follows today", group: "Planning Center", min_role: "operator" },
    Permission { id: "planning_center.manage", label: "Change the Planning Center setup", group: "Planning Center", min_role: "admin" },
];

const PAGES: &[AdminPage] = &[AdminPage { id: "planningcenter", title: "Planning Center", template: "admin.html", icon: r#"<path d="M3 13V6l5-4 5 4v7zM6.5 13V9h3v4"/>"#, permission: "", tab_of: "" }];

struct Parts {
    ctx: Ctx,
    plan: Arc<PlanCap>,
    memory: Arc<Memory>,
}

pub struct PlanningCenter {
    parts: OnceLock<Parts>,
    maker: Mutex<Option<MakeClient>>,
    memory: Mutex<Option<Arc<Memory>>>,
    tasks: Mutex<Vec<tokio::task::JoinHandle<()>>>,
}

impl Default for PlanningCenter {
    fn default() -> Self {
        Self::new()
    }
}

impl PlanningCenter {
    pub fn new() -> Self {
        Self { parts: OnceLock::new(), maker: Mutex::new(None), memory: Mutex::new(None), tasks: Mutex::new(Vec::new()) }
    }

    /// With the client the caller builds for a saved token, in place of the real one: a test's, answering from a table.
    pub fn with_client(maker: MakeClient) -> Self {
        let me = Self::new();
        *me.maker.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(maker);
        me
    }

    /// Keep the signage sync's memory in `memory` instead of the state folder.
    pub fn with_memory(self, memory: Arc<Memory>) -> Self {
        *self.memory.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(memory);
        self
    }

    /// The parts the routes and tests reach: the follower, the Service feed, the presses and the signage sync.
    pub fn ctx(&self) -> Option<&Ctx> {
        self.parts.get().map(|p| &p.ctx)
    }
}

impl Module for PlanningCenter {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn defaults(&self) -> Value {
        settings::clean_stored(&json!({}))
    }

    fn secret_settings(&self) -> &'static [&'static str] {
        &["secret"]
    }

    fn permissions(&self) -> &'static [Permission] {
        PERMISSIONS
    }

    fn clean(&self, raw: &Value) -> Value {
        settings::clean_stored(raw)
    }

    fn admin_pages(&self) -> &'static [AdminPage] {
        PAGES
    }

    fn admin_scripts(&self) -> &'static [&'static str] {
        &["planningcenter.js", "pcrundown.js", "pcchannels.js", "pcsignage.js"]
    }

    fn admin_styles(&self) -> &'static [&'static str] {
        &["planningcenter.css"]
    }

    fn routes(&self) -> Router {
        match self.parts.get() {
            Some(p) => routes::router(p.ctx.clone()),
            None => Router::new(),
        }
    }

    fn streams(&self) -> &'static [&'static str] {
        &[SERVICE_STREAM, RUNDOWN_STREAM]
    }

    fn stream(&self, name: &str, query: &BTreeMap<String, String>, headers: &HeaderMap) -> Option<Response> {
        let c = &self.parts.get()?.ctx;
        match name {
            SERVICE_STREAM => Some(routes::service_stream(c, query)),
            RUNDOWN_STREAM => Some(routes::rundown_stream(c, query, headers)),
            _ => None,
        }
    }

    fn attach(&self, host: &Host) {
        let maker: MakeClient = self.maker.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone().unwrap_or_else(|| {
            let net = host.net().clone();
            Arc::new(move |settings: &Value| Client::from_settings(&net, settings).map(|c| Arc::new(c) as Arc<dyn Services>))
        });
        let memory = self.memory.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone().unwrap_or_else(|| Arc::new(Memory::new()));
        let follower = Follower::new(host.clone(), Arc::clone(&maker));
        let feed = ServiceFeed::new(host.clone(), Arc::clone(&follower));
        let control = Control::new(host.clone(), Arc::clone(&follower), Arc::clone(&feed));
        let sync = PlanSync::new(host.clone(), Arc::clone(&follower), Arc::clone(&maker), Arc::clone(&memory));
        let ctx = Ctx { host: host.clone(), follower, feed, control, sync, make_client: maker };
        let (f1, f2, f3, f4, f5, f6) = (ctx.follower.clone(), ctx.follower.clone(), ctx.follower.clone(), ctx.follower.clone(), ctx.follower.clone(), ctx.follower.clone());
        let feed = Arc::clone(&ctx.feed);
        let plan = Arc::new(PlanCap {
            service_types: Box::new(move || f1.settings()["service_types"].as_array().into_iter().flatten().filter_map(|v| v.as_str().map(str::to_owned)).collect()),
            offered: Box::new(move || feed.offered()),
            label: Box::new(move || plan_label(&f2)),
            starts_at: Box::new(move || starts_at(&f3)),
            times: Box::new(move |from, to| service_times(&f4, from, to)),
            people: Box::new(move || people(&f5)),
            teams: Box::new(move || teams(&f6)),
        });
        let _ = self.parts.set(Parts { ctx, plan, memory });
    }

    fn detach(&self, host: &Host) {
        host.withdraw_all(MANIFEST.id);
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            let Some(p) = self.parts.get() else { return Ok(()) };
            // What core and other modules ask for by name (#523), offered only while the module is on.
            host.provide(MANIFEST.id, PLAN, Arc::clone(&p.plan));
            let source = signagesync::source(Arc::clone(&p.memory));
            host.provide(MANIFEST.id, &format!("{SIGNAGE_SOURCE}{}", signagesync::KIND), Arc::new(source));
            let mut tasks = self.tasks.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            // Idle until a token is saved and a service type followed, and until plan files for signage are switched on.
            tasks.push(tokio::spawn(Arc::clone(&p.ctx.follower).run()));
            tasks.push(tokio::spawn(Arc::clone(&p.ctx.sync).run()));
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            let tasks: Vec<_> = self.tasks.lock().unwrap_or_else(std::sync::PoisonError::into_inner).drain(..).collect();
            for t in tasks {
                t.abort();
                let _ = t.await;
            }
            if let Some(p) = self.parts.get() {
                p.memory.forget();
                p.ctx.host.withdraw_all(MANIFEST.id);
            }
        })
    }

    fn health(&self) -> Health {
        let Some(p) = self.parts.get() else { return Health::idle() };
        let st = p.ctx.follower.settings();
        if plan::s(&st, "app_id").is_empty() || plan::s(&st, "secret").is_empty() {
            return Health::new(HealthState::Warn, "No token saved");
        }
        let n = st["service_types"].as_array().map_or(0, Vec::len);
        if n == 0 {
            return Health::new(HealthState::Warn, "Connected, no service types followed");
        }
        if p.ctx.follower.tracks().iter().any(|t| t.error.is_some()) {
            return Health::new(HealthState::Error, "Planning Center is not answering");
        }
        Health::new(HealthState::Ok, if n == 1 { "Following 1 service type" } else { "Following service types" })
    }
}

fn plan_label(f: &Follower) -> String {
    for t in f.tracks() {
        let Some(plan) = f.state(&t.st, None).and_then(|s| s["plan"].as_object().map(|_| s["plan"].clone())) else { continue };
        let first = plan["times"].as_array().into_iter().flatten().find_map(|t| plan::f(t, "starts_at"));
        let title = plan::s(&plan, "title");
        return match first {
            None => title,
            Some(at) => format!("{title} {}", f.zone().clock(at)).trim().to_owned(),
        };
    }
    String::new()
}

fn starts_at(f: &Follower) -> Option<f64> {
    let at = follower::now();
    f.tracks().iter().flat_map(|t| t.plan.iter().flat_map(|p| p["times"].as_array().cloned().unwrap_or_default())).filter_map(|t| plan::f(&t, "starts_at")).filter(|s| *s > at).fold(None, |best: Option<f64>, s| Some(best.map_or(s, |b| b.min(s))))
}

fn service_times(f: &Follower, from: i64, to: i64) -> Vec<i64> {
    let mut out = Vec::new();
    for t in f.tracks() {
        for time in t.plan.iter().flat_map(|p| p["times"].as_array().cloned().unwrap_or_default()) {
            if time["type"] == "service" {
                if let Some(at) = plan::f(&time, "starts_at") {
                    let at = at as i64;
                    if from <= at && at < to {
                        out.push(at);
                    }
                }
            }
        }
    }
    out
}

/// Everyone scheduled on the followed plans, once each.
fn people(f: &Follower) -> Vec<Value> {
    let mut found: BTreeMap<String, Value> = BTreeMap::new();
    for t in f.tracks() {
        for m in &t.team {
            if !plan::s(m, "person").is_empty() && !plan::s(m, "name").is_empty() {
                found.insert(plan::s(m, "person"), json!({"id": m["person"], "name": m["name"]}));
            }
        }
    }
    found.into_values().collect()
}

/// Every team of the followed service types with its positions.
fn teams(f: &Follower) -> Vec<Value> {
    let mut found: Vec<Value> = Vec::new();
    for t in f.tracks() {
        for team in &t.catalog {
            let id = plan::s(team, "id");
            let at = found.iter().position(|x| plan::s(x, "id") == id);
            let mine = match at {
                Some(i) => &mut found[i],
                None => {
                    found.push(json!({"id": id, "name": team["name"], "positions": []}));
                    found.last_mut().unwrap_or_else(|| unreachable!("just pushed"))
                }
            };
            let known: Vec<String> = mine["positions"].as_array().into_iter().flatten().map(|p| plan::s(p, "id")).collect();
            for p in team["positions"].as_array().into_iter().flatten() {
                if !known.contains(&plan::s(p, "id")) {
                    if let Some(a) = mine["positions"].as_array_mut() {
                        a.push(p.clone());
                    }
                }
            }
        }
    }
    found
}
