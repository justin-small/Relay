//! In-app updates (#184): the Tauri updater plugin against the GitHub release's `latest.json`.
//!
//! * **Where it looks is changeable.** `RELAY_UPDATE_URL` (environment) beats `update_url` in `shell.json`
//!   (state folder), which beats the built-in default. Either takes a full URL to a `latest.json`, or just
//!   `owner/repo` for a GitHub release. The default is an address on Relay's own domain that redirects
//!   (`site/public/_redirects`) to the GitHub release, so if the repository moves, changing that one
//!   redirect moves every install with nothing done on the machines.
//! * **Nothing is installed unasked.** It checks at start and daily and only *shows* what it found (tray,
//!   and a banner on the panel). Installing is the operator's click; if captions are running there is one
//!   more question first.
//! * **The backend stops first.** Its files are replaced by the update, so it is asked to quit (SQLite
//!   checkpoint, mDNS goodbye) before the installer touches them, then Relay restarts.
//! * **A tampered download is refused** by the plugin: every artifact is checked against the minisign
//!   public key in tauri.conf.json before anything is written.
//!
//! The panel is remote content with no Tauri IPC, so its banner talks to this module through the loopback
//! proxy (`/__shell/update/install`, proxy.rs), guarded by a per-run token that only the injected script has.

use std::{
    collections::hash_map::RandomState,
    hash::{BuildHasher, Hasher},
    sync::{
        atomic::{AtomicBool, Ordering},
        Mutex,
    },
    time::Duration,
};

use serde::Serialize;
use tauri::{AppHandle, Manager, Url};
use tauri_plugin_dialog::{DialogExt, MessageDialogButtons};
use tauri_plugin_updater::{Update, UpdaterExt};
use tokio::sync::oneshot;

use crate::{backend::Status, settings, tray};

/// Redirects (302, site/public/_redirects) to the GitHub release's `releases/latest/download/latest.json`.
/// Stable releases only: `releases/latest` skips pre-releases, so `-rc` and `-beta` tags never reach a
/// stable install. A pre-release install that wants them sets `update_url` to a specific tag's file.
pub const DEFAULT_URL: &str = "https://relaychurchsystems.com/updates/latest.json";
const CHECK_EVERY: Duration = Duration::from_secs(24 * 60 * 60);
const FIRST_CHECK_AFTER: Duration = Duration::from_secs(20);

/// What the tray and the panel banner show. `kind` is one of: idle, checking, uptodate, available,
/// downloading, installing, failed.
#[derive(Clone, Serialize, Debug)]
pub struct State {
    pub kind: &'static str,
    pub version: String,
    pub notes: String,
    pub percent: u8,
    pub message: String,
}

impl State {
    fn of(kind: &'static str) -> State {
        State { kind, version: String::new(), notes: String::new(), percent: 0, message: String::new() }
    }
}

pub struct Updates {
    state: Mutex<State>,
    update: Mutex<Option<Update>>,
    busy: AtomicBool,
    /// Proves a request to the proxy's /__shell/ routes came from the script this module injected.
    pub token: String,
}

impl Updates {
    pub fn new() -> Updates {
        // 128 random bits from the std hasher's per-process OS-seeded keys; no extra dependency.
        let part = || RandomState::new().build_hasher().finish();
        Updates {
            state: Mutex::new(State::of("idle")),
            update: Mutex::new(None),
            busy: AtomicBool::new(false),
            token: format!("{:016x}{:016x}", part(), part()),
        }
    }
}

fn updates(app: &AppHandle) -> tauri::State<'_, Updates> {
    app.state::<Updates>()
}

/// `owner/repo` becomes that repository's latest release file; anything else is taken as a URL.
pub fn resolve_url(raw: &str) -> String {
    let raw = raw.trim();
    let is_slug = !raw.contains("://")
        && raw.split('/').count() == 2
        && raw.split('/').all(|p| !p.is_empty() && p.chars().all(|c| c.is_ascii_alphanumeric() || "-_.".contains(c)));
    if is_slug {
        format!("https://github.com/{raw}/releases/latest/download/latest.json")
    } else {
        raw.to_string()
    }
}

pub fn update_url() -> String {
    let from_env = std::env::var("RELAY_UPDATE_URL").ok().filter(|v| !v.trim().is_empty());
    let chosen = from_env.or_else(|| settings::load().update_url.filter(|v| !v.trim().is_empty()));
    chosen.map(|v| resolve_url(&v)).unwrap_or_else(|| DEFAULT_URL.to_string())
}

fn set(app: &AppHandle, st: State) {
    *updates(app).state.lock().unwrap() = st.clone();
    if let Some(t) = tray::get(app) {
        t.set_update(&st);
    }
    push(app);
}

pub fn current(app: &AppHandle) -> State {
    updates(app).state.lock().unwrap().clone()
}

const BANNER_JS: &str = r##"
(function () {
  var TOKEN = "__TOKEN__";
  window.__relayUpdate = function (s) {
    var old = document.getElementById("relay-update-banner");
    if (old) old.remove();
    var live = s.kind === "available" || s.kind === "downloading" || s.kind === "installing";
    if (!live || !document.body) return;
    var dismissed = null;
    try { dismissed = sessionStorage.getItem("relay-update-dismissed"); } catch (e) {}
    if (s.kind === "available" && dismissed === s.version) return;
    var box = document.createElement("div");
    box.id = "relay-update-banner";
    box.setAttribute("role", "status");
    var css = {
      position: "fixed", right: "16px", bottom: "16px", zIndex: "2147483647", maxWidth: "360px",
      padding: "12px 14px", borderRadius: "8px", background: "#1f2937", color: "#fff",
      font: "14px/1.4 system-ui, sans-serif", boxShadow: "0 4px 16px rgba(0,0,0,.35)"
    };
    for (var k in css) box.style[k] = css[k];
    var text = document.createElement("div");
    if (s.kind === "available") text.textContent = "Relay " + s.version + " is available.";
    else if (s.kind === "downloading") text.textContent = "Downloading Relay " + s.version + "… " + s.percent + "%";
    else text.textContent = "Installing Relay " + s.version + ". Relay will restart.";
    box.appendChild(text);
    if (s.kind === "available") {
      var row = document.createElement("div");
      row.style.marginTop = "8px";
      var go = document.createElement("button");
      go.textContent = "Install and restart";
      var later = document.createElement("button");
      later.textContent = "Later";
      later.style.marginLeft = "8px";
      go.onclick = function () {
        go.disabled = true;
        fetch("/__shell/update/install", { method: "POST", headers: { "X-Relay-Shell": TOKEN } });
      };
      later.onclick = function () {
        try { sessionStorage.setItem("relay-update-dismissed", s.version); } catch (e) {}
        box.remove();
      };
      row.appendChild(go);
      row.appendChild(later);
      box.appendChild(row);
    }
    document.body.appendChild(box);
  };
})();
"##;

/// Put the current state on the panel (a no-op while the window shows the start-up page).
pub fn push(app: &AppHandle) {
    let Some(sh) = app.try_state::<crate::Shell>() else { return };
    if !sh.on_panel.load(Ordering::SeqCst) {
        return;
    }
    if let Some(w) = crate::window(app) {
        let state = serde_json::to_string(&current(app)).unwrap_or_else(|_| "{}".into());
        let script = BANNER_JS.replace("__TOKEN__", &updates(app).token);
        let _ = w.eval(format!("{script}\nwindow.__relayUpdate({state});"));
    }
}

/// Look for an update. `manual` is a person asking (they get an answer even when there is nothing).
pub async fn check(app: &AppHandle, manual: bool) {
    let u = updates(app);
    if u.busy.swap(true, Ordering::SeqCst) {
        return;
    }
    // A found update stays offered while later checks run; only an idle state shows "checking".
    let had = current(app);
    if had.kind != "available" {
        set(app, State::of("checking"));
    }
    let result: Result<Option<Update>, String> = async {
        let url = Url::parse(&update_url()).map_err(|e| format!("The update address is not a valid URL: {e}"))?;
        let updater = app
            .updater_builder()
            .endpoints(vec![url])
            .map_err(|e| e.to_string())?
            .timeout(Duration::from_secs(30))
            .build()
            .map_err(|e| e.to_string())?;
        updater.check().await.map_err(|e| e.to_string())
    }
    .await;
    match result {
        Ok(Some(update)) => {
            let mut st = State::of("available");
            st.version = update.version.clone();
            st.notes = update.body.clone().unwrap_or_default();
            *u.update.lock().unwrap() = Some(update);
            set(app, st);
        }
        Ok(None) => {
            *u.update.lock().unwrap() = None;
            set(app, State::of("uptodate"));
            if manual {
                notify(app, "Relay is up to date", format!("You have the latest version, {}.", app.package_info().version));
            }
        }
        Err(message) => {
            // An automatic check that fails (offline at a venue) is silent; a person who asked is told.
            if had.kind == "available" {
                set(app, had);
            } else {
                let mut st = State::of("failed");
                st.message = message.clone();
                set(app, st);
            }
            if manual {
                notify(app, "Could not check for updates", message);
            }
        }
    }
    u.busy.store(false, Ordering::SeqCst);
}

fn notify(app: &AppHandle, title: &str, text: String) {
    app.dialog().message(text).title(title).show(|_| {});
}

async fn confirm_live(app: &AppHandle, version: &str) -> bool {
    let (tx, rx) = oneshot::channel();
    app.dialog()
        .message(format!(
            "Relay is capturing captions right now. Installing {version} stops them until Relay has restarted, which takes about a minute."
        ))
        .title("Install during a live service?")
        .buttons(MessageDialogButtons::OkCancelCustom("Install now".into(), "Not now".into()))
        .show(move |ok| {
            let _ = tx.send(ok);
        });
    rx.await.unwrap_or(false)
}

/// Download the offered update, stop the backend cleanly, replace the files and restart. Only ever started
/// by the operator (the tray item or the banner button).
pub async fn install(app: &AppHandle) {
    let u = updates(app);
    if u.busy.swap(true, Ordering::SeqCst) {
        return;
    }
    let offered = u.update.lock().unwrap().clone();
    let Some(update) = offered else {
        u.busy.store(false, Ordering::SeqCst);
        return;
    };
    let version = update.version.clone();
    let offered_state = current(app);
    let sh = crate::shell(app);
    if sh.backend.snapshot().status == Status::Capturing && !confirm_live(app, &version).await {
        u.busy.store(false, Ordering::SeqCst);
        return;
    }

    let mut st = State::of("downloading");
    st.version = version.clone();
    set(app, st.clone());
    let (mut got, mut last) = (0u64, 0u8);
    let handle = app.clone();
    let bytes = update
        .download(
            |chunk, total| {
                got += chunk as u64;
                let pct = total.filter(|t| *t > 0).map(|t| (got * 100 / t).min(100) as u8).unwrap_or(0);
                if pct != last {
                    last = pct;
                    let mut s = st.clone();
                    s.percent = pct;
                    set(&handle, s);
                }
            },
            || {},
        )
        .await;
    let bytes = match bytes {
        Ok(b) => b,
        Err(e) => {
            // The plugin refuses a download whose signature does not match, and says so here.
            let mut f = State::of("failed");
            f.message = e.to_string();
            set(app, f);
            notify(app, "The update was not installed", format!("{e}\n\nRelay keeps running the version you have."));
            set(app, offered_state);
            u.busy.store(false, Ordering::SeqCst);
            return;
        }
    };

    let mut st = State::of("installing");
    st.version = version;
    set(app, st);
    // Its files are about to be replaced: stop it gracefully first, and keep the quit handlers from
    // treating the exit the installer causes as anything but expected.
    sh.quitting.store(true, Ordering::SeqCst);
    sh.backend.quit().await;
    // Copy the app and the settings aside so a version that will not start can be undone (rollback.rs). If
    // that cannot be done, nothing is installed.
    if let Err(e) = crate::rollback::prepare(&app.package_info().version.to_string(), &update.version) {
        notify(app, "The update was not installed", format!("{e}\n\nRelay will restart with the version you have."));
        app.restart()
    }
    match update.install(bytes) {
        // On Windows the installer ends this process; elsewhere the new files are in place and Relay restarts.
        Ok(()) => app.restart(),
        Err(e) => {
            notify(app, "The update was not installed", format!("{e}\n\nRelay will restart with the version you have."));
            app.restart()
        }
    }
}

/// The tray item: install what was found, otherwise look again.
pub fn on_menu(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        match current(&app).kind {
            "available" => install(&app).await,
            "checking" | "downloading" | "installing" => {}
            _ => check(&app, true).await,
        }
    });
}

/// At start (after the backend has had a moment) and then daily.
pub fn spawn_checks(app: &AppHandle) {
    let app = app.clone();
    tauri::async_runtime::spawn(async move {
        tokio::time::sleep(FIRST_CHECK_AFTER).await;
        loop {
            check(&app, false).await;
            tokio::time::sleep(CHECK_EVERY).await;
        }
    });
}

/// The proxy's `/__shell/` routes. Only `POST /__shell/update/install` with this run's token does anything.
pub fn http(app: &AppHandle, method: &str, path: &str, token: Option<&str>) -> (u16, &'static str) {
    if path != "/__shell/update/install" {
        return (404, "Not found.");
    }
    if method != "POST" || token != Some(updates(app).token.as_str()) {
        return (403, "Not allowed.");
    }
    let app = app.clone();
    tauri::async_runtime::spawn(async move { install(&app).await });
    (202, "Installing.")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_repository_name_becomes_its_latest_release_file() {
        assert_eq!(resolve_url("acme/Relay"), "https://github.com/acme/Relay/releases/latest/download/latest.json");
        assert_eq!(resolve_url(" acme/Relay.app "), "https://github.com/acme/Relay.app/releases/latest/download/latest.json");
    }

    #[test]
    fn a_full_url_is_left_alone() {
        let u = "https://example.org/relay/latest.json";
        assert_eq!(resolve_url(u), u);
        assert_eq!(resolve_url("a/b/c"), "a/b/c");
    }

    #[test]
    fn tokens_differ_between_runs() {
        assert_ne!(Updates::new().token, Updates::new().token);
        assert_eq!(Updates::new().token.len(), 32);
    }
}
