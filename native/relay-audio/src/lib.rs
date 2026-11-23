//! Relay's audio bus, routes and meters in Rust (#657, part of #650). The Rust form of `app/audiobus.py`, `app/routing.py`,
//! `app/channels.py` and `app/metering.py`, on `relay-bus` directly and on the same `relay.db` and config.json.
//!
//! * [`normalize`] is the saved bus setting's rules; [`channels`] and [`routing`] are the channel list, the routes and the presets;
//! * [`bus`] runs the engine and keeps the crosspoint matrix cached in memory, written through to the routes;
//! * [`metering`] opens a probe per meter and reads the numbers.
//!
//! The library knows no HTTP and no integration: `relay-server` mounts the routes, and a module's gear is reached through the
//! [`routing::Provider`] and [`channels::Gear`] traits.

pub mod bus;
pub mod channels;
pub mod error;
pub mod metering;
pub mod normalize;
pub mod routing;

pub use bus::{AudioBus, Backing, Store};
pub use channels::Channels;
pub use error::{Error, Result};
pub use metering::{MeterStore, Metering};
pub use routing::Routing;

/// The database handle the audio code reads and writes (`relay-auth` opens it).
pub use sqlx::SqlitePool as Pool;

/// Whether `relay.db` has the tables of the Python migrations 0014 (channels) and 0015 (routes). A file this server made itself, or
/// one older than those, does not: the Python Relay brings it up to date.
pub async fn check_tables(pool: &Pool) -> std::result::Result<(), String> {
    for (table, probe) in [
        ("channels", "select 1 from channels limit 1"),
        ("channel_ends", "select 1 from channel_ends limit 1"),
        ("routes", "select 1 from routes limit 1"),
        ("salvos", "select 1 from salvos limit 1"),
        ("salvo_routes", "select 1 from salvo_routes limit 1"),
    ] {
        if let Err(e) = sqlx::query(probe).fetch_optional(pool).await {
            return Err(format!("relay.db has no {table} table yet ({e}); start the Python Relay once to update it"));
        }
    }
    Ok(())
}
