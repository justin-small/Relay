//! Server-side config (`app/config.py`, ported by #656): load, validate, atomic save and hot reload of config.json, with the Python
//! server's defaults and clamping, and every secret sealed on disk and plain only in memory.
//!
//! What this layer normalises is what `config.py` itself does: the defaults merged under the file, the ports, the languages, the
//! font and history clamps, the realtime and recording blocks. A block another Python module cleans (`audio_bus`, `schedules`,
//! `overlays`, `boards`, `panels`, `alerts`, `network`, `network_map`, `ai`, `metering`, `mixreport`, a module's own settings) is
//! kept exactly as stored until the issue that ports that module (#657, #660, #661) adds its `clean`; the file's key order is kept,
//! and a key nothing here knows is written back where it was.
//!
//! A secret is `enc:v1:...` on disk and plain text in [`Settings::get`]. A sealed value this computer cannot open (its key was lost)
//! is kept as stored until a new value replaces it, so a locked keyring on one start does not wipe it on the next save.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::time::SystemTime;

use relay_core::languages;
use relay_core::{AppError, ErrorCode};
use serde_json::{json, Map, Value};

use crate::private;
use crate::secretbox::{self, SecretBox};

/// Secrets core keeps sealed in config.json. A module adds its own with [`Settings::add_secret`].
pub const SECRET_KEYS: [&str; 4] = ["openai_api_key", "vercel_api_key", "admin_token", "backup_key"];

pub const LANES: i64 = 32;

type Object = Map<String, Value>;

/// The Python `DEFAULTS` that `config.py` owns. The blocks other modules own start as their own empty shapes.
pub fn defaults() -> Value {
    json!({
        "openai_api_key": "",
        "vercel_api_key": "",
        "ai": {},
        "admin_token": "",
        "backup_key": "",
        "audio_lane": 1,
        "audio_bus": {"lanes": [], "outputs": [], "matrix": []},
        "mixreport": {"enabled": false, "left": 1, "right": 2, "silence_dbfs": -50.0, "target_lufs": -16.0,
                      "tolerance_lu": 2.0, "peak_limit_dbfs": -1.0, "rehearsal": false},
        "metering": {"meters": []},
        "source_language": "ENGLISH",
        "targets": {"SPANISH": {"enabled": true}},
        "default_font_px": 40,
        "speaking_indicator_vad": true,
        "blocklist_file": "blocklist.txt",
        "history_lines": 40,
        "stream_captions": true,
        "recording": {"enabled": false, "dir": "recordings", "keep_runs": 20},
        "timezone": "",
        "schedules": [],
        "overlays": [],
        "boards": [],
        "panels": {},
        "alerts": {"auto_stop_s": 120, "buttons": []},
        "modules": {},
        "network": {"routes": {}, "policy": {}},
        "network_map": {"names": {}, "notes": {}, "places": []},
        "host": "0.0.0.0",
        "admin_host": "0.0.0.0",
        "port": 80,
        "admin_port": 443,
        "admin_fqdn": "",
        "realtime": {
            "transcribe_model": "gpt-live-transcribe",
            "backend": "scribe",
            "scribe_model": "elevenlabs/scribe-v2-realtime",
            "text_reasoning_effort": "none",
            "segment_idle_s": 1.0,
            "segment_max_idle_s": 10.0,
            "gate_silence": true,
            "noise_reduction": null,
        },
    })
}

/// Python's `_merge`: objects merge key by key, anything else in `over` replaces.
pub fn merge(base: &Value, over: &Value) -> Value {
    let (Some(b), Some(o)) = (base.as_object(), over.as_object()) else { return over.clone() };
    let mut out = b.clone();
    for (k, v) in o {
        let merged = match (out.get(k), v) {
            (Some(existing), Value::Object(_)) if existing.is_object() => merge(existing, v),
            _ => v.clone(),
        };
        out.insert(k.clone(), merged);
    }
    Value::Object(out)
}

/// Python truthiness of a JSON value (`bool(x)`, `x or default`).
fn truthy(v: &Value) -> bool {
    match v {
        Value::Null => false,
        Value::Bool(b) => *b,
        Value::Number(n) => n.as_f64().is_some_and(|f| f != 0.0),
        Value::String(s) => !s.is_empty(),
        Value::Array(a) => !a.is_empty(),
        Value::Object(o) => !o.is_empty(),
    }
}

/// `int(x or default)`: a number truncates toward zero, a numeric string parses, anything else (and anything falsy) is the default.
fn int_or(v: Option<&Value>, default: i64) -> i64 {
    let Some(v) = v.filter(|v| truthy(v)) else { return default };
    match v {
        Value::Number(n) => n.as_i64().or_else(|| n.as_f64().map(|f| f as i64)).unwrap_or(default),
        Value::String(s) => s.trim().parse::<i64>().ok().or_else(|| s.trim().parse::<f64>().ok().map(|f| f as i64)).unwrap_or(default),
        Value::Bool(_) => 1,
        _ => default,
    }
}

/// `float(x or default)`.
fn float_or(v: Option<&Value>, default: f64) -> f64 {
    let Some(v) = v.filter(|v| truthy(v)) else { return default };
    match v {
        Value::Number(n) => n.as_f64().unwrap_or(default),
        Value::String(s) => s.trim().parse::<f64>().unwrap_or(default),
        Value::Bool(_) => 1.0,
        _ => default,
    }
}

/// `str(x or "")`, the way Python prints it for the types a config holds.
fn text_of(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => String::new(),
        Some(Value::String(s)) => s.clone(),
        Some(Value::Bool(true)) => "True".to_owned(),
        Some(Value::Bool(false)) => String::new(),
        Some(Value::Number(n)) => n.to_string(),
        Some(other) => other.to_string(),
    }
}

fn clip(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

fn number(f: f64) -> Value {
    serde_json::Number::from_f64(f).map(Value::Number).unwrap_or(Value::Null)
}

/// What `config.py` `_normalise` does on its own (see the module note for what it leaves to the modules).
pub fn normalise(cfg: &Value) -> Value {
    let mut out = merge(&defaults(), cfg);
    let Some(c) = out.as_object_mut() else { return out };

    let source = c.get("source_language").and_then(Value::as_str).unwrap_or("").to_owned();
    if !languages::is_source(&source) {
        c.insert("source_language".into(), json!("ENGLISH"));
    }
    let source = c.get("source_language").and_then(Value::as_str).unwrap_or("ENGLISH").to_owned();
    let allowed = languages::target_names(&source);
    let mut targets = Object::new();
    if let Some(given) = c.get("targets").and_then(Value::as_object) {
        for (name, spec) in given {
            let name = name.to_uppercase();
            if !allowed.contains(&name.as_str()) {
                continue;
            }
            let on = spec.get("enabled").is_some_and(truthy);
            targets.insert(name, json!({"enabled": on}));
        }
    }
    for name in &allowed {
        targets.entry((*name).to_owned()).or_insert_with(|| json!({"enabled": false}));
    }
    c.insert("targets".into(), Value::Object(targets));

    let px = int_or(c.get("default_font_px"), 40).clamp(16, 160);
    c.insert("default_font_px".into(), json!(px));
    let lines = int_or(c.get("history_lines"), 40).clamp(1, 500);
    c.insert("history_lines".into(), json!(lines));
    let captions = c.get("stream_captions").map(truthy).unwrap_or(true);
    c.insert("stream_captions".into(), json!(captions));
    let lane = int_or(c.get("audio_lane"), 1).clamp(1, LANES);
    c.insert("audio_lane".into(), json!(lane));

    // Viewers get `port`; the operator panel is served on `admin_port`, over HTTPS only. They cannot be the same port: that
    // would serve the panel where viewers are served, in cleartext.
    let port = int_or(c.get("port"), 80);
    let mut admin = int_or(c.get("admin_port"), 443);
    if admin == port {
        admin = if port == 443 { 8443 } else { 443 };
    }
    c.insert("port".into(), json!(port));
    c.insert("admin_port".into(), json!(admin));
    let fqdn = text_of(c.get("admin_fqdn")).trim().trim_matches('.').to_owned();
    c.insert("admin_fqdn".into(), json!(fqdn));
    for key in ["host", "admin_host"] {
        let v = text_of(c.get(key)).trim().to_owned();
        c.insert(key.into(), json!(if v.is_empty() { "0.0.0.0".to_owned() } else { v }));
    }

    let mut rt = c.get("realtime").filter(|v| truthy(v)).and_then(Value::as_object).cloned().unwrap_or_default();
    // translation_mode and translate_model are gone (#496); the dialect-less turn-detection settings never worked.
    for gone in ["translation_mode", "translate_model", "vad_mode", "vad_eagerness", "vad_threshold", "vad_prefix_padding_ms", "vad_silence_duration_ms"] {
        rt.remove(gone);
    }
    rt.insert("gate_silence".into(), json!(rt.get("gate_silence").map(truthy).unwrap_or(true)));
    let idle = float_or(rt.get("segment_idle_s"), 1.0).clamp(0.2, 10.0);
    rt.insert("segment_idle_s".into(), number(idle));
    let max_idle = float_or(rt.get("segment_max_idle_s"), 10.0).min(60.0).max(idle);
    rt.insert("segment_max_idle_s".into(), number(max_idle));
    match rt.get("transcribe_model") {
        None => {
            rt.insert("transcribe_model".into(), json!("gpt-live-transcribe"));
        }
        Some(v) if !truthy(v) => {
            rt.insert("transcribe_model".into(), json!(""));
        }
        Some(_) => {}
    }
    let backend = text_of(rt.get("backend")).trim().to_owned();
    rt.insert("backend".into(), json!(if backend.is_empty() { "scribe".to_owned() } else { clip(&backend, 40) }));
    let scribe = clip(text_of(rt.get("scribe_model")).trim(), 120);
    rt.insert("scribe_model".into(), json!(if scribe.is_empty() { "elevenlabs/scribe-v2-realtime".to_owned() } else { scribe }));
    let effort = match rt.get("text_reasoning_effort") {
        None => "none".to_owned(),
        Some(v) => text_of(Some(v)).trim().to_owned(),
    };
    rt.insert("text_reasoning_effort".into(), json!(effort));
    if !matches!(rt.get("noise_reduction").and_then(Value::as_str), Some("near_field" | "far_field")) {
        rt.insert("noise_reduction".into(), Value::Null);
    }
    c.insert("realtime".into(), Value::Object(rt));

    let rec = c.get("recording").filter(|v| truthy(v)).and_then(Value::as_object).cloned().unwrap_or_default();
    let dir = text_of(rec.get("dir")).trim().to_owned();
    c.insert(
        "recording".into(),
        json!({
            "enabled": rec.get("enabled").is_some_and(truthy),
            "dir": if dir.is_empty() { "recordings".to_owned() } else { dir },
            "keep_runs": int_or(rec.get("keep_runs"), 20).clamp(1, 500),
        }),
    );

    let blocklist = text_of(c.get("blocklist_file"));
    c.insert("blocklist_file".into(), json!(if blocklist.is_empty() { "blocklist.txt".to_owned() } else { blocklist }));
    out
}

struct State {
    data: Value,
    mtime: Option<SystemTime>,
    /// Purpose -> the sealed text this computer could not open.
    unopened: HashMap<String, String>,
    loaded: bool,
}

pub struct Settings {
    path: PathBuf,
    secrets: SecretBox,
    extra: Mutex<Vec<(String, String)>>,
    state: Mutex<State>,
}

fn internal(why: impl Into<String>) -> AppError {
    AppError::new(ErrorCode::Internal).because(why.into())
}

fn mtime_of(path: &Path) -> Option<SystemTime> {
    std::fs::metadata(path).and_then(|m| m.modified()).ok()
}

impl Settings {
    /// `path` is config.json; the key store lives beside it.
    pub fn new(path: &Path) -> Self {
        let dir = path.parent().map(Path::to_path_buf).unwrap_or_default();
        Self::with_secretbox(path, SecretBox::new(&dir))
    }

    pub fn with_secretbox(path: &Path, secrets: SecretBox) -> Self {
        Self {
            path: path.to_path_buf(),
            secrets,
            extra: Mutex::new(Vec::new()),
            state: Mutex::new(State { data: Value::Null, mtime: None, unopened: HashMap::new(), loaded: false }),
        }
    }

    pub fn path(&self) -> &Path {
        &self.path
    }

    pub fn secretbox(&self) -> &SecretBox {
        &self.secrets
    }

    /// A module's secret (`Module.secret_settings`): `field` inside the object at `settings_key`, sealed as
    /// `config:<settings_key>.<field>`.
    pub fn add_secret(&self, settings_key: &str, field: &str) {
        if let Ok(mut extra) = self.extra.lock() {
            let pair = (settings_key.to_owned(), field.to_owned());
            if !extra.contains(&pair) {
                extra.push(pair);
            }
        }
    }

    /// (settings key or "" for the top level, field, purpose) for every secret.
    fn secret_fields(&self) -> Vec<(String, String, String)> {
        let mut out: Vec<(String, String, String)> =
            SECRET_KEYS.iter().map(|k| (String::new(), (*k).to_owned(), format!("config:{k}"))).collect();
        if let Ok(extra) = self.extra.lock() {
            out.extend(extra.iter().map(|(s, f)| (s.clone(), f.clone(), format!("config:{s}.{f}"))));
        }
        out
    }

    fn boxed<'a>(root: &'a mut Object, settings_key: &str) -> Option<&'a mut Object> {
        if settings_key.is_empty() {
            Some(root)
        } else {
            root.get_mut(settings_key).and_then(Value::as_object_mut)
        }
    }

    /// Unseal `raw`'s secrets in place. True when one was still stored as plain text.
    fn open_secrets(&self, raw: &mut Object, unopened: &mut HashMap<String, String>) -> bool {
        let mut plain = false;
        unopened.clear();
        for (settings_key, field, purpose) in self.secret_fields() {
            let Some(b) = Self::boxed(raw, &settings_key) else { continue };
            let Some(value) = b.get(&field).and_then(Value::as_str).map(str::to_owned) else { continue };
            if secretbox::is_sealed(&value) {
                let opened = self.secrets.unseal(&value, &purpose);
                if opened.is_none() {
                    unopened.insert(purpose, value);
                }
                b.insert(field, json!(opened.unwrap_or_default()));
            } else if !value.trim().is_empty() {
                plain = true;
            }
        }
        plain
    }

    /// A copy of `cfg` as it is written to disk: every secret sealed.
    fn sealed(&self, cfg: &Value, unopened: &HashMap<String, String>) -> Result<Value, AppError> {
        let mut out = cfg.clone();
        let Some(root) = out.as_object_mut() else { return Ok(out) };
        for (settings_key, field, purpose) in self.secret_fields() {
            let Some(b) = Self::boxed(root, &settings_key) else { continue };
            match b.get(&field).and_then(Value::as_str).map(str::to_owned) {
                Some(v) if !v.is_empty() => {
                    let sealed = self.secrets.seal(&v, &purpose).map_err(|e| internal(format!("sealing {purpose}: {e}")))?;
                    b.insert(field, json!(sealed));
                }
                _ => {
                    if let Some(old) = unopened.get(&purpose) {
                        b.insert(field, json!(old));
                    }
                }
            }
        }
        Ok(out)
    }

    fn write(&self, cfg: &Value, unopened: &HashMap<String, String>) -> Result<Option<SystemTime>, AppError> {
        let text = serde_json::to_string_pretty(&self.sealed(cfg, unopened)?).map_err(|e| internal(e.to_string()))?;
        let fail = |what: &str, e: std::io::Error| internal(format!("config.json: {what}: {e}"));
        if let Some(dir) = self.path.parent().filter(|d| !d.as_os_str().is_empty()) {
            std::fs::create_dir_all(dir).map_err(|e| fail("make the folder", e))?;
        }
        let tmp = self.path.with_extension("json.tmp");
        private::write_private(&self.path, &tmp, &text).map_err(|e| fail("write", e))?;
        Ok(mtime_of(&self.path))
    }

    /// Read config.json, open its secrets and normalise it. A missing file is the defaults; a file that is not a JSON object stops
    /// the server (the Python server exits) rather than being overwritten.
    pub fn load(&self) -> Result<Value, AppError> {
        let mut state = self.state.lock().map_err(|_| internal("config lock poisoned"))?;
        self.load_locked(&mut state)
    }

    fn load_locked(&self, state: &mut State) -> Result<Value, AppError> {
        let mtime = mtime_of(&self.path);
        let mut raw = match std::fs::read_to_string(&self.path) {
            Ok(text) => match serde_json::from_str::<Value>(&text) {
                Ok(Value::Object(o)) => o,
                Ok(_) => return Err(internal("config.json is not a JSON object")),
                Err(e) => return Err(internal(format!("config.json is not valid JSON: {e}"))),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => Object::new(),
            Err(e) => return Err(internal(format!("config.json could not be read: {e}"))),
        };
        let mut unopened = HashMap::new();
        let plain = self.open_secrets(&mut raw, &mut unopened);
        let data = normalise(&Value::Object(raw));
        state.unopened = unopened;
        state.mtime = mtime;
        if plain {
            // A secret typed into config.json by hand (the first admin token) works, and is sealed straight away.
            match self.write(&data, &state.unopened) {
                Ok(m) => state.mtime = m,
                Err(e) => tracing::warn!("could not seal the secrets in config.json: {}", e.cause().unwrap_or("")),
            }
        }
        state.data = data.clone();
        state.loaded = true;
        Ok(data)
    }

    /// Live config. Reads the file again when it changed on disk: hand-editing config.json is the documented way to set the
    /// first admin token, and that edit has to take effect without a restart.
    pub fn get(&self) -> Result<Value, AppError> {
        let mut state = self.state.lock().map_err(|_| internal("config lock poisoned"))?;
        if !state.loaded || mtime_of(&self.path) != state.mtime {
            return self.load_locked(&mut state);
        }
        Ok(state.data.clone())
    }

    /// Merge `updates` into the live config and save it atomically. A key named in `replace` overwrites instead of merging, for
    /// an object whose entries have to be removable (the panel list).
    pub fn save(&self, updates: &Value, replace: &[&str]) -> Result<Value, AppError> {
        let mut state = self.state.lock().map_err(|_| internal("config lock poisoned"))?;
        let mut base = if !state.loaded || mtime_of(&self.path) != state.mtime { self.load_locked(&mut state)? } else { state.data.clone() };
        if let Some(b) = base.as_object_mut() {
            for key in replace {
                if updates.get(key).is_some() {
                    b.remove(*key);
                }
            }
        }
        let merged = normalise(&merge(&base, updates));
        let mut unopened = state.unopened.clone();
        if let Some(u) = updates.as_object() {
            let mut u = u.clone();
            for (settings_key, field, purpose) in self.secret_fields() {
                if Self::boxed(&mut u, &settings_key).and_then(|b| b.get(&field)).is_some_and(truthy) {
                    unopened.remove(&purpose); // a new value replaces one that could not be opened
                }
            }
        }
        let mtime = self.write(&merged, &unopened)?;
        state.unopened = unopened;
        state.mtime = mtime;
        state.data = merged.clone();
        Ok(merged)
    }

    /// config.json as stored, before normalising and with its secrets still sealed ({} when missing or unreadable).
    pub fn read_raw(&self) -> Value {
        std::fs::read_to_string(&self.path)
            .ok()
            .and_then(|t| serde_json::from_str::<Value>(&t).ok())
            .filter(Value::is_object)
            .unwrap_or_else(|| json!({}))
    }

    /// Config safe to hand to the admin browser: no key, no token, only whether each is set and its last four characters.
    /// A module's own `public()` view is applied by the module registry once modules are ported (#661).
    pub fn redacted(&self) -> Result<Value, AppError> {
        let mut cfg = self.get()?;
        let Some(c) = cfg.as_object_mut() else { return Ok(cfg) };
        let take = |c: &mut Object, key: &str| c.remove(key).and_then(|v| v.as_str().map(str::to_owned)).unwrap_or_default();
        let key = take(c, "openai_api_key");
        let vkey = take(c, "vercel_api_key");
        c.remove("admin_token");
        let backup = take(c, "backup_key");
        c.insert("backup_passphrase_set".into(), json!(!backup.is_empty()));
        c.insert("secret_store".into(), json!(self.secrets.location()));
        let hint = |k: &str| {
            let n = k.chars().count();
            if n >= 4 { format!("…{}", k.chars().skip(n - 4).collect::<String>()) } else { String::new() }
        };
        c.insert("openai_api_key_set".into(), json!(!key.is_empty()));
        c.insert("openai_api_key_hint".into(), json!(hint(&key)));
        c.insert("vercel_api_key_set".into(), json!(!vkey.is_empty()));
        c.insert("vercel_api_key_hint".into(), json!(hint(&vkey)));
        Ok(cfg)
    }

    /// Create the state folder and config.json on first run, from `example` when it exists, else the defaults; make an existing
    /// file owner-only.
    pub fn ensure_file(&self, example: Option<&Path>) -> Result<(), AppError> {
        if let Some(dir) = self.path.parent().filter(|d| !d.as_os_str().is_empty()) {
            private::make_private_dir(dir).map_err(|e| internal(format!("state folder: {e}")))?;
        }
        if self.path.exists() {
            return tighten(&self.path);
        }
        let text = match example.and_then(|p| std::fs::read_to_string(p).ok()) {
            Some(t) => t,
            None => serde_json::to_string_pretty(&defaults()).map_err(|e| internal(e.to_string()))?,
        };
        std::fs::write(&self.path, text).map_err(|e| internal(format!("config.json: write: {e}")))?;
        private::restrict(&self.path);
        Ok(())
    }
}

/// Make an existing file 0600, taking ownership by rewriting it through a fresh private file when a plain chmod is refused (a
/// file copied in from another machine). Where even that fails the error is returned: a secret that cannot be made private
/// should stop the start, not be served as it is.
fn tighten(path: &Path) -> Result<(), AppError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o600)).is_ok() {
            return Ok(());
        }
        let bytes = std::fs::read(path).map_err(|e| internal(format!("config.json: read: {e}")))?;
        let tmp = path.with_extension("json.tmp");
        let text = String::from_utf8_lossy(&bytes).into_owned();
        private::write_private(path, &tmp, &text).map_err(|e| internal(format!("config.json: make private: {e}")))
    }
    #[cfg(not(unix))]
    {
        private::restrict(path);
        Ok(())
    }
}
