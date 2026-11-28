//! Rehearsal mode (`app/demo.py`, `RELAY_DEMO=1`): canned captions, no API calls. The viewer pages are driven from a scripted
//! transcript so the operator can check fonts, projector legibility and the link on every phone in the room before the event,
//! without opening a billed session.

use std::sync::Arc;
use std::time::Duration;

use relay_core::hub::Hub;
use tokio::task::JoinHandle;

use crate::engine::{TRANSCRIPTION_STREAM, TRANSLATION_STREAM};

pub const SCRIPT: [(&str, &str); 5] = [
    ("Good afternoon everyone, and thank you for coming.", "Buenas tardes a todos, y gracias por venir."),
    ("We have a lot to cover today, so let's get started.", "Tenemos mucho que cubrir hoy, así que empecemos."),
    ("The first item on the agenda is the quarterly review.", "El primer punto de la agenda es la revisión trimestral."),
    ("Revenue came in at four point two million, up eleven percent.", "Los ingresos fueron de cuatro punto dos millones, un once por ciento más."),
    ("If you have questions, please hold them until the end.", "Si tienen preguntas, por favor guárdenlas para el final."),
];

/// Whether the environment asks for rehearsal mode. Read on every call, so the value always matches the process's environment.
pub fn demo_mode() -> bool {
    std::env::var("RELAY_DEMO").is_ok_and(|v| v == "1")
}

/// A little jitter between words, as the model's deltas have; no need for a real generator.
struct Jitter(u64);

impl Jitter {
    fn between(&mut self, lo: f64, hi: f64) -> Duration {
        self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        let unit = ((self.0 >> 33) as f64) / f64::from(1u32 << 31);
        Duration::from_secs_f64(lo + (hi - lo) * unit)
    }
}

/// Emit word-by-word deltas the way the model does, then commit the line.
async fn feed(hub: &Hub, stream: &str, lang: &str, text: &str, seed: u64, pace: f64) {
    let mut jitter = Jitter(seed);
    for (i, word) in text.split(' ').enumerate() {
        hub.publish_delta(stream, lang, &if i == 0 { word.to_owned() } else { format!(" {word}") });
        tokio::time::sleep(jitter.between(0.06 * pace, 0.16 * pace)).await;
    }
    hub.publish_final(stream, lang, Some(text));
}

/// Run the script for ever. `pace` scales every pause (1.0 is the real rehearsal; a test uses a small number).
pub async fn run(hub: Arc<Hub>, source_language: String, target: String, pace: f64) {
    tokio::time::sleep(Duration::from_secs_f64(pace)).await;
    let mut jitter = Jitter(7);
    loop {
        for (n, (english, spanish)) in SCRIPT.iter().enumerate() {
            tokio::join!(
                feed(&hub, TRANSCRIPTION_STREAM, &source_language, english, 11 + n as u64, pace),
                feed(&hub, TRANSLATION_STREAM, &target, spanish, 101 + n as u64, pace),
            );
            tokio::time::sleep(jitter.between(0.5 * pace, 1.2 * pace)).await;
        }
        tokio::time::sleep(Duration::from_secs_f64(2.0 * pace)).await;
    }
}

/// Start the script as a task; abort the handle to stop it.
pub fn start(hub: Arc<Hub>, source_language: &str, target: &str, pace: f64) -> JoinHandle<()> {
    tokio::spawn(run(hub, source_language.to_owned(), target.to_owned(), pace))
}
