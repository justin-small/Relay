//! Five fixed roles on top of named permissions (app/users.py). Code asks `user.can("settings.manage")`, never "is the role
//! at least admin". [`role_permissions`] is the one place that decides what a role holds.

use std::collections::BTreeSet;
use std::sync::{OnceLock, RwLock};

/// Member < viewer < operator < admin < owner; the rank is the index plus one.
pub const ROLES: [&str; 5] = ["member", "viewer", "operator", "admin", "owner"];

pub fn rank(role: &str) -> usize {
    ROLES.iter().position(|r| *r == role).map_or(0, |i| i + 1)
}

#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct Permission {
    pub id: String,
    pub label: String,
    pub group: String,
    pub min_role: String,
}

const fn p(id: &'static str, label: &'static str, group: &'static str, min_role: &'static str) -> (&'static str, &'static str, &'static str, &'static str) {
    (id, label, group, min_role)
}

/// Core's catalogue, in Python's order (the Users page lists them so).
const CORE: &[(&str, &str, &str, &str)] = &[
    p("admin.open", "Open the operator panel", "Operator panel", "viewer"),
    p("session.view", "See session health and status", "Session", "viewer"),
    p("session.control", "Start and stop captions", "Session", "operator"),
    p("audio.manage", "Change audio device, input channel and source language", "Audio", "admin"),
    p("targets.manage", "Change target languages", "Session", "operator"),
    p("panels.view", "See panels", "Panels", "viewer"),
    p("panels.manage", "Change panels, firmware, flash, reboot, forget", "Panels", "admin"),
    p("alerts.view", "See alert setup", "Alerts", "viewer"),
    p("alerts.manage", "Change alert setup", "Alerts", "admin"),
    p("alerts.history", "Read who sent which alerts, and when", "Alerts", "admin"),
    p("alerts.send", "Send alerts from /alert when it needs a sign-in", "Alerts", "member"),
    p("overlays.view", "See overlays", "Overlays", "viewer"),
    p("overlays.manage", "Create, change and delete overlays", "Overlays", "operator"),
    p("boards.view", "Open boards that are not public", "Boards", "member"),
    p("boards.manage", "Create, lay out, change and delete boards", "Boards", "admin"),
    p("recordings.view", "List and download recordings", "Recordings", "operator"),
    p("recordings.manage", "Delete recordings and change recording settings", "Recordings", "admin"),
    p("schedules.view", "See schedules", "Schedules", "viewer"),
    p("schedules.manage", "Change schedules", "Schedules", "admin"),
    p("screens.view", "See screens and places", "Screens", "viewer"),
    p("screens.manage", "Name, place and set what screens show, and flash them", "Screens", "admin"),
    p("blocklist.manage", "Edit blocked words", "Settings", "admin"),
    p("settings.view", "Read the configuration (secrets hidden)", "Settings", "viewer"),
    p("settings.manage", "Change appearance, tuning and realtime settings", "Settings", "admin"),
    p("credentials.manage", "Change the OpenAI API key and the backup passphrase", "Settings", "owner"),
    p("modules.view", "See which modules exist and their health", "Modules", "viewer"),
    p("modules.manage", "Switch modules on and off, run a module's test", "Modules", "admin"),
    p("network.view", "See this computer's network ports and which traffic uses each", "Network", "viewer"),
    p("network.manage", "Choose which network port each kind of traffic uses", "Network", "admin"),
    p("connections.view", "See the serial, Modbus and MQTT connections", "Connections", "admin"),
    p("connections.manage", "Add, change, test and remove serial, Modbus and MQTT connections", "Connections", "admin"),
    p("helpers.view", "See the helper programs paired with Relay", "Helpers", "admin"),
    p("helpers.manage", "Make pairing codes for, rename and remove helper programs", "Helpers", "admin"),
    p("users.view", "See users and trusted devices", "Users", "admin"),
    p("users.manage", "Create and change users, reset passwords, trust devices", "Users", "admin"),
    p("tokens.own", "Create API tokens for your own scripts", "Users", "admin"),
    p("tokens.manage", "See and revoke everyone's API tokens", "Users", "owner"),
    p("audit.view", "Read the audit log of sign-ins and changes", "Users", "admin"),
];

/// What an API token never does, whatever its user's role: manage accounts, passwords, devices, tokens or the OpenAI key, or
/// read the audit log. A leaked token can use Relay but cannot take over an account or mint more tokens.
pub const TOKEN_BARRED: [&str; 8] = [
    "connections.manage", "helpers.manage", "users.view", "users.manage", "credentials.manage", "tokens.own", "tokens.manage",
    "audit.view",
];

fn catalogue_lock() -> &'static RwLock<Vec<Permission>> {
    static CAT: OnceLock<RwLock<Vec<Permission>>> = OnceLock::new();
    CAT.get_or_init(|| {
        RwLock::new(CORE.iter().map(|(id, label, group, min)| Permission {
            id: (*id).into(), label: (*label).into(), group: (*group).into(), min_role: (*min).into(),
        }).collect())
    })
}

fn valid_id(id: &str) -> bool {
    let mut parts = id.split('.');
    let ok = |s: &str| {
        let mut c = s.chars();
        matches!(c.next(), Some('a'..='z')) && c.all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || c == '_')
    };
    matches!((parts.next(), parts.next(), parts.next()), (Some(a), Some(b), None) if ok(a) && ok(b))
}

/// Add a module's permissions. A bad id or role, or an id already registered differently, registers none of them and returns
/// the reason; registering an identical permission again does nothing.
pub fn register(perms: &[Permission]) -> Result<(), String> {
    let mut cat = catalogue_lock().write().unwrap_or_else(|e| e.into_inner());
    let mut seen: Vec<&Permission> = Vec::new();
    for perm in perms {
        if !valid_id(&perm.id) {
            return Err(format!("bad permission id: {}", perm.id));
        }
        if rank(&perm.min_role) == 0 {
            return Err(format!("permission {}: unknown role {}", perm.id, perm.min_role));
        }
        if seen.iter().any(|s| s.id == perm.id && *s != perm) {
            return Err(format!("permission {} is declared twice", perm.id));
        }
        if cat.iter().any(|c| c.id == perm.id && c != perm) {
            return Err(format!("permission {} is already registered", perm.id));
        }
        seen.push(perm);
    }
    for perm in perms {
        if !cat.iter().any(|c| c.id == perm.id) {
            cat.push(perm.clone());
        }
    }
    Ok(())
}

pub fn catalogue() -> Vec<Permission> {
    catalogue_lock().read().unwrap_or_else(|e| e.into_inner()).clone()
}

/// The permissions a role grants.
pub fn role_permissions(role: &str) -> BTreeSet<String> {
    let r = rank(role);
    catalogue().into_iter().filter(|p| r > 0 && rank(&p.min_role) <= r).map(|p| p.id).collect()
}

/// A signed-in person, or one of their API tokens (`token_id` is not 0).
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct User {
    pub id: i64,
    pub username: String,
    pub name: String,
    pub role: String,
    pub disabled: bool,
    pub must_change_password: bool,
    pub pin_set: bool,
    #[serde(skip)]
    pub token_id: i64,
}

impl User {
    pub fn is_token(&self) -> bool {
        self.token_id != 0
    }

    pub fn can(&self, perm: &str) -> bool {
        if self.is_token() && TOKEN_BARRED.contains(&perm) {
            return false;
        }
        role_permissions(&self.role).contains(perm)
    }

    pub fn permissions(&self) -> BTreeSet<String> {
        let mut all = role_permissions(&self.role);
        if self.is_token() {
            for b in TOKEN_BARRED {
                all.remove(b);
            }
        }
        all
    }
}
