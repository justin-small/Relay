//! The operator panel's page (#719) served by the Rust server: the gates of `_page_gate`, and the sidebar, tabs, scripts and
//! styles of the modules that are on, for the user asking. Real database, real modules, the real `admin.html`.

use std::net::SocketAddr;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use axum::body::Body;
use axum::http::{header, Method, Request, StatusCode};
use axum::Router;
use http_body_util::BodyExt;
use relay_auth::{Auth, Cost, NewUser};
use relay_core::future::BoxFuture;
use relay_core::{AdminPage, AppError, Config, Host, Manifest, Module, Permission, Registry, Shutdown};
use relay_server::accounts::{admin_route, merge_admin, shell_view, Accounts, Shell};
use relay_server::forward::Conn;
use relay_server::pages::Pages;
use serde_json::{json, Value};
use tower::ServiceExt;

fn password() -> &'static str {
    static P: std::sync::OnceLock<String> = std::sync::OnceLock::new();
    P.get_or_init(|| format!("{:016x}{:016x}", rand::random::<u64>(), rand::random::<u64>()))
}

struct Site {
    dir: PathBuf,
    auth: Auth,
    registry: Arc<Registry>,
    router: Router,
}

impl Drop for Site {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.dir);
    }
}

async fn site(tag: &str, on: &[&str]) -> Site {
    let dir = std::env::temp_dir().join(format!("relay-adminpage-{tag}-{}-{}", std::process::id(), relay_auth::now().replace(':', "")));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.json"), "{}").unwrap();
    let config = Arc::new(Config::load(&dir.join("config.json")).unwrap());
    for id in on {
        config.set_module_enabled(id, true).unwrap();
    }
    let mut registry = Registry::new(Arc::clone(&config), Host::new(Shutdown::new()));
    let modules = relay_server_modules();
    // The server tells the role table what each module adds (`modulehost::register_permissions`); `can` knows nothing before that.
    let _ = relay_server::modulehost::register_permissions(&modules);
    for module in modules {
        registry.register(module).unwrap();
    }
    let registry = Arc::new(registry);
    let auth = Auth::open(&dir.join("relay.db"), Cost::fast()).await.unwrap();
    let app_dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app");
    let acc = Accounts::with_fail_delay(auth.clone(), Pages::new(&app_dir), dir.join("config.json"), Duration::ZERO);
    let shell = Arc::new(Shell { registry: Arc::clone(&registry), front: None, upstream: None });
    Site { dir, auth, registry, router: admin_route(acc, shell) }
}

/// A module that is only a declaration: the server's tests may not name a module crate (`tests/test_rust_modules.py`), and what is
/// checked here is the page the server draws from what a module declares, using the real templates under `app/modules/<id>/`.
struct Stub {
    manifest: Manifest,
    pages: &'static [AdminPage],
    scripts: &'static [&'static str],
    styles: &'static [&'static str],
    permissions: &'static [Permission],
}

impl Module for Stub {
    fn manifest(&self) -> &Manifest {
        &self.manifest
    }
    fn permissions(&self) -> &'static [Permission] {
        self.permissions
    }
    fn admin_pages(&self) -> &'static [AdminPage] {
        self.pages
    }
    fn admin_scripts(&self) -> &'static [&'static str] {
        self.scripts
    }
    fn admin_styles(&self) -> &'static [&'static str] {
        self.styles
    }
    fn start(&self, _host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async { Ok(()) })
    }
    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

fn stub(id: &'static str, section: &'static str, pages: &'static [AdminPage], scripts: &'static [&'static str], styles: &'static [&'static str]) -> Arc<dyn Module> {
    let permissions: &'static [Permission] = Box::leak(vec![Permission { id: Box::leak(format!("{id}.view").into_boxed_str()), label: "See it", group: "Stub", min_role: "viewer" }].into_boxed_slice());
    Arc::new(Stub {
        manifest: Manifest { id, name: id, version: "1.0.0", summary: "A stub.", settings_key: id, guide: "", uses: &[], route_prefixes: &[], renamed_from: "", section },
        pages,
        scripts,
        styles,
        permissions,
    })
}

const fn page(id: &'static str, title: &'static str, tab_of: &'static str) -> AdminPage {
    AdminPage { id, title, template: "admin.html", icon: "<i/>", permission: "", tab_of }
}

const REMOTE: &[AdminPage] = &[page("remoteaccess", "Remote access", "")];
const SIGNAGE: &[AdminPage] = &[page("signage", "Signage", "")];
const WEB: &[AdminPage] = &[page("signageweb", "Web & YouTube", "signage")];

fn relay_server_modules() -> Vec<Arc<dyn Module>> {
    vec![
        stub("remote_access", "", REMOTE, &["remoteaccess.js"], &["remote_access.css"]),
        stub("signage", "Screens", SIGNAGE, &["signage.js", "player.js"], &["signage.css"]),
        stub("signage_web", "Screens", WEB, &["signageweb.js"], &[]),
    ]
}

struct Reply {
    status: StatusCode,
    headers: axum::http::HeaderMap,
    body: String,
}

impl Site {
    async fn get(&self, uri: &str, cookie: &str) -> Reply {
        let mut req = Request::builder().method(Method::GET).uri(uri).header("cookie", cookie).body(Body::empty()).unwrap();
        req.extensions_mut().insert(Conn { peer: "192.168.1.50:5000".parse::<SocketAddr>().unwrap(), local_port: 8443, secure: true });
        let (parts, body) = self.router.clone().oneshot(req).await.unwrap().into_parts();
        Reply { status: parts.status, headers: parts.headers, body: String::from_utf8_lossy(&body.collect().await.unwrap().to_bytes()).into_owned() }
    }

    async fn sign_in_as(&self, username: &str, role: &str) -> String {
        let user = self.auth.create_user(NewUser { username, name: username, role, password: password(), temporary: false }, None).await.unwrap();
        let session = self.auth.start_session(&user, "password", None, "").await.unwrap();
        format!("relay_admin={}", session.token)
    }
}

#[tokio::test]
async fn the_gates_answer_as_the_apps_page_gate_does() {
    let s = site("gates", &[]).await;
    let r = s.get("/admin", "").await;
    assert_eq!(r.status, StatusCode::SERVICE_UNAVAILABLE, "no account yet and no app to take the first-run form: a sentence, not a loop");
    let member = s.sign_in_as("mem", "member").await;
    let r = s.get("/admin", "").await;
    assert_eq!(r.status, StatusCode::OK);
    assert!(r.body.contains("<form"), "no session shows the sign-in form");
    assert_eq!(r.headers[header::CACHE_CONTROL], "no-store");
    let r = s.get("/admin", &member).await;
    assert_eq!((r.status, r.headers[header::LOCATION].to_str().unwrap()), (StatusCode::SEE_OTHER, "/home"), "a member never opens the panel");
}

#[tokio::test]
async fn the_sidebar_lists_only_modules_that_are_on_to_a_user_who_may_see_them() {
    let s = site("nav", &["signage", "signage_web", "remote_access"]).await;
    let owner = s.sign_in_as("boss", "owner").await;
    let r = s.get("/admin", &owner).await;
    assert_eq!(r.status, StatusCode::OK, "{}", r.body);
    for page in ["signage", "remoteaccess"] {
        assert!(r.body.contains(&format!("id=\"page-{page}\"")), "{page} has a page");
    }
    assert!(r.body.contains("/static/modules/signage/signage.js"), "a module's script is loaded");
    assert!(r.body.contains("/static/modules/signage_web/signageweb.js"));
    assert!(r.body.contains("/static/modules/remote_access/remote_access.css"), "and its stylesheet");
    assert!(r.body.contains("Web &amp; YouTube"), "signage_web is a tab of the Signage page, titled as Python titles it");

    s.registry.set_enabled("signage_web", false).await.unwrap();
    s.registry.set_enabled("remote_access", false).await.unwrap();
    let r = s.get("/admin", &owner).await;
    assert!(!r.body.contains("signageweb.js") && !r.body.contains("remote_access.css") && !r.body.contains("Web &amp; YouTube"), "a module that is off adds nothing");
    assert!(r.body.contains("signage.js"));
}

#[test]
fn the_view_is_cut_down_to_the_permissions_of_the_user() {
    let s = tokio::runtime::Builder::new_current_thread().enable_all().build().unwrap().block_on(site("view", &["signage", "signage_web", "remote_access"]));
    let all = |_: &str| true;
    let view = s.registry.admin_view(&all);
    let ids: Vec<&str> = view.nav.iter().map(|e| e["id"].as_str().unwrap()).collect();
    assert_eq!(ids, vec!["signage", "remote_access"], "Screens before Modules");
    assert_eq!(view.tabs["signage"][0]["id"], "signageweb");
    assert_eq!(view.scripts, vec!["remote_access/remoteaccess.js", "signage/signage.js", "signage/player.js", "signage_web/signageweb.js"]);
    let only_signage = |p: &str| p == "signage.view";
    let view = s.registry.admin_view(&only_signage);
    assert_eq!(view.nav.len(), 1);
    assert!(view.tabs.is_empty(), "the web tab needs signage_web.view");
    assert_eq!(view.scripts, vec!["signage/signage.js", "signage/player.js"]);
    assert_eq!(view.styles, vec!["signage/signage.css"]);
    let none = |_: &str| false;
    assert_eq!(s.registry.admin_view(&none), Default::default(), "no page, no entry, no script");
}

#[test]
fn modules_only_the_app_has_are_listed_beside_the_ones_the_server_has() {
    let app = vec![
        json!({"id": "reaper", "name": "REAPER", "version": "1.0.0", "guide": "g", "section": "Show", "health": "idle", "detail": "Add one.", "tabbed": false,
               "scripts": ["reaper.js"], "styles": ["reaper.css"],
               "pages": [{"id": "reaper", "title": "REAPER", "icon": "<i/>", "template": "reaper.html", "permission": "reaper.view", "tab_of": ""}]}),
        // The server has signage itself: the app's copy is not listed twice.
        json!({"id": "signage", "name": "Signage (the app's)", "version": "0", "guide": "", "section": "Screens", "health": "ok", "detail": "", "tabbed": false,
               "scripts": ["old.js"], "styles": [], "pages": [{"id": "signage", "title": "Signage", "icon": "", "template": "admin.html", "permission": "signage.view", "tab_of": ""}]}),
        json!({"id": "unifi", "name": "UniFi", "version": "1.0.0", "guide": "", "section": "", "health": "ok", "detail": "", "tabbed": false, "scripts": [], "styles": [],
               "pages": [{"id": "unifi", "title": "Internet", "icon": "", "template": "unifi_internet.html", "permission": "unifi.view", "tab_of": "network"},
                         {"id": "lost", "title": "Lost", "icon": "", "template": "x.html", "permission": "unifi.view", "tab_of": "nowhere"}]}),
    ];
    let own: std::collections::BTreeSet<&str> = ["signage"].into_iter().collect();
    let view = merge_admin(Default::default(), &app, &own, &|_| true);
    let nav: Vec<(&str, &str)> = view.nav.iter().map(|e| (e["id"].as_str().unwrap(), e["section"].as_str().unwrap())).collect();
    assert_eq!(nav, vec![("reaper", "Show")], "the server's own id is skipped; a tab-only module has no sidebar entry");
    assert_eq!(view.nav[0]["pages"][0]["template"], "reaper/reaper.html");
    assert_eq!(view.tabs["network"][0]["template"], "unifi/unifi_internet.html");
    assert_eq!(view.tabs.len(), 1, "a page that is a tab of nothing core has is dropped");
    assert_eq!(view.scripts, vec!["reaper/reaper.js"]);
    let view = merge_admin(Default::default(), &app, &own, &|p| p == "unifi.view");
    assert!(view.nav.is_empty() && view.scripts.is_empty() && view.styles.is_empty(), "a user who may not see REAPER gets none of it");
}

#[test]
fn the_look_is_every_key_with_an_allowed_value() {
    assert_eq!(
        shell_view(&json!({})),
        json!({"ui_preset": "relay", "ui_mode": "dark", "ui_sidebar": "inset", "ui_collapse": "icon", "ui_layout": "centered", "ui_navbar": "sticky", "ui_font": "system", "ui_collapsed": false})
    );
    let v = shell_view(&json!({"ui_preset": "brutalist", "ui_mode": "purple", "ui_collapsed": true, "ui_font": 3}));
    assert_eq!((v["ui_preset"].as_str(), v["ui_mode"].as_str(), v["ui_font"].as_str(), v["ui_collapsed"].as_bool()), (Some("brutalist"), Some("dark"), Some("system"), Some(true)));
    let _: Value = v;
}
