//! What ProPresenter's slide-note tags (slidetags) do in Relay: start and stop captioning, switch panels to the lyrics and back, change the panels'
//! language, raise an alert, fire a lighting cue, switch OBS, tell REAPER, and drive signage screens. Set as the tag actions by the module's `start`.
//!
//! Relay's own doings (capture, panels, alerts) are provided by core as `tag.action.<name>` and a module's by that module (`light`, `obs`,
//! `reaper`); this finds them when a tag fires, so it does not matter which came on first, and a module that is off answers with a sentence saying so.
//! None of them calls ProPresenter, so view-only (which is about writes to ProPresenter) does not stop them.

use std::sync::Arc;

use relay_core::caps::{TagAction, TAG_ACTION};
use relay_core::Host;

use crate::signagetags::{self, SignageTags};
use crate::slidetags::{Action, Tags};

/// What is said when the thing that would do it is not there.
fn missing(name: &str) -> &'static str {
    match name {
        "light" => "Lighting is off.",
        "obs" => "The OBS module is off.",
        "reaper" => "The REAPER module is off.",
        _ => "Relay has nothing wired to that action.",
    }
}

/// The sentence a tag with no usable value says.
fn needs(name: &str) -> Option<&'static str> {
    match name {
        "light" => Some("Name the cue after light=, like [relay:light=Walk-in]."),
        "obs" => Some("Name the scene after obs=, like [relay:obs=Message]."),
        "reaper" => Some("Say what REAPER should do, like [relay:reaper=record], [relay:reaper=stop] or [relay:reaper=marker=Sermon]."),
        _ => None,
    }
}

fn core_action(host: Host, name: &'static str) -> Action {
    Arc::new(move |value: Option<String>| {
        let host = host.clone();
        Box::pin(async move {
            let value = value.map(|v| v.trim().to_owned());
            if let Some(why) = needs(name) {
                let v = value.clone().unwrap_or_default();
                let ok = match name {
                    "reaper" => matches!(v.split('=').next().unwrap_or("").trim().to_lowercase().as_str(), "record" | "stop" | "marker" | "action"),
                    _ => !v.is_empty(),
                };
                if !ok {
                    return Err(why.to_owned());
                }
            }
            let Some(a) = host.using::<TagAction>(&format!("{TAG_ACTION}{name}")) else { return Err(missing(name).to_owned()) };
            (a.0)(value).await
        })
    })
}

/// Every action a tag may name, onto `tags`.
pub fn install(tags: &Tags, host: &Host, signage: &Arc<SignageTags>) {
    for name in ["capture-start", "capture-stop", "lyrics", "captions", "lang", "alert", "light", "obs", "reaper"] {
        tags.set_action(name, core_action(host.clone(), name));
    }
    for (name, _) in signagetags::names() {
        let s = Arc::clone(signage);
        let n = name.clone();
        tags.set_action(&name, Arc::new(move |value: Option<String>| {
            let (s, n) = (Arc::clone(&s), n.clone());
            Box::pin(async move { s.run(&n, value.as_deref()).await })
        }));
    }
}
