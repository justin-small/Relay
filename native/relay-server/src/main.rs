//! Relay's Rust server (#652, part of #650). Today it is the skeleton the later issues fill: it reads config.json in place,
//! starts the modules that are on, and waits for Ctrl-C or SIGTERM. Listeners and TLS are #653.

mod modules;

use std::process::ExitCode;
use std::sync::Arc;
use std::time::Duration;

use relay_core::{paths, Config, Host, Registry, Shutdown};
use tracing_subscriber::EnvFilter;

/// How long the modules get to stop before the process exits anyway.
const STOP_GRACE: Duration = Duration::from_secs(10);

#[tokio::main]
async fn main() -> ExitCode {
    // RELAY_LOG takes tracing's filter syntax ("relay_core=debug,info"); the default is info.
    let filter = EnvFilter::try_from_env("RELAY_LOG").unwrap_or_else(|_| EnvFilter::new("info"));
    tracing_subscriber::fmt().with_env_filter(filter).with_writer(std::io::stderr).init();

    match run().await {
        Ok(()) => ExitCode::SUCCESS,
        Err(e) => {
            tracing::error!("Relay could not start: {e}");
            ExitCode::FAILURE
        }
    }
}

async fn run() -> Result<(), relay_core::AppError> {
    let path = paths::config_path();
    tracing::info!(config = %path.display(), "reading config");
    let config = Arc::new(Config::load(&path)?);
    let shutdown = Shutdown::new();
    let mut registry = Registry::new(Arc::clone(&config), Host::new(shutdown.clone()));
    modules::register_all(&mut registry)?;
    let registry = Arc::new(registry);
    registry.start_enabled().await;
    tracing::info!(modules = registry.ids().len(), "Relay is up");

    wait_for_signal().await;
    tracing::info!("shutting down");
    shutdown.trigger();
    if tokio::time::timeout(STOP_GRACE, registry.stop_all()).await.is_err() {
        tracing::warn!("a module did not stop in time; exiting anyway");
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
