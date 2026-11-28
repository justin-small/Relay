//! Announce this Relay on the LAN so caption panels find it (`app/announce.py`): the service `_relay-captions._tcp` on the
//! viewer port actually in use, with TXT `v=<version>`, `name=<install name>`, `path=/`. It says so at start-up, again when the
//! address or the port changes, and says goodbye when stopped.
//!
//! The address is the "discovery" class's port when it is routed (`netroute`), else the first LAN address. Failing to announce
//! (port 5353 taken, no network) is logged and never stops Relay: an unannounced Relay still works at its address.
//! `RELAY_ANNOUNCE=0` turns it off and `RELAY_INSTALL_NAME` names the install.

use mdns_sd::{ServiceDaemon, ServiceInfo};

pub const SERVICE: &str = "_relay-captions._tcp.local.";

pub fn enabled() -> bool {
    let raw = std::env::var("RELAY_ANNOUNCE").unwrap_or_else(|_| "1".to_owned());
    !matches!(raw.trim().to_ascii_lowercase().as_str(), "0" | "false" | "no" | "off")
}

pub fn install_name() -> String {
    std::env::var("RELAY_INSTALL_NAME")
        .ok()
        .filter(|n| !n.trim().is_empty())
        .unwrap_or_else(|| gethostname::gethostname().to_string_lossy().split('.').next().unwrap_or_default().to_owned())
}

/// One DNS label's worth of an install name: printable, no dots, bounded.
pub fn clean_label(text: &str) -> String {
    let printable: String = text.chars().filter(|c| !c.is_control()).collect::<String>().replace('.', " ");
    let mut out = String::new();
    for c in printable.trim().chars() {
        if out.len() + c.len_utf8() > 63 {
            break;
        }
        out.push(c);
    }
    out.trim().to_owned()
}

/// `relay-<name>` made of letters, digits and hyphens, for the A record.
pub fn host_label(name: &str) -> String {
    let mut slug = String::new();
    let mut gap = false;
    for c in name.to_lowercase().chars() {
        if c.is_ascii_alphanumeric() {
            if gap && !slug.is_empty() {
                slug.push('-');
            }
            gap = false;
            slug.push(c);
        } else {
            gap = true;
        }
    }
    let slug: String = slug.chars().take(40).collect::<String>().trim_matches('-').to_owned();
    format!("relay-{}", if slug.is_empty() { "install" } else { &slug })
}

/// What is announced now.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Where {
    pub ip: String,
    pub port: u16,
}

pub struct Announcer {
    name: String,
    version: String,
    daemon: Option<ServiceDaemon>,
    announced: Option<(Where, String)>,
    failed: bool,
}

impl Announcer {
    pub fn new(name: &str, version: &str) -> Self {
        let name = clean_label(name);
        Self {
            name: if name.is_empty() { "Relay".to_owned() } else { name },
            version: version.to_owned(),
            daemon: None,
            announced: None,
            failed: false,
        }
    }

    pub fn current(&self) -> Option<&Where> {
        self.announced.as_ref().map(|(w, _)| w)
    }

    /// Announce at `ip`:`port`, or stop announcing when there is no address. Does nothing when nothing changed.
    pub fn update(&mut self, at: Option<Where>) {
        if self.announced.as_ref().map(|(w, _)| w) == at.as_ref() {
            return;
        }
        if let (Some(daemon), Some((_, fullname))) = (&self.daemon, self.announced.take()) {
            let _ = daemon.unregister(&fullname);
        }
        let Some(at) = at else { return };
        if self.failed {
            return;
        }
        if self.daemon.is_none() {
            match ServiceDaemon::new() {
                Ok(d) => self.daemon = Some(d),
                Err(e) => {
                    self.failed = true;
                    tracing::warn!(error = %e, "Panel announcement could not start");
                    return;
                }
            }
        }
        let host = format!("{}.local.", host_label(&self.name));
        let props = [("v", self.version.as_str()), ("name", self.name.as_str()), ("path", "/")];
        let info = match ServiceInfo::new(SERVICE, &self.name, &host, at.ip.as_str(), at.port, &props[..]) {
            Ok(info) => info,
            Err(e) => {
                tracing::warn!(error = %e, "Panel announcement could not be built");
                return;
            }
        };
        let fullname = info.get_fullname().to_owned();
        if let Some(daemon) = &self.daemon {
            match daemon.register(info) {
                Ok(()) => self.announced = Some((at, fullname)),
                Err(e) => tracing::warn!(error = %e, "Panel announcement stopped"),
            }
        }
    }

    /// Say goodbye and release the multicast socket.
    pub fn stop(&mut self) {
        self.update(None);
        if let Some(daemon) = self.daemon.take() {
            let _ = daemon.shutdown();
        }
    }
}
