//! Run at login (#636). macOS: a launchd agent in `~/Library/LaunchAgents`, which launchd also restarts if the helper
//! ever exits. Windows: a value in the user's `Run` key, so it starts in the signed-in user's session, which is where
//! ProPresenter's window lives (a Windows service runs apart from the desktop and could not open a `.pro` file).
//! The files and values are built by plain functions so tests can read them; only `install` and `uninstall` touch the
//! system.

use std::path::{Path, PathBuf};
use std::process::Command;

pub const LABEL: &str = "org.relay.helper";
#[cfg_attr(not(windows), expect(dead_code, reason = "only the Windows autostart uses the Run key"))]
const RUN_VALUE: &str = "Relay Helper";
#[cfg_attr(not(windows), expect(dead_code, reason = "only the Windows autostart uses the Run key"))]
const RUN_KEY: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";

fn xml_escape(text: &str) -> String {
    text.replace('&', "&amp;").replace('<', "&lt;").replace('>', "&gt;").replace('"', "&quot;")
}

/// Where launchd looks for this user's agents.
pub fn launch_agent_path(home: &Path) -> PathBuf {
    home.join("Library").join("LaunchAgents").join(format!("{LABEL}.plist"))
}

/// The launchd agent: start `<exe> run` at login and keep it running. `config` is set only when the settings file is
/// not in its usual place.
pub fn plist(exe: &Path, config: Option<&Path>, log: &Path) -> String {
    let env = match config {
        Some(c) => format!(
            "  <key>EnvironmentVariables</key>\n  <dict>\n    <key>RELAY_HELPER_CONFIG</key>\n    <string>{}</string>\n  </dict>\n",
            xml_escape(&c.display().to_string())
        ),
        None => String::new(),
    };
    format!(
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n\
         <!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">\n\
         <plist version=\"1.0\">\n<dict>\n  <key>Label</key>\n  <string>{LABEL}</string>\n  <key>ProgramArguments</key>\n  <array>\n    \
         <string>{}</string>\n    <string>run</string>\n    <string>--background</string>\n  </array>\n{env}  <key>RunAtLoad</key>\n  <true/>\n  <key>KeepAlive</key>\n  <true/>\n  \
         <key>ProcessType</key>\n  <string>Interactive</string>\n  <key>StandardOutPath</key>\n  <string>{log}</string>\n  \
         <key>StandardErrorPath</key>\n  <string>{log}</string>\n</dict>\n</plist>\n",
        xml_escape(&exe.display().to_string()),
        log = xml_escape(&log.display().to_string()),
    )
}

/// What goes in Windows' `Run` key: the program, quoted, and `run --background` so it lets go of its console window.
pub fn run_value(exe: &Path) -> String {
    format!("\"{}\" run --background", exe.display())
}

/// Let go of the console window the program was started with (Windows only; elsewhere there is nothing to do).
pub fn detach_console() {
    #[cfg(windows)]
    {
        #[link(name = "kernel32")]
        extern "system" {
            fn FreeConsole() -> i32;
        }
        // SAFETY: FreeConsole takes no arguments and only detaches this process from its console.
        unsafe {
            FreeConsole();
        }
    }
}

#[cfg_attr(not(any(target_os = "macos", windows)), allow(dead_code))]
fn run(cmd: &mut Command) -> Result<bool, String> {
    cmd.output().map(|o| o.status.success()).map_err(|e| format!("Could not run {}: {e}", cmd.get_program().to_string_lossy()))
}

#[cfg(target_os = "macos")]
fn uid() -> Result<String, String> {
    let out = Command::new("id").arg("-u").output().map_err(|e| format!("Could not find the user id: {e}"))?;
    Ok(String::from_utf8_lossy(&out.stdout).trim().to_string())
}

/// Start at login from now on, and start now.
pub fn install(config_override: Option<&Path>) -> Result<String, String> {
    let exe = std::env::current_exe().map_err(|e| format!("Cannot tell where this program is: {e}"))?;
    #[cfg(target_os = "macos")]
    {
        let home = PathBuf::from(std::env::var_os("HOME").ok_or("HOME is not set.")?);
        let path = launch_agent_path(&home);
        let log = home.join("Library").join("Logs").join("Relay Helper.log");
        std::fs::create_dir_all(path.parent().ok_or("The launch agent path has no folder.")?).map_err(|e| format!("Cannot create {}: {e}", path.display()))?;
        std::fs::write(&path, plist(&exe, config_override, &log)).map_err(|e| format!("Cannot write {}: {e}", path.display()))?;
        let domain = format!("gui/{}", uid()?);
        let _ = run(Command::new("launchctl").args(["bootout", &format!("{domain}/{LABEL}")]));
        if !run(Command::new("launchctl").args(["bootstrap", &domain]).arg(&path))? {
            return Err(format!("launchd would not load {}. Log out and in again to start it.", path.display()));
        }
        Ok(format!("The helper now starts at login and is running. Its log is {}.", log.display()))
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let _ = config_override;
        if !run(Command::new("reg").args(["add", RUN_KEY, "/v", RUN_VALUE, "/t", "REG_SZ", "/d", &run_value(&exe), "/f"]))? {
            return Err("Windows would not save the startup entry.".to_string());
        }
        // CREATE_NO_WINDOW | DETACHED_PROCESS: running now, with no console, and not tied to this one.
        Command::new(&exe)
            .args(["run", "--background"])
            .creation_flags(0x0800_0000 | 0x0000_0008)
            .spawn()
            .map_err(|e| format!("The startup entry is saved, but the helper did not start: {e}"))?;
        Ok("The helper now starts when you sign in to Windows and is running.".to_string())
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        let _ = (exe, config_override);
        Err("Run at login is set up on macOS and Windows.".to_string())
    }
}

/// Stop starting at login, and stop the one running.
pub fn uninstall() -> Result<String, String> {
    #[cfg(target_os = "macos")]
    {
        let home = PathBuf::from(std::env::var_os("HOME").ok_or("HOME is not set.")?);
        let path = launch_agent_path(&home);
        let _ = run(Command::new("launchctl").args(["bootout", &format!("gui/{}/{LABEL}", uid()?)]));
        match std::fs::remove_file(&path) {
            Ok(()) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => return Err(format!("Cannot remove {}: {e}", path.display())),
        }
        Ok("The helper no longer starts at login.".to_string())
    }
    #[cfg(windows)]
    {
        let _ = run(Command::new("reg").args(["delete", RUN_KEY, "/v", RUN_VALUE, "/f"]));
        let _ = run(Command::new("taskkill").args(["/F", "/IM", "relay-helper.exe", "/FI", &format!("PID ne {}", std::process::id())]));
        Ok("The helper no longer starts when you sign in.".to_string())
    }
    #[cfg(not(any(target_os = "macos", windows)))]
    {
        Err("Run at login is set up on macOS and Windows.".to_string())
    }
}
