//! Pages (#654): the Jinja templates of `app/templates/` and `app/modules/<id>/templates/`, rendered by minijinja so a page
//! comes out the same as the Python server's (`Jinja2Templates` in `app/main.py`).
//!
//! A core template is named `home.html`; a module's is `<module id>/<file>`. Each is read from disk when first asked for, so a
//! changed template shows on the next start without a rebuild. The few places minijinja and Jinja2 differ are closed here, and
//! `tests/pages.rs` renders pages Python rendered and compares the HTML byte for byte:
//!
//! - `None`, `True` and `False` print as Python prints them (minijinja writes `none`, `true`, `false`);
//! - `tojson` sorts keys, writes `", "` and `": "`, escapes non-ASCII as `\uXXXX` and escapes `< > & '` for HTML, as Jinja2's does;
//! - `static(path)` is `app/main.py`'s `_static_url`: `/static/<path>?v=<mtime in hex>`;
//! - Python's string, list and dict methods (`startswith`, `items`, ...) come from `minijinja-contrib`'s `pycompat`.

use std::fmt::Write as _;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::UNIX_EPOCH;

use minijinja::value::Value;
use minijinja::{AutoEscape, Environment, Error, ErrorKind, Output, State};
use serde_json::ser::Formatter;

pub struct Pages {
    env: Environment<'static>,
}

impl Pages {
    /// `app_dir` is the folder holding `templates/`, `static/` and `modules/` (the Python app's `app/`).
    pub fn new(app_dir: &Path) -> Self {
        let mut env = Environment::new();
        let root = app_dir.to_path_buf();
        env.set_loader({
            let root = root.clone();
            move |name| Ok(template_path(&root, name).and_then(|p| fs::read_to_string(p).ok()))
        });
        env.set_auto_escape_callback(|name| {
            let ext = name.rsplit_once('.').map(|(_, e)| e).unwrap_or("");
            if matches!(ext, "html" | "htm" | "xml") {
                AutoEscape::Html
            } else {
                AutoEscape::None
            }
        });
        env.set_formatter(python_format);
        env.add_filter("tojson", tojson);
        env.add_function("static", move |path: String| static_url(&root, &path));
        env.set_unknown_method_callback(minijinja_contrib::pycompat::unknown_method_callback);
        Self { env }
    }

    /// Render `name` with `context` (a JSON object). The error is for the log, not for a response.
    pub fn render(&self, name: &str, context: &serde_json::Value) -> Result<String, Error> {
        self.env.get_template(name)?.render(Value::from_serialize(context))
    }
}

/// The file behind a template name, or `None` when the name steps outside the template folders.
fn template_path(root: &Path, name: &str) -> Option<PathBuf> {
    let rel = Path::new(name);
    if rel.components().any(|c| !matches!(c, Component::Normal(_))) {
        return None;
    }
    if let Some((module, rest)) = name.split_once('/') {
        let p = root.join("modules").join(module).join("templates").join(rest);
        if p.is_file() {
            return Some(p);
        }
    }
    Some(root.join("templates").join(name))
}

/// A `/static` URL that changes whenever the file does (`_static_url`): `?v=` and the file's mtime in nanoseconds, in hex.
/// `modules/<id>/<file>` is in that module's `static/`. A file that cannot be read gets the plain URL.
pub fn static_url(root: &Path, path: &str) -> String {
    match static_file(root, path).and_then(|p| fs::metadata(p).ok()).and_then(|m| m.modified().ok()) {
        Some(t) => match t.duration_since(UNIX_EPOCH) {
            Ok(d) => format!("/static/{path}?v={:x}", d.as_nanos()),
            Err(_) => format!("/static/{path}"),
        },
        None => format!("/static/{path}"),
    }
}

/// The file behind a static path, or `None` when it steps outside the static folders.
pub fn static_file(root: &Path, path: &str) -> Option<PathBuf> {
    if Path::new(path).components().any(|c| !matches!(c, Component::Normal(_))) {
        return None;
    }
    match path.strip_prefix("modules/") {
        Some(rest) => {
            let (module, file) = rest.split_once('/')?;
            Some(root.join("modules").join(module).join("static").join(file))
        }
        None => Some(root.join("static").join(path)),
    }
}

/// How Python prints a value: `None`, `True`, `False`; everything else as minijinja does (escaped for HTML).
fn python_format(out: &mut Output, state: &State, value: &Value) -> Result<(), Error> {
    if value.is_none() {
        out.write_str("None")?;
        Ok(())
    } else if let Some(b) = value.as_bool_exact() {
        out.write_str(if b { "True" } else { "False" })?;
        Ok(())
    } else if matches!(state.auto_escape(), AutoEscape::Html) && !value.is_safe() {
        // markupsafe's five characters (minijinja's own escaper also writes `/` as `&#x2f;` and `'` as `&#x27;`).
        for c in value.to_string().chars() {
            match c {
                '&' => out.write_str("&amp;")?,
                '<' => out.write_str("&lt;")?,
                '>' => out.write_str("&gt;")?,
                '\'' => out.write_str("&#39;")?,
                '"' => out.write_str("&#34;")?,
                c => out.write_char(c)?,
            }
        }
        Ok(())
    } else {
        write!(out, "{value}")?;
        Ok(())
    }
}

trait BoolExact {
    fn as_bool_exact(&self) -> Option<bool>;
}

impl BoolExact for Value {
    fn as_bool_exact(&self) -> Option<bool> {
        if self.kind() == minijinja::value::ValueKind::Bool {
            Some(self.is_true())
        } else {
            None
        }
    }
}

// ----- tojson -----------------------------------------------------------------------------------------------------------

/// `json.dumps` as Python writes it with `sort_keys=True`: `", "` and `": "` between items, non-ASCII as `\uXXXX`.
struct PythonJson;

impl Formatter for PythonJson {
    fn begin_array_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        if first {
            Ok(())
        } else {
            w.write_all(b", ")
        }
    }
    fn begin_object_key<W: ?Sized + std::io::Write>(&mut self, w: &mut W, first: bool) -> std::io::Result<()> {
        if first {
            Ok(())
        } else {
            w.write_all(b", ")
        }
    }
    fn begin_object_value<W: ?Sized + std::io::Write>(&mut self, w: &mut W) -> std::io::Result<()> {
        w.write_all(b": ")
    }
    fn write_string_fragment<W: ?Sized + std::io::Write>(&mut self, w: &mut W, fragment: &str) -> std::io::Result<()> {
        let mut plain = String::new();
        for c in fragment.chars() {
            if c.is_ascii() {
                plain.push(c);
            } else {
                let mut units = [0u16; 2];
                for u in c.encode_utf16(&mut units) {
                    let _ = write!(plain, "\\u{u:04x}");
                }
            }
        }
        w.write_all(plain.as_bytes())
    }
}

/// The value with every object's keys in sorted order.
fn sorted(v: serde_json::Value) -> serde_json::Value {
    match v {
        serde_json::Value::Object(map) => {
            let mut pairs: Vec<_> = map.into_iter().collect();
            pairs.sort_by(|a, b| a.0.cmp(&b.0));
            serde_json::Value::Object(pairs.into_iter().map(|(k, v)| (k, sorted(v))).collect())
        }
        serde_json::Value::Array(items) => serde_json::Value::Array(items.into_iter().map(sorted).collect()),
        other => other,
    }
}

/// Jinja2's `tojson`: safe to put inside a `<script>` or a single-quoted attribute.
fn tojson(value: Value) -> Result<Value, Error> {
    let json = sorted(serde_json::to_value(&value).map_err(|e| Error::new(ErrorKind::BadSerialization, e.to_string()))?);
    let mut buf = Vec::new();
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, PythonJson);
    serde::Serialize::serialize(&json, &mut ser).map_err(|e| Error::new(ErrorKind::BadSerialization, e.to_string()))?;
    let text = String::from_utf8(buf).map_err(|e| Error::new(ErrorKind::BadSerialization, e.to_string()))?;
    let text = text.replace('<', "\\u003c").replace('>', "\\u003e").replace('&', "\\u0026").replace('\'', "\\u0027");
    Ok(Value::from_safe_string(text))
}
