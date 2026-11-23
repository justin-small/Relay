//! Which network port each kind of traffic uses (`app/netroute.py`, #344 to #348), as far as the listeners need it: where a
//! traffic class goes right now, from the routes saved under `"network"` in config.json and the ports the machine shows.
//!
//! `resolve` is a pure function of its inputs (the saved routes, the ports, the LAN addresses), so every rule can be tested
//! without touching an interface. The Python server writes the routes (the Network page); this side only reads them, and
//! reads them again on every supervisor tick, so a route changed on the page moves a listener within seconds.

use std::collections::BTreeMap;

use serde_json::Value;

use crate::netinfo::{self, Port};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Policy {
    /// The class runs on the system default network when its port is missing.
    Fallback,
    /// The class is not run when its port is missing (audio and video never end up on the crew network unasked).
    Strict,
}

impl Policy {
    pub fn parse(text: &str) -> Option<Self> {
        match text {
            "fallback" => Some(Self::Fallback),
            "strict" => Some(Self::Strict),
            _ => None,
        }
    }
}

/// Where a traffic class goes right now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Resolved {
    pub traffic: String,
    /// The routed adapter's id; `None` when the class follows the system default.
    pub port_id: Option<String>,
    pub address: Option<String>,
    pub available: bool,
    pub reason: String,
    pub policy: Policy,
    /// The routed port is unavailable and the class uses the default route instead.
    pub fallback: bool,
}

impl Resolved {
    pub fn routed(&self) -> bool {
        self.port_id.is_some()
    }
}

/// The adapter id a saved route resolves by: `{"port", "key"}` now, the bare id before #540.
pub fn route_key(route: &Value) -> Option<String> {
    let key = match route {
        Value::Object(m) => m.get("key")?.as_str()?,
        Value::String(s) => s.as_str(),
        _ => return None,
    };
    let valid = (key.starts_with("mac:") || key.starts_with("name:"))
        && key.len() > 4
        && key.len() <= 205
        && !key.chars().any(|c| (c as u32) < 0x20);
    valid.then(|| key.to_owned())
}

/// The first address to use on a port: a routable one first, then a link-local one (a Dante network with no DHCP).
fn first_address(port: &Port) -> Option<String> {
    let addrs = port.addresses();
    addrs.iter().find(|a| netinfo::usable(a)).or_else(|| addrs.first()).cloned()
}

fn default_route(class: &str, policy: Policy, ports: &[Port], reason: &str, fallback: bool) -> Resolved {
    let ips = netinfo::lan_ips(ports);
    match ips.first() {
        None => Resolved {
            traffic: class.to_owned(),
            port_id: None,
            address: None,
            available: false,
            reason: if reason.is_empty() { "This computer has no network address.".to_owned() } else { reason.to_owned() },
            policy,
            fallback,
        },
        Some(first) => Resolved {
            traffic: class.to_owned(),
            port_id: None,
            address: Some(first.clone()),
            available: true,
            reason: reason.to_owned(),
            policy,
            fallback,
        },
    }
}

/// What to call a port in a sentence: as the machine shows it, else as it was last seen (config `network.known_ports`).
fn port_name(port: Option<&Port>, id: &str, network: &Value) -> String {
    if let Some(p) = port {
        return if p.friendly_name.is_empty() { p.name.clone() } else { p.friendly_name.clone() };
    }
    let info = network.get("known_ports").and_then(|k| k.get(id));
    let text = |key: &str| info.and_then(|i| i.get(key)).and_then(Value::as_str).filter(|s| !s.is_empty());
    text("friendly_name").or_else(|| text("name")).unwrap_or("the chosen network port").to_owned()
}

/// Where a traffic class should go. `network` is the config's `"network"` value, `label` the class's label for sentences,
/// `default_policy` its policy when none is saved.
pub fn resolve(class: &str, label: &str, default_policy: Policy, network: &Value, ports: &[Port]) -> Resolved {
    let policy = network
        .get("policy")
        .and_then(|p| p.get(class))
        .and_then(Value::as_str)
        .and_then(Policy::parse)
        .unwrap_or(default_policy);
    let route = network.get("routes").and_then(|r| r.get(class));
    let Some(id) = route.and_then(route_key) else {
        return default_route(class, policy, ports, "", false);
    };
    let port = ports.iter().find(|p| p.id == id);
    let name = port_name(port, &id, network);
    let address = port.and_then(first_address);
    let problem = match (port, address.as_deref()) {
        (None, _) => format!("{label}: {name} is not connected to this computer."),
        (Some(p), _) if p.up == Some(false) => format!("{label}: {name} is down (no cable or no link)."),
        (Some(_), None) => format!("{label}: {name} has no network address."),
        (Some(_), Some(addr)) => {
            return Resolved {
                traffic: class.to_owned(),
                port_id: Some(id),
                address: Some(addr.to_owned()),
                available: true,
                reason: String::new(),
                policy,
                fallback: false,
            };
        }
    };
    if policy == Policy::Fallback {
        let reason = format!("{problem} Using the system's default network instead.");
        let fell = default_route(class, policy, ports, &reason, true);
        return Resolved { port_id: Some(id), ..fell };
    }
    Resolved {
        traffic: class.to_owned(),
        port_id: Some(id),
        address: None,
        available: false,
        reason: problem,
        policy,
        fallback: false,
    }
}

/// The addresses to put in links for a class's pages: its routed port's address while that port is up; none while a strict
/// route's port is missing; every LAN address, default route's first, when the class follows the system default.
pub fn link_addresses(r: &Resolved, ports: &[Port]) -> Vec<String> {
    if r.routed() && !r.fallback {
        return if r.available { r.address.clone().into_iter().collect() } else { Vec::new() };
    }
    netinfo::lan_ips(ports)
}

/// The classes core knows: id -> (label, default policy). A class a Python module declares arrives with its listener.
pub fn core_classes() -> BTreeMap<&'static str, (&'static str, Policy)> {
    BTreeMap::from([
        ("viewers", ("Caption and translation pages (plain HTTP)", Policy::Fallback)),
        ("crew", ("Operator panel, sign-in and crew pages (HTTPS)", Policy::Fallback)),
        ("discovery", ("Relay's announcement to caption panels (mDNS)", Policy::Fallback)),
        ("translation", ("Translation service (OpenAI, over the internet)", Policy::Fallback)),
        ("connections", ("Serial, Modbus and MQTT connections", Policy::Fallback)),
    ])
}
