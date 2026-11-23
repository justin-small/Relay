//! What can go wrong, as the fixed sentences of `app/channels.py` and `app/routing.py`. A refusal carries a key; the sentence a
//! browser sees is looked up from it, never taken from an error's text (the cause goes to the log).

use std::fmt;

#[derive(Debug)]
pub enum Error {
    /// A change that cannot be made: `key` picks the sentence.
    Refused(&'static str),
    /// The database could not be read or written.
    Unavailable(String),
}

pub type Result<T> = std::result::Result<T, Error>;

impl From<sqlx::Error> for Error {
    fn from(e: sqlx::Error) -> Self {
        Error::Unavailable(e.to_string())
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Refused(key) => f.write_str(sentence(key)),
            Error::Unavailable(_) => f.write_str("The database is down."),
        }
    }
}

impl std::error::Error for Error {}

/// The sentence for a refusal key (`channels.REFUSALS` and `routing.REFUSALS`).
pub fn sentence(key: &str) -> &'static str {
    match key {
        "no_channel" => "That channel does not exist.",
        "no_end" => "That channel end does not exist.",
        "same" => "A channel cannot be joined to itself.",
        "kind" => "Only channels of the same kind can be joined.",
        "name" => "Name the channel.",
        "device" => "The gear did not take the new name, so nothing was changed. Check that it is connected and try again.",
        "no_device" => "That channel's gear cannot be renamed from Relay.",
        "too_many" => "That is as many channels as Relay keeps.",
        "present" => "That channel is still reported; only a missing one can be forgotten.",
        "only_end" => "That is the only end of its channel; there is nothing to split.",
        // routes
        "route_same" => "A channel cannot feed itself.",
        "route_kind" => "Only audio and video can be routed, and both ends must be the same kind.",
        "no_route" => "That route does not exist.",
        "no_destination" => "That channel is not something that takes a signal.",
        "no_source" => "That channel is not something that gives a signal.",
        "no_salvo" => "That preset does not exist.",
        "salvo_name" => "Give the preset a name.",
        "name_taken" => "There is already a preset with that name.",
        "too_many_salvos" => "That is as many presets as Relay keeps.",
        _ => "That cannot be done.",
    }
}

/// The status a channel refusal answers with (`app/main.py` `_channel_change`).
pub fn channel_status(key: &str) -> u16 {
    if matches!(key, "kind" | "present" | "same" | "device" | "no_device") { 409 } else { 404 }
}

/// The status a route or preset refusal answers with (`app/main.py` `_routes_do`).
pub fn route_status(key: &str) -> u16 {
    if matches!(key, "no_channel" | "no_route" | "no_salvo") { 404 } else { 409 }
}
