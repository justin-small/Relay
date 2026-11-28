//! ProPresenter (#138; ported from `app/modules/propresenter` in #667): follow the slides, put the lyrics and timers on panels and screens, control
//! ProPresenter from the admin page, the slide view and lobby rotation, slide-note tags, and the link to Planning Center (follow the plan,
//! ProPresenter drives Services LIVE). A module (#157): switched on and off at run time; while off it contacts ProPresenter never and adds no route,
//! page, stream or panel feed.
//!
//! Relay only ever talks out to the address the operator typed (through the `propresenter` traffic class's network port); ProPresenter's API has no
//! authentication. Nothing is sent until the operator has switched the integration on and entered an address.

// axum's `Response` is the error half of route helpers: the shape of the Python it ports.
#![allow(clippy::result_large_err)]

pub const KEY: &str = "propresenter";

pub mod client;
pub mod control;
pub mod discover;
pub mod feeds;
pub mod follower;
pub mod lobby;
pub mod pclink;
pub mod plmatch;
pub mod routes;
pub mod routes_ctl;
pub mod settings;
pub mod signagesync;
pub mod signagetags;
pub mod slidetags;
pub mod tagactions;
pub mod view;
pub mod web;

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex, OnceLock, PoisonError};

use axum::http::HeaderMap;
use axum::response::Response;
use axum::Router;
use relay_core::caps::{FeedExtra, ProOffers, FEED_EXTRA, PLAN_CHANGED, SignageLayer, ALERT_STAGE, PANEL_TIMERS_CHANGED, PRO_OFFERS, SIGNAGE_LAYER};
use relay_core::future::BoxFuture;
use relay_core::{AdminPage, AppError, Health, HealthState, Host, Manifest, Module, Permission};
use serde_json::Value;

use crate::client::Client;
use crate::control::Control;
use crate::feeds::{LyricsFeed, TimersFeed};
use crate::lobby::Lobby;
use crate::pclink::Link;
use crate::view::View;
use crate::follower::{Follower, MakeClient, Timing};
use crate::routes::Ctx;
use crate::signagetags::SignageTags;
use crate::slidetags::Tags;

pub const LYRICS_STREAM: &str = "lyrics";
pub const TIMERS_STREAM: &str = "pptimers";

pub const MANIFEST: Manifest = Manifest {
    id: "propresenter",
    name: "ProPresenter",
    version: "1.2.0",
    summary: "Follows ProPresenter's slides: lyrics and timers on panels and screens, control from this page, the slide view and lobby rotation, slide-note tags, and the link to Planning Center.",
    settings_key: KEY,
    guide: "https://github.com/justin-small/relay#propresenter",
    uses: &["planning_center", "signage"],
    route_prefixes: &["/api/admin/propresenter", "/api/stage/live", "/stage"],
    renamed_from: "",
    section: "Show",
};

pub const PERMISSIONS: &[Permission] = &[
    Permission { id: "propresenter.view", label: "See ProPresenter slides, playlists and status", group: "ProPresenter", min_role: "viewer" },
    Permission { id: "propresenter.control", label: "Control ProPresenter: step, trigger, clear, timers", group: "ProPresenter", min_role: "operator" },
    Permission { id: "propresenter.manage", label: "Change the ProPresenter setup and its link to Planning Center", group: "ProPresenter", min_role: "admin" },
];

const PAGES: &[AdminPage] = &[
    AdminPage {
        id: "ppcontrol",
        title: "Control",
        template: "control.html",
        icon: r##"<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M6.5 5l3 1.5-3 1.5z" fill="currentColor"/><path d="M4 13.5h8"/>"##,
        permission: "",
        tab_of: "",
    },
    AdminPage {
        id: "ppsongs",
        title: "Songs",
        template: "songs.html",
        icon: r##"<path d="M6 12.5V3.5l7-1.5v9"/><circle cx="4.5" cy="12.5" r="1.8"/><circle cx="11.5" cy="11" r="1.8"/>"##,
        permission: "",
        tab_of: "",
    },
    AdminPage {
        id: "pplive",
        title: "Slide view and lobby",
        template: "live.html",
        icon: r##"<rect x="1.5" y="3" width="9" height="6.5" rx="1"/><rect x="8" y="7" width="6.5" height="5.5" rx="1" fill="#0a0b0d"/><rect x="8" y="7" width="6.5" height="5.5" rx="1"/>"##,
        permission: "",
        tab_of: "",
    },
    AdminPage {
        id: "propresenter",
        title: "Settings",
        template: "settings.html",
        icon: r##"<rect x="2" y="2.5" width="12" height="8" rx="1"/><path d="M5 13.5h6M8 10.5v3"/>"##,
        permission: "",
        tab_of: "",
    },
];

struct Parts {
    ctx: Ctx,
}

pub struct ProPresenter {
    parts: OnceLock<Parts>,
    maker: Mutex<Option<MakeClient>>,
    timing: Mutex<Timing>,
    tasks: Mutex<Vec<tokio::task::JoinHandle<()>>>,
}

impl Default for ProPresenter {
    fn default() -> Self {
        Self::new()
    }
}

impl ProPresenter {
    pub fn new() -> Self {
        Self { parts: OnceLock::new(), maker: Mutex::new(None), timing: Mutex::new(Timing::default()), tasks: Mutex::new(Vec::new()) }
    }

    /// With the client the caller builds for a saved address, in place of the real one.
    pub fn with_client(maker: MakeClient) -> Self {
        let me = Self::new();
        *me.maker.lock().unwrap_or_else(PoisonError::into_inner) = Some(maker);
        me
    }

    /// With shorter waits (a test makes the stall and the backoff seconds, not minutes).
    pub fn with_timing(self, timing: Timing) -> Self {
        *self.timing.lock().unwrap_or_else(PoisonError::into_inner) = timing;
        self
    }

    /// The parts the routes and tests reach: the follower and the feeds.
    pub fn ctx(&self) -> Option<&Ctx> {
        self.parts.get().map(|p| &p.ctx)
    }
}

/// What a panel's menu may offer from ProPresenter: it rides the panels' status. Both false until ProPresenter is set up.
fn offers(follower: &Arc<Follower>) -> ProOffers {
    let (a, b) = (Arc::clone(follower), Arc::clone(follower));
    ProOffers {
        lyrics: Box::new(move || {
            let s = a.settings();
            settings::configured(&s) && s["lyrics"]["panels"] == Value::Bool(true)
        }),
        timers: Box::new(move || b.configured()),
    }
}

impl Module for ProPresenter {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }

    fn defaults(&self) -> Value {
        settings::default_settings()
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

    fn admin_tabbed(&self) -> bool {
        true
    }

    fn admin_scripts(&self) -> &'static [&'static str] {
        &["propresenter.js", "slideview.js", "ppcontrol.js", "ppsongs.js", "pplive.js", "pclink.js"]
    }

    fn routes(&self) -> Router {
        match self.parts.get() {
            Some(p) => routes::router(p.ctx.clone()),
            None => Router::new(),
        }
    }

    fn streams(&self) -> &'static [&'static str] {
        &[LYRICS_STREAM, TIMERS_STREAM]
    }

    fn stream(&self, name: &str, query: &BTreeMap<String, String>, _headers: &HeaderMap) -> Option<Response> {
        let c = &self.parts.get()?.ctx;
        match name {
            LYRICS_STREAM => {
                let audience = query.get("for").map_or("viewer", String::as_str);
                Some(routes::refuse_audience(c, audience).unwrap_or_else(|| routes::lyrics_stream(c, audience)))
            }
            TIMERS_STREAM => {
                // Timers are not licensed text, so there is no audience switch: the stream is open while ProPresenter is set up (and says
                // `enabled: false` otherwise). `panel` selects that panel's own settings.
                if query.get("for").map_or("panel", String::as_str) != "panel" {
                    return Some(web::detail(400, "unknown audience"));
                }
                let pid = query.get("panel").map_or("", String::as_str);
                if !pid.is_empty() && !(pid.len() == 32 && pid.bytes().all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))) {
                    return Some(web::detail(400, "not a panel id"));
                }
                if !c.timers.allowed() {
                    return Some(web::detail(403, "ProPresenter is not set up"));
                }
                Some(routes::timers_stream(c, pid))
            }
            _ => None,
        }
    }

    fn attach(&self, host: &Host) {
        let maker: MakeClient = self.maker.lock().unwrap_or_else(PoisonError::into_inner).clone().unwrap_or_else(|| {
            let net = host.net().clone();
            Arc::new(move |h: &str, port: u16, view_only| Client::new(&net, h, port, view_only))
        });
        let timing = self.timing.lock().unwrap_or_else(PoisonError::into_inner).clone();
        let follower = Follower::new(host.clone(), maker, timing, Tags::new());
        let lyrics = LyricsFeed::new(Arc::clone(&follower));
        let timers = TimersFeed::new(Arc::clone(&follower));
        let control = Control::new(Arc::clone(&follower));
        let lobby = Lobby::new(Arc::clone(&follower), Arc::clone(&control));
        let view = View::new(Arc::clone(&follower), Arc::clone(&control));
        let sync = crate::signagesync::Sync::new(Arc::clone(&follower), Arc::clone(&control));
        let signage = SignageTags::new(host.clone());
        let link = Link::new(Arc::clone(&follower));
        let _ = self.parts.set(Parts { ctx: Ctx { host: host.clone(), follower, lyrics, timers, control, lobby, view, sync, signage, link } });
    }

    fn detach(&self, host: &Host) {
        host.withdraw_all(MANIFEST.id);
    }

    fn start(&self, host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            let Some(p) = self.parts.get() else { return Ok(()) };
            let c = &p.ctx;
            // What a panel's menu may offer, asked for by name while this module is on.
            host.provide(MANIFEST.id, PRO_OFFERS, Arc::new(offers(&c.follower)));
            // An alert flashing on the panels is shown on ProPresenter's stage screens too.
            let follower = Arc::clone(&c.follower);
            host.listen(
                MANIFEST.id,
                ALERT_STAGE,
                Arc::new(move |arg: &str| {
                    let Ok(v) = serde_json::from_str::<Value>(arg) else { return };
                    let id = v["id"].as_str().unwrap_or("").to_owned();
                    match v["text"].as_str() {
                        Some(text) => follower.alert_started(&id, text),
                        None => follower.alert_ended(&id),
                    }
                }),
            );
            // A panel's own timer settings changed: its readers are told at once.
            let timers = Arc::clone(&c.timers);
            host.listen(MANIFEST.id, PANEL_TIMERS_CHANGED, Arc::new(move |_: &str| timers.refresh()));
            let mut tasks = self.tasks.lock().unwrap_or_else(PoisonError::into_inner);
            // Idle until an address is entered and ProPresenter is switched on on its page.
            tasks.push(tokio::spawn(Arc::clone(&c.follower).run()));
            // Idle until the operator presses Start on the lobby rotation (never started by Relay itself).
            c.lobby.start();
            // Idle until announcement slides for signage are switched on and a playlist is picked.
            c.sync.start();
            // Idle until the Planning Center link is switched on (follow the plan, or ProPresenter drives LIVE).
            c.link.start();
            // The Service feed asks which plan item ProPresenter has up; the link answers while it is following the plan.
            let link = Arc::clone(&c.link);
            host.provide(MANIFEST.id, FEED_EXTRA, Arc::new(FeedExtra(Box::new(move |st: &str| link.feed_extra(st)))));
            let again = Arc::clone(&c.link);
            host.listen(MANIFEST.id, PLAN_CHANGED, Arc::new(move |_: &str| again.changed()));
            // What a slide-note tag does in Relay, and what a tag may put on a screen (signage asks for the layer while it is on).
            tagactions::install(c.follower.tags(), &host, &c.signage);
            let signage = Arc::clone(&c.signage);
            host.provide(MANIFEST.id, &format!("{SIGNAGE_LAYER}tag"), Arc::new(SignageLayer(Box::new(move |screen: &Value, _now: i64| signage.layer(screen)))));
            Ok(())
        })
    }

    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            let tasks: Vec<_> = self.tasks.lock().unwrap_or_else(PoisonError::into_inner).drain(..).collect();
            if let Some(p) = self.parts.get() {
                p.ctx.follower.shutdown();
                p.ctx.lobby.cancel();
                p.ctx.sync.cancel();
                p.ctx.link.cancel();
            }
            for t in tasks {
                t.abort();
                let _ = t.await;
            }
            if let Some(p) = self.parts.get() {
                p.ctx.follower.tags().reset();
                p.ctx.follower.tags().clear_actions();
                p.ctx.signage.forget();
                p.ctx.host.withdraw_all(MANIFEST.id);
            }
        })
    }

    fn health(&self) -> Health {
        let Some(p) = self.parts.get() else { return Health::idle() };
        let f = &p.ctx.follower;
        let st = f.state();
        if st["configured"] != Value::Bool(true) {
            return Health::new(HealthState::Warn, "Not set up: enter the address and switch it on");
        }
        if st["connected"] == Value::Bool(true) {
            return Health::new(HealthState::Ok, "Connected");
        }
        if st["connecting"] == Value::Bool(true) {
            return Health::new(HealthState::Warn, "Connecting");
        }
        if st["error"].as_str().is_some_and(|e| !e.is_empty()) {
            return Health::new(HealthState::Error, "ProPresenter is not answering");
        }
        Health::new(HealthState::Error, "Not connected")
    }
}
