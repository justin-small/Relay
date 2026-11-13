//! The backend supervisor: starts the frozen Relay, follows its events, restarts it when it dies.
//!
//! The channel is the child's stdin/stdout (app/sidecar.py): `RELAY-EVENT {...}` lines out, `quit` in.
//! Closing the pipe (even if this process is killed) makes the backend exit, so none is orphaned.
//! Captions matter more than a clean state, so an unexpected exit is restarted with backoff, and says so.

use std::{
    collections::VecDeque,
    io::Write,
    process::Stdio,
    sync::{
        atomic::{AtomicU16, Ordering},
        Arc, Mutex, RwLock,
    },
    time::{Duration, Instant},
};

use serde::Deserialize;
use tauri::{AppHandle, Manager};
use tokio::{
    io::{AsyncBufReadExt, AsyncWriteExt, BufReader},
    process::{Child, ChildStdin, Command},
    sync::{mpsc, oneshot, watch},
};

use crate::paths;

const EVENT_PREFIX: &str = "RELAY-EVENT ";
/// How long the backend gets to say it is ready before it is declared failed.
const READY_TIMEOUT: Duration = Duration::from_secs(60);
/// How long a graceful stop gets (SQLite checkpoint, mDNS goodbye) before the process is killed.
const STOP_TIMEOUT: Duration = Duration::from_secs(15);
const MAX_BACKOFF_S: u64 = 30;
const LOG_TAIL_LINES: usize = 20;
const LOG_KEEP_BYTES: u64 = 5 * 1024 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Stopped,
    Starting,
    Running,
    Capturing,
    Error,
}

#[derive(Clone, Debug, Default)]
pub struct Info {
    pub viewer_port: u16,
    pub admin_port: u16,
    pub fingerprint: String,
    pub ips: Vec<String>,
}

#[derive(Clone, Debug)]
pub struct Snapshot {
    pub status: Status,
    pub info: Option<Info>,
    /// One sentence for the user: why it is in `Error`, or that it is restarting.
    pub message: String,
    /// The last log lines, shown with an error.
    pub log_tail: String,
}

/// What the proxy (proxy.rs) needs to reach the panel, kept current by the supervisor.
#[derive(Default, Debug)]
pub struct Pins {
    pub fingerprint: RwLock<String>,
    pub admin_port: AtomicU16,
}

enum Cmd {
    Restart,
    Quit(oneshot::Sender<()>),
}

#[derive(Clone)]
pub struct Backend {
    cmd: mpsc::UnboundedSender<Cmd>,
    pub state: watch::Receiver<Snapshot>,
    pub pins: Arc<Pins>,
}

impl Backend {
    pub fn snapshot(&self) -> Snapshot {
        self.state.borrow().clone()
    }

    pub fn restart(&self) {
        let _ = self.cmd.send(Cmd::Restart);
    }

    /// Stop the backend gracefully and wait until it is gone (or killed after the timeout).
    pub async fn quit(&self) {
        let (ack, done) = oneshot::channel();
        if self.cmd.send(Cmd::Quit(ack)).is_ok() {
            let _ = tokio::time::timeout(STOP_TIMEOUT + Duration::from_secs(5), done).await;
        }
    }
}

#[derive(Deserialize)]
struct Event {
    event: String,
    #[serde(default)]
    viewer_port: u16,
    #[serde(default)]
    admin_port: u16,
    #[serde(default)]
    fingerprint: String,
    #[serde(default)]
    ips: Vec<String>,
}

enum Line {
    Event(Event),
    Log(String),
}

struct Logs {
    ring: Mutex<VecDeque<String>>,
    file: Mutex<Option<std::fs::File>>,
}

impl Logs {
    fn open() -> Logs {
        let dir = paths::logs_dir();
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("relay.log");
        if std::fs::metadata(&path).map(|m| m.len() > LOG_KEEP_BYTES).unwrap_or(false) {
            let _ = std::fs::rename(&path, dir.join("relay.log.1"));
        }
        let file = std::fs::OpenOptions::new().create(true).append(true).open(path).ok();
        Logs { ring: Mutex::new(VecDeque::new()), file: Mutex::new(file) }
    }

    fn push(&self, line: &str) {
        if let Ok(mut f) = self.file.lock() {
            if let Some(f) = f.as_mut() {
                let _ = writeln!(f, "{line}");
            }
        }
        if let Ok(mut r) = self.ring.lock() {
            if r.len() >= LOG_TAIL_LINES {
                r.pop_front();
            }
            r.push_back(line.to_string());
        }
    }

    fn tail(&self) -> String {
        self.ring.lock().map(|r| r.iter().cloned().collect::<Vec<_>>().join("\n")).unwrap_or_default()
    }
}

pub fn spawn(app: AppHandle) -> Backend {
    let (cmd_tx, cmd_rx) = mpsc::unbounded_channel();
    let (state_tx, state_rx) = watch::channel(Snapshot {
        status: Status::Starting,
        info: None,
        message: String::new(),
        log_tail: String::new(),
    });
    let pins = Arc::new(Pins::default());
    tauri::async_runtime::spawn(supervise(app, cmd_rx, state_tx, pins.clone()));
    Backend { cmd: cmd_tx, state: state_rx, pins }
}

fn backend_command(app: &AppHandle) -> Result<Command, String> {
    let exe = if cfg!(windows) { "relay.exe" } else { "relay" };
    let mut cmd = None;
    if let Ok(res) = app.path().resource_dir() {
        let path = res.join("relay-backend").join(exe);
        if path.exists() {
            let mut c = Command::new(&path);
            if let Some(dir) = path.parent() {
                c.current_dir(dir);
            }
            cmd = Some(c);
        }
    }
    #[cfg(debug_assertions)]
    if cmd.is_none() {
        // Development: run the checkout's run.py when no frozen backend has been bundled.
        let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
        let venv = if cfg!(windows) { root.join(".venv/Scripts/python.exe") } else { root.join(".venv/bin/python") };
        let python = std::env::var("RELAY_DEV_PYTHON")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|_| if venv.exists() { venv } else { "python3".into() });
        let mut c = Command::new(python);
        c.arg("run.py").current_dir(root);
        cmd = Some(c);
    }
    let mut cmd = cmd.ok_or_else(|| "The Relay backend is missing from this install. Reinstall Relay.".to_string())?;
    cmd.env("RELAY_HOME", paths::state_dir())
        .env("RELAY_SIDECAR", "1")
        .env("RELAY_VERSION", app.package_info().version.to_string())
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONUTF8", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .kill_on_drop(true);
    #[cfg(windows)]
    cmd.creation_flags(0x0800_0000); // CREATE_NO_WINDOW: no console flashes up beside the app
    Ok(cmd)
}

enum Outcome {
    /// The process ended without being asked to.
    Exited(String),
    /// It never reported ready.
    NeverReady,
    /// Asked to restart; start again straight away.
    Restart,
    Quit(oneshot::Sender<()>),
}

/// `stdin` is the pipe's write end, held by the caller for the whole run: tokio's `Child::wait` closes the
/// child's own stdin, which the backend reads as "the shell is gone" and would stop it at once.
async fn stop_gracefully(child: &mut Child, stdin: Option<ChildStdin>) {
    if let Some(mut stdin) = stdin {
        let _ = stdin.write_all(b"quit\n").await;
        let _ = stdin.shutdown().await;
    }
    if tokio::time::timeout(STOP_TIMEOUT, child.wait()).await.is_err() {
        let _ = child.kill().await;
    }
}

async fn health(client: &reqwest::Client, port: u16) -> Option<bool> {
    #[derive(Deserialize)]
    struct H {
        #[serde(default)]
        running: bool,
    }
    let r = client.get(format!("http://127.0.0.1:{port}/healthz")).send().await.ok()?;
    if !r.status().is_success() {
        return None;
    }
    r.json::<H>().await.ok().map(|h| h.running)
}

async fn supervise(
    app: AppHandle,
    mut cmds: mpsc::UnboundedReceiver<Cmd>,
    state: watch::Sender<Snapshot>,
    pins: Arc<Pins>,
) {
    let logs = Arc::new(Logs::open());
    let http = reqwest::Client::builder().timeout(Duration::from_secs(3)).no_proxy().build().expect("http client");
    let mut backoff = 1u64;
    let mut restarts = 0u32;

    let set = |status: Status, info: Option<Info>, message: String| {
        let tail = if status == Status::Error { logs.tail() } else { String::new() };
        let _ = state.send(Snapshot { status, info, message, log_tail: tail });
    };

    loop {
        set(
            Status::Starting,
            None,
            if restarts == 0 { String::new() } else { "Restarting Relay.".into() },
        );
        let started = Instant::now();
        let mut child = match backend_command(&app).and_then(|mut c| c.spawn().map_err(|e| format!("Relay could not be started: {e}"))) {
            Ok(c) => c,
            Err(msg) => {
                logs.push(&msg);
                set(Status::Error, None, msg);
                match wait_backoff(&mut cmds, backoff).await {
                    Some(Cmd::Quit(ack)) => {
                        set(Status::Stopped, None, String::new());
                        let _ = ack.send(());
                        return;
                    }
                    _ => {
                        backoff = (backoff * 2).min(MAX_BACKOFF_S);
                        restarts += 1;
                        continue;
                    }
                }
            }
        };

        let stdin = child.stdin.take();
        let (line_tx, mut lines) = mpsc::unbounded_channel::<Line>();
        for stream in [
            child.stdout.take().map(|s| Box::new(s) as Box<dyn tokio::io::AsyncRead + Unpin + Send>),
            child.stderr.take().map(|s| Box::new(s) as Box<dyn tokio::io::AsyncRead + Unpin + Send>),
        ]
        .into_iter()
        .flatten()
        {
            let tx = line_tx.clone();
            tokio::spawn(async move {
                let mut reader = BufReader::new(stream).lines();
                while let Ok(Some(line)) = reader.next_line().await {
                    let item = match line.strip_prefix(EVENT_PREFIX).and_then(|j| serde_json::from_str::<Event>(j).ok()) {
                        Some(ev) => Line::Event(ev),
                        None => Line::Log(line),
                    };
                    if tx.send(item).is_err() {
                        break;
                    }
                }
            });
        }
        drop(line_tx);

        let mut info: Option<Info> = None;
        let mut poll = tokio::time::interval(Duration::from_secs(3));
        poll.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        let ready_deadline = tokio::time::sleep(READY_TIMEOUT);
        tokio::pin!(ready_deadline);

        let outcome = loop {
            tokio::select! {
                Some(line) = lines.recv() => match line {
                    Line::Log(l) => logs.push(&l),
                    Line::Event(ev) => match ev.event.as_str() {
                        "ready" => {
                            let i = Info { viewer_port: ev.viewer_port, admin_port: ev.admin_port,
                                           fingerprint: ev.fingerprint, ips: ev.ips };
                            *pins.fingerprint.write().unwrap() = i.fingerprint.clone();
                            pins.admin_port.store(i.admin_port, Ordering::SeqCst);
                            // Ready means listening; the health endpoint confirms the app answers.
                            let running = health(&http, i.viewer_port).await;
                            info = Some(i.clone());
                            match running {
                                Some(c) => set(if c { Status::Capturing } else { Status::Running }, Some(i), String::new()),
                                None => set(Status::Starting, Some(i), String::new()),
                            }
                        }
                        "fingerprint" => if let Some(i) = info.as_mut() {
                            i.fingerprint = ev.fingerprint.clone();
                            *pins.fingerprint.write().unwrap() = ev.fingerprint;
                            let cur = state.borrow().clone();
                            let _ = state.send(Snapshot { info: Some(i.clone()), ..cur });
                        },
                        "network" => if let Some(i) = info.as_mut() {
                            i.ips = ev.ips;
                            let cur = state.borrow().clone();
                            let _ = state.send(Snapshot { info: Some(i.clone()), ..cur });
                        },
                        _ => {}
                    },
                },
                _ = poll.tick(), if info.is_some() => {
                    let i = info.clone().unwrap();
                    if let Some(c) = health(&http, i.viewer_port).await {
                        let status = if c { Status::Capturing } else { Status::Running };
                        if state.borrow().status != status {
                            set(status, Some(i), String::new());
                        }
                    }
                }
                _ = &mut ready_deadline, if info.is_none() => break Outcome::NeverReady,
                status = child.wait() => {
                    let code = status.map(|s| s.to_string()).unwrap_or_else(|e| e.to_string());
                    break Outcome::Exited(code);
                }
                Some(cmd) = cmds.recv() => match cmd {
                    Cmd::Restart => break Outcome::Restart,
                    Cmd::Quit(ack) => break Outcome::Quit(ack),
                },
            }
        };

        let was_stable = info.is_some() && started.elapsed() > Duration::from_secs(60);
        match outcome {
            Outcome::Quit(ack) => {
                stop_gracefully(&mut child, stdin).await;
                set(Status::Stopped, None, String::new());
                let _ = ack.send(());
                return;
            }
            Outcome::Restart => {
                stop_gracefully(&mut child, stdin).await;
                backoff = 1;
                restarts += 1;
            }
            Outcome::NeverReady => {
                let _ = child.kill().await;
                drain(&mut lines, &logs);
                set(Status::Error, None, "Relay did not finish starting within a minute.".into());
                if let Some(Cmd::Quit(ack)) = wait_backoff(&mut cmds, backoff).await {
                    set(Status::Stopped, None, String::new());
                    let _ = ack.send(());
                    return;
                }
                backoff = (backoff * 2).min(MAX_BACKOFF_S);
                restarts += 1;
            }
            Outcome::Exited(code) => {
                drain(&mut lines, &logs);
                if was_stable {
                    backoff = 1;
                }
                logs.push(&format!("[shell] backend exited unexpectedly ({code}); restarting in {backoff}s"));
                set(
                    Status::Error,
                    None,
                    format!("Relay stopped unexpectedly ({code}). Restarting in {backoff} second{}.", if backoff == 1 { "" } else { "s" }),
                );
                if let Some(Cmd::Quit(ack)) = wait_backoff(&mut cmds, backoff).await {
                    set(Status::Stopped, None, String::new());
                    let _ = ack.send(());
                    return;
                }
                backoff = (backoff * 2).min(MAX_BACKOFF_S);
                restarts += 1;
            }
        }
    }
}

/// Lines the process wrote just before it ended.
fn drain(lines: &mut mpsc::UnboundedReceiver<Line>, logs: &Logs) {
    while let Ok(l) = lines.try_recv() {
        if let Line::Log(l) = l {
            logs.push(&l);
        }
    }
}

/// Wait out the backoff; a restart request cuts it short (None), a quit request is returned.
async fn wait_backoff(cmds: &mut mpsc::UnboundedReceiver<Cmd>, secs: u64) -> Option<Cmd> {
    tokio::select! {
        _ = tokio::time::sleep(Duration::from_secs(secs)) => None,
        cmd = cmds.recv() => match cmd {
            Some(Cmd::Quit(ack)) => Some(Cmd::Quit(ack)),
            _ => None,
        },
    }
}
