//! Session recording (`app/recorder.py`): every committed line of a start-to-stop run, on disk, in the layout a Python Relay wrote.
//!
//! ```text
//! <recordings dir>/20260920-143012/
//!     meta.json     run id, start/stop time, source language
//!     lines.jsonl   one committed caption line per row, as it happened
//! ```
//!
//! Off unless the operator turns it on. Lines arrive after the blocklist ran, so a blocked term never reaches the file. A recording
//! failure never stops an event: it is logged, and the run closes.

use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::path::{Path, PathBuf};

use serde_json::{json, Map, Value};

use crate::clock::{self, Zone};

/// A run id is a timestamp, and it is used to build a path from an HTTP request. Nothing but this shape is ever accepted.
pub fn is_run_id(id: &str) -> bool {
    let (head, tail) = match id.split_once('-') {
        Some(p) => p,
        None => return false,
    };
    let digits = |s: &str, n: usize| s.len() == n && s.bytes().all(|b| b.is_ascii_digit());
    if !digits(head, 8) {
        return false;
    }
    match tail.split_once('-') {
        None => digits(tail, 6),
        Some((time, n)) => digits(time, 6) && !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()),
    }
}

pub struct Recorder {
    root: Option<PathBuf>,
    keep_runs: usize,
    run_id: Option<String>,
    dir: Option<PathBuf>,
    file: Option<File>,
    meta: Map<String, Value>,
    count: u64,
    now: fn() -> f64,
    zone: Zone,
}

impl Default for Recorder {
    fn default() -> Self {
        Self::new(clock::wall, clock::utc)
    }
}

fn round3(x: f64) -> f64 {
    (x * 1000.0).round() / 1000.0
}

impl Recorder {
    pub fn new(now: fn() -> f64, zone: Zone) -> Self {
        Self { root: None, keep_runs: 20, run_id: None, dir: None, file: None, meta: Map::new(), count: 0, now, zone }
    }

    pub fn configure(&mut self, root: &Path, keep_runs: i64) {
        self.root = Some(root.to_owned());
        self.keep_runs = keep_runs.max(1) as usize;
    }

    pub fn active(&self) -> bool {
        self.file.is_some()
    }

    pub fn run_id(&self) -> Option<&str> {
        self.run_id.as_deref()
    }

    /// Open a new run directory. Returns its id, or `None` if it could not be created.
    pub fn start_run(&mut self, source_language: &str, targets: &[String]) -> Option<String> {
        self.finish_run();
        let root = self.root.clone()?;
        let started = (self.now)();
        let stamp = (self.zone)(started).run_stamp();
        let opened = (|| -> std::io::Result<(String, PathBuf, File)> {
            fs::create_dir_all(&root)?;
            let (mut id, mut dir) = (stamp.clone(), root.join(&stamp));
            // Two starts inside one second would otherwise land in the same directory and interleave two events in one file.
            let mut n = 1;
            while dir.exists() {
                n += 1;
                id = format!("{stamp}-{n}");
                dir = root.join(&id);
            }
            fs::create_dir_all(&dir)?;
            let file = OpenOptions::new().create(true).append(true).open(dir.join("lines.jsonl"))?;
            Ok((id, dir, file))
        })();
        let (id, dir, file) = match opened {
            Ok(v) => v,
            Err(e) => {
                tracing::error!("Could not start recording: {e}");
                return None;
            }
        };
        self.file = Some(file);
        self.run_id = Some(id.clone());
        self.dir = Some(dir.clone());
        self.count = 0;
        self.meta = match json!({
            "run_id": id, "started": started, "ended": null, "source_language": source_language, "targets": targets, "lines": 0,
        }) {
            Value::Object(m) => m,
            _ => Map::new(),
        };
        self.write_meta();
        self.prune();
        tracing::info!("Recording run {id} to {}", dir.display());
        Some(id)
    }

    /// Record one committed caption line. A write error is logged and closes the run: a full disk should cost the transcript, not the event.
    pub fn append(&mut self, stream: &str, lang: &str, seq: u64, text: &str, t0: f64, t1: f64) {
        let Some(file) = self.file.as_mut() else { return };
        let row = json!({"stream": stream, "lang": lang, "seq": seq, "t0": round3(t0), "t1": round3(t1), "text": text});
        // Flushed per line: the value of this file is that it survives the crash that loses everything else.
        let wrote = writeln!(file, "{row}").and_then(|()| file.flush());
        if let Err(e) = wrote {
            tracing::error!("Recording stopped, could not write line: {e}");
            self.file = None;
            return;
        }
        self.count += 1;
    }

    /// Close the open run and stamp its end time. Returns the run id.
    pub fn finish_run(&mut self) -> Option<String> {
        self.file.take()?;
        let id = self.run_id.clone();
        self.meta.insert("ended".into(), json!((self.now)()));
        self.meta.insert("lines".into(), json!(self.count));
        self.write_meta();
        tracing::info!("Recorded {} lines to run {}", self.count, id.as_deref().unwrap_or("?"));
        id
    }

    fn write_meta(&self) {
        let Some(dir) = &self.dir else { return };
        let text = serde_json::to_string_pretty(&self.meta).unwrap_or_default();
        if let Err(e) = fs::write(dir.join("meta.json"), text) {
            tracing::error!("Could not write run metadata: {e}");
        }
    }

    /// The directory for `run_id`, or `None` if the id is not one we would have written.
    pub fn run_dir(&self, run_id: &str) -> Option<PathBuf> {
        let root = self.root.as_ref()?;
        if !is_run_id(run_id) {
            return None;
        }
        let d = root.join(run_id);
        d.is_dir().then_some(d)
    }

    /// Every recorded run, newest first, with the live one marked.
    pub fn list_runs(&self) -> Vec<Value> {
        let Some(root) = self.root.as_ref().filter(|r| r.is_dir()) else { return Vec::new() };
        let Ok(entries) = fs::read_dir(root) else { return Vec::new() };
        let mut out = Vec::new();
        for entry in entries.flatten() {
            let d = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            if !d.is_dir() || !is_run_id(&name) {
                continue;
            }
            let Some(mut meta) = read_meta(&d, &name) else { continue };
            if self.active() && self.run_id.as_deref() == Some(name.as_str()) {
                // The open run's meta.json still says lines: 0, as it is stamped at stop: report what has been written so far.
                meta.insert("lines".into(), json!(self.count));
                meta.insert("recording".into(), json!(true));
                meta.insert("interrupted".into(), json!(false));
            }
            out.push(Value::Object(meta));
        }
        let started = |m: &Value| m.get("started").and_then(Value::as_f64).unwrap_or(0.0);
        out.sort_by(|a, b| started(b).total_cmp(&started(a)));
        out
    }

    pub fn list_run(&self, run_id: &str) -> Option<Value> {
        self.list_runs().into_iter().find(|m| m.get("run_id").and_then(Value::as_str) == Some(run_id))
    }

    /// Delete a finished run. The run being written is refused.
    pub fn delete_run(&self, run_id: &str) -> bool {
        let Some(d) = self.run_dir(run_id) else { return false };
        if self.active() && self.run_id.as_deref() == Some(run_id) {
            return false;
        }
        let _ = fs::remove_dir_all(&d);
        !d.exists()
    }

    /// Drop the oldest runs beyond `keep_runs`. Runs at start, so the cap holds before an event fills the disk.
    fn prune(&self) {
        let runs: Vec<String> = self.list_runs().iter().filter_map(|m| m.get("run_id").and_then(Value::as_str).map(str::to_owned)).collect();
        for id in runs.into_iter().skip(self.keep_runs) {
            if self.run_id.as_deref() == Some(id.as_str()) {
                continue;
            }
            tracing::info!("Pruning old recording {id}");
            self.delete_run(&id);
        }
    }
}

fn read_meta(dir: &Path, name: &str) -> Option<Map<String, Value>> {
    let text = fs::read_to_string(dir.join("meta.json")).ok()?;
    let Value::Object(mut meta) = serde_json::from_str::<Value>(&text).ok()? else { return None };
    meta.entry("run_id").or_insert_with(|| json!(name));
    meta.entry("lines").or_insert_with(|| json!(0));
    meta.insert("recording".into(), json!(false));
    if meta.get("ended").is_none_or(Value::is_null) {
        // Killed mid-run: the end was never stamped and the count in meta.json is still zero. The lines are on disk, so count them
        // and say plainly that this run was cut short.
        meta.insert("interrupted".into(), json!(true));
        meta.insert("lines".into(), json!(read_lines(dir).len()));
    }
    Some(meta)
}

/// Parse a run's lines.jsonl. A truncated final row, the shape a kill during a write leaves behind, is skipped.
pub fn read_lines(dir: &Path) -> Vec<Value> {
    let Ok(text) = fs::read_to_string(dir.join("lines.jsonl")) else { return Vec::new() };
    text.lines()
        .filter_map(|l| serde_json::from_str::<Value>(l.trim()).ok())
        .filter(|row| row.is_object() && row.get("text").is_some_and(|t| t.as_str().is_some_and(|s| !s.is_empty())))
        .collect()
}
