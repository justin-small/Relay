//! Relay desktop shell (#183): starts the backend, sits in the tray, shows the panel, starts at login.
//!
//! It does not replace the web UI; it launches and looks after the server. Nothing here is reachable
//! from web content: the webview has no Tauri permissions (capabilities/default.json), the backend is
//! started only from Rust (backend.rs), and the panel is loaded through a loopback proxy that pins the
//! panel's certificate (proxy.rs).

#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

mod backend;
mod paths;
mod proxy;
mod settings;
mod tray;

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;

use backend::{Backend, Status};
use tauri::{AppHandle, Manager, RunEvent, Url, WebviewWindow, WindowEvent};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt as _};
use tauri_plugin_clipboard_manager::ClipboardExt;
use tauri_plugin_dialog::DialogExt;

struct Shell {
    backend: Backend,
    proxy_port: u16,
    /// The start-up page's own URL, so the window can come back to it.
    splash_url: Mutex<Option<Url>>,
    on_panel: AtomicBool,
    quitting: AtomicBool,
}

fn window(app: &AppHandle) -> Option<WebviewWindow> {
    app.get_webview_window("main")
}

fn shell(app: &AppHandle) -> tauri::State<'_, Shell> {
    app.state::<Shell>()
}

pub fn open_panel(app: &AppHandle) {
    if let Some(w) = window(app) {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

/// What the start-up page shows for a backend state (kind is a CSS class on the page).
fn splash_state(snap: &backend::Snapshot) -> serde_json::Value {
    let (kind, title, detail) = match snap.status {
        Status::Error => ("error", "Relay is not running", snap.message.clone()),
        Status::Stopped => ("error", "Relay has stopped", String::new()),
        _ if !snap.message.is_empty() => ("starting", "Starting Relay", snap.message.clone()),
        _ => ("starting", "Starting Relay", "This takes a few seconds.".into()),
    };
    serde_json::json!({ "kind": kind, "title": title, "detail": detail, "log": snap.log_tail })
}

fn push_splash(app: &AppHandle, snap: &backend::Snapshot) {
    if let Some(w) = window(app) {
        let _ = w.eval(format!("window.__relay && window.__relay({})", splash_state(snap)));
    }
}

/// Keep the window, the tray and the start-at-login default in step with the backend.
fn on_backend_change(app: &AppHandle, snap: backend::Snapshot) {
    if let Some(t) = tray::get(app) {
        t.update(&snap);
    }
    let sh = shell(app);
    let live = matches!(snap.status, Status::Running | Status::Capturing);
    if let Some(w) = window(app) {
        if live && !sh.on_panel.swap(true, Ordering::SeqCst) {
            let url = format!("http://127.0.0.1:{}/admin", sh.proxy_port);
            if let Ok(u) = Url::parse(&url) {
                let _ = w.navigate(u);
            }
            first_run_defaults(app);
        } else if !live {
            if sh.on_panel.swap(false, Ordering::SeqCst) {
                if let Some(u) = sh.splash_url.lock().unwrap().clone() {
                    let _ = w.navigate(u);
                }
            }
            push_splash(app, &snap);
        }
    }
}

/// Start at login is on by default after first-run setup, once; after that it is the user's choice.
fn first_run_defaults(app: &AppHandle) {
    let mut s = settings::load();
    if s.autostart_initialised {
        return;
    }
    s.autostart_initialised = true;
    settings::save(&s);
    let _ = app.autolaunch().enable();
    if let Some(t) = tray::get(app) {
        let _ = t.autostart.set_checked(true);
    }
}

fn viewer_link(app: &AppHandle) -> Option<String> {
    let snap = shell(app).backend.snapshot();
    let info = snap.info?;
    let host = info.ips.first().cloned().unwrap_or_else(|| "127.0.0.1".into());
    let port = if info.viewer_port == 80 { String::new() } else { format!(":{}", info.viewer_port) };
    Some(format!("http://{host}{port}/"))
}

pub fn on_menu(app: &AppHandle, id: &str) {
    let sh = shell(app);
    match id {
        "open_panel" => open_panel(app),
        "open_browser" => {
            // The firmware flasher needs Web Serial, which the system webviews do not have.
            let port = sh.backend.pins.admin_port.load(Ordering::SeqCst);
            if port != 0 {
                let _ = open::that(format!("https://127.0.0.1:{port}/admin"));
            }
        }
        "copy_link" => {
            if let Some(link) = viewer_link(app) {
                let _ = app.clipboard().write_text(link);
            }
        }
        "fingerprint" => {
            let fp = sh.backend.snapshot().info.map(|i| i.fingerprint).unwrap_or_default();
            app.dialog()
                .message(format!(
                    "SHA-256 fingerprint of the panel certificate:\n\n{fp}\n\nCheck it against what a browser shows when it warns about the certificate."
                ))
                .title("Certificate fingerprint")
                .show(|_| {});
        }
        "open_logs" => {
            let _ = std::fs::create_dir_all(paths::logs_dir());
            let _ = open::that(paths::logs_dir());
        }
        "open_state" => {
            let _ = open::that(paths::state_dir());
        }
        "restart" => sh.backend.restart(),
        // The in-app updater arrives with #184; until then the release page is the way.
        "updates" => {
            let _ = open::that("https://github.com/justin-small/Relay/releases/latest");
        }
        "autostart" => {
            let manager = app.autolaunch();
            let on = manager.is_enabled().unwrap_or(false);
            let _ = if on { manager.disable() } else { manager.enable() };
            if let Some(t) = tray::get(app) {
                let _ = t.autostart.set_checked(manager.is_enabled().unwrap_or(!on));
            }
        }
        "quit" => begin_quit(app),
        _ => {}
    }
}

/// Stop the backend gracefully (it is force-killed after a timeout), then exit. No backend is left behind.
fn begin_quit(app: &AppHandle) {
    if shell(app).quitting.swap(true, Ordering::SeqCst) {
        return;
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        let backend = shell(&app).backend.clone();
        backend.quit().await;
        app.exit(0);
    });
}

#[cfg(unix)]
async fn wait_for_termination() {
    use tokio::signal::unix::{signal, SignalKind};
    let (Ok(mut term), Ok(mut int)) = (signal(SignalKind::terminate()), signal(SignalKind::interrupt())) else { return };
    tokio::select! { _ = term.recv() => {}, _ = int.recv() => {} }
}

#[cfg(not(unix))]
async fn wait_for_termination() {
    let _ = tokio::signal::ctrl_c().await;
}

fn explain_tray_once(app: &AppHandle) {
    let mut s = settings::load();
    if s.tray_notice_shown {
        return;
    }
    s.tray_notice_shown = true;
    settings::save(&s);
    app.dialog()
        .message("Relay is still running. Closing this window keeps captions going; Relay lives in the tray (the menu bar on a Mac). Use Quit Relay from its menu to stop it.")
        .title("Relay keeps running")
        .show(|_| {});
}

fn main() {
    let minimized = std::env::args().any(|a| a == "--minimized");

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| open_panel(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, Some(vec!["--minimized"])))
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_clipboard_manager::init())
        .on_page_load(|webview, payload| {
            if matches!(payload.event(), tauri::webview::PageLoadEvent::Finished) {
                let app = webview.app_handle();
                if let Some(sh) = app.try_state::<Shell>() {
                    if !sh.on_panel.load(Ordering::SeqCst) {
                        push_splash(app, &sh.backend.snapshot());
                    }
                }
            }
        })
        .setup(move |app| {
            let handle = app.handle().clone();
            let backend = backend::spawn(handle.clone());
            let proxy_port = tauri::async_runtime::block_on(proxy::start(backend.pins.clone()))?;
            let autostart_on = handle.autolaunch().is_enabled().unwrap_or(false);
            app.manage(tray::build(&handle, autostart_on)?);
            let splash = window(&handle).and_then(|w| w.url().ok());
            app.manage(Shell {
                backend: backend.clone(),
                proxy_port,
                splash_url: Mutex::new(splash),
                on_panel: AtomicBool::new(false),
                quitting: AtomicBool::new(false),
            });
            if !minimized {
                open_panel(&handle);
            }
            // A logout, shutdown or `kill` is a quit too: the backend is stopped gracefully first.
            let quit_handle = handle.clone();
            tauri::async_runtime::spawn(async move {
                wait_for_termination().await;
                begin_quit(&quit_handle);
            });
            let mut rx = backend.state.clone();
            tauri::async_runtime::spawn(async move {
                loop {
                    let snap = rx.borrow_and_update().clone();
                    on_backend_change(&handle, snap);
                    if rx.changed().await.is_err() {
                        break;
                    }
                }
            });
            Ok(())
        })
        .on_window_event(|w, event| {
            if let WindowEvent::CloseRequested { api, .. } = event {
                api.prevent_close();
                let _ = w.hide();
                explain_tray_once(w.app_handle());
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building Relay");

    app.run(|handle, event| match event {
        // Closing the last window must not end the app, and an exit that did not come from Quit still
        // stops the backend gracefully first.
        RunEvent::ExitRequested { api, code, .. } => {
            if let Some(sh) = handle.try_state::<Shell>() {
                if !sh.quitting.load(Ordering::SeqCst) {
                    api.prevent_exit();
                    if code.is_some() {
                        begin_quit(handle);
                    }
                }
            }
        }
        _ => {}
    });
}
