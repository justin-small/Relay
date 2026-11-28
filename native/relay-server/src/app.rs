//! Start-up and the watch over the network (`run.py`, `app/main.py`'s `_on_lan_change`): open the listeners, mint or reuse the
//! panel certificate, announce on the LAN, and tell the app behind the server what is really bound. Then, every few seconds, look
//! again: a route changed on the Network page, a port whose address moved, a laptop on a new Wi-Fi network. Each moves a
//! listener, re-mints the certificate if its names no longer fit, or re-announces.

use std::collections::BTreeSet;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::Duration;

use relay_core::{Config, Host, Registry, Shutdown};
use serde_json::json;

use crate::announce::{self, Announcer};
use crate::forward::Upstream;
use crate::front::{AppPlan, Front, GroupReport, Report};
use crate::gate::{Kind, Prefixes};
use crate::listen::{DEFAULT_ADMIN_PORT, DEFAULT_VIEWER_PORT};
use crate::netinfo;
use crate::netroute::{self, Policy};
use crate::serve::{GroupInfo, Net, Server, Spec};
use crate::tls::{self, Names, Panel};

/// How often the machine's network is read again (Python's `netinfo.POLL_SECONDS`).
pub const TICK: Duration = Duration::from_secs(5);
/// How long start-up waits for the app behind the server to answer before serving without its listeners.
const APP_WAIT: Duration = Duration::from_secs(60);

pub struct Options {
    pub config_path: PathBuf,
    /// The folder holding `static/`, `templates/` and `modules/<id>/{static,templates}/` (the Python app's `app/`), #654.
    pub app_dir: PathBuf,
    /// `http://127.0.0.1:18080`: the running FastAPI app. `None` serves only the routes the server has.
    pub upstream: Option<String>,
    /// `RELAY_FRONT_TOKEN`: lets the two programs ask each other for the listeners and the bound ports.
    pub token: Option<String>,
    pub version: String,
    pub install_name: String,
    pub announce: bool,
    pub tick: Duration,
    /// Run the audio bus, routes and meters here (#657) instead of in the app behind the server.
    pub rust_audio: bool,
    /// Run alerts, stage messages and panels here (#660) instead of in the app behind the server.
    pub rust_show: bool,
    /// Run live captions here (#659) instead of in the app behind the server. Needs `rust_audio`: the room's audio is a lane of the bus.
    pub rust_captions: bool,
}

/// config.json's keys for the sockets.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Settings {
    pub host: String,
    pub port: u16,
    pub admin_host: String,
    pub admin_port: u16,
    pub admin_fqdn: String,
}

impl Settings {
    pub fn read(cfg: &Config) -> Self {
        let text = |key: &str, default: &str| cfg.get(key).and_then(|v| v.as_str().map(str::to_owned)).unwrap_or_else(|| default.to_owned());
        let number = |key: &str, default: u16| {
            cfg.get(key)
                .and_then(|v| v.as_u64().or_else(|| v.as_str().and_then(|s| s.trim().parse().ok())))
                .and_then(|n| u16::try_from(n).ok())
                .unwrap_or(default)
        };
        Self {
            host: text("host", "0.0.0.0"),
            port: number("port", DEFAULT_VIEWER_PORT),
            admin_host: text("admin_host", "0.0.0.0"),
            admin_port: number("admin_port", DEFAULT_ADMIN_PORT),
            admin_fqdn: text("admin_fqdn", ""),
        }
    }
}

impl Net {
    pub fn read(cfg: &Config) -> Self {
        Self { network: cfg.get("network").unwrap_or_else(|| json!({})), ports: netinfo::interfaces() }
    }
}

pub struct App {
    pub server: Arc<Server>,
    config_path: PathBuf,
    settings: Settings,
    front: Option<Front>,
    panel: Mutex<Panel>,
    announcer: Mutex<Option<Announcer>>,
    notices: Vec<String>,
    last_report: Mutex<Option<Report>>,
    reported: Mutex<bool>,
    tick: Duration,
    shutdown: Shutdown,
}

/// `relay.db`: `RELAY_DATABASE` when set, else beside config.json (`app/db.py` `path`).
fn database_path() -> PathBuf {
    match std::env::var("RELAY_DATABASE").map(|v| v.trim().to_owned()) {
        Ok(v) if !v.is_empty() => PathBuf::from(v),
        _ => relay_core::paths::state_dir().join("relay.db"),
    }
}

/// What modules are handed through `Host` (#660): the database, the screens, the app folder and the addresses this machine is reached at.
fn provide_core(host: &relay_core::Host, pool: &sqlx::SqlitePool, opts: &Options) {
    use relay_core::screens::{AppDir, LinksCap, ScreensCap, APP_DIR, DB, LINKS, SCREENS};
    host.provide("core", DB, Arc::new(pool.clone()));
    host.provide("core", SCREENS, Arc::new(ScreensCap(Arc::new(relay_show::screens::Screens::new(pool.clone())))));
    host.provide("core", APP_DIR, Arc::new(AppDir(opts.app_dir.clone())));
    let path = opts.config_path.clone();
    host.provide(
        "core",
        LINKS,
        Arc::new(LinksCap(Box::new(move |class: &str| {
            let Ok(cfg) = Config::load(&path) else { return Vec::new() };
            let net = Net::read(&cfg);
            let (label, policy) = netroute::core_classes().get(class).copied().unwrap_or((class, Policy::Fallback));
            let resolved = netroute::resolve(class, label, policy, &net.network, &net.ports);
            netroute::link_addresses(&resolved, &net.ports)
        }))),
    );
}

fn core_spec(key: &str, what: &str, class: &str, secure: bool, configured: &str, port: u16) -> Spec {
    let (label, policy) = netroute::core_classes().get(class).copied().unwrap_or((class, Policy::Fallback));
    Spec {
        key: key.to_owned(),
        what: what.to_owned(),
        traffic: Some(class.to_owned()),
        traffic_label: label.to_owned(),
        policy,
        configured: configured.to_owned(),
        port,
        secure,
        kind: if secure { Kind::Panel } else { Kind::Viewer },
    }
}

fn crew_addresses(net: &Net) -> Vec<String> {
    let label = netroute::core_classes().get("crew").map(|c| c.0).unwrap_or("crew");
    let resolved = netroute::resolve("crew", label, Policy::Fallback, &net.network, &net.ports);
    netroute::link_addresses(&resolved, &net.ports)
}

fn names_for(settings: &Settings, net: &Net) -> Names {
    tls::names(&settings.admin_fqdn, &netinfo::lan_ips(&net.ports), &crew_addresses(net))
}

/// The address to announce: the "discovery" class's port when it is routed, else the first LAN address.
fn discovery_ip(net: &Net) -> Option<String> {
    let label = netroute::core_classes().get("discovery").map(|c| c.0).unwrap_or("discovery");
    let resolved = netroute::resolve("discovery", label, Policy::Fallback, &net.network, &net.ports);
    netroute::link_addresses(&resolved, &net.ports).into_iter().next()
}

impl App {
    /// Open everything. Fails with a plain sentence when the viewer or panel port cannot be opened at all.
    pub async fn start(opts: Options, registry: Arc<Registry>, host: Host, shutdown: Shutdown) -> Result<Arc<Self>, String> {
        let cfg = Config::load(&opts.config_path).map_err(|e| format!("{e}"))?;
        let settings = Settings::read(&cfg);
        let net = Net::read(&cfg);

        let upstream = opts.upstream.as_deref().map(Upstream::new);
        let upstream_for_show = upstream.clone();
        let front = match (&opts.upstream, &opts.token) {
            (Some(base), Some(token)) if !token.is_empty() => Some(Front::new(base, token)),
            _ => None,
        };
        let plan = match &front {
            Some(f) => wait_for_plan(f, &shutdown).await,
            None => AppPlan::default(),
        };

        // The app's modules add permissions this server has no module for yet; the role table must know them (`can`, `/api/admin/me`).
        // One at a time: a permission a module here already registered, or one that is not valid, is skipped without losing the rest.
        for p in &plan.permissions {
            let perm = relay_auth::perms::Permission { id: p.id.clone(), label: p.label.clone(), group: p.group.clone(), min_role: p.min_role.clone() };
            if let Err(why) = relay_auth::perms::register(&[perm]) {
                tracing::debug!("the app's permission {} is not added here: {why}", p.id);
            }
        }

        let panel = Panel::start(&tls::cert_dir(), &settings.admin_fqdn, &names_for(&settings, &net))?;
        let mut routes = registry.routes().merge(crate::statics::routes(&opts.app_dir));
        // Accounts (#655): the sign-in forms and the account, token, device and audit routes. When the database cannot be used
        // (an older file than this server reads, a disk problem) the server says why and forwards them to the app instead.
        match relay_auth::Auth::open(&database_path(), relay_auth::Cost::standard()).await {
            Ok(auth) => {
                let auth_pool = auth.pool().clone();
                let auth_pool_show = auth.pool().clone();
                let accounts = crate::accounts::Accounts::new(auth, crate::pages::Pages::new(&opts.app_dir), opts.config_path.clone());
                host.set_authorizer(Arc::new(crate::modulehost::AccountsAuthorizer(Arc::clone(&accounts))));
                provide_core(&host, &auth_pool, &opts);
                let mut audio_api = None;
                if opts.rust_audio {
                    let app = match (&opts.upstream, &opts.token) {
                        (Some(base), Some(token)) if !token.is_empty() => Some(crate::pyapp::PyApp::new(base, token)),
                        _ => None,
                    };
                    if let Some(api) = crate::audio::start(auth_pool, Arc::clone(&accounts), &opts.config_path, app, shutdown.clone()).await {
                        host.provide("core", relay_core::lanes::LANES, Arc::new(relay_core::lanes::LanesCap(Arc::new(crate::lanescap::CoreLanes::new(Arc::clone(&api.bus))))));
                        host.provide(
                            "core",
                            relay_core::routes::ROUTES,
                            Arc::new(relay_core::routes::RoutesCap(Arc::new(crate::routescap::CoreRoutes { routing: Arc::clone(&api.routing), channels: Arc::clone(&api.channels) }))),
                        );
                        if let Some(token) = opts.token.as_deref().filter(|t| !t.is_empty()) {
                            routes = routes.merge(crate::internal::routes(api.clone(), token));
                        }
                        audio_api = Some(Arc::clone(&api));
                        routes = routes.merge(crate::audio::routes(api));
                    }
                }
                let mut show_api = None;
                if opts.rust_show {
                    if let Some(api) = crate::show::start(auth_pool_show, Arc::clone(&accounts), &opts.config_path, upstream_for_show.clone(), shutdown.clone()).await {
                        let alerts = Arc::clone(&api.alerts);
                        host.provide(
                            "core",
                            relay_core::screens::ALERTS,
                            Arc::new(relay_core::screens::AlertsCap(Box::new(move || {
                                alerts.state()["buttons"].as_array().into_iter().flatten().filter(|b| b["state"] == "on").map(|b| json!({"id": b["id"], "to": b["name"], "from": b["from"]})).collect()
                            }))),
                        );
                        host.provide("core", relay_core::caps::PANELS, Arc::new(relay_core::caps::PanelsCap(Arc::new(crate::show::PanelsForModules(Arc::clone(&api.panels))))));
                        show_api = Some(Arc::clone(&api));
                        // With captions here too, `/stream` is the captions' (it hands the alert feed on).
                        routes = routes.merge(crate::show::routes_with(api, !opts.rust_captions));
                    }
                }
                if opts.rust_captions {
                    match audio_api {
                        Some(audio) => {
                            let app = match (&opts.upstream, &opts.token) {
                                (Some(base), Some(token)) if !token.is_empty() => Some(crate::pyapp::PyApp::new(base, token)),
                                _ => None,
                            };
                            let api = crate::captions::start(crate::captions::Start {
                                acc: Arc::clone(&accounts),
                                bus: Arc::clone(&audio.bus),
                                config: Arc::new(relay_config::Settings::new(&opts.config_path)),
                                state_dir: opts.config_path.parent().map(std::path::Path::to_path_buf).unwrap_or_default(),
                                app,
                                upstream: upstream_for_show.clone(),
                                show: show_api,
                                net: host.net().clone(),
                                hub: relay_core::hub::Hub::with(relay_captions::redact::factory(), Box::new(relay_captions::clock::wall)),
                                shutdown: shutdown.clone(),
                                registry: Some(Arc::clone(&registry)),
                            });
                            routes = routes.merge(crate::captions::routes(api));
                        }
                        None => tracing::error!("captions are served by the app behind this server, not by it: they read the room's audio from the audio bus, which needs --rust-audio"),
                    }
                }
                let shell = Arc::new(crate::accounts::Shell { registry: Arc::clone(&registry), front: front.clone(), upstream: upstream.clone() });
                routes = routes.merge(crate::accounts::admin_route(Arc::clone(&accounts), shell)).merge(crate::accounts::routes(accounts));
            }
            Err(e) => tracing::error!("accounts are served by the app behind this server, not by it: {e}"),
        }
        let mut listener_prefixes = plan.listener_prefixes.clone();
        listener_prefixes.extend(registry.listener_prefixes());
        let server = Arc::new(
            Server::new(routes, upstream)
                .with_prefixes(Prefixes { listener: listener_prefixes, panel: plan.panel_prefixes.clone() })
                .with_tls(panel.server_config()),
        );

        server.open(core_spec("viewers", "viewer pages", "viewers", false, &settings.host, settings.port), &net)?;
        if let Err(e) = server.open(core_spec("panel", "operator panel", "crew", true, &settings.admin_host, settings.admin_port), &net) {
            server.close();
            return Err(e);
        }
        let mut taken: BTreeSet<u16> = server.groups().iter().map(|g| g.port).collect();
        let mut notices: Vec<String> = Vec::new();

        let mut wanted: Vec<Spec> = Vec::new();
        for l in registry.listeners() {
            let class = if l.listener.traffic.is_empty() { "viewers" } else { l.listener.traffic };
            let (label, policy) = netroute::core_classes().get(class).copied().unwrap_or((class, Policy::Fallback));
            wanted.push(Spec {
                key: format!("{}.{}", l.module, l.listener.name),
                what: format!("{} ({})", l.module_name, l.listener.label),
                traffic: Some(class.to_owned()),
                traffic_label: label.to_owned(),
                policy,
                configured: settings.host.clone(),
                port: l.port,
                secure: false,
                kind: Kind::Module(Arc::new(l.listener.prefixes.iter().map(|p| (*p).to_owned()).collect())),
            });
        }
        for l in &plan.listeners {
            wanted.push(Spec {
                key: l.key.clone(),
                what: l.what.clone(),
                traffic: Some(l.traffic.clone()),
                traffic_label: l.traffic_label.clone(),
                policy: Policy::parse(&l.policy).unwrap_or(Policy::Fallback),
                configured: settings.host.clone(),
                port: l.port,
                secure: false,
                kind: Kind::Module(Arc::new(l.prefixes.clone())),
            });
        }
        for spec in wanted {
            if taken.contains(&spec.port) {
                notices.push(format!(
                    "{} is set to port {}, which Relay already uses, so it is not served. Choose another port in its settings and restart Relay.",
                    spec.what, spec.port
                ));
                continue;
            }
            let what = spec.what.clone();
            match server.open(spec, &net) {
                Ok(()) => {
                    if let Some(g) = server.groups().last() {
                        taken.insert(g.port);
                    }
                }
                Err(e) => notices.push(format!("{e} Choose another port in its settings and restart Relay.")),
            }
            tracing::debug!(listener = %what, "module listener handled");
        }

        let announcer = opts.announce.then(|| Announcer::new(&opts.install_name, &opts.version));
        let app = Arc::new(Self {
            server,
            config_path: opts.config_path,
            settings,
            front,
            panel: Mutex::new(panel),
            announcer: Mutex::new(announcer),
            notices,
            last_report: Mutex::new(None),
            reported: Mutex::new(false),
            tick: opts.tick,
            shutdown,
        });
        app.announce(&net);
        app.report().await;
        Ok(app)
    }

    pub fn viewer_port(&self) -> u16 {
        self.find("viewers").map(|g| g.port).unwrap_or(self.settings.port)
    }

    pub fn admin_port(&self) -> u16 {
        self.find("panel").map(|g| g.port).unwrap_or(self.settings.admin_port)
    }

    fn find(&self, key: &str) -> Option<GroupInfo> {
        self.server.groups().into_iter().find(|g| g.spec.key == key)
    }

    pub fn fingerprint(&self) -> String {
        self.panel.lock().map(|p| p.fingerprint().to_owned()).unwrap_or_default()
    }

    /// The notices the admin page shows: a module port that could not open, and each listener's own.
    pub fn notices(&self) -> Vec<String> {
        let mut out = self.notices.clone();
        out.extend(self.server.groups().into_iter().flat_map(|g| g.notes));
        out
    }

    /// The lines printed at start-up (`run.py`).
    pub fn banner(&self) -> Vec<String> {
        let groups = self.server.groups();
        let hosts = |g: &GroupInfo| g.hosts.join(", ");
        let mut out = Vec::new();
        for g in &groups {
            let how = match g.spec.key.as_str() {
                "viewers" => "(plain HTTP)",
                "panel" => "(HTTPS, self-signed)",
                _ => "(plain HTTP)",
            };
            let head = match g.spec.key.as_str() {
                "viewers" => "Viewer pages   -> http://".to_owned(),
                "panel" => "Operator panel -> https://".to_owned(),
                _ => format!("{} -> http://", g.spec.what),
            };
            out.push(format!("{head}{} port {}   {how}", hosts(g), g.port));
        }
        for n in self.notices() {
            out.push(format!("  {n}"));
        }
        if let Ok(panel) = self.panel.lock() {
            let primary = if self.settings.admin_fqdn.is_empty() { None } else { Some(self.settings.admin_fqdn.as_str()) };
            out.extend(panel.banner(primary, self.admin_port(), self.viewer_port()));
        }
        out
    }

    fn announce(&self, net: &Net) {
        let Ok(mut slot) = self.announcer.lock() else { return };
        if let Some(a) = slot.as_mut() {
            a.update(discovery_ip(net).map(|ip| announce::Where { ip, port: self.viewer_port() }));
        }
    }

    fn build_report(&self) -> Option<Report> {
        let panel = self.panel.lock().ok()?;
        let groups = self
            .server
            .groups()
            .into_iter()
            .map(|g| GroupReport {
                key: g.spec.key.clone(),
                label: g.spec.what.clone(),
                traffic: g.spec.traffic.clone(),
                configured: g.spec.configured.clone(),
                port: g.port,
                secure: g.spec.secure,
                hosts: g.hosts.clone(),
                notes: g.notes.clone(),
                prefixes: match &g.spec.kind {
                    Kind::Module(p) => p.to_vec(),
                    _ => Vec::new(),
                },
            })
            .collect();
        Some(Report {
            viewer_port: self.viewer_port(),
            admin_port: self.admin_port(),
            notices: self.notices.clone(),
            groups,
            certificate: panel.report(),
        })
    }

    /// Tell the app what is bound, when it has changed or the last try failed.
    async fn report(&self) {
        let Some(front) = &self.front else { return };
        let Some(report) = self.build_report() else { return };
        let unchanged = self.last_report.lock().map(|l| l.as_ref() == Some(&report)).unwrap_or(false);
        let delivered = self.reported.lock().map(|r| *r).unwrap_or(false);
        if unchanged && delivered {
            return;
        }
        let ok = front.report(&report).await.is_ok();
        if !ok {
            tracing::debug!("the app did not take the report; trying again at the next look");
        }
        if let Ok(mut r) = self.reported.lock() {
            *r = ok;
        }
        if let Ok(mut l) = self.last_report.lock() {
            *l = Some(report);
        }
    }

    /// One look at the network: move listeners, renew the certificate, re-announce, report.
    pub async fn look(&self) {
        let Ok(cfg) = Config::load(&self.config_path) else { return };
        let net = Net::read(&cfg);
        self.server.rebind(&net).await;
        let names = names_for(&self.settings, &net);
        if let Ok(mut panel) = self.panel.lock() {
            match panel.refresh(&names) {
                Ok(true) => tracing::info!(fingerprint = %panel.fingerprint(), "the panel certificate was renewed"),
                Ok(false) => {}
                Err(e) => tracing::error!("the panel certificate could not be renewed: {e}"),
            }
        }
        self.announce(&net);
        self.report().await;
    }

    /// Look every tick until shutdown, then stop accepting and say goodbye on the LAN.
    pub async fn run(self: Arc<Self>) {
        loop {
            tokio::select! {
                () = self.shutdown.wait() => break,
                () = tokio::time::sleep(self.tick) => self.look().await,
            }
        }
        self.server.close();
        if let Ok(mut slot) = self.announcer.lock() {
            if let Some(a) = slot.as_mut() {
                a.stop();
            }
        }
    }
}

/// Ask the app for its listeners until it answers (it starts at the same time) or the wait is over.
async fn wait_for_plan(front: &Front, shutdown: &Shutdown) -> AppPlan {
    let deadline = tokio::time::Instant::now() + APP_WAIT;
    loop {
        match front.plan().await {
            Ok(plan) => return plan,
            Err(why) => {
                if tokio::time::Instant::now() >= deadline || shutdown.is_triggered() {
                    tracing::warn!("{why}; serving without the app's module listeners");
                    return AppPlan::default();
                }
                tokio::time::sleep(Duration::from_millis(500)).await;
            }
        }
    }
}
