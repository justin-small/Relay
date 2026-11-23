//! Outbound network traffic for modules (#662): a module never opens its own socket. It asks `Host::net()` for a TCP
//! connection or a UDP socket **for a traffic class**, and the server's router sends it out the network port the operator
//! assigned that class (`app/netsock.py`, #345). The sockets are tokio's; the routing is built around them.
//!
//! Core defines the interface and a plain, unrouted fallback (what the tests use); `relay-server` supplies the real router,
//! which reads the saved routes and pins each socket to its port's interface. `tests/test_rust_modules.py` fails a module
//! source that names `tokio::net` or `std::net` sockets directly.

use std::fmt;
use std::io;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;

use tokio::net::{TcpStream, UdpSocket};

use crate::module::BoxFuture;

/// A routed socket could not be made. `message` is a fixed sentence fit for the admin page, never the OS's error text.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RouteError {
    pub message: String,
}

impl RouteError {
    pub fn new(message: impl Into<String>) -> Self {
        Self { message: message.into() }
    }
}

impl fmt::Display for RouteError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.message)
    }
}

impl std::error::Error for RouteError {}

impl From<RouteError> for io::Error {
    fn from(e: RouteError) -> Self {
        io::Error::other(e)
    }
}

/// How to open a UDP socket.
#[derive(Clone, Debug, Default)]
pub struct UdpOptions {
    /// The local port; 0 lets the OS choose.
    pub port: u16,
    /// A local address to bind when the class is not routed (a receiver names one). A routed class binds its port's address.
    pub host: Option<IpAddr>,
    pub broadcast: bool,
    pub reuse: bool,
}

pub trait Router: Send + Sync + 'static {
    /// Connect to `host:port` (a name or an address) through the class's network port, giving up after `timeout`.
    fn connect_tcp<'a>(
        &'a self,
        class: &'a str,
        host: &'a str,
        port: u16,
        timeout: Duration,
    ) -> BoxFuture<'a, Result<TcpStream, RouteError>>;

    /// A UDP socket bound and pinned for the class.
    fn udp(&self, class: &str, options: &UdpOptions) -> Result<UdpSocket, RouteError>;

    /// The address of the network port the class is routed to, when it is routed, available and not fallen back: for a program
    /// that opens its own connections and can only be told a source address (cloudflared's `--edge-bind-address`).
    fn routed_address(&self, _class: &str) -> Option<IpAddr> {
        None
    }
}

/// What a module holds: cheap to clone, the same router everywhere.
#[derive(Clone)]
pub struct Net(Arc<dyn Router>);

impl Net {
    pub fn new(router: Arc<dyn Router>) -> Self {
        Self(router)
    }

    /// No routing: every class follows the system's default network. The default for `Host::new`, and what tests use.
    pub fn unrouted() -> Self {
        Self(Arc::new(Unrouted))
    }

    pub async fn connect_tcp(&self, class: &str, host: &str, port: u16, timeout: Duration) -> Result<TcpStream, RouteError> {
        self.0.connect_tcp(class, host, port, timeout).await
    }

    pub fn udp(&self, class: &str, options: &UdpOptions) -> Result<UdpSocket, RouteError> {
        self.0.udp(class, options)
    }

    /// See [`Router::routed_address`].
    pub fn routed_address(&self, class: &str) -> Option<IpAddr> {
        self.0.routed_address(class)
    }
}

/// The IPv4 address a name resolves to first (Relay's networks are IPv4), or a fixed sentence.
pub async fn resolve_v4(host: &str, port: u16) -> Result<SocketAddr, RouteError> {
    let found = tokio::net::lookup_host((host, port)).await.map_err(|_| RouteError::new("The address could not be found."))?;
    found.into_iter().find(SocketAddr::is_ipv4).ok_or_else(|| RouteError::new("The address could not be found."))
}

struct Unrouted;

impl Router for Unrouted {
    fn connect_tcp<'a>(
        &'a self,
        _class: &'a str,
        host: &'a str,
        port: u16,
        timeout: Duration,
    ) -> BoxFuture<'a, Result<TcpStream, RouteError>> {
        Box::pin(async move {
            let addr = resolve_v4(host, port).await?;
            match tokio::time::timeout(timeout, TcpStream::connect(addr)).await {
                Ok(Ok(s)) => Ok(s),
                _ => Err(RouteError::new("The connection could not be made.")),
            }
        })
    }

    fn udp(&self, _class: &str, o: &UdpOptions) -> Result<UdpSocket, RouteError> {
        let addr = SocketAddr::new(o.host.unwrap_or(IpAddr::V4(Ipv4Addr::UNSPECIFIED)), o.port);
        let std = std::net::UdpSocket::bind(addr)
            .map_err(|_| RouteError::new(format!("UDP port {} is already in use or cannot be opened.", o.port)))?;
        if o.broadcast {
            let _ = std.set_broadcast(true);
        }
        std.set_nonblocking(true).map_err(|_| RouteError::new("The socket could not be set up."))?;
        UdpSocket::from_std(std).map_err(|_| RouteError::new("The socket could not be set up."))
    }
}
