//! One record per Dante device (`service.py` `inventory`), keyed by its Dante name, from what mDNS announced.
//!
//! From the field survey on a real church network (issue #257):
//! * A device is told apart by its mDNS service types, never by an Audinate MAC prefix (Allen & Heath's stage boxes carry their own).
//! * A computer running Dante Virtual Soundcard or Dante Via announces from every network port it has, so the inventory is keyed by the Dante device
//!   name, never by an address.
//! * Ports come from the SRV records (Via's control port is 24440, not 4440), never from a constant.
//! * A transmit channel is the instance `<channel>@<device>` of `_netaudio-chan._udp`.

use std::net::Ipv4Addr;

use serde_json::{json, Value};

use crate::mdns::Found;

pub const VIA: &str = "_netaudio-via._tcp.local";
pub const ARC: &str = "_netaudio-arc._udp.local";
pub const CHAN: &str = "_netaudio-chan._udp.local";
pub const CMC: &str = "_netaudio-cmc._udp.local";
pub const DBC: &str = "_netaudio-dbc._udp.local";
/// What Relay asks for: core's list (NDI and the four Dante announcements) and Via's.
pub const SERVICES: [&str; 6] = ["_ndi._tcp.local", ARC, CMC, DBC, CHAN, VIA];
pub const DEFAULT_ARC: u16 = 4440;
pub const MAX_DEVICES: usize = 200;
pub const MAX_CHANNELS: usize = 512;

fn port_key(service: &str) -> Option<&'static str> {
    Some(match service {
        ARC => "arc",
        CMC => "cmc",
        DBC => "dbc",
        CHAN => "chan",
        VIA => "via",
        _ => return None,
    })
}

/// `<channel>@<device>` of a `_netaudio-chan` instance: `(channel, device)`. A channel name may hold an `@`, so the device is what follows the last one.
pub fn split_channel(label: &str) -> (String, String) {
    match label.rsplit_once('@') {
        Some((c, d)) if !c.is_empty() && !d.is_empty() => (c.to_owned(), d.to_owned()),
        _ => (String::new(), label.to_owned()),
    }
}

fn is_ipv4(ip: &str) -> bool {
    ip.parse::<Ipv4Addr>().is_ok()
}

fn text(v: &Value) -> &str {
    v.as_str().unwrap_or("")
}

/// `{"name", "key", "host", "ips", "ports": {"arc": 4440, ...}, "model", "manufacturer", "router_info", "arc_version", "via", "transmit": [..]}` per device.
pub fn inventory(found: &[Found]) -> Vec<Value> {
    let mut out: Vec<Value> = Vec::new();
    for f in found {
        let Some(key_of_port) = port_key(&f.service) else { continue };
        let (channel, name) = if f.service == CHAN { split_channel(f.label()) } else { (String::new(), f.label().to_owned()) };
        let key = name.trim().to_lowercase();
        if key.is_empty() {
            continue;
        }
        let at = match out.iter().position(|d| d["key"] == json!(key)) {
            Some(i) => i,
            None => {
                if out.len() >= MAX_DEVICES {
                    continue;
                }
                out.push(json!({"name": name.trim(), "key": key, "host": "", "ips": [], "ports": {}, "model": "", "manufacturer": "", "router_info": "", "arc_version": "", "via": false, "transmit": []}));
                out.len() - 1
            }
        };
        let d = &mut out[at];
        if !f.host.is_empty() && text(&d["host"]).is_empty() {
            d["host"] = json!(f.host);
        }
        for ip in f.ips.iter().filter(|i| is_ipv4(i)) {
            if !d["ips"].as_array().is_some_and(|a| a.iter().any(|x| x == ip)) {
                d["ips"].as_array_mut().expect("ips is a list").push(json!(ip));
            }
        }
        if let Some(port) = f.port.filter(|p| *p != 0) {
            let ports = d["ports"].as_object_mut().expect("ports is an object");
            ports.entry(key_of_port.to_owned()).or_insert(json!(port));
        }
        if f.service == VIA {
            d["via"] = json!(true);
        }
        if f.service == ARC {
            // The ARC announcement carries what the device says about itself.
            for (field, txt) in [("model", "model"), ("manufacturer", "mf"), ("router_info", "router_info"), ("arc_version", "arcp_vers")] {
                if text(&d[field]).is_empty() {
                    d[field] = json!(f.txt.get(txt).cloned().unwrap_or_default());
                }
            }
        }
        if !channel.is_empty() {
            let list = d["transmit"].as_array_mut().expect("transmit is a list");
            if !list.iter().any(|c| c == &json!(channel)) && list.len() < MAX_CHANNELS {
                list.push(json!(channel));
            }
        }
    }
    for d in &mut out {
        let via = d["via"] == json!(true) || text(&d["router_info"]).to_lowercase() == "dante via" || text(&d["model"]).to_lowercase().starts_with("via");
        d["via"] = json!(via);
        if let Some(list) = d["transmit"].as_array_mut() {
            list.sort_by_key(|c| text(c).to_lowercase());
        }
    }
    out.sort_by_key(|d| text(&d["name"]).to_lowercase());
    out
}
