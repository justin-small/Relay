//! Undoing a bad update (#184): before an update is installed, the app and the state folder are copied
//! aside; if the new version does not come up, the copies are put back and the old version starts again.
//!
//! * **Before installing** (`prepare`): the state folder (config, database, certificates; not recordings or
//!   logs) goes to `backups/state`, the installed app to `backups/app`, and `update-pending.json` says what
//!   is being tried.
//! * **Success** is the backend reporting ready (`on_backend`), which clears the marker.
//! * **Failure** is the backend still in error 30 s after it first failed, or the new version having been
//!   launched three times without ever reaching ready (it crashes before it can say so). Either way the
//!   marker is flagged and Relay restarts; the *next launch* (`on_launch`, first thing in `main`) does the
//!   restoring, while nothing is running and no file is in use. On Windows the app's own folder cannot be
//!   replaced by the program it holds open, so a detached PowerShell script waits for it to exit, copies the
//!   old files back and starts it.
//!
//! The first install of a version that has this code cannot protect itself; the next update can.

use std::{
    path::{Path, PathBuf},
    process::Command,
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

use serde::{Deserialize, Serialize};
use tauri::AppHandle;
use tauri_plugin_dialog::DialogExt;

use crate::{backend::Status, paths};

const MAX_LAUNCHES: u32 = 3;
const ERROR_GRACE: Duration = Duration::from_secs(30);
/// Never copied into the backup: large, or the user's recordings, not Relay's settings.
const NOT_BACKED_UP: [&str; 3] = ["recordings", "logs", "backups"];

static PENDING: AtomicBool = AtomicBool::new(false);
static WATCHING: AtomicBool = AtomicBool::new(false);

#[derive(Serialize, Deserialize, Default, Clone, Debug)]
struct Pending {
    from: String,
    to: String,
    /// The installed app that was backed up (macOS bundle, Windows install folder, Linux AppImage file).
    app_path: Option<PathBuf>,
    launches: u32,
    /// Set when the new version failed: the next launch restores instead of starting.
    rollback: bool,
}

fn marker() -> PathBuf {
    paths::state_dir().join("update-pending.json")
}

fn notice() -> PathBuf {
    paths::state_dir().join("rolled-back.txt")
}

fn load() -> Option<Pending> {
    serde_json::from_slice(&std::fs::read(marker()).ok()?).ok()
}

fn save(p: &Pending) {
    if let Ok(json) = serde_json::to_vec_pretty(p) {
        let _ = std::fs::write(marker(), json);
    }
}

/// The installed app, if this kind of install can be replaced and so backed up.
fn app_path() -> Option<PathBuf> {
    let exe = std::env::current_exe().ok()?;
    if cfg!(target_os = "macos") {
        exe.ancestors().nth(3).filter(|p| p.extension().map(|e| e == "app").unwrap_or(false)).map(Path::to_path_buf)
    } else if cfg!(windows) {
        exe.parent().map(Path::to_path_buf)
    } else {
        std::env::var_os("APPIMAGE").map(PathBuf::from)
    }
}

fn run(cmd: &mut Command) -> Result<(), String> {
    let status = cmd.status().map_err(|e| e.to_string())?;
    // robocopy's codes below 8 all mean "copied" (1 = something was copied, 2 = extras, ...).
    let ok = if cfg!(windows) { status.code().map(|c| c < 8).unwrap_or(false) } else { status.success() };
    if ok { Ok(()) } else { Err(format!("copy failed ({status})")) }
}

/// Copy a folder, or a single file, keeping permissions and links, with what each OS ships for it.
fn copy_app(src: &Path, dst: &Path) -> Result<(), String> {
    if dst.exists() {
        if dst.is_dir() { std::fs::remove_dir_all(dst) } else { std::fs::remove_file(dst) }.map_err(|e| e.to_string())?;
    }
    if let Some(parent) = dst.parent() {
        std::fs::create_dir_all(parent).map_err(|e| e.to_string())?;
    }
    if cfg!(target_os = "macos") {
        run(Command::new("ditto").arg(src).arg(dst))
    } else if cfg!(windows) {
        run(Command::new("robocopy").arg(src).arg(dst).args(["/MIR", "/NFL", "/NDL", "/NJH", "/NJS", "/NP"]))
    } else {
        run(Command::new("cp").arg("-a").arg(src).arg(dst))
    }
}

fn copy_tree(src: &Path, dst: &Path, skip_top: bool) -> std::io::Result<()> {
    std::fs::create_dir_all(dst)?;
    for entry in std::fs::read_dir(src)? {
        let entry = entry?;
        let name = entry.file_name();
        if skip_top && NOT_BACKED_UP.iter().any(|n| name == *n) {
            continue;
        }
        let to = dst.join(&name);
        if entry.file_type()?.is_dir() {
            copy_tree(&entry.path(), &to, false)?;
        } else {
            std::fs::copy(entry.path(), to)?;
        }
    }
    Ok(())
}

/// Put the state backup back: what is there now is cleared (except what is never backed up and the marker),
/// then the saved files are copied in.
fn restore_state() -> std::io::Result<()> {
    let saved = paths::backups_dir().join("state");
    if !saved.is_dir() {
        return Ok(());
    }
    let state = paths::state_dir();
    for entry in std::fs::read_dir(&state)? {
        let entry = entry?;
        let name = entry.file_name();
        if NOT_BACKED_UP.iter().any(|n| name == *n) || name == "update-pending.json" {
            continue;
        }
        if entry.file_type()?.is_dir() { std::fs::remove_dir_all(entry.path())? } else { std::fs::remove_file(entry.path())? }
    }
    copy_tree(&saved, &state, false)
}

/// Back everything up and write the marker. Called with the backend stopped, just before the installer runs.
/// An error means nothing was installed and the caller must not install.
pub fn prepare(from: &str, to: &str) -> Result<(), String> {
    let backups = paths::backups_dir();
    let state_copy = backups.join("state");
    if state_copy.exists() {
        std::fs::remove_dir_all(&state_copy).map_err(|e| e.to_string())?;
    }
    let state = paths::state_dir();
    if state.is_dir() {
        copy_tree(&state, &state_copy, true).map_err(|e| format!("The settings could not be backed up: {e}"))?;
    }
    let app = app_path();
    if let Some(app) = &app {
        copy_app(app, &backups.join("app")).map_err(|e| format!("The installed Relay could not be backed up: {e}"))?;
    }
    save(&Pending { from: from.into(), to: to.into(), app_path: app, launches: 0, rollback: false });
    Ok(())
}

pub enum Launch {
    Normal,
    /// This process has handed over (a rollback started the old version, or will); exit now.
    Exit,
}

/// First thing in `main`: count this launch of an update on trial, or carry out a rollback.
pub fn on_launch() -> Launch {
    let Some(mut p) = load() else { return Launch::Normal };
    if !p.rollback && p.launches + 1 < MAX_LAUNCHES {
        p.launches += 1;
        save(&p);
        PENDING.store(true, Ordering::SeqCst);
        return Launch::Normal;
    }
    match roll_back(&p) {
        Ok(handover) => if handover { Launch::Exit } else { Launch::Normal },
        Err(e) => {
            // Cannot undo it: say so and keep the new version running, which is all there is left.
            let _ = std::fs::remove_file(marker());
            let _ = std::fs::write(notice(), format!("Relay {} did not start and the automatic rollback to {} failed: {e}\nReinstall {} from the releases page. Your settings are in {}.", p.to, p.from, p.from, paths::backups_dir().display()));
            Launch::Normal
        }
    }
}

/// Returns whether another process now has the job of starting the old version (this one must exit).
fn roll_back(p: &Pending) -> Result<bool, String> {
    restore_state().map_err(|e| format!("settings: {e}"))?;
    let _ = std::fs::remove_file(marker());
    let _ = std::fs::write(notice(), format!("Relay {} did not start, so Relay went back to {}. Your settings were restored from before the update.", p.to, p.from));
    let (Some(app), backup) = (&p.app_path, paths::backups_dir().join("app")) else { return Ok(false) };
    if !backup.exists() {
        return Ok(false);
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    if cfg!(windows) {
        // This program's own files are in use until it exits; a detached script copies them back after.
        let q = |p: &Path| p.display().to_string().replace('\'', "''");
        let script = format!(
            "$p={}; while (Get-Process -Id $p -ErrorAction SilentlyContinue) {{ Start-Sleep -Milliseconds 300 }}; \
             robocopy '{}' '{}' /MIR /NFL /NDL /NJH /NJS /NP | Out-Null; Start-Process '{}'",
            std::process::id(),
            q(&backup),
            q(app),
            q(&exe),
        );
        Command::new("powershell")
            .args(["-NoProfile", "-WindowStyle", "Hidden", "-Command", &script])
            .spawn()
            .map_err(|e| e.to_string())?;
        return Ok(true);
    }
    copy_app(&backup, app)?;
    Command::new(&exe).args(std::env::args().skip(1)).spawn().map_err(|e| e.to_string())?;
    Ok(true)
}

/// If a rollback happened, tell the operator once (called after start-up, when dialogs work).
pub fn show_notice(app: &AppHandle) {
    if let Ok(text) = std::fs::read_to_string(notice()) {
        let _ = std::fs::remove_file(notice());
        app.dialog().message(text).title("Relay update undone").show(|_| {});
    }
}

/// Watch the backend while an update is on trial: ready clears it, a lasting error rolls it back.
pub fn on_backend(app: &AppHandle, status: Status) {
    if !PENDING.load(Ordering::SeqCst) {
        return;
    }
    match status {
        Status::Running | Status::Capturing => {
            PENDING.store(false, Ordering::SeqCst);
            let _ = std::fs::remove_file(marker());
        }
        Status::Error if !WATCHING.swap(true, Ordering::SeqCst) => {
            let app = app.clone();
            tauri::async_runtime::spawn(async move {
                tokio::time::sleep(ERROR_GRACE).await;
                WATCHING.store(false, Ordering::SeqCst);
                if PENDING.load(Ordering::SeqCst) && crate::shell(&app).backend.snapshot().status == Status::Error {
                    if let Some(mut p) = load() {
                        p.rollback = true;
                        save(&p);
                        app.restart();
                    }
                }
            });
        }
        _ => {}
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// One test, because it sets RELAY_HOME for the whole process.
    #[test]
    fn settings_are_backed_up_without_recordings_and_put_back() {
        let home = std::env::temp_dir().join(format!("relay-rollback-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&home);
        std::fs::create_dir_all(home.join("recordings")).unwrap();
        std::fs::create_dir_all(home.join("certs")).unwrap();
        std::fs::write(home.join("config.json"), "old").unwrap();
        std::fs::write(home.join("certs/panel.pem"), "cert").unwrap();
        std::fs::write(home.join("recordings/a.wav"), "audio").unwrap();
        std::env::set_var("RELAY_HOME", &home);

        let saved = paths::backups_dir().join("state");
        copy_tree(&home, &saved, true).unwrap();
        assert!(saved.join("config.json").exists() && saved.join("certs/panel.pem").exists());
        assert!(!saved.join("recordings").exists() && !saved.join("backups").exists());

        // The new version rewrites the config and adds a file the old one never had.
        std::fs::write(home.join("config.json"), "new").unwrap();
        std::fs::write(home.join("relay.db-new"), "x").unwrap();
        std::fs::write(marker(), "{}").unwrap();
        restore_state().unwrap();
        assert_eq!(std::fs::read_to_string(home.join("config.json")).unwrap(), "old");
        assert!(!home.join("relay.db-new").exists());
        assert!(home.join("recordings/a.wav").exists(), "recordings are never touched");
        assert!(marker().exists(), "the marker is the caller's to clear");
        let _ = std::fs::remove_dir_all(&home);
    }
}
