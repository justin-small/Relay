//! Which ports Relay serves on, and the sockets that serve them (`app/serving.py`, `run.py`).
//!
//! Viewers get plain HTTP on `port` (80 by default); the operator panel is only ever served over HTTPS on `admin_port` (443
//! by default). If 80 or 443 cannot be opened (in use, or not permitted) Relay falls back to 8080 / 8443 and says so in a
//! notice for the admin page. Only the defaults fall back: a port the operator chose that cannot be opened is an error they
//! should see, not a silent substitution.
//!
//! Which addresses a listener binds follows its traffic class's route (`netroute`): no route binds the configured host; a
//! routed class binds its port's address and 127.0.0.1 (so this computer always reaches it), and a request to any other
//! address is refused at the socket.

use std::io;
use std::net::{IpAddr, SocketAddr};

use socket2::{Domain, Protocol, SockAddr, Socket, Type};

use crate::netroute::Resolved;

pub const DEFAULT_VIEWER_PORT: u16 = 80;
pub const DEFAULT_ADMIN_PORT: u16 = 443;
pub const LOOPBACK: &str = "127.0.0.1";

/// Where a default port goes when it cannot be opened.
pub fn fallback(port: u16) -> Option<u16> {
    match port {
        80 => Some(8080),
        443 => Some(8443),
        _ => None,
    }
}

/// The listener of one kind of traffic: its sockets' addresses and the notices about them.
#[derive(Clone, Debug)]
pub struct Plan {
    pub hosts: Vec<String>,
    pub notes: Vec<String>,
}

/// `scheme://host[:port]path`, leaving the port out when it is the scheme's own.
pub fn link(scheme: &str, host: &str, port: u16, path: &str) -> String {
    let default = if scheme == "https" { 443 } else { 80 };
    let port = if port == default { String::new() } else { format!(":{port}") };
    format!("{scheme}://{host}{port}{path}")
}

/// The hosts to bind for a listener of a traffic class, and notices.
///
/// No route, or a route that fell back to the default network: the configured host. A route whose port is there: that
/// port's address and 127.0.0.1. A strict route whose port is missing: only 127.0.0.1.
pub fn plan(resolved: Option<&Resolved>, crew_panel: bool, configured: &str, port: u16) -> Plan {
    let plain = || Plan { hosts: vec![configured.to_owned()], notes: Vec::new() };
    let Some(r) = resolved else { return plain() };
    if !r.routed() || r.fallback {
        return plain();
    }
    let mut notes = Vec::new();
    let hosts = match (&r.address, r.available) {
        (Some(addr), true) if addr != LOOPBACK => vec![addr.clone(), LOOPBACK.to_owned()],
        (Some(addr), true) => vec![addr.clone()],
        _ => {
            notes.push(format!("{} Until it is back, only this computer can reach it.", r.reason));
            vec![LOOPBACK.to_owned()]
        }
    };
    if crew_panel {
        let where_ = match (&r.address, r.available) {
            (Some(a), true) => format!("{a} and "),
            _ => String::new(),
        };
        notes.push(format!(
            "The operator panel answers only on {where_}this computer ({}). If you are locked out, open it on this computer, \
             or remove \"crew\" from \"routes\" under \"network\" in config.json and restart Relay.",
            link("https", LOOPBACK, port, "/admin")
        ));
    }
    Plan { hosts, notes }
}

/// A fixed sentence for an error, never the error's own text: this reaches the admin page.
pub fn why(e: &io::Error) -> &'static str {
    match e.kind() {
        io::ErrorKind::AddrInUse => "it is already in use",
        io::ErrorKind::PermissionDenied => "this account is not permitted to use it",
        _ => match e.raw_os_error() {
            Some(10048) => "it is already in use",
            Some(10013) => "this account is not permitted to use it",
            _ => "it could not be opened",
        },
    }
}

/// Open a listening socket on `host:port`.
pub fn bind(host: &str, port: u16) -> io::Result<std::net::TcpListener> {
    let ip: IpAddr = host.parse().map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "not an address"))?;
    let addr = SocketAddr::new(ip, port);
    let sock = Socket::new(Domain::for_address(addr), Type::STREAM, Some(Protocol::TCP))?;
    // On Windows SO_REUSEADDR lets a second process take a port that is already in use, which would defeat the fallback;
    // leaving it off makes the bind fail when the port is taken.
    #[cfg(not(windows))]
    sock.set_reuse_address(true)?;
    sock.bind(&SockAddr::from(addr))?;
    sock.listen(2048)?;
    sock.set_nonblocking(true)?;
    Ok(sock.into())
}

/// One opened socket, the port it really got, and a sentence when it had to fall back.
pub struct Opened {
    pub socket: std::net::TcpListener,
    pub port: u16,
    pub note: Option<String>,
}

/// Open a listening socket on `port`, falling back for 80 and 443. The error is a plain sentence.
pub fn listen(host: &str, port: u16, what: &str) -> Result<Opened, String> {
    listen_via(host, port, what, fallback(port))
}

/// `listen` with the fallback port given, so a test need not depend on what is free on 80 and 8080.
pub fn listen_via(host: &str, port: u16, what: &str, alt: Option<u16>) -> Result<Opened, String> {
    match bind(host, port) {
        Ok(socket) => {
            let port = socket.local_addr().map(|a| a.port()).unwrap_or(port);
            Ok(Opened { socket, port, note: None })
        }
        Err(e) => {
            let Some(alt) = alt else {
                return Err(format!("Cannot listen on {host}:{port} for the {what}: {}.", why(&e)));
            };
            match bind(host, alt) {
                Ok(socket) => Ok(Opened {
                    socket,
                    port: alt,
                    note: Some(format!("The {what} could not use port {port} ({}), so it is on port {alt}.", why(&e))),
                }),
                Err(e2) => Err(format!(
                    "Cannot listen on {host}:{port} for the {what} ({}) or on {alt} ({}).",
                    why(&e),
                    why(&e2)
                )),
            }
        }
    }
}

/// Every socket of one listener, falling back from 80/443 together. The first host decides the port; the others join it.
pub struct Opening {
    pub sockets: Vec<(String, std::net::TcpListener)>,
    pub port: u16,
    pub notes: Vec<String>,
}

pub fn open_all(hosts: &[String], port: u16, what: &str, mut notes: Vec<String>) -> Result<Opening, String> {
    let mut sockets: Vec<(String, std::net::TcpListener)> = Vec::new();
    let mut bound = port;
    for (i, host) in hosts.iter().enumerate() {
        if i == 0 {
            let o = listen(host, port, what)?;
            bound = o.port;
            notes.extend(o.note);
            sockets.push((host.clone(), o.socket));
        } else {
            match bind(host, bound) {
                Ok(s) => sockets.push((host.clone(), s)),
                Err(e) => notes.push(format!("The {what} could not also listen on {host}:{bound} ({}).", why(&e))),
            }
        }
    }
    Ok(Opening { sockets, port: bound, notes })
}
