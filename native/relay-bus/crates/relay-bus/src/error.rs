//! One error type for the non-realtime API. The realtime code never returns errors; it counts glitches instead.

use std::fmt;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Error {
    /// The configuration is not valid (a lane number out of range, a duplicate output id, ...).
    Config(String),
    /// No input with that id is configured (or it is not an external input).
    UnknownInput(String),
    /// No output with that id is configured (or it is not the right kind for the call).
    UnknownOutput(String),
    /// No tap with that id.
    UnknownTap(u64),
    /// No meter probe with that id.
    UnknownProbe(u64),
    /// The thing exists but is not running right now (device closed, engine disabled).
    NotRunning(String),
    /// A command queue to the realtime thread is full; try again.
    Busy,
    /// The audio backend refused.
    Backend(String),
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Error::Config(s) => write!(f, "invalid configuration: {s}"),
            Error::UnknownInput(s) => write!(f, "no external input {s:?}"),
            Error::UnknownOutput(s) => write!(f, "no output {s:?}"),
            Error::UnknownTap(id) => write!(f, "no tap {id}"),
            Error::UnknownProbe(id) => write!(f, "no meter probe {id}"),
            Error::NotRunning(s) => write!(f, "{s} is not running"),
            Error::Busy => write!(f, "the realtime thread is busy; try again"),
            Error::Backend(s) => write!(f, "audio backend: {s}"),
        }
    }
}

impl std::error::Error for Error {}
