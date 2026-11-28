//! The admin page this module declares is the one the Python app drew (#719): its sidebar entry, its tabs, its scripts and its
//! styles, compared with the render `tools/capture_pages.py` recorded of `admin.html` with every module on.
//! (Health and its sentence are the module's own and are not compared.)

use std::path::Path;
use std::sync::Arc;

use relay_core::{Config, Host, Module, Registry, Shutdown};
use serde_json::Value;

const ID: &str = "obs";

fn recorded() -> Value {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../relay-server/tests/pages");
    let mut best: Option<Value> = None;
    for e in std::fs::read_dir(dir).expect("relay-server/tests/pages is there").flatten() {
        let name = e.file_name().to_string_lossy().into_owned();
        if !name.starts_with("admin.html.") {
            continue;
        }
        let page: Value = serde_json::from_str(&std::fs::read_to_string(e.path()).unwrap()).unwrap();
        let ctx = &page["context"];
        let lists = ctx["module_nav"].as_array().unwrap().iter().any(|m| m["id"] == ID) || ctx["module_tabs"].as_object().unwrap().values().flat_map(|v| v.as_array().unwrap()).any(|t| t["module_id"] == ID);
        if lists && (best.is_none() || ctx["module_nav"].as_array().unwrap().len() > best.as_ref().unwrap()["context"]["module_nav"].as_array().unwrap().len()) {
            best = Some(page);
        }
    }
    best.expect("a recorded admin page with this module on: run tools/capture_pages.py")["context"].clone()
}

#[test]
fn what_the_module_declares_is_what_python_drew() {
    let dir = std::env::temp_dir().join(format!("relay-obs-admin-{}", std::process::id()));
    std::fs::create_dir_all(&dir).unwrap();
    std::fs::write(dir.join("config.json"), "{}").unwrap();
    let config = Arc::new(Config::load(&dir.join("config.json")).unwrap());
    config.set_module_enabled(ID, true).unwrap();
    let mut registry = Registry::new(config, Host::new(Shutdown::new()));
    let module: Arc<dyn Module> = Arc::new(relay_module_obs::Obs::new());
    registry.register(module).unwrap();
    let view = registry.admin_view(&|_| true);
    let _ = std::fs::remove_dir_all(&dir);

    let want = recorded();
    let mine = |v: &Value| v["id"] == ID;
    let nav = |entries: &Value| -> Vec<Value> {
        entries.as_array().unwrap().iter().filter(|m| mine(m)).map(|m| {
            let mut m = m.clone();
            let o = m.as_object_mut().unwrap();
            o.remove("health");
            o.remove("detail");
            Value::Object(o.clone())
        }).collect()
    };
    assert_eq!(nav(&Value::Array(view.nav.clone())), nav(&want["module_nav"]), "the sidebar entry");

    let tabs = |t: &Value| -> Vec<(String, Value)> {
        let mut out: Vec<(String, Value)> = t.as_object().unwrap().iter().flat_map(|(page, tabs)| tabs.as_array().unwrap().iter().filter(|t| t["module_id"] == ID).map(|t| (page.clone(), t.clone())).collect::<Vec<_>>()).collect();
        out.sort_by(|a, b| a.0.cmp(&b.0));
        out
    };
    let mine_tabs = serde_json::to_value(view.tabs.iter().map(|(k, v)| (k.clone(), Value::Array(v.clone()))).collect::<serde_json::Map<_, _>>()).unwrap();
    assert_eq!(tabs(&mine_tabs), tabs(&want["module_tabs"]), "its tabs on core pages");

    let files = |v: &Value| -> Vec<String> { v.as_array().unwrap().iter().filter_map(Value::as_str).filter(|f| f.starts_with(&format!("{ID}/"))).map(str::to_owned).collect() };
    assert_eq!(view.scripts, files(&want["module_scripts"]), "its scripts, in order");
    assert_eq!(view.styles, files(&want["module_styles"]), "its stylesheets, in order");
}
