//! The slide view (#138, stage 7; `ppview.py`): the live slide and the next slide as images (or as text when no image is available), with a countdown
//! overlay for the timer the operator chose and the progress of the video playing on the slide.
//!
//! Everything is read from the follower, which already holds the slide, the timers and the presentation layer's media position (read with the slide every
//! second because the stream misses changes). Images are ProPresenter's own thumbnails fetched by Relay, at a larger size than the slide grid's, through
//! the same cache. Browsers never reach ProPresenter, and a viewer can only ask for "the slide that is up now" and "the next one", never for an
//! arbitrary slide of a presentation.
//!
//! Slide images are licensed content like lyrics, so the page is behind the admin login unless the operator turns `view.public` on (off by default),
//! which opens the same page without a login on the viewer port at /stage.

use std::sync::Arc;

use serde_json::{json, Value};

use crate::control::{rev_of, Control, ControlError, LIVE_QUALITY};
use crate::feeds::{timer_level, RUNNING_STATES};
use crate::follower::{now, Follower};
use crate::settings::configured;

/// A reading older than this is not carried forward: Relay would be guessing.
pub const CARRY_MAX_S: f64 = 5.0;

fn round1(v: f64) -> f64 {
    (v * 10.0).round() / 10.0
}

pub struct View {
    f: Arc<Follower>,
    ctl: Arc<Control>,
}

/// The slide a `which` names: `{uuid, index, v}`. `v` changes with the slide, so a changed slide is a new image URL.
pub struct Target {
    pub uuid: String,
    pub index: usize,
    pub v: String,
}

impl View {
    pub fn new(f: Arc<Follower>, ctl: Arc<Control>) -> Arc<Self> {
        Arc::new(Self { f, ctl })
    }

    /// The slide `which` ("current" or "next") names, or `None` when there is no such slide or no way to draw it.
    pub fn target(&self, which: &str) -> Option<Target> {
        let (pres, idx) = (self.f.presentation(), self.f.slide_index());
        if !matches!(which, "current" | "next") {
            return None;
        }
        let (pres, idx) = (pres.filter(|_| self.f.is_up())?, idx?);
        let n = usize::try_from(idx + i64::from(which == "next")).ok()?;
        let cues = &pres.cues;
        cues.get(n)?;
        let texts: Vec<&str> = cues.iter().map(|c| c["text"].as_str().unwrap_or("")).collect();
        Some(Target { uuid: pres.uuid.clone(), index: n, v: rev_of(&json!([pres.uuid, n, texts])) })
    }

    fn timer(&self, at: f64) -> Option<Value> {
        let s = self.f.settings();
        let want = s["view"]["timer"].as_str().filter(|w| !w.is_empty())?;
        if !self.f.is_up() {
            return None;
        }
        let t = self.f.timers().into_iter().find(|t| t["id"] == want)?;
        Some(timer_row(&t, self.f.timers_read_at(), at, &s["timers"]))
    }

    fn media(&self, at: f64) -> Option<Value> {
        let m = self.f.media().filter(|_| self.f.is_up())?;
        media_row(&m, at)
    }

    /// What the page draws. `base` is the URL prefix of the image route (admin or public).
    pub fn snapshot(&self, base: &str) -> Value {
        self.snapshot_at(base, now())
    }

    /// [`View::snapshot`] as it reads at `at` (epoch seconds).
    pub fn snapshot_at(&self, base: &str, at: f64) -> Value {
        let s = self.f.settings();
        let mut out = json!({
            "configured": configured(&s), "connected": self.f.is_up(), "live": false, "view_only": s["view_only"] == Value::Bool(true), "server_time": at,
            "title": "", "part": "", "part_color": "", "slide_number": null, "slide_count": 0,
            "current": {"text": "", "image": ""}, "next": {"text": "", "image": ""}, "timer": null, "media": null,
        });
        let Some(pres) = self.f.presentation().filter(|_| self.f.is_up()) else { return out };
        let item = self.f.item().unwrap_or_else(|| json!({}));
        out["live"] = json!(true);
        out["title"] = if item["title"].as_str().is_some_and(|t| !t.is_empty()) { item["title"].clone() } else { json!(pres.name) };
        out["part"] = item["part"].as_str().map_or(json!(""), |p| json!(p));
        out["part_color"] = item["part_color"].as_str().map_or(json!(""), |p| json!(p));
        out["slide_number"] = item["slide_number"].clone();
        out["slide_count"] = item["slide_count"].as_i64().map_or(json!(0), |n| json!(n));
        out["timer"] = self.timer(at).unwrap_or(Value::Null);
        out["media"] = self.media(at).unwrap_or(Value::Null);
        for (which, side) in [("current", self.f.current()), ("next", self.f.next_side())] {
            let image = self.target(which).map_or(String::new(), |t| format!("{base}/image/{which}?v={}", t.v));
            out[which] = json!({"text": side.text, "image": image});
        }
        out
    }

    /// The picture of the slide that is `which` right now, if `v` is still its marker. `Ok(None)` when the slide has moved on (the page asks again with
    /// the new marker) or there is nothing to draw.
    pub async fn image(&self, which: &str, v: &str) -> Result<Option<(String, Vec<u8>)>, ControlError> {
        let Some(t) = self.target(which).filter(|t| t.v == v) else { return Ok(None) };
        self.ctl.thumbnail(&t.uuid, &json!(t.index), &t.v, LIVE_QUALITY).await.map(Some)
    }
}

/// A chosen timer as the overlay draws it at `at`: a running one is carried forward from when it was `read_at`, by at most [`CARRY_MAX_S`].
pub fn timer_row(t: &Value, read_at: f64, at: f64, cfg: &Value) -> Value {
    let (mut value, state) = (t["value"].as_f64().unwrap_or(0.0), t["state"].as_str().unwrap_or("stopped").to_owned());
    let dir = t["dir"].as_str().unwrap_or("down");
    if RUNNING_STATES.contains(&state.as_str()) && read_at > 0.0 {
        let dt = (at - read_at).clamp(0.0, CARRY_MAX_S);
        value += if dir == "down" { -dt } else { dt };
    }
    let value = round1(value);
    json!({"id": t["id"], "name": t["name"], "state": state, "dir": dir, "value": value,
           "level": timer_level(value, &state, dir, cfg["amber_s"].as_i64().unwrap_or(0), cfg["red_s"].as_i64().unwrap_or(0))})
}

/// The progress of the video on the slide at `at`; `None` for no media, or one with no length (a still).
pub fn media_row(m: &Value, at: f64) -> Option<Value> {
    let duration = m["duration"].as_f64().filter(|d| *d > 0.0)?;
    let mut t = m["time"].as_f64().unwrap_or(0.0);
    if m["playing"] == true {
        t += (at - m["read_at"].as_f64().unwrap_or(at)).clamp(0.0, CARRY_MAX_S);
    }
    let t = t.clamp(0.0, duration);
    Some(json!({"name": m["name"], "playing": m["playing"], "time": round1(t), "duration": round1(duration), "remaining": round1(duration - t),
                "percent": round1(100.0 * t / duration)}))
}
