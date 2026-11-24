//! Signage takeovers (`takeover.py`, #207): what is put over a screen's playlist, and Relay's alerts as a banner.
//!
//! Three kinds, highest first: a **push** (one image or video from the library, sent to a screen, a group or all screens, to play once,
//! for a set time, or until cleared), a **live** view (the captions, the transcription or the service rundown) and the **banner**
//! (Relay's alerts over whatever is playing; the playlist is not interrupted). Push and live are rows of `signage_takeovers`, one per
//! target; a screen takes the most specific one that applies (its own, then its group's, then all screens), a push before a live view.

use relay_core::screens::{AlertsCap, ALERTS};
use serde_json::{json, Value};

use crate::store::{gone, invalid, now_iso, row_json, Result, Store};
use crate::timewin_iso;

pub const MODES: [&str; 3] = ["once", "seconds", "cleared"];
pub const ALERT_MODES: [&str; 3] = ["all", "groups", "none"];
pub const SECONDS_MIN: i64 = 1;
pub const SECONDS_MAX: i64 = 86400;
/// How long a picture shown "once" stays up.
pub const ONCE_PICTURE_S: i64 = 10;
/// A "once" push no screen reports done still ends after this long.
pub const ONCE_FAILSAFE_S: i64 = 3600;

/// `(label, path on the viewer port)` of a live view. `/screen` is the kiosk view: no chrome, nothing to press.
pub fn view(id: &str) -> Option<(&'static str, &'static str)> {
    Some(match id {
        "captions" => ("Live captions (translation)", "/screen?stream=translation"),
        "transcription" => ("Live captions (original language)", "/screen?stream=transcription"),
        "service" => ("Live service rundown", "/screen?rundown=full"),
        _ => return None,
    })
}

pub const VIEWS: [&str; 3] = ["captions", "transcription", "service"];

impl Store {
    /// The takeovers that have not run out, each with a `label`.
    pub async fn takeover_rows(&self) -> Result<Vec<Value>> {
        let mut out: Vec<Value> = sqlx::query("select * from signage_takeovers where expires_at = '' or expires_at > ? order by id").bind(now_iso()).fetch_all(&self.pool).await?.iter().map(row_json).collect();
        for r in &mut out {
            r["label"] = json!(self.takeover_label(r).await?);
        }
        Ok(out)
    }

    async fn takeover_label(&self, r: &Value) -> Result<String> {
        if r["kind"] == "live" {
            return Ok(view(r["view"].as_str().unwrap_or("")).map_or("Live view", |v| v.0).to_owned());
        }
        let m = self.media_get(r["media_id"].as_str().unwrap_or("")).await?;
        Ok(m.and_then(|m| m["name"].as_str().filter(|n| !n.is_empty()).map(str::to_owned)).unwrap_or_else(|| "Deleted file".to_owned()))
    }

    async fn takeover_target(&self, target: &str, target_id: i64) -> Result<i64> {
        let target_id = if target == "all" { 0 } else { target_id };
        let sql = match target {
            "screen" => Some("select id from signage_screens where id = ?"),
            "group" => Some("select id from signage_groups where id = ?"),
            _ => None,
        };
        if let Some(sql) = sql {
            if sqlx::query(sql).bind(target_id).fetch_optional(&self.pool).await?.is_none() {
                return gone(if target == "screen" { "That screen does not exist." } else { "That group does not exist." });
            }
        }
        Ok(target_id)
    }

    async fn takeover_save(&self, kind: &str, target: &str, target_id: i64, media_id: &str, view_id: &str, mode: &str, seconds: i64) -> Result<Value> {
        if !["screen", "group", "all"].contains(&target) {
            return invalid("Choose a screen, a group or all screens.");
        }
        let target_id = self.takeover_target(target, target_id).await?;
        let now = crate::store::epoch_secs();
        let (mut expires, mut seconds, mut mode, mut media_id) = (String::new(), seconds, mode.to_owned(), media_id.to_owned());
        if kind == "push" {
            if !MODES.contains(&mode.as_str()) {
                return invalid("A push plays once, for a set time, or until it is cleared.");
            }
            if mode == "seconds" {
                if !(SECONDS_MIN..=SECONDS_MAX).contains(&seconds) {
                    return invalid("Choose a time from 1 second to 24 hours.");
                }
                expires = timewin_iso(now + seconds);
            } else if mode == "once" {
                seconds = 0;
                expires = timewin_iso(now + ONCE_FAILSAFE_S);
            } else {
                seconds = 0;
            }
            if self.media_get(&media_id).await?.is_none() {
                return gone("That file is not in the media library.");
            }
        } else {
            if view(view_id).is_none() {
                return invalid("Choose live captions or the service view.");
            }
            (mode, seconds, media_id) = ("cleared".to_owned(), 0, String::new());
        }
        let mut tx = self.pool.begin().await?;
        sqlx::query("delete from signage_takeovers where target = ? and target_id = ?").bind(target).bind(target_id).execute(&mut *tx).await?;
        sqlx::query("insert into signage_takeovers (kind, target, target_id, media_id, view, mode, seconds, set_at, expires_at) values (?, ?, ?, ?, ?, ?, ?, ?, ?)")
            .bind(kind)
            .bind(target)
            .bind(target_id)
            .bind(&media_id)
            .bind(if kind == "live" { view_id } else { "" })
            .bind(&mode)
            .bind(seconds)
            .bind(now_iso())
            .bind(&expires)
            .execute(&mut *tx)
            .await?;
        let row = sqlx::query("select * from signage_takeovers where target = ? and target_id = ?").bind(target).bind(target_id).fetch_one(&mut *tx).await?;
        tx.commit().await?;
        self.live.bump();
        Ok(row_json(&row))
    }

    /// Send one image or video to a screen, a group or all screens.
    pub async fn push_set(&self, target: &str, target_id: i64, media_id: &str, mode: &str, seconds: i64) -> Result<Value> {
        self.takeover_save("push", target, target_id, media_id, "", mode, seconds).await
    }

    /// Put live captions or the service view on a screen, a group or all screens until cleared.
    pub async fn live_view_set(&self, target: &str, target_id: i64, view_id: &str) -> Result<Value> {
        self.takeover_save("live", target, target_id, "", view_id, "cleared", 0).await
    }

    pub async fn takeover_clear(&self, target: &str, target_id: i64) -> Result<()> {
        let target_id = if target == "all" { 0 } else { target_id };
        if sqlx::query("delete from signage_takeovers where target = ? and target_id = ?").bind(target).bind(target_id).execute(&self.pool).await?.rows_affected() == 0 {
            return gone("Nothing is taken over there.");
        }
        self.live.bump();
        Ok(())
    }

    /// The "return to playlist" button: every push and live view ends. Returns how many were cleared.
    pub async fn takeover_clear_all(&self) -> Result<u64> {
        let n = sqlx::query("delete from signage_takeovers").execute(&self.pool).await?.rows_affected();
        self.live.done_clear();
        self.live.bump();
        Ok(n)
    }

    /// Housekeeping: drop what ran out and what points at a screen or group that is gone.
    pub async fn takeover_sweep(&self) -> Result<()> {
        let mut n = sqlx::query("delete from signage_takeovers where expires_at != '' and expires_at <= ?").bind(now_iso()).execute(&self.pool).await?.rows_affected();
        n += sqlx::query("delete from signage_takeovers where (target = 'screen' and target_id not in (select id from signage_screens)) or (target = 'group' and target_id not in (select id from signage_groups))")
            .execute(&self.pool)
            .await?
            .rows_affected();
        if n > 0 {
            self.live.bump();
        }
        Ok(())
    }

    /// How specifically takeover `r` names `screen` (0 its own, 1 its group, 2 all), `None` when it does not.
    fn applies(r: &Value, screen: &Value) -> Option<i64> {
        match r["target"].as_str()? {
            "screen" => (r["target_id"] == screen["id"]).then_some(0),
            "group" => (!screen["group_id"].is_null() && r["target_id"] == screen["group_id"]).then_some(1),
            _ => Some(2),
        }
    }

    /// The takeover row `screen` shows now, or `None`. A push beats a live view; then the most specific target, then the newest. A
    /// "once" push the screen has already played is skipped.
    pub fn takeover_pick<'a>(&self, screen: &Value, taken: &'a [Value]) -> Option<&'a Value> {
        let done = self.live.done_id(screen["slug"].as_str().unwrap_or(""));
        taken
            .iter()
            .filter_map(|r| {
                let rank = Self::applies(r, screen)?;
                if r["kind"] == "push" && r["mode"] == "once" && r["id"].as_i64() == done {
                    return None;
                }
                Some(((i64::from(r["kind"] != "push"), rank, -r["id"].as_i64().unwrap_or(0)), r))
            })
            .min_by_key(|(k, _)| *k)
            .map(|(_, r)| r)
    }

    /// What the player gets for a takeover: a push to play or a live view to frame.
    pub async fn takeover_payload(&self, screen: &Value, taken: &[Value]) -> Result<Option<Value>> {
        let Some(r) = self.takeover_pick(screen, taken) else { return Ok(None) };
        let base = json!({"id": r["id"], "kind": r["kind"], "mode": r["mode"], "until": r["expires_at"], "from": r["target"]});
        let mut out = base.as_object().cloned().unwrap_or_default();
        if r["kind"] == "live" {
            let (label, path) = view(r["view"].as_str().unwrap_or("")).unwrap_or(("Live view", ""));
            out.extend([("view".to_owned(), r["view"].clone()), ("label".to_owned(), json!(label)), ("path".to_owned(), json!(path)), ("port".to_owned(), json!(self.host.viewer_port()))]);
            return Ok(Some(Value::Object(out)));
        }
        let Some(m) = self.media_get(r["media_id"].as_str().unwrap_or("")).await? else { return Ok(None) };
        let Some(target) = self.source_resolve("media", r["media_id"].as_str().unwrap_or("")).await? else { return Ok(None) };
        let seconds = match r["seconds"].as_i64().filter(|s| *s != 0) {
            Some(s) => s,
            None if r["mode"] == "once" => ONCE_PICTURE_S,
            None => 0,
        };
        out.extend([
            ("label".to_owned(), m["name"].clone()),
            ("seconds".to_owned(), json!(seconds)),
            ("item".to_owned(), json!({"id": -r["id"].as_i64().unwrap_or(0), "label": m["name"], "type": target["type"], "url": target["url"], "fit": "fit", "transition": "cut", "duration_s": 0})),
        ]);
        Ok(Some(Value::Object(out)))
    }

    // ------------------------------------------------------------------ alerts as a banner
    fn active_alerts(&self) -> Vec<Value> {
        self.host.using::<AlertsCap>(ALERTS).map(|a| (a.0)()).unwrap_or_default()
    }

    pub fn alert_wanted(screen: &Value, settings: &Value) -> bool {
        match settings["alert_screens"].as_str().unwrap_or("all") {
            "all" => true,
            "groups" => screen["group_id"].as_i64().is_some_and(|g| settings["alert_groups"].as_array().is_some_and(|a| a.iter().any(|x| x.as_i64() == Some(g)))),
            _ => false,
        }
    }

    /// The alert to show over `screen`, or `None`: `{"text", "count"}` for the first alert flashing now.
    pub fn banner(&self, screen: &Value) -> Option<Value> {
        if !Self::alert_wanted(screen, &self.settings()) {
            return None;
        }
        let flashing = self.active_alerts();
        let first = flashing.first()?;
        let mut text = first["to"].as_str().filter(|t| !t.is_empty()).unwrap_or("Alert").to_owned();
        if let Some(from) = first["from"].as_str().filter(|f| !f.is_empty()) {
            text.push_str(&format!(" (from {from})"));
        }
        Some(json!({"text": text, "count": flashing.len()}))
    }

    /// Changes whenever the set of alerts flashing now does.
    pub fn alert_sig(&self) -> Vec<String> {
        self.active_alerts().iter().filter_map(|a| a["id"].as_str().map(str::to_owned)).collect()
    }
}
