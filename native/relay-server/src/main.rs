//! Relay's Rust server (#652, part of #650). It reads config.json in place, opens the viewer, panel and module ports with the
//! port rules of the Python server (`run.py`, `app/serving.py`, `app/tls.py`), starts the modules that are on, and waits for
//! Ctrl-C or SIGTERM.
//!
//!   relay-server [--upstream http://127.0.0.1:18080] [--front-token TOKEN] [--fingerprint]
//!
//! `--upstream` (or `RELAY_UPSTREAM`) is the running FastAPI app: what the server does not serve itself is forwarded to it, and
//! with `--front-token` (or `RELAY_FRONT_TOKEN`, the same value the app is given) the two exchange the module listeners and the
//! ports really bound. `--app-dir` (or `RELAY_APP_DIR`, default `app`) is the folder with `static/`, `templates/` and `modules/`. `--fingerprint` prints the panel certificate's SHA-256 and exits.

mod modules;

use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use relay_core::{paths, Config, Host, Registry, Shutdown};
use relay_server::announce;
use relay_server::app::{App, Options, Settings, TICK};
use relay_server::serve::Net;
use relay_server::tls::{self, Panel};
use tracing_subscriber::EnvFilter;

/// How long the modules get to stop before the process exits anyway.
const STOP_GRACE: Duration = Duration::from_secs(10);

fn arg(name: &str, env: &str) -> Option<String> {
    let mut it = std::env::args().skip(1);
    while let Some(a) = it.next() {
        if a == name {
            return it.next();
        }
        if let Some(v) = a.strip_prefix(&format!("{name}=")) {
            return Some(v.to_owned());
        }
    }
    std::env::var(env).ok().map(|v| v.trim().to_owned()).filter(|v| !v.is_empty())
}

#[tokio::main]
async fn main() -> ExitCode {
    // RELAY_LOG takes tracing's filter syntax ("relay_core=debug,info"); the default is info.
    let filter = EnvFilter::try_from_env("RELAY_LOG").unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt().with_env_filter(filter).with_writer(std::io::stderr).init();
    if rustls::crypto::ring::default_provider().install_default().is_err() {
        tracing::debug!("a crypto provider was already installed");
    }

    let result = if std::env::args().any(|a| a == "--fingerprint") { fingerprint() } else { run().await };
    match result {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            tracing::error!("Relay could not start: {e}");
            ExitCode::FAILURE
        }
    }
}

/// `relay-server --fingerprint`: print the panel certificate's fingerprint (minting it if there is none).
fn fingerprint() -> Result<(), String> {
    let cfg = Config::load(&paths::config_path()).map_err(|e| e.to_string())?;
    let net = Net::read(&cfg);
    let settings = Settings::read(&cfg);
    let names = tls::names(&settings.admin_fqdn, &relay_server::netinfo::lan_ips(&net.ports), &[]);
    let panel = Panel::start(&tls::cert_dir(), &settings.admin_fqdn, &names)?;
    println!("{}", panel.info.fingerprint);
    Ok(())
}

async fn run() -> Result<(), String> {
    let path = paths::config_path();
    tracing::info!(config = %path.display(), "reading config");
    let config = Arc::new(Config::load(&path).map_err(|e| e.to_string())?);
    let shutdown = Shutdown::new();
    let sealed = Arc::new(relay_config::Settings::new(&path));
    let mut registry = Registry::new(Arc::clone(&config), Host::new(shutdown.clone())
        .with_store(Arc::new(relay_server::modulehost::SealedStore(sealed)))
        .with_net(relay_core::Net::new(Arc::new(relay_server::netsock::RoutedNet::new(
        Arc::clone(&config),
        std::collections::BTreeMap::new(),
    )))));
    modules::register_all(&mut registry).map_err(|e| e.to_string())?;
    let registry = Arc::new(registry);

    let options = Options {
        config_path: path,
        app_dir: arg("--app-dir", "RELAY_APP_DIR").map(Into::into).unwrap_or_else(|| "app".into()),
        upstream: arg("--upstream", "RELAY_UPSTREAM"),
        token: arg("--front-token", "RELAY_FRONT_TOKEN"),
        version: std::env::var("RELAY_VERSION").ok().filter(|v| !v.is_empty()).unwrap_or_else(|| "dev".to_owned()),
        install_name: announce::install_name(),
        announce: announce::enabled(),
        tick: TICK,
        rust_audio: arg("--rust-audio", "RELAY_RUST_AUDIO").is_some_and(|v| v != "0" && !v.eq_ignore_ascii_case("false")) || std::env::args().any(|a| a == "--rust-audio"),
        rust_show: arg("--rust-show", "RELAY_RUST_SHOW").is_some_and(|v| v != "0" && !v.eq_ignore_ascii_case("false")) || std::env::args().any(|a| a == "--rust-show"),
    };
    let app = App::start(options, Arc::clone(&registry), shutdown.clone()).await?;
    registry.host().set_viewer_port(app.viewer_port());
    registry.start_enabled().await;
    for line in app.banner() {
        println!("{line}");
    }
    tracing::info!(modules = registry.ids().len(), viewer_port = app.viewer_port(), admin_port = app.admin_port(), "Relay is up");
    let watch = tokio::spawn(Arc::clone(&app).run());

    wait_for_signal().await;
    tracing::info!("shutting down");
    shutdown.trigger();
    if tokio::time::timeout(STOP_GRACE, async {
        registry.stop_all().await;
        let _ = watch.await;
    })
    .await
    .is_err()
    {
        tracing::warn!("something did not stop in time; exiting anyway");
    }
    Ok(())
}

async fn wait_for_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        match signal(SignalKind::terminate()) {
            Ok(mut term) => {
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {}
                    _ = term.recv() => {}
                }
            }
            Err(_) => {
                let _ = tokio::signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
