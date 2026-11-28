//! This computer's network ports (`app/netinfo.py`): one record per usable adapter, with a stable id, its addresses and
//! whether the link is up. The Rust form reads them with `netdev` instead of `ifaddr`, `ifconfig` and `/sys/class/net`.
//!
//! The ids match Python's, because a route saved in `config.json` names a port by id (`mac:<mac>` for hardware with a MAC,
//! `name:<name>` for a bridge, a VPN or an adapter with no MAC), and the Python server may read the same file.

use std::net::Ipv4Addr;

/// One network port as the machine shows it now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Port {
    pub id: String,
    pub name: String,
    pub friendly_name: String,
    pub mac: Option<String>,
    /// Interface notation, `192.168.1.20/24`.
    pub ipv4: Vec<String>,
    pub default_route: bool,
    /// `None` when the OS does not say.
    pub up: Option<bool>,
    pub virtual_: bool,
    pub vpn: bool,
    pub loopback: bool,
    pub link_local_only: bool,
}

impl Port {
    pub fn addresses(&self) -> Vec<String> {
        self.ipv4.iter().map(|a| a.split('/').next().unwrap_or(a).to_owned()).collect()
    }
}

/// An address Relay may hand out: not loopback, link-local, unspecified or multicast.
pub fn usable(ip: &str) -> bool {
    match ip.parse::<Ipv4Addr>() {
        Ok(a) => !(a.is_loopback() || a.is_link_local() || a.is_unspecified() || a.is_multicast()),
        Err(_) => false,
    }
}

fn starts_with_any(name: &str, prefixes: &[&str]) -> bool {
    let lower = name.to_ascii_lowercase();
    prefixes.iter().any(|p| lower.starts_with(p))
}

fn is_vpn_name(name: &str) -> bool {
    starts_with_any(name, &["utun", "ipsec", "ppp", "tun", "tap", "wg", "tailscale", "zt", "nordlynx", "vpn"])
}

fn is_virtual_name(name: &str) -> bool {
    starts_with_any(
        name,
        &["bridge", "br-", "docker", "veth", "vmnet", "vboxnet", "virbr", "vnic", "lxc", "lxd", "cni", "flannel", "podman"],
    )
}

/// The OS's own plumbing (Apple Wireless Direct, the hotspot, IPv6 tunnels, container veths): `awdl0`, `llw0`, `ap1`...
fn is_noise_name(name: &str) -> bool {
    let lower = name.to_ascii_lowercase();
    if lower.starts_with("veth") {
        return true;
    }
    for stem in ["awdl", "llw", "anpi", "ap", "gif", "stf", "xhc", "p2p"] {
        if let Some(rest) = lower.strip_prefix(stem) {
            if !rest.is_empty() && rest.bytes().all(|b| b.is_ascii_digit()) {
                return true;
            }
        }
    }
    false
}

fn mac_text(octets: [u8; 6]) -> Option<String> {
    if octets == [0; 6] {
        return None;
    }
    Some(octets.iter().map(|b| format!("{b:02x}")).collect::<Vec<_>>().join(":"))
}

/// Give each port its stable id, as `_with_ids` does: `mac:<mac>` for hardware with a MAC, `name:<name>` otherwise, and the
/// name added when two hardware ports share a MAC.
pub fn with_ids(mut ports: Vec<Port>) -> Vec<Port> {
    let base: Vec<String> = ports
        .iter()
        .map(|p| match (&p.mac, p.virtual_) {
            (Some(mac), false) => format!("mac:{mac}"),
            _ => format!("name:{}", p.name),
        })
        .collect();
    for (i, p) in ports.iter_mut().enumerate() {
        let shared = base[i].starts_with("mac:") && base.iter().filter(|b| **b == base[i]).count() > 1;
        p.id = if shared { format!("{}/{}", base[i], p.name) } else { base[i].clone() };
    }
    ports
}

/// Ports left out of the list: loopback and the OS's own plumbing, unless one carries a LAN address.
pub fn hidden(p: &Port) -> bool {
    if p.loopback {
        return true;
    }
    if p.addresses().iter().any(|a| usable(a)) {
        return false;
    }
    if is_noise_name(&p.name) {
        return true;
    }
    p.virtual_ && p.ipv4.is_empty()
}

/// One record per usable port, in the order the OS lists them. Unplugged hardware is listed, marked down.
pub fn interfaces() -> Vec<Port> {
    let ports: Vec<Port> = netdev::get_interfaces()
        .into_iter()
        .map(|i| {
            let ipv4: Vec<String> = i.ipv4.iter().map(|n| format!("{}/{}", n.addr(), n.prefix_len())).collect();
            let loopback = i.is_loopback() || i.ipv4.iter().any(|n| n.addr().is_loopback());
            let vpn = !loopback && (is_vpn_name(&i.name) || i.is_tun());
            let virtual_ = loopback
                || vpn
                || is_virtual_name(&i.name)
                || matches!(i.if_type, netdev::interface::InterfaceType::Tunnel);
            let link_local_only = !i.ipv4.is_empty() && i.ipv4.iter().all(|n| n.addr().is_link_local());
            let friendly = i.friendly_name.clone().or_else(|| i.description.clone()).unwrap_or_else(|| i.name.clone());
            Port {
                id: String::new(),
                name: i.name.clone(),
                friendly_name: friendly,
                mac: i.mac_addr.and_then(|m| mac_text(m.octets())),
                ipv4,
                default_route: i.default,
                up: Some(i.is_running() || i.is_up() && !i.ipv4.is_empty()),
                virtual_,
                vpn,
                loopback,
                link_local_only,
            }
        })
        .collect();
    with_ids(ports).into_iter().filter(|p| !hidden(p)).collect()
}

/// LAN addresses, the default route's first, then the operator's extras (`RELAY_ADMIN_IPS`). Empty with no network.
pub fn lan_ips(ports: &[Port]) -> Vec<String> {
    let mut found: Vec<String> = Vec::new();
    for p in ports.iter().filter(|p| !p.loopback) {
        for a in p.addresses() {
            if usable(&a) && !found.contains(&a) {
                found.push(a);
            }
        }
    }
    if let Some(first) = ports.iter().filter(|p| p.default_route && !p.loopback).flat_map(Port::addresses).find(|a| usable(a)) {
        found.retain(|a| *a != first);
        found.insert(0, first);
    }
    for extra in extra_ips() {
        if !found.contains(&extra) {
            found.push(extra);
        }
    }
    found
}

/// `RELAY_ADMIN_IPS`, parsed; entries that are not IP addresses are ignored.
pub fn extra_ips() -> Vec<String> {
    let raw = std::env::var("RELAY_ADMIN_IPS").unwrap_or_default();
    let mut out: Vec<String> = Vec::new();
    for item in raw.replace(';', ",").split(',') {
        let item = item.trim();
        if item.parse::<std::net::IpAddr>().is_ok() && !out.iter().any(|o| o == item) {
            out.push(item.to_owned());
        }
    }
    out
}
