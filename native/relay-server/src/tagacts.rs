//! What ProPresenter's slide-note tags do in Relay, for the parts of Relay the server owns (`app/modules/propresenter/tagactions.py`): start and stop
//! captioning, switch panels to the lyrics and back, change the panels' language and raise an alert. Offered as `tag.action.<name>` (core's own) while the
//! server holds these parts; the ProPresenter module finds them when a tag fires. They are Relay's own doing, so ProPresenter's view-only mode (which is about
//! writes to ProPresenter) does not stop them, and none of them calls ProPresenter.

use std::sync::Arc;

use relay_captions::engine::Captions;
use relay_core::caps::{TagAction, TAG_ACTION};
use relay_core::Host;

use crate::show::Api;

fn offer(host: &Host, name: &str, action: Box<relay_core::caps::TagActionFn>) {
    host.provide("core", &format!("{TAG_ACTION}{name}"), Arc::new(TagAction(action)));
}

/// Panels and alerts, when this server serves them.
pub fn provide_show(host: &Host, show: &Api) {
    let panels = Arc::clone(&show.panels);
    offer(host, "lyrics", Box::new(move |_| {
        let panels = Arc::clone(&panels);
        Box::pin(async move { panels.show_lyrics() })
    }));
    let panels = Arc::clone(&show.panels);
    offer(host, "captions", Box::new(move |_| {
        let panels = Arc::clone(&panels);
        Box::pin(async move { Ok(panels.show_captions()) })
    }));
    let panels = Arc::clone(&show.panels);
    offer(host, "lang", Box::new(move |value| {
        let panels = Arc::clone(&panels);
        Box::pin(async move { panels.show_language(value.as_deref().unwrap_or("")) })
    }));
    let alerts = Arc::clone(&show.alerts);
    offer(host, "alert", Box::new(move |value| {
        let alerts = Arc::clone(&alerts);
        Box::pin(async move { alerts.raise_named(value.as_deref().unwrap_or(""), "ProPresenter").map_err(|r| r.detail.clone().unwrap_or_else(|| r.sentence().to_owned())) })
    }));
}

/// The captions engine, when this server runs it.
pub fn provide_capture(host: &Host, captions: &Arc<Captions>) {
    let engine = Arc::clone(captions);
    offer(host, "capture-start", Box::new(move |_| {
        let engine = Arc::clone(&engine);
        Box::pin(async move {
            if engine.running() {
                return Ok("The stream was already running.".to_owned());
            }
            engine.start().await.map(|_| "The stream started.".to_owned()).map_err(|e| format!("The stream did not start: {}", e.message))
        })
    }));
    let engine = Arc::clone(captions);
    offer(host, "capture-stop", Box::new(move |_| {
        let engine = Arc::clone(&engine);
        Box::pin(async move {
            if !engine.running() {
                return Ok("The stream was already stopped.".to_owned());
            }
            engine.stop().await;
            Ok("The stream stopped.".to_owned())
        })
    }));
}
