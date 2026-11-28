//! Live captions in Rust (#659, part of #650): the rules of `app/redact.py`, `app/realtime.py`, `app/transcribe.py`,
//! `app/texttranslate.py` and `app/recorder.py`, with no HTTP in the crate. `relay-server` mounts the routes.
//!
//! * [`redact`] is the blocklist, a [`relay_core::hub::Redact`] for the hub's channels;
//! * [`recorder`] writes each committed line of a run to disk and [`exporting`] turns a run into transcripts, pairs and a fine-tune file;
//! * [`aimodels`] picks the provider and model the translation task runs on, and [`translate`] is the translator that uses them;
//! * [`net`] is the real HTTPS and WebSocket, through the operator's network port for translation;
//! * [`capture`] is the room's audio on its way there: the meter, the speaking gate, the resampler and the fan-out;
//! * [`engine`] is what Start and Stop mean and what every page reads, and [`demo`] is rehearsal mode;
//! * [`clock`] is the wall clock and the zone those read;
//! * [`transcribe`] lists the backends and [`session`] is one live connection to one of them;
//! * [`transcript`] is what a transcription session does with the events a backend sends: when a line is committed.

pub mod aimodels;
pub mod capture;
pub mod clock;
pub mod demo;
pub mod engine;
pub mod exporting;
pub mod net;
pub mod recorder;
pub mod redact;
pub mod session;
pub mod translate;
pub mod transcribe;
pub mod transcript;
