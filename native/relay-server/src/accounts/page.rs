//! The operator panel's page (`GET /admin`, #719): the sign-in, password and first-run gates of `_page_gate`, then `admin.html`
//! with the sidebar, tabs, scripts and styles of the modules that are on. The modules the Rust server has come from its registry;
//! the ones only the Python app has (until they are ported) come from the app's `/api/internal/front/admin`, so the panel lists
//! both and the Python app is not what draws it.

use std::collections::BTreeSet;
use std::sync::Arc;

use axum::extract::{Request, State};
use axum::response::Response;
use relay_core::registry::{AdminView, NAV_SECTIONS};
use relay_core::module::TAB_PAGES;
use relay_core::Registry;
use serde_json::{json, Map, Value};

use super::request::*;
use super::*;
use crate::forward::Upstream;
use crate::front::Front;

/// What the page asks of the rest of the server: the modules it has, and the app that has the others.
pub struct Shell {
    pub registry: Arc<Registry>,
    /// Asked for the pages of the modules only the app has. `None` when no app runs behind this server.
    pub front: Option<Front>,
    /// Takes the first-run form (no account yet), which is still the app's.
    pub upstream: Option<Arc<Upstream>>,
}

const SHELL_CHOICES: [(&str, &[&str]); 7] = [
    ("ui_preset", &["relay", "softpop", "tangerine", "brutalist"]),
    ("ui_mode", &["light", "dark", "system"]),
    ("ui_sidebar", &["inset", "sidebar", "floating"]),
    ("ui_collapse", &["icon", "offcanvas"]),
    ("ui_layout", &["centered", "full"]),
    ("ui_navbar", &["sticky", "scroll"]),
    ("ui_font", &["system", "rounded", "serif", "mono"]),
];

/// The panel's look from a person's preferences: every key `prefs.SHELL_DEFAULTS` has, each an allowed value. The first choice
/// is the default, except the mode, which is dark.
pub fn shell_view(stored: &Value) -> Value {
    let mut out = Map::new();
    for (name, allowed) in SHELL_CHOICES {
        let chosen = stored.get(name).and_then(Value::as_str).filter(|v| allowed.contains(v));
        let default = if name == "ui_mode" { "dark" } else { allowed[0] };
        out.insert(name.into(), json!(chosen.unwrap_or(default)));
    }
    out.insert("ui_collapsed".into(), json!(stored.get("ui_collapsed") == Some(&Value::Bool(true))));
    Value::Object(out)
}

/// The admin page's module context for a user: the registry's own modules, then the app's that the registry does not have.
/// `app` is the app's listing (`ModuleRegistry.admin_listing`); `can` is the user's permissions.
pub fn merge(mut view: AdminView, app: &[Value], own: &BTreeSet<&str>, can: &dyn Fn(&str) -> bool) -> AdminView {
    let mut scripts: Vec<(String, String)> = Vec::new();
    let mut styles: Vec<(String, String)> = Vec::new();
    for m in app {
        let Some(id) = m["id"].as_str().filter(|id| !own.contains(id)) else { continue };
        let pages: Vec<&Value> = m["pages"].as_array().into_iter().flatten().collect();
        let allowed: Vec<&Value> = pages.iter().copied().filter(|p| p["permission"].as_str().is_some_and(can)).collect();
        let full = |p: &Value| format!("{id}/{}", p["template"].as_str().unwrap_or(""));
        let entries: Vec<Value> = allowed
            .iter()
            .filter(|p| p["tab_of"].as_str().unwrap_or("").is_empty())
            .map(|p| json!({"id": p["id"], "title": p["title"], "icon": p["icon"], "template": full(p), "module": m["name"], "version": m["version"], "guide": m["guide"]}))
            .collect();
        for p in allowed.iter().filter(|p| p["tab_of"].as_str().is_some_and(|t| TAB_PAGES.contains(&t))) {
            let tab = p["tab_of"].as_str().unwrap_or("").to_owned();
            view.tabs.entry(tab).or_default().push(json!({"id": p["id"], "title": p["title"], "template": full(p), "module": m["name"], "module_id": id, "version": m["version"], "guide": m["guide"]}));
        }
        if !entries.is_empty() {
            let section = m["section"].as_str().filter(|s| NAV_SECTIONS.contains(s)).unwrap_or("Modules");
            view.nav.push(json!({"id": id, "name": m["name"], "section": section, "icon": entries[0]["icon"], "health": m["health"], "detail": m["detail"], "pages": entries, "tabbed": m["tabbed"]}));
        }
        let sees = if pages.is_empty() { can(&format!("{id}.view")) } else { !allowed.is_empty() };
        if sees {
            let files = |key: &str| -> Vec<String> { m[key].as_array().into_iter().flatten().filter_map(Value::as_str).map(|f| format!("{id}/{f}")).collect() };
            scripts.extend(files("scripts").into_iter().map(|f| (id.to_owned(), f)));
            styles.extend(files("styles").into_iter().map(|f| (id.to_owned(), f)));
        }
    }
    // One order for both sources: the sidebar by heading then module id (the app lists modules by folder name), files by module.
    let rank = |e: &Value| (NAV_SECTIONS.iter().position(|s| Some(*s) == e["section"].as_str()), e["id"].as_str().unwrap_or("").to_owned());
    view.nav.sort_by_key(rank);
    let own_files = |v: Vec<String>| v.into_iter().map(|f| (f.split('/').next().unwrap_or("").to_owned(), f));
    let mut all_scripts: Vec<(String, String)> = own_files(std::mem::take(&mut view.scripts)).collect();
    all_scripts.extend(scripts);
    all_scripts.sort_by(|a, b| a.0.cmp(&b.0));
    let mut all_styles: Vec<(String, String)> = own_files(std::mem::take(&mut view.styles)).collect();
    all_styles.extend(styles);
    all_styles.sort_by(|a, b| a.0.cmp(&b.0));
    view.scripts = all_scripts.into_iter().map(|(_, f)| f).collect();
    view.styles = all_styles.into_iter().map(|(_, f)| f).collect();
    for tabs in view.tabs.values_mut() {
        tabs.sort_by(|a, b| a["module_id"].as_str().cmp(&b["module_id"].as_str()));
    }
    view
}

impl Accounts {
    pub(super) async fn admin_page(State((acc, shell)): State<(Arc<Accounts>, Arc<Shell>)>, conn: Option<axum::Extension<Conn>>, req: Request) -> Response {
        let who = Who::of(req.headers(), conn.map(|c| c.0));
        let pw = req.uri().query().is_some_and(|q| q.split('&').any(|p| p == "pw=1"));
        match acc.any_users().await {
            Ok(true) => {}
            Ok(false) => {
                // No account yet: the first-run form is the app's (it stores the OpenAI key, which needs the sealed store).
                return match &shell.upstream {
                    Some(up) => up.forward(req).await,
                    None => detail(503, "Relay is not set up yet and the app that sets it up is not running."),
                };
            }
            Err(e) => return failure(e),
        }
        let user = match acc.auth.session_user(&who.cookie(ADMIN_COOKIE)).await {
            Ok(Some(u)) => u,
            Ok(None) => return Accounts::login_page(&acc, &who, "", 200, "", pw, None).await,
            Err(e) => return failure(e),
        };
        if user.must_change_password {
            return Accounts::password_page(&acc, "", 200, false);
        }
        if !user.can("admin.open") {
            return see_other(HOME_PATH);
        }
        let me = match Accounts::me_payload(&acc, &who, &user).await {
            Ok(v) => v,
            Err(r) => return r,
        };
        let can = |p: &str| user.can(p);
        let own: BTreeSet<&str> = shell.registry.ids().into_iter().collect();
        let app = match &shell.front {
            Some(front) => front.admin().await.unwrap_or_else(|why| {
                tracing::warn!("the app's module pages are not listed: {why}");
                Vec::new()
            }),
            None => Vec::new(),
        };
        let view = merge(shell.registry.admin_view(&can), &app, &own, &can);
        let tabs: Map<String, Value> = view.tabs.into_iter().map(|(k, v)| (k, Value::Array(v))).collect();
        let look = shell_view(&me["prefs"]);
        Accounts::render(
            &acc,
            "admin.html",
            json!({"me": me, "module_nav": view.nav, "module_tabs": tabs, "module_scripts": view.scripts, "module_styles": view.styles, "look": look}),
            200,
        )
    }
}

/// `GET /admin`.
pub fn route(acc: Shared, shell: Arc<Shell>) -> axum::Router {
    axum::Router::new().route("/admin", axum::routing::get(Accounts::admin_page)).with_state((acc, shell))
}
