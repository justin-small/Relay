//! What relay-core promises (#652): the error table, the on/off switch on routes, config.json compatibility, the state
//! folder and shutdown. Each test runs real code; the module is a small one written here.

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::routing::get;
use axum::Router;
use relay_core::future::BoxFuture;
use relay_core::paths::resolve_config_path;
use relay_core::{AppError, Config, ErrorCode, Host, Manifest, Module, Registry, Shutdown};
use serde_json::{json, Value};
use tower::ServiceExt;

static MANIFEST: Manifest = Manifest {
    id: "demo",
    name: "Demo",
    version: "1.0.0",
    summary: "A module for the tests",
    settings_key: "demo",
    guide: "",
    uses: &[],
    route_prefixes: &["/api/demo"],
    renamed_from: "",
    section: "",
};

#[derive(Default)]
struct Demo {
    on: AtomicBool,
}

impl Module for Demo {
    fn manifest(&self) -> &Manifest {
        &MANIFEST
    }
    fn routes(&self) -> Router {
        Router::new().route("/api/demo/ping", get(|| async { "pong" }))
    }
    fn start(&self, _host: Host, _settings: Value) -> BoxFuture<'_, Result<(), AppError>> {
        Box::pin(async move {
            self.on.store(true, Ordering::SeqCst);
            Ok(())
        })
    }
    fn stop(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move { self.on.store(false, Ordering::SeqCst) })
    }
}

fn folder(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("relay-core-{name}-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("temp folder");
    dir
}

async fn status(app: &Router, path: &str) -> (StatusCode, String) {
    let res = app.clone().oneshot(Request::get(path).body(Body::empty()).expect("request")).await.expect("response");
    let code = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), 4096).await.expect("body");
    (code, String::from_utf8_lossy(&bytes).into_owned())
}

#[tokio::test]
async fn an_error_answers_with_its_fixed_sentence_and_never_the_cause() {
    let secret = "connect to 10.0.0.9:5432 refused (password=hunter2)";
    let res = axum::response::IntoResponse::into_response(AppError::new(ErrorCode::Unavailable).because(secret));
    assert_eq!(res.status(), StatusCode::SERVICE_UNAVAILABLE);
    let bytes = axum::body::to_bytes(res.into_body(), 4096).await.expect("body");
    let body: Value = serde_json::from_slice(&bytes).expect("json");
    assert_eq!(body, json!({ "detail": ErrorCode::Unavailable.sentence() }));
    assert!(!String::from_utf8_lossy(&bytes).contains("hunter2"));
}

#[test]
fn every_code_has_one_status_and_a_distinct_sentence() {
    let codes = [
        ErrorCode::BadRequest, ErrorCode::SignInRequired, ErrorCode::NotAllowed, ErrorCode::NotFound, ErrorCode::Conflict,
        ErrorCode::TooMany, ErrorCode::Upstream, ErrorCode::Unavailable, ErrorCode::Internal,
    ];
    let mut sentences: Vec<&str> = codes.iter().map(|c| c.sentence()).collect();
    sentences.sort_unstable();
    sentences.dedup();
    assert_eq!(sentences.len(), codes.len());
    assert_eq!(ErrorCode::SignInRequired.sentence(), "Sign in required");
}

#[tokio::test]
async fn a_module_that_is_off_answers_404_and_one_that_is_on_answers() {
    let dir = folder("gate");
    let config = Arc::new(Config::load(&dir.join("config.json")).expect("config"));
    let demo = Arc::new(Demo::default());
    let mut registry = Registry::new(Arc::clone(&config), Host::new(Shutdown::new()));
    registry.register(demo.clone()).expect("register");
    let registry = Arc::new(registry);
    let app = registry.routes();

    registry.start_enabled().await;
    assert!(!demo.on.load(Ordering::SeqCst), "an off module is never started");
    let (code, body) = status(&app, "/api/demo/ping").await;
    assert_eq!(code, StatusCode::NOT_FOUND);
    assert!(body.contains("Not found"));

    registry.set_enabled("demo", true).await.expect("switch on");
    assert!(demo.on.load(Ordering::SeqCst) && registry.running("demo"));
    assert_eq!(status(&app, "/api/demo/ping").await, (StatusCode::OK, "pong".to_owned()));

    registry.set_enabled("demo", false).await.expect("switch off");
    assert!(!demo.on.load(Ordering::SeqCst) && !registry.running("demo"));
    assert_eq!(status(&app, "/api/demo/ping").await.0, StatusCode::NOT_FOUND);

    let on_disk: Value = serde_json::from_str(&std::fs::read_to_string(dir.join("config.json")).expect("file")).expect("json");
    assert_eq!(on_disk["modules"]["demo"]["enabled"], json!(false), "the switch is saved where Python keeps it");
    assert_eq!(registry.set_enabled("nothing", true).await.expect_err("unknown id").code(), ErrorCode::NotFound);
}

#[test]
fn a_registry_refuses_a_bad_or_repeated_id() {
    static BAD: Manifest = Manifest { id: "Bad-Id", ..MANIFEST_COPY };
    const MANIFEST_COPY: Manifest = Manifest {
        id: "demo", name: "", version: "1.0.0", summary: "", settings_key: "x", guide: "", uses: &[],
        route_prefixes: &[], renamed_from: "", section: "",
    };
    struct Bad;
    impl Module for Bad {
        fn manifest(&self) -> &Manifest { &BAD }
        fn start(&self, _h: Host, _s: Value) -> BoxFuture<'_, Result<(), AppError>> { Box::pin(async { Ok(()) }) }
        fn stop(&self) -> BoxFuture<'_, ()> { Box::pin(async {}) }
    }
    let dir = folder("ids");
    let config = Arc::new(Config::load(&dir.join("config.json")).expect("config"));
    let mut registry = Registry::new(config, Host::new(Shutdown::new()));
    assert!(registry.register(Arc::new(Bad)).is_err());
    registry.register(Arc::new(Demo::default())).expect("first");
    assert!(registry.register(Arc::new(Demo::default())).is_err());
}

#[test]
fn config_keeps_keys_order_and_sealed_secrets_it_does_not_know() {
    let dir = folder("config");
    let path = dir.join("config.json");
    let original = r#"{"zeta": 1, "openai_api_key": "enc:v1:abc:Zm9v", "modules": {"older": {"enabled": true}}, "alpha": {"deep": [1, 2]}}"#;
    std::fs::write(&path, original).expect("seed");
    let config = Config::load(&path).expect("load");
    assert!(config.module_enabled("older"));
    config.set_module_enabled("newer", true).expect("save");

    let saved: Value = serde_json::from_str(&std::fs::read_to_string(&path).expect("file")).expect("json");
    let keys: Vec<&String> = saved.as_object().expect("object").keys().collect();
    assert_eq!(keys, ["zeta", "openai_api_key", "modules", "alpha"], "order is kept");
    assert_eq!(saved["openai_api_key"], "enc:v1:abc:Zm9v", "a sealed secret is written back untouched");
    assert_eq!(saved["alpha"], json!({"deep": [1, 2]}));
    assert_eq!(saved["modules"]["newer"]["enabled"], json!(true));
    assert!(!path.with_extension("json.tmp").exists(), "no temp file is left behind");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).expect("meta").permissions().mode() & 0o777, 0o600);
    }
}

#[test]
fn config_refuses_a_file_that_is_not_an_object_and_a_missing_file_is_empty() {
    let dir = folder("bad-config");
    let path = dir.join("config.json");
    assert!(Config::load(&path).expect("missing is fine").get("anything").is_none());
    std::fs::write(&path, "{not json").expect("seed");
    assert!(Config::load(&path).is_err());
    std::fs::write(&path, "[1, 2]").expect("seed");
    assert!(Config::load(&path).is_err());
}

#[test]
fn the_state_folder_is_found_in_the_order_python_uses() {
    let cwd = folder("cwd");
    let home = PathBuf::from("/default/home");
    assert_eq!(resolve_config_path(Some("/etc/relay/c.json"), Some("/h"), &cwd, &home), PathBuf::from("/etc/relay/c.json"));
    assert_eq!(resolve_config_path(None, Some("/h"), &cwd, &home), PathBuf::from("/h/config.json"));
    assert_eq!(resolve_config_path(Some("  "), None, &cwd, &home), PathBuf::from("/default/home/config.json"));
    std::fs::write(cwd.join("config.json"), "{}").expect("seed");
    assert_eq!(resolve_config_path(None, None, &cwd, &home), cwd.join("config.json"));
}

#[tokio::test]
async fn shutdown_wakes_every_waiter_and_a_late_waiter_returns_at_once() {
    let shutdown = Shutdown::new();
    let waiter = tokio::spawn({
        let s = shutdown.clone();
        async move { s.wait().await }
    });
    assert!(!shutdown.is_triggered());
    shutdown.trigger();
    shutdown.trigger();
    tokio::time::timeout(Duration::from_secs(5), waiter).await.expect("woke").expect("task");
    tokio::time::timeout(Duration::from_secs(5), shutdown.wait()).await.expect("late waiter");
}

#[test]
fn a_module_finds_what_another_provides_only_while_it_is_provided() {
    let host = Host::new(Shutdown::new());
    assert!(host.using::<String>("gear.list").is_none());
    host.provide("demo", "gear.list", Arc::new(String::from("rack")));
    assert_eq!(host.using::<String>("gear.list").as_deref().map(String::as_str), Some("rack"));
    assert!(host.using::<u32>("gear.list").is_none(), "a different type is not the same thing");
    host.withdraw_all("demo");
    assert!(host.using::<String>("gear.list").is_none());
}

#[test]
fn a_change_does_not_undo_what_another_writer_saved_since_the_file_was_read() {
    let dir = std::env::temp_dir().join(format!("relay-core-stale-{}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let path = dir.join("config.json");
    std::fs::write(&path, r#"{"port": 80}"#).unwrap();
    let cfg = Config::load(&path).unwrap();
    // relay-config's Settings (or the Python server) saves the same file after this one read it
    std::fs::write(&path, r#"{"port": 8123, "history_lines": 90}"#).unwrap();
    cfg.set_module_enabled("demo", true).unwrap();
    let after: Value = serde_json::from_str(&std::fs::read_to_string(&path).unwrap()).unwrap();
    assert_eq!(after["port"], 8123);
    assert_eq!(after["history_lines"], 90);
    assert_eq!(after["modules"]["demo"]["enabled"], true);
    assert!(cfg.module_enabled("demo"));
    let _ = std::fs::remove_dir_all(&dir);
}

#[test]
fn a_module_hears_what_another_announces_until_it_is_withdrawn() {
    use std::sync::atomic::AtomicUsize;
    let host = relay_core::Host::new(relay_core::Shutdown::new());
    let (heard, other) = (Arc::new(AtomicUsize::new(0)), Arc::new(AtomicUsize::new(0)));
    let args = Arc::new(Mutex::new(Vec::<String>::new()));
    let (h, o, a) = (Arc::clone(&heard), Arc::clone(&other), Arc::clone(&args));
    host.listen("crew", "plan.changed", Arc::new(move |arg| {
        h.fetch_add(1, Ordering::SeqCst);
        a.lock().unwrap().push(arg.to_owned());
    }));
    host.listen("mics", "directory.changed", Arc::new(move |_| {
        o.fetch_add(1, Ordering::SeqCst);
    }));
    host.announce("plan.changed", "");
    host.announce("plan.changed", "pco");
    assert_eq!((heard.load(Ordering::SeqCst), other.load(Ordering::SeqCst)), (2, 0), "each listener hears only its topic");
    assert_eq!(args.lock().unwrap().as_slice(), ["", "pco"], "with the argument it was announced with");
    host.announce("nobody.listens", "");
    host.withdraw_all("crew");
    host.announce("plan.changed", "");
    assert_eq!(heard.load(Ordering::SeqCst), 2, "a module taken back hears nothing more");
    host.announce("directory.changed", "pco");
    assert_eq!(other.load(Ordering::SeqCst), 1, "and the others keep listening");
}
