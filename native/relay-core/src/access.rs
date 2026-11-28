//! Who may call a module's routes (#662): the module names the permission a route needs, and the server's accounts decide.
//!
//! Core knows no account. It defines [`Authorizer`], which the server implements over its accounts (`relay-server`'s
//! `Accounts::require`), and (in `gate`) the helper that puts one permission in front of a route. A module declares the
//! permissions it adds ([`Permission`], Python's `Module.permissions`) and the server registers them with the role table.
//!
//! Until the server hands its accounts over, a gated route refuses everything (401): a module never runs open.

use axum::http::request::Parts;
use axum::response::Response;

use crate::future::BoxFuture;

/// One permission a module adds: `<area>.<action>`, the sentence the Users page shows, its group, and the lowest role that holds it
/// (`member`, `viewer`, `operator`, `admin` or `owner`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Permission {
    pub id: &'static str,
    pub label: &'static str,
    pub group: &'static str,
    pub min_role: &'static str,
}

/// Set on every request by the server's listener: the address it came from, for a module that limits a public route by client.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Peer(pub std::net::IpAddr);

pub trait Authorizer: Send + Sync + 'static {
    /// Is the caller of this request signed in (or holding an API token) with `permission`? `Err` is the response to send.
    fn require<'a>(&'a self, request: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>>;

    /// Write an audit entry for whoever made `request`, once the change succeeded. Never fails the request; the default writes nothing.
    fn note<'a>(&'a self, _request: &'a Parts, _action: &'a str, _summary: &'a str, _target: &'a str, _detail: Option<serde_json::Value>) -> BoxFuture<'a, ()> {
        Box::pin(async {})
    }

    /// Write an audit entry with no request behind it (a module acting on its own: OBS starting a stream because Start was pressed). Never fails.
    fn record<'a>(&'a self, _action: &'a str, _summary: &'a str, _target: &'a str) -> BoxFuture<'a, ()> {
        Box::pin(async {})
    }
}
