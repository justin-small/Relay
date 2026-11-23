//! The listeners: one group of sockets for the viewer pages, one for the HTTPS panel and one for each module port, each serving
//! the same routes behind its own port rules (`gate`). A group's sockets follow its traffic class's route (`netroute`), and
//! `rebind` moves them while Relay runs when that route's address changes (`rebind` in `app/serving.py`).
//!
//! New sockets are opened after the old ones close, because Linux will not bind a port's address while the same port is bound
//! to every address. If an address cannot be opened the listener keeps 127.0.0.1. A connection already open is never cut: it
//! belongs to its own task and ends when its client leaves.

use std::collections::BTreeMap;
use std::convert::Infallible;
use std::sync::{Arc, Mutex, RwLock};
use std::time::Duration;

use axum::body::Body;
use axum::extract::Request;
use axum::middleware::from_fn_with_state;
use axum::Router;
use hyper::body::Incoming;
use hyper::service::service_fn;
use hyper_util::rt::{TokioExecutor, TokioIo};
use hyper_util::server::conn::auto;
use tokio::net::TcpListener;
use tokio::task::JoinHandle;
use tokio_rustls::TlsAcceptor;
use tower::ServiceExt;

use crate::forward::{self, Conn, Upstream};
use crate::gate::{gate, GateState, Kind, Prefixes};
use crate::listen::{self, LOOPBACK};
use crate::netinfo::Port;
use crate::netroute::{self, Policy, Resolved};

/// How long a client may take to finish the TLS handshake.
const HANDSHAKE: Duration = Duration::from_secs(10);

/// What the machine's network looks like right now: the saved routes and the ports.
#[derive(Clone, Debug)]
pub struct Net {
    /// config.json's `"network"` value.
    pub network: serde_json::Value,
    pub ports: Vec<Port>,
}

/// What a listener is, before any socket is open.
#[derive(Clone, Debug)]
pub struct Spec {
    /// `"viewers"`, `"panel"`, `"<module id>.<listener name>"`.
    pub key: String,
    /// What to call it in a sentence: "viewer pages", "operator panel", "Signage (screens)".
    pub what: String,
    /// The traffic class that places its sockets; `None` binds the configured host (tests).
    pub traffic: Option<String>,
    pub traffic_label: String,
    pub policy: Policy,
    /// The host it binds when the class has no route.
    pub configured: String,
    /// The port asked for; 80 and 443 fall back to 8080 and 8443.
    pub port: u16,
    pub secure: bool,
    pub kind: Kind,
}

impl Spec {
    fn resolved(&self, net: &Net) -> Option<Resolved> {
        let class = self.traffic.as_deref()?;
        Some(netroute::resolve(class, &self.traffic_label, self.policy, &net.network, &net.ports))
    }

    fn plan(&self, net: &Net, port: u16) -> listen::Plan {
        let resolved = self.resolved(net);
        listen::plan(resolved.as_ref(), self.traffic.as_deref() == Some("crew") && self.secure, &self.configured, port)
    }
}

/// A listener as the app and the admin page see it.
#[derive(Clone, Debug)]
pub struct GroupInfo {
    pub spec: Spec,
    /// The port really bound.
    pub port: u16,
    pub hosts: Vec<String>,
    pub notes: Vec<String>,
}

struct Group {
    spec: Spec,
    port: u16,
    tasks: BTreeMap<String, JoinHandle<()>>,
    notes: Vec<String>,
}

pub struct Server {
    routes: Router,
    upstream: Option<Arc<Upstream>>,
    prefixes: Arc<RwLock<Prefixes>>,
    tls: Mutex<Option<Arc<rustls::ServerConfig>>>,
    groups: Mutex<Vec<Group>>,
}

impl Server {
    /// `routes` are the routes the server serves itself; what they do not answer goes to `upstream`, or is a 404 without one.
    pub fn new(routes: Router, upstream: Option<Arc<Upstream>>) -> Self {
        Self {
            routes,
            upstream,
            prefixes: Arc::new(RwLock::new(Prefixes::default())),
            tls: Mutex::new(None),
            groups: Mutex::new(Vec::new()),
        }
    }

    pub fn set_prefixes(&self, prefixes: Prefixes) {
        if let Ok(mut slot) = self.prefixes.write() {
            *slot = prefixes;
        }
    }

    /// The TLS config the secure groups serve with. Set before the panel is opened.
    pub fn set_tls(&self, config: Arc<rustls::ServerConfig>) {
        if let Ok(mut slot) = self.tls.lock() {
            *slot = Some(config);
        }
    }

    fn router(&self, kind: &Kind) -> Router {
        let routes = self.routes.clone();
        let routes = match &self.upstream {
            Some(up) => {
                let up = Arc::clone(up);
                routes.fallback(move |req: Request| {
                    let up = Arc::clone(&up);
                    async move { up.forward(req).await }
                })
            }
            None => routes.fallback(|| async { forward::nothing_here() }),
        };
        routes.layer(from_fn_with_state(GateState { kind: kind.clone(), prefixes: Arc::clone(&self.prefixes) }, gate))
    }

    fn serve(&self, spec: &Spec, sock: std::net::TcpListener, port: u16) -> Result<JoinHandle<()>, String> {
        let listener = TcpListener::from_std(sock).map_err(|_| "A listening socket could not be used.".to_owned())?;
        let acceptor = if spec.secure {
            let cfg = self.tls.lock().ok().and_then(|t| t.clone()).ok_or("The panel has no certificate yet.")?;
            Some(TlsAcceptor::from(cfg))
        } else {
            None
        };
        Ok(tokio::spawn(accept_loop(listener, self.router(&spec.kind), acceptor, port)))
    }

    /// Open every socket of a listener. An error is a plain sentence; nothing is left open then.
    pub fn open(&self, spec: Spec, net: &Net) -> Result<(), String> {
        let plan = spec.plan(net, spec.port);
        let opening = listen::open_all(&plan.hosts, spec.port, &spec.what, plan.notes)?;
        let mut tasks = BTreeMap::new();
        for (host, sock) in opening.sockets {
            match self.serve(&spec, sock, opening.port) {
                Ok(task) => {
                    tasks.insert(host, task);
                }
                Err(e) => {
                    for t in tasks.values() {
                        t.abort();
                    }
                    return Err(e);
                }
            }
        }
        if let Ok(mut groups) = self.groups.lock() {
            groups.push(Group { spec, port: opening.port, tasks, notes: opening.notes });
        }
        Ok(())
    }

    /// Move each listener whose class now resolves to other addresses. Returns the keys of those moved.
    pub async fn rebind(&self, net: &Net) -> Vec<String> {
        // Taken out while it works, so no lock is held across an await; only the watch calls this.
        let mut groups = match self.groups.lock() {
            Ok(mut slot) => std::mem::take(&mut *slot),
            Err(_) => return Vec::new(),
        };
        let mut moved = Vec::new();
        for g in groups.iter_mut() {
            if g.spec.traffic.is_none() {
                continue;
            }
            let plan = g.spec.plan(net, g.port);
            g.notes = plan.notes.clone();
            let have: Vec<String> = g.tasks.keys().cloned().collect();
            if have.len() == plan.hosts.len() && plan.hosts.iter().all(|h| have.contains(h)) {
                continue;
            }
            for host in have.iter().filter(|h| !plan.hosts.contains(h)) {
                if let Some(task) = g.tasks.remove(host) {
                    // Waited for, so the socket is really closed before the new one is opened.
                    task.abort();
                    let _ = task.await;
                }
            }
            let missing: Vec<&String> = plan.hosts.iter().filter(|h| !g.tasks.contains_key(*h)).collect();
            for host in missing {
                match listen::bind(host, g.port) {
                    Ok(sock) => match self.serve(&g.spec, sock, g.port) {
                        Ok(task) => {
                            g.tasks.insert(host.clone(), task);
                        }
                        Err(_) => g.notes.push(format!("The {} could not listen on {host}:{}.", g.spec.what, g.port)),
                    },
                    Err(e) => g.notes.push(format!(
                        "The {} could not listen on {host}:{} ({}). Restart Relay if it does not recover.",
                        g.spec.what,
                        g.port,
                        listen::why(&e)
                    )),
                }
            }
            if g.tasks.is_empty() {
                if let Ok(sock) = listen::bind(LOOPBACK, g.port) {
                    if let Ok(task) = self.serve(&g.spec, sock, g.port) {
                        g.tasks.insert(LOOPBACK.to_owned(), task);
                    }
                }
            }
            tracing::info!(listener = %g.spec.what, hosts = %g.tasks.keys().cloned().collect::<Vec<_>>().join(", "), port = g.port, "listener moved");
            moved.push(g.spec.key.clone());
        }
        if let Ok(mut slot) = self.groups.lock() {
            slot.append(&mut groups);
        }
        moved
    }

    pub fn groups(&self) -> Vec<GroupInfo> {
        let Ok(groups) = self.groups.lock() else { return Vec::new() };
        groups
            .iter()
            .map(|g| GroupInfo {
                spec: g.spec.clone(),
                port: g.port,
                hosts: g.tasks.keys().cloned().collect(),
                notes: g.notes.clone(),
            })
            .collect()
    }

    /// Stop accepting. Connections already open end with their clients.
    pub fn close(&self) {
        if let Ok(mut groups) = self.groups.lock() {
            for g in groups.drain(..) {
                for t in g.tasks.values() {
                    t.abort();
                }
            }
        }
    }
}

async fn accept_loop(listener: TcpListener, router: Router, tls: Option<TlsAcceptor>, local_port: u16) {
    loop {
        let (sock, peer) = match listener.accept().await {
            Ok(pair) => pair,
            // A connection that died before it was accepted, or no file descriptors for a moment: keep serving.
            Err(_) => {
                tokio::time::sleep(Duration::from_millis(50)).await;
                continue;
            }
        };
        let _ = sock.set_nodelay(true);
        let (router, tls) = (router.clone(), tls.clone());
        tokio::spawn(async move {
            let conn = Conn { peer, local_port, secure: tls.is_some() };
            let svc = service_fn(move |req: hyper::Request<Incoming>| {
                let mut req = req.map(Body::new);
                req.extensions_mut().insert(conn);
                let router = router.clone();
                async move { Ok::<_, Infallible>(router.oneshot(req).await.unwrap_or_else(|e| match e {})) }
            });
            let builder = auto::Builder::new(TokioExecutor::new());
            match tls {
                Some(acceptor) => {
                    if let Ok(Ok(stream)) = tokio::time::timeout(HANDSHAKE, acceptor.accept(sock)).await {
                        let _ = builder.serve_connection_with_upgrades(TokioIo::new(stream), svc).await;
                    }
                }
                None => {
                    let _ = builder.serve_connection_with_upgrades(TokioIo::new(sock), svc).await;
                }
            }
        });
    }
}
