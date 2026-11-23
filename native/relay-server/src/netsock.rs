//! Sockets that really leave through the network port a traffic class is routed to (`app/netsock.py`, #345), for modules.
//!
//! Binding a socket to a port's address picks only the source address: the route table still picks the interface by
//! destination, so traffic for another network could leave by the default port anyway. A routed socket is therefore also
//! pinned to the interface: SO_BINDTODEVICE on Linux, IP_BOUND_IF on macOS, IP_UNICAST_IF on Windows. A class with no route,
//! or one that fell back to the default network, gets an ordinary socket, exactly as before routes existed.
//!
//! Linux kernels before 5.7 refuse the pin without CAP_NET_RAW; the socket then keeps the source address only, which is
//! right whenever the device is on that port's own network. Every error is a fixed sentence (`RouteError`), never the OS's
//! text, because it reaches the admin page.

use std::collections::BTreeMap;
use std::net::{IpAddr, Ipv4Addr, SocketAddr};
use std::sync::Arc;
use std::time::Duration;

use relay_core::module::BoxFuture;
use relay_core::{Config, RouteError, Router, UdpOptions};
use serde_json::Value;
use socket2::{Domain, Protocol, SockAddr, Socket, Type};
use tokio::net::{TcpSocket, TcpStream, UdpSocket};

use crate::netinfo::{self, Port};
use crate::netroute::{self, Policy, Resolved};

/// The router `relay-server` gives modules (`Host::with_net`): reads the saved routes on every call, so a route changed on
/// the Network page applies to the next connection.
pub struct RoutedNet {
    config: Arc<Config>,
    /// Classes a module declared, id -> (label, default policy), beside core's.
    classes: BTreeMap<String, (String, Policy)>,
}

impl RoutedNet {
    pub fn new(config: Arc<Config>, module_classes: BTreeMap<String, (String, Policy)>) -> Self {
        Self { config, classes: module_classes }
    }

    fn resolve(&self, class: &str) -> (Resolved, Option<Port>) {
        let network = self.config.get("network").unwrap_or(Value::Null);
        let ports = netinfo::interfaces();
        let (label, policy) = match self.classes.get(class) {
            Some((l, p)) => (l.clone(), *p),
            None => match netroute::core_classes().get(class) {
                Some((l, p)) => ((*l).to_owned(), *p),
                None => (class.to_owned(), Policy::Fallback),
            },
        };
        let r = netroute::resolve(class, &label, policy, &network, &ports);
        let port = r.port_id.as_ref().and_then(|id| ports.into_iter().find(|p| &p.id == id));
        (r, port)
    }
}

fn name_of(r: &Resolved, port: Option<&Port>) -> String {
    match port {
        Some(p) if !p.friendly_name.is_empty() => p.friendly_name.clone(),
        Some(p) => p.name.clone(),
        None => r.port_id.clone().map_or_else(|| "the chosen network port".to_owned(), |_| "the chosen network port".to_owned()),
    }
}

fn require(r: &Resolved) -> Result<(), RouteError> {
    if r.available {
        Ok(())
    } else if r.reason.is_empty() {
        Err(RouteError::new("The chosen network port cannot be used right now."))
    } else {
        Err(RouteError::new(r.reason.clone()))
    }
}

/// A route that needs a pinned socket: routed to a port that is there, not fallen back.
fn pinned(r: &Resolved) -> bool {
    r.routed() && !r.fallback && r.address.is_some()
}

/// The OS's index for an interface name, which macOS and Windows pin by.
fn index_of(name: &str) -> Option<u32> {
    netdev::get_interfaces().into_iter().find(|i| i.name == name).map(|i| i.index).filter(|i| *i != 0)
}

/// Pin `sock` to the route's interface before it connects or binds. `Ok(false)` for an unrouted class or a refused pin.
fn pin(sock: &Socket, r: &Resolved, port: Option<&Port>) -> Result<bool, RouteError> {
    if !pinned(r) {
        return Ok(false);
    }
    let Some(port) = port else { return Ok(false) };
    let could_not = || RouteError::new(format!("Relay could not send through {}.", name_of(r, Some(port))));
    #[cfg(any(target_os = "linux", target_os = "android"))]
    {
        match sock.bind_device(Some(port.name.as_bytes())) {
            Ok(()) => return Ok(true),
            Err(e) if e.kind() == std::io::ErrorKind::PermissionDenied => {
                tracing::warn!("Could not pin traffic to {} without CAP_NET_RAW; sending from its address only", port.name);
                return Ok(false);
            }
            Err(_) => return Err(could_not()),
        }
    }
    #[cfg(target_os = "macos")]
    {
        let Some(index) = index_of(&port.name).and_then(std::num::NonZeroU32::new) else {
            return Err(RouteError::new(format!("Relay could not find {} to send through.", name_of(r, Some(port)))));
        };
        return sock.bind_device_by_index_v4(Some(index)).map(|()| true).map_err(|_| could_not());
    }
    #[cfg(windows)]
    {
        use std::os::windows::io::AsRawSocket;
        use windows_sys::Win32::Networking::WinSock::{setsockopt, IPPROTO_IP, IP_UNICAST_IF};
        let Some(index) = index_of(&port.name) else {
            return Err(RouteError::new(format!("Relay could not find {} to send through.", name_of(r, Some(port)))));
        };
        // IP_UNICAST_IF takes the index in network byte order.
        let value = index.to_be();
        let rc = unsafe {
            setsockopt(sock.as_raw_socket() as usize, IPPROTO_IP, IP_UNICAST_IF, std::ptr::from_ref(&value).cast::<u8>(), 4)
        };
        return if rc == 0 { Ok(true) } else { Err(could_not()) };
    }
    #[allow(unreachable_code)]
    {
        let _ = (sock, could_not, port);
        Ok(false)
    }
}

/// Windows reports an ICMP "port unreachable" as an error on the next receive; turn that off for every UDP socket.
#[cfg(windows)]
fn no_connreset(sock: &Socket) {
    use std::os::windows::io::AsRawSocket;
    use windows_sys::Win32::Networking::WinSock::{WSAIoctl, SIO_UDP_CONNRESET};
    let off: u32 = 0;
    let mut returned: u32 = 0;
    unsafe {
        WSAIoctl(
            sock.as_raw_socket() as usize,
            SIO_UDP_CONNRESET,
            std::ptr::from_ref(&off).cast(),
            4,
            std::ptr::null_mut(),
            0,
            &mut returned,
            std::ptr::null_mut(),
            None,
        );
    }
}

#[cfg(not(windows))]
fn no_connreset(_sock: &Socket) {}

impl Router for RoutedNet {
    fn connect_tcp<'a>(
        &'a self,
        class: &'a str,
        host: &'a str,
        port: u16,
        timeout: Duration,
    ) -> BoxFuture<'a, Result<TcpStream, RouteError>> {
        Box::pin(async move {
            let (r, p) = self.resolve(class);
            require(&r)?;
            let addr = relay_core::net::resolve_v4(host, port).await?;
            let refused = || RouteError::new(format!("The connection through {} could not be made.", name_of(&r, p.as_ref())));
            let sock = Socket::new(Domain::IPV4, Type::STREAM, Some(Protocol::TCP)).map_err(|_| refused())?;
            if pinned(&r) {
                pin(&sock, &r, p.as_ref())?;
                let local: Ipv4Addr = r.address.as_deref().and_then(|a| a.parse().ok()).ok_or_else(refused)?;
                sock.bind(&SockAddr::from(SocketAddr::new(IpAddr::V4(local), 0))).map_err(|_| refused())?;
            }
            sock.set_nonblocking(true).map_err(|_| refused())?;
            let std: std::net::TcpStream = sock.into();
            let tcp = TcpSocket::from_std_stream(std);
            match tokio::time::timeout(timeout, tcp.connect(addr)).await {
                Ok(Ok(s)) => Ok(s),
                _ => Err(refused()),
            }
        })
    }

    fn udp(&self, class: &str, o: &UdpOptions) -> Result<UdpSocket, RouteError> {
        let (r, p) = self.resolve(class);
        require(&r)?;
        let name = name_of(&r, p.as_ref());
        let in_use = || RouteError::new(format!("UDP port {} on {name} is already in use or cannot be opened.", o.port));
        let sock = Socket::new(Domain::IPV4, Type::DGRAM, Some(Protocol::UDP)).map_err(|_| in_use())?;
        no_connreset(&sock);
        if o.reuse {
            let _ = sock.set_reuse_address(true);
            #[cfg(all(unix, not(target_os = "solaris"), not(target_os = "illumos")))]
            let _ = sock.set_reuse_port(true);
        }
        if o.broadcast {
            sock.set_broadcast(true).map_err(|_| in_use())?;
        }
        // An unrouted UDP socket is always bound (an unbound one has no address on Windows).
        let local = if pinned(&r) {
            pin(&sock, &r, p.as_ref())?;
            r.address.as_deref().and_then(|a| a.parse().ok()).ok_or_else(in_use)?
        } else {
            match o.host {
                Some(IpAddr::V4(a)) => a,
                _ => Ipv4Addr::UNSPECIFIED,
            }
        };
        sock.bind(&SockAddr::from(SocketAddr::new(IpAddr::V4(local), o.port))).map_err(|_| in_use())?;
        sock.set_nonblocking(true).map_err(|_| in_use())?;
        UdpSocket::from_std(sock.into()).map_err(|_| in_use())
    }
}
