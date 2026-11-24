//! The NDI module's saved settings (`clean_settings` of `service.py` and the cleaners of `multiview.py`, `outputs.py`,
//! `testpatterns.py`, `router.py`, `watch.py` and `bridge.py`). Same keys, same limits, same fallbacks, so config.json
//! moves between the Python and the Rust server unchanged.

use std::net::IpAddr;

use serde_json::{json, Map, Value};

pub const MAX_EXTRA: usize = 20;
pub const MAX_GROUPS: usize = 20;
pub const MAX_SERVERS: usize = 4;
pub const MAX_TALLY: usize = 64;
pub const MIN_INTERVAL: i64 = 5;
pub const MAX_INTERVAL: i64 = 300;
pub const SERVER_PORT: u16 = 5959;
pub const MAX_MULTIVIEWS: usize = 20;
pub const MAX_ON: usize = 8;
pub const MAX_TESTS: usize = 12;
pub const MAX_TEST_ON: usize = 4;
pub const MAX_OUTPUTS: usize = 16;
pub const MAX_MACHINES: usize = 8;
pub const BRIDGE_PORT: i64 = 5990;
pub const MAX_WATCH_SOURCES: usize = 32;

/// The viewer's limits: key, lowest, highest, default.
pub const VIEW_LIMITS: [(&str, i64, i64, i64); 4] = [("view_fps", 1, 30, 15), ("view_quality", 20, 90, 60), ("view_width", 320, 1920, 1280), ("view_max", 1, 8, 4)];

const LAYOUTS: [(&str, usize); 5] = [("2x2", 4), ("3x3", 9), ("4x4", 16), ("1+5", 6), ("1+7", 8)];
const BOX_KINDS: [&str; 4] = ["empty", "source", "clock", "timer"];
const PATTERNS: [&str; 11] = ["bars", "grid", "ramp", "white", "black", "red", "green", "blue", "motion", "alpha", "image"];
const TEST_AUDIO: [&str; 4] = ["", "tone", "id", "silence"];
const TEST_LEVELS: [i64; 8] = [0, -3, -9, -12, -18, -20, -30, -40];
const TEST_SIZES: [&str; 4] = ["640x360", "1280x720", "1920x1080", "3840x2160"];

/// Whitespace squeezed to single spaces (`text.squeeze`).
pub fn squeeze(s: &str) -> String {
    s.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// Two names are the same ignoring case and spacing; two empty names are not a match (`text.same_name`).
pub fn same_name(a: &str, b: &str) -> bool {
    let fa = squeeze(a).to_lowercase();
    !fa.is_empty() && fa == squeeze(b).to_lowercase()
}

fn take_chars(s: &str, n: usize) -> String {
    s.chars().take(n).collect()
}

/// A lowercase address made from `name` (`text.slug`): letters and digits joined by `-`, at most `limit` characters.
pub fn slug(name: &str, limit: usize, fallback: &str) -> String {
    let mut out = String::new();
    let mut pending = false;
    for c in name.to_lowercase().chars() {
        if c.is_ascii_lowercase() || c.is_ascii_digit() {
            if pending && !out.is_empty() {
                out.push('-');
            }
            pending = false;
            out.push(c);
        } else {
            pending = true;
        }
    }
    let cut = take_chars(&out, limit);
    let cut = cut.trim_matches('-').to_owned();
    if cut.is_empty() {
        fallback.to_owned()
    } else {
        cut
    }
}

fn int(v: Option<&Value>) -> Option<i64> {
    v.and_then(Value::as_i64)
}

fn list(v: Option<&Value>) -> &[Value] {
    v.and_then(Value::as_array).map_or(&[], Vec::as_slice)
}

fn text(v: Option<&Value>) -> Option<&str> {
    v.and_then(Value::as_str)
}

/// `a.b.c.d` or an IPv6 address, normalised, else "".
pub fn clean_ip(v: &Value) -> String {
    v.as_str().and_then(|s| s.trim().parse::<IpAddr>().ok()).map(|a| a.to_string()).unwrap_or_default()
}

fn host_ok(h: &str) -> bool {
    let b = h.as_bytes();
    let ok = |c: u8| c.is_ascii_alphanumeric();
    if b.is_empty() || b.len() > 253 || !ok(b[0]) || !ok(b[b.len() - 1]) {
        return false;
    }
    b.iter().all(|c| ok(*c) || *c == b'.' || *c == b'-')
}

/// `host` or `host:port`, else "". The default port is left off.
pub fn clean_server(v: &Value) -> String {
    let Some(s) = v.as_str() else { return String::new() };
    let s = s.trim();
    let (host, port) = match s.split_once(':') {
        Some((h, p)) => (h, Some(p)),
        None => (s, None),
    };
    if !host_ok(host) {
        return String::new();
    }
    match port {
        Some("") | None => host.to_owned(),
        Some(p) => match p.parse::<u32>() {
            Ok(n) if p.bytes().all(|c| c.is_ascii_digit()) && (1..=65535).contains(&n) => {
                if n == u32::from(SERVER_PORT) {
                    host.to_owned()
                } else {
                    format!("{host}:{n}")
                }
            }
            _ => String::new(),
        },
    }
}

/// `host:port` of a discovery server, the default port filled in.
pub fn split_server(server: &str) -> (String, u16) {
    match server.split_once(':') {
        Some((h, p)) => (h.to_owned(), p.parse().unwrap_or(SERVER_PORT)),
        None => (server.to_owned(), SERVER_PORT),
    }
}

fn group_ok(g: &str) -> bool {
    let n = g.chars().count();
    (1..=40).contains(&n) && g.chars().all(|c| c.is_ascii_alphanumeric() || " _.-".contains(c))
}

fn is_hex_id(s: &str, lo: usize, hi: usize) -> bool {
    (lo..=hi).contains(&s.len()) && s.bytes().all(|c| c.is_ascii_digit() || c.is_ascii_lowercase())
}

fn is_clock(s: &str) -> bool {
    relay_core::timewin::is_clock(s)
}

pub fn layout_cells(layout: &str) -> Option<usize> {
    LAYOUTS.iter().find(|(l, _)| *l == layout).map(|(_, n)| *n)
}

fn clean_box(raw: &Value) -> Value {
    let o = raw.as_object();
    let get = |k: &str| o.and_then(|o| o.get(k));
    let kind = text(get("kind")).filter(|k| BOX_KINDS.contains(k)).unwrap_or("empty");
    let mode = text(get("mode")).filter(|m| ["time", "service"].contains(m)).unwrap_or("time");
    let at = text(get("at")).filter(|a| is_clock(a)).unwrap_or("10:30");
    json!({"kind": kind, "source": take_chars(text(get("source")).unwrap_or(""), 160), "label": take_chars(&squeeze(text(get("label")).unwrap_or("")), 40),
           "meter": get("meter") == Some(&Value::Bool(true)), "mode": mode, "at": at})
}

fn mv_id_ok(s: &str) -> bool {
    let b = s.as_bytes();
    (1..=40).contains(&b.len()) && (b[0].is_ascii_digit() || b[0].is_ascii_lowercase()) && b.iter().all(|c| c.is_ascii_digit() || c.is_ascii_lowercase() || *c == b'-')
}

pub fn clean_multiviews(raw: Option<&Value>) -> Value {
    let mut out: Vec<Value> = Vec::new();
    let mut used: Vec<String> = Vec::new();
    for r in list(raw) {
        let Some(o) = r.as_object() else { continue };
        let name = take_chars(&squeeze(text(o.get("name")).unwrap_or("")), 60);
        if name.is_empty() || out.len() >= MAX_MULTIVIEWS {
            continue;
        }
        let layout = text(o.get("layout")).filter(|l| layout_cells(l).is_some()).unwrap_or("2x2");
        let n = layout_cells(layout).unwrap_or(4);
        let mut boxes: Vec<Value> = list(o.get("boxes")).iter().map(clean_box).collect();
        boxes.resize(n, clean_box(&Value::Null));
        boxes.truncate(n);
        let given = text(o.get("id")).filter(|i| mv_id_ok(i)).unwrap_or("");
        let base = if given.is_empty() { slug(&name, 30, "multiview") } else { given.to_owned() };
        let (mut id, mut k) = (base.clone(), 2);
        while used.contains(&id) {
            id = format!("{base}-{k}");
            k += 1;
        }
        used.push(id.clone());
        out.push(json!({"id": id, "name": name, "layout": layout, "boxes": boxes}));
    }
    Value::Array(out)
}

pub fn clean_tests(raw: Option<&Value>) -> Value {
    let mut out: Vec<Map<String, Value>> = Vec::new();
    for t in list(raw) {
        let Some(o) = t.as_object() else { continue };
        if out.len() >= MAX_TESTS {
            continue;
        }
        let id = text(o.get("id")).filter(|i| is_hex_id(i, 4, 12)).unwrap_or("");
        let name = squeeze(text(o.get("name")).unwrap_or(""));
        let name_ok = {
            let mut cs = name.chars();
            let n = name.chars().count();
            cs.next().is_some_and(|c| c.is_alphanumeric()) && n <= 60 && name.chars().all(|c| c.is_alphanumeric() || c == '_' || " .-()".contains(c))
        };
        if id.is_empty() || !name_ok || out.iter().any(|x| x["id"] == id || same_name(x["name"].as_str().unwrap_or(""), &name)) {
            continue;
        }
        let level = int(o.get("level")).filter(|l| TEST_LEVELS.contains(l) && !o["level"].is_boolean()).unwrap_or(-18);
        let fps = int(o.get("fps")).map_or(30, |f| f.clamp(5, 60));
        let mut m = Map::new();
        m.insert("id".into(), json!(id));
        m.insert("name".into(), json!(name));
        m.insert("pattern".into(), json!(text(o.get("pattern")).filter(|p| PATTERNS.contains(p)).unwrap_or("bars")));
        m.insert("burn".into(), json!(o.get("burn").and_then(Value::as_bool).unwrap_or(true)));
        m.insert("audio".into(), json!(text(o.get("audio")).filter(|a| TEST_AUDIO.contains(a)).unwrap_or("")));
        m.insert("level".into(), json!(level));
        m.insert("size".into(), json!(text(o.get("size")).filter(|s| TEST_SIZES.contains(s)).unwrap_or("1280x720")));
        m.insert("fps".into(), json!(fps));
        out.push(m);
    }
    Value::Array(out.into_iter().map(Value::Object).collect())
}

fn source_text(v: Option<&Value>) -> String {
    take_chars(text(v).unwrap_or("").trim(), 160)
}

fn out_name(v: Option<&Value>) -> String {
    take_chars(&squeeze(text(v).unwrap_or("")), 70)
}

pub fn clean_router_outputs(raw: Option<&Value>) -> Value {
    let mut out: Vec<Value> = Vec::new();
    for r in list(raw) {
        let Some(o) = r.as_object() else { continue };
        let Some(id) = text(o.get("id")).filter(|i| is_hex_id(i, 4, 16)) else { continue };
        let name = out_name(o.get("name"));
        if name.is_empty() || out.iter().any(|x| x["id"] == id || x["name"].as_str().unwrap_or("").to_lowercase() == name.to_lowercase()) {
            continue;
        }
        out.push(json!({"id": id, "name": name, "audio": source_text(o.get("audio"))}));
    }
    out.truncate(MAX_OUTPUTS);
    Value::Array(out)
}

pub fn clean_watch(raw: Option<&Value>) -> Value {
    let o = raw.and_then(Value::as_object);
    let get = |k: &str| o.and_then(|o| o.get(k));
    let flag = |k: &str, d: bool| get(k).and_then(Value::as_bool).unwrap_or(d);
    let mut names: Vec<String> = Vec::new();
    for v in list(get("sources")) {
        if let Some(s) = v.as_str().filter(|s| !s.is_empty() && s.chars().count() <= 160) {
            if !names.iter().any(|n| n == s) {
                names.push(s.to_owned());
            }
        }
    }
    names.truncate(MAX_WATCH_SOURCES);
    let hold = int(get("hold")).map_or(10, |h| h.clamp(3, 300));
    json!({"on": flag("on", false), "auto": flag("auto", true), "sources": names, "hold": hold,
           "schedule": take_chars(text(get("schedule")).unwrap_or("").trim(), 80), "sound": flag("sound", true)})
}

fn bridge_host_ok(h: &str) -> bool {
    (1..=100).contains(&h.len()) && h.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'.' || c == b'-')
}

pub fn new_id() -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    (0..4).map(|_| format!("{:02x}", rng.gen::<u8>())).collect()
}

pub fn clean_machines(raw: Option<&Value>) -> Value {
    let mut out: Vec<Value> = Vec::new();
    for m in list(raw) {
        let Some(o) = m.as_object() else { continue };
        let name = take_chars(&squeeze(text(o.get("name")).unwrap_or("")), 60);
        let addr = text(o.get("address")).unwrap_or("").trim().to_owned();
        if name.is_empty() || !bridge_host_ok(&addr) || out.iter().any(|x| same_name(x["name"].as_str().unwrap_or(""), &name)) {
            continue;
        }
        let id = text(o.get("id")).filter(|i| is_hex_id(i, 4, 16) && !out.iter().any(|x| x["id"] == *i)).map_or_else(new_id, str::to_owned);
        let port = int(o.get("port")).filter(|p| (1..=65535).contains(p) && !o["port"].is_boolean()).unwrap_or(BRIDGE_PORT);
        out.push(json!({"id": id, "name": name, "role": if text(o.get("role")) == Some("host") { "host" } else { "join" }, "address": addr, "port": port}));
    }
    out.truncate(MAX_MACHINES);
    Value::Array(out)
}

/// Settings as saved, made safe. Every key `defaults` names is present afterwards.
pub fn clean_settings(raw: &Value) -> Value {
    let o = raw.as_object();
    let get = |k: &str| o.and_then(|o| o.get(k));
    let mut ips: Vec<String> = Vec::new();
    for v in list(get("extra_ips")) {
        let ip = clean_ip(v);
        if !ip.is_empty() && !ips.contains(&ip) {
            ips.push(ip);
        }
    }
    let mut groups: Vec<String> = Vec::new();
    for v in list(get("groups")) {
        let g = squeeze(v.as_str().unwrap_or(""));
        if group_ok(&g) && !groups.iter().any(|x| x.to_lowercase() == g.to_lowercase()) {
            groups.push(g);
        }
    }
    let mut servers: Vec<String> = Vec::new();
    for v in list(get("servers")) {
        let s = clean_server(v);
        if !s.is_empty() && !servers.contains(&s) {
            servers.push(s);
        }
    }
    ips.truncate(MAX_EXTRA);
    groups.truncate(MAX_GROUPS);
    servers.truncate(MAX_SERVERS);
    let interval = int(get("interval")).map_or(15, |i| i.clamp(MIN_INTERVAL, MAX_INTERVAL));
    let mut out = Map::new();
    out.insert("servers".into(), json!(servers));
    out.insert("mdns".into(), json!(get("mdns").and_then(Value::as_bool).unwrap_or(true)));
    out.insert("extra_ips".into(), json!(ips));
    out.insert("groups".into(), json!(groups));
    out.insert("interval".into(), json!(interval));
    for (key, lo, hi, default) in VIEW_LIMITS {
        out.insert(key.into(), json!(int(get(key)).map_or(default, |v| v.clamp(lo, hi))));
    }
    let mut tally = Map::new();
    if let Some(t) = get("tally").and_then(Value::as_object) {
        for (name, n) in t {
            if let Some(n) = n.as_i64().filter(|n| (1..=999).contains(n)) {
                if !name.is_empty() && name.chars().count() <= 160 && tally.len() < MAX_TALLY {
                    tally.insert(name.clone(), json!(n));
                }
            }
        }
    }
    out.insert("tally".into(), Value::Object(tally));
    out.insert("multiviews".into(), clean_multiviews(get("multiviews")));
    let mut on: Vec<String> = Vec::new();
    for v in list(get("out_on")) {
        if let Some(s) = v.as_str().filter(|s| !s.is_empty() && s.chars().count() <= 80) {
            if !on.iter().any(|x| x == s) {
                on.push(s.to_owned());
            }
        }
    }
    on.truncate(MAX_ON);
    out.insert("out_on".into(), json!(on));
    out.insert("out_size".into(), json!(text(get("out_size")).filter(|s| ["1280x720", "1920x1080"].contains(s)).unwrap_or("1280x720")));
    out.insert("out_fps".into(), json!(int(get("out_fps")).map_or(30, |f| f.clamp(5, 30))));
    let tests = clean_tests(get("tests"));
    let known: Vec<String> = tests.as_array().map(|a| a.iter().filter_map(|t| t["id"].as_str().map(str::to_owned)).collect()).unwrap_or_default();
    let mut tests_on: Vec<String> = Vec::new();
    for v in list(get("tests_on")) {
        if let Some(s) = v.as_str().filter(|s| known.iter().any(|k| k == s)) {
            if !tests_on.iter().any(|x| x == s) {
                tests_on.push(s.to_owned());
            }
        }
    }
    tests_on.truncate(MAX_TEST_ON);
    out.insert("tests".into(), tests);
    out.insert("tests_on".into(), json!(tests_on));
    out.insert("router_outputs".into(), clean_router_outputs(get("router_outputs")));
    out.insert("watch".into(), clean_watch(get("watch")));
    out.insert("bridge".into(), clean_machines(get("bridge")));
    // Saved before core routes (#544): carried as they are until the router has moved them.
    out.insert("routes".into(), get("routes").filter(|v| v.is_array()).cloned().unwrap_or_else(|| json!([])));
    out.insert("salvos".into(), get("salvos").filter(|v| v.is_array()).cloned().unwrap_or_else(|| json!([])));
    Value::Object(out)
}

pub fn defaults() -> Value {
    clean_settings(&Value::Null)
}

/// The NDI SDK's own config (`ndi-config.v1.json`) for these network settings: discovery servers, mDNS on or off, and the groups to receive.
pub fn sdk_config(net: &Value) -> Value {
    let join = |k: &str| list(net.get(k)).iter().filter_map(Value::as_str).collect::<Vec<_>>().join(",");
    json!({"ndi": {"networks": {"ips": join("extra_ips"), "discovery": join("servers")},
                   "mdns": {"enable": net.get("mdns").and_then(Value::as_bool).unwrap_or(true)},
                   "groups": {"recv": join("groups")}}})
}
