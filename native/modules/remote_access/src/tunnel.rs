//! Runs cloudflared for this install's tunnel and keeps it running while the module is on.
//!
//! cloudflared ships beside the server (`tools/fetch_cloudflared.py` puts it there at build time); nothing is downloaded at run
//! time. The tunnel token goes to cloudflared through its environment (`TUNNEL_TOKEN`), never on the command line, where any
//! local user could read it from the process list.
//!
//! The tunnel's ingress is set in Relay's cloud and points at this machine's viewer port only, so the operator panel is never
//! reachable through it. Relay refuses admin paths on the viewer port as well.

use std::path::{Path, PathBuf};
use std::process::Stdio;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use relay_core::Net;
use tokio::io::{AsyncBufReadExt, BufReader};
use tokio::process::Command;
use tokio::task::JoinHandle;

use crate::cloud::TRAFFIC;

pub const BACKOFF: [Duration; 5] =
    [Duration::from_secs(2), Duration::from_secs(5), Duration::from_secs(10), Duration::from_secs(30), Duration::from_secs(60)];

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum State {
    Off,
    Missing,
    Connecting,
    Connected,
    Retrying,
}

impl State {
    pub fn name(self) -> &'static str {
        match self {
            Self::Off => "off",
            Self::Missing => "missing",
            Self::Connecting => "connecting",
            Self::Connected => "connected",
            Self::Retrying => "retrying",
        }
    }
}

/// `RELAY_CLOUDFLARED`, else the copy shipped beside the server, else one on PATH (a source checkout).
pub fn find_binary() -> Option<PathBuf> {
    let exe = if cfg!(windows) { "cloudflared.exe" } else { "cloudflared" };
    if let Some(over) = std::env::var_os("RELAY_CLOUDFLARED").filter(|v| !v.is_empty()) {
        let p = PathBuf::from(over);
        return p.is_file().then_some(p);
    }
    if let Some(beside) = std::env::current_exe().ok().and_then(|p| p.parent().map(|d| d.join(exe))) {
        if beside.is_file() {
            return Some(beside);
        }
    }
    let path = std::env::var_os("PATH")?;
    std::env::split_paths(&path).map(|d| d.join(exe)).find(|p| Path::is_file(p))
}

struct Inner {
    state: State,
    connections: u32,
    since: u64,
    token: String,
    task: Option<JoinHandle<()>>,
}

/// One cloudflared process, restarted with back-off when it exits.
#[derive(Clone)]
pub struct Tunnel {
    inner: Arc<Mutex<Inner>>,
    net: Net,
    backoff: Arc<Vec<Duration>>,
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0, |d| d.as_secs())
}

impl Tunnel {
    pub fn new(net: Net) -> Self {
        Self::with_backoff(net, BACKOFF.to_vec())
    }

    pub fn with_backoff(net: Net, backoff: Vec<Duration>) -> Self {
        let inner = Inner { state: State::Off, connections: 0, since: now(), token: String::new(), task: None };
        Self { inner: Arc::new(Mutex::new(inner)), net, backoff: Arc::new(backoff) }
    }

    pub fn state(&self) -> State {
        self.inner.lock().map_or(State::Off, |i| i.state)
    }

    pub fn connections(&self) -> u32 {
        self.inner.lock().map_or(0, |i| i.connections)
    }

    fn set(&self, state: State) {
        if let Ok(mut i) = self.inner.lock() {
            if i.state != state {
                i.state = state;
                i.since = now();
                tracing::info!("Remote access tunnel: {}", state.name());
            }
        }
    }

    /// cloudflared's own option to leave through the port remote access is routed to (#348): it opens its edge connections
    /// itself, so Relay can only tell it the source address. Nothing when the class follows the system default.
    fn edge_bind_args(&self) -> Vec<String> {
        self.net.routed_address(TRAFFIC).map(|a| vec!["--edge-bind-address".to_owned(), a.to_string()]).unwrap_or_default()
    }

    /// Run the tunnel with `token`; a no-op while it already runs with that token.
    pub fn start(&self, token: &str) {
        let Ok(mut i) = self.inner.lock() else { return };
        if i.task.as_ref().is_some_and(|t| !t.is_finished()) && i.token == token {
            return;
        }
        i.token = token.to_owned();
        if let Some(old) = i.task.take() {
            old.abort();
        }
        let me = self.clone();
        let token = token.to_owned();
        i.task = Some(tokio::spawn(async move { me.run(token).await }));
    }

    /// Start cloudflared again with the same token, so it binds the address its route gives now.
    pub async fn restart(&self) {
        let token = {
            let Ok(i) = self.inner.lock() else { return };
            if i.task.as_ref().is_none_or(JoinHandle::is_finished) {
                return;
            }
            i.token.clone()
        };
        self.stop().await;
        self.start(&token);
    }

    pub async fn stop(&self) {
        let task = self.inner.lock().ok().and_then(|mut i| i.task.take());
        if let Some(task) = task {
            task.abort();
            let _ = task.await; // the child has kill_on_drop, so ending the task ends cloudflared
        }
        if let Ok(mut i) = self.inner.lock() {
            i.connections = 0;
        }
        self.set(State::Off);
    }

    async fn run(&self, token: String) {
        let mut attempt = 0usize;
        loop {
            let Some(binary) = find_binary() else {
                self.set(State::Missing);
                tracing::warn!("Remote access: cloudflared is not installed beside Relay");
                return;
            };
            self.set(if attempt == 0 { State::Connecting } else { State::Retrying });
            let started = Instant::now();
            self.once(&binary, &token).await;
            // A run that stayed up a while was healthy: start the back-off again from the beginning.
            attempt = if started.elapsed() > Duration::from_secs(120) { 0 } else { attempt + 1 };
            if let Ok(mut i) = self.inner.lock() {
                i.connections = 0;
            }
            self.set(State::Retrying);
            let wait = self.backoff.get(attempt.min(self.backoff.len().saturating_sub(1))).copied().unwrap_or(Duration::from_secs(60));
            tokio::time::sleep(wait).await;
        }
    }

    async fn once(&self, binary: &Path, token: &str) {
        let mut command = Command::new(binary);
        command
            .args(["tunnel", "--no-autoupdate", "--loglevel", "info"])
            .args(self.edge_bind_args())
            .arg("run")
            .env("TUNNEL_TOKEN", token)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        #[cfg(windows)]
        command.creation_flags(0x0800_0000); // CREATE_NO_WINDOW
        let mut child = match command.spawn() {
            Ok(c) => c,
            Err(e) => {
                tracing::warn!("Remote access: cloudflared would not start ({:?})", e.kind());
                return;
            }
        };
        if let Some(stderr) = child.stderr.take() {
            let mut lines = BufReader::new(stderr).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                self.observe(&line);
            }
        }
        let code = child.wait().await.ok().and_then(|s| s.code());
        tracing::info!("Remote access: cloudflared exited with {code:?}");
    }

    /// Follow cloudflared's log: it says when each edge connection registers and drops.
    pub fn observe(&self, line: &str) {
        if line.contains("Registered tunnel connection") {
            if let Ok(mut i) = self.inner.lock() {
                i.connections += 1;
            }
            self.set(State::Connected);
        } else if line.contains("Unregistered tunnel connection") || line.contains("Connection terminated") {
            let left = self.inner.lock().map_or(0, |mut i| {
                i.connections = i.connections.saturating_sub(1);
                i.connections
            });
            if left == 0 {
                self.set(State::Retrying);
            }
        }
        if line.contains(" ERR ") {
            // The start of the message only: cloudflared's lines can carry addresses and ids.
            tracing::warn!("cloudflared: {}", line.trim().chars().take(200).collect::<String>());
        }
    }
}
