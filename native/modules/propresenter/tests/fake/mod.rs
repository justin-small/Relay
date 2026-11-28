//! A fake ProPresenter: a real HTTP server on 127.0.0.1 that answers the way ProPresenter 22 did when it was tried live
//! (docs/reference/propresenter-live-test.md): `POST /v1/status/updates` streams `{"url","data"}` messages separated by a blank line, in HTTP chunks
//! that cut messages anywhere; slide pushes are sometimes missed; `timer/system_time` arrives unasked; one unknown path fails the request with 400.
//! Mutate `fake.lock()` from the test; `push()` sends a stream message (or doesn't, to model a missed push).
#![allow(dead_code)]

use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use serde_json::{json, Value};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::Notify;

/// The 46 paths a live install accepted for the stream.
pub const ACCEPTED: [&str; 43] = [
    "announcement/active", "announcement/slide_index", "announcement/active/timeline", "audio/playlists", "audio/playlist/active", "audio/playlist/focused",
    "capture/status", "looks", "look/current", "macros", "macro_collections", "media/playlists", "media/playlist/active", "media/playlist/focused",
    "messages", "playlists", "playlist/active", "playlist/focused", "presentation/active", "presentation/current", "presentation/focused",
    "presentation/slide_index", "presentation/active/timeline", "presentation/focused/timeline", "presentation/chord_chart/updates", "props",
    "prop_collections", "stage/message", "stage/layout_map", "stage/screens", "stage/layouts", "status/layers", "status/stage_screens",
    "status/audience_screens", "status/screens", "status/slide", "timers", "timers/current", "timer/system_time", "timer/video_countdown",
    "transport/presentation/current", "transport/presentation/time", "transport/audio/time",
];

#[derive(Clone, Debug)]
pub struct Pres {
    pub uuid: String,
    pub name: String,
    pub path: String,
    pub groups: Vec<(String, Vec<String>)>,
    pub arrangement: Option<Vec<String>>,
}

/// `(uuid, name, groups)` of an announcement.
pub type Announcement = (String, String, Vec<(String, Vec<String>)>);

pub struct Inner {
    pub version: Value,
    pub presentation: Option<Pres>,
    pub index: Option<usize>,
    pub stage: String,
    pub beat: bool,
    pub refuse: bool,
    /// `(method, path, body)` of everything except the stream.
    pub requests: Vec<(String, String, Vec<u8>)>,
    pub thumb_calls: u32,
    pub msg_active: bool,
    pub timer_rows: Value,
    pub timer_cfg: Option<Value>,
    pub timer_cfg_calls: u32,
    pub announcement: Option<Announcement>,
    pub ann_index: Option<usize>,
    pub screens_audience: bool,
    pub screens_stage: bool,
    pub missing: HashSet<String>,
    pub notes: HashMap<usize, String>,
    pub stream_count: u32,
    pub stream_bodies: Vec<Vec<String>>,
    pub stale: Value,
    pub no_focus: bool,
    pub slide_override: Option<Value>,
    /// What `GET /v1/transport/presentation/time` and `.../current` answer; `None` is a 404.
    pub media: Option<(Value, Value)>,
    /// Paths that answer a status code of the test's choosing: `(path, status, body)`.
    pub forced: Vec<(String, u16, String)>,
}

pub struct Fake {
    inner: Mutex<Inner>,
    out: Mutex<VecDeque<Vec<u8>>>,
    wake: Notify,
    kill: AtomicU64,
    pub port: u16,
}

fn slide(text: &str) -> Value {
    json!({"text": text, "notes": "", "uuid": format!("S-{}", text.chars().take(4).collect::<String>())})
}

impl Inner {
    pub fn song(&mut self, name: &str, library: &str, groups: Vec<(&str, Vec<&str>)>) {
        self.presentation = Some(Pres {
            uuid: format!("P-{}", name.trim().chars().take(4).collect::<String>()),
            name: name.to_owned(),
            path: format!("/Users/x/Libraries/{library}/{name}.pro"),
            groups: groups.into_iter().map(|(n, g)| (n.to_owned(), g.into_iter().map(str::to_owned).collect())).collect(),
            arrangement: None,
        });
        self.index = Some(0);
    }

    pub fn default_song(&mut self) {
        self.song("Great Song  ", "Songs", vec![("Verse 1", vec!["line one\nline two", "line three"]), ("Chorus", vec!["chorus a"])]);
    }

    pub fn texts(&self) -> Vec<String> {
        self.presentation.as_ref().map(|p| p.groups.iter().flat_map(|(_, g)| g.clone()).collect()).unwrap_or_default()
    }

    pub fn status_slide(&self) -> Value {
        if let Some(o) = &self.slide_override {
            return o.clone();
        }
        let t = self.texts();
        let Some(i) = self.index.filter(|i| self.presentation.is_some() && *i < t.len()) else { return self.stale.clone() };
        let mut cur = slide(&t[i]);
        cur["notes"] = json!(self.notes.get(&i).cloned().unwrap_or_default());
        let next = t.get(i + 1).map(|x| {
            let mut n = slide(x);
            n["notes"] = json!(self.notes.get(&(i + 1)).cloned().unwrap_or_default());
            n
        });
        json!({"current": cur, "next": next})
    }

    pub fn clear(&mut self) {
        self.stale = self.status_slide(); // status/slide keeps its old text after a clear
        self.presentation = None;
        self.index = None;
    }

    fn slide_no(&self, gi: usize, si: usize) -> usize {
        self.presentation.as_ref().map_or(0, |p| p.groups[..gi].iter().map(|(_, g)| g.len()).sum::<usize>() + si)
    }

    fn current_body(&self) -> Value {
        json!({"presentation": self.presentation.as_ref().map(|p| json!({"id": {"uuid": p.uuid, "name": p.name, "index": 0}}))})
    }

    fn index_body(&self) -> Value {
        match (&self.presentation, self.index) {
            (Some(p), Some(i)) => json!({"presentation_index": {"index": i, "presentation_id": {"uuid": p.uuid, "name": p.name, "index": 0}}}),
            _ => json!({"presentation_index": null}),
        }
    }

    fn detail(&self) -> Value {
        let Some(p) = &self.presentation else { return Value::Null };
        let groups: Vec<Value> = p
            .groups
            .iter()
            .enumerate()
            .map(|(gi, (n, g))| {
                json!({"name": n, "uuid": format!("G-{n}"), "color": {"red": 1.0, "green": 0.0, "blue": 0.5, "alpha": 1},
                       "slides": g.iter().enumerate().map(|(si, t)| json!({"text": t, "notes": self.notes.get(&self.slide_no(gi, si)).cloned().unwrap_or_default()})).collect::<Vec<_>>()})
            })
            .collect();
        let mut d = json!({"id": {"uuid": p.uuid, "name": p.name, "index": 0}, "presentation_path": p.path, "groups": groups});
        if let Some(arr) = &p.arrangement {
            d["arrangements"] = json!([{"id": {"uuid": "ARR", "name": "Master", "index": 0}, "groups": arr.iter().map(|n| format!("G-{n}")).collect::<Vec<_>>()}]);
            d["current_arrangement"] = json!("ARR");
        }
        json!({"presentation": d})
    }

    fn announcement_body(&self) -> Value {
        match &self.announcement {
            None => json!({"announcement": null}),
            Some((uuid, name, groups)) => json!({"announcement": {
                "id": {"uuid": uuid, "name": name, "index": 0},
                "groups": groups.iter().map(|(n, g)| json!({"name": n, "color": {"red": 0, "green": 1, "blue": 0, "alpha": 1},
                    "slides": g.iter().map(|t| json!({"text": t, "notes": format!("ann note {t}"), "label": "", "enabled": true})).collect::<Vec<_>>()})).collect::<Vec<_>>()}}),
        }
    }

    fn announcement_index_body(&self) -> Value {
        match (&self.announcement, self.ann_index) {
            (Some((uuid, name, _)), Some(i)) => json!({"announcement_index": {"index": i, "presentation_id": {"uuid": uuid, "name": name, "index": 0}}}),
            _ => json!({"announcement_index": null}),
        }
    }

    fn screens_body(&self) -> Value {
        json!([{"id": {"uuid": "SCR", "name": "Screen", "index": 0}, "size": {"width": 1920, "height": 1080}, "screen_type": "audience"}])
    }

    /// `(status, body, content type)` for the control paths, or `None`.
    pub fn extra_get(&mut self, path: &str) -> Option<(u16, Vec<u8>, &'static str)> {
        let base = path.split('?').next().unwrap_or("");
        let j = |o: Value| Some((200u16, format!("{o}\r\n\r\n").into_bytes(), "application/json"));
        match base {
            "/v1/announcement/active" => return j(self.announcement_body()),
            "/v1/announcement/slide_index" => return j(self.announcement_index_body()),
            "/v1/status/screens" => return j(self.screens_body()),
            "/v1/status/audience_screens" => return j(json!(self.screens_audience)),
            "/v1/status/stage_screens" => return j(json!(self.screens_stage)),
            "/v1/playlist/PL3" => {
                return j(json!({"id": {"uuid": "PL3", "name": "Setlist", "index": 2}, "items": [
                    {"id": {"uuid": "H0", "name": "Worship", "index": 0}, "type": "header", "is_hidden": false, "is_pco": false, "header_color": {"red": 0, "green": 0, "blue": 1, "alpha": 1}},
                    {"id": {"uuid": "S1", "name": "Great Song", "index": 1}, "type": "presentation", "is_hidden": false, "is_pco": true,
                     "presentation_info": {"presentation_uuid": "P-Grea", "arrangement_name": "Master", "arrangement_uuid": "ARR"}},
                    {"id": {"uuid": "S2", "name": "Gone Song", "index": 2}, "type": "presentation", "is_hidden": true, "is_pco": false,
                     "presentation_info": {"presentation_uuid": "P-GONE", "arrangement_name": "", "arrangement_uuid": ""}},
                    {"id": {"uuid": "S3", "name": "Empty slot", "index": 3}, "type": "placeholder", "is_hidden": false, "is_pco": false},
                    {"id": {"uuid": "S4", "name": "Unlinked", "index": 4}, "type": "presentation", "is_hidden": false, "is_pco": false, "presentation_info": {"presentation_uuid": ""}},
                    {"id": {"uuid": "H1", "name": "Sermon", "index": 5}, "type": "header", "is_hidden": false, "is_pco": false, "header_color": {"red": 1, "green": 0, "blue": 0, "alpha": 1}}]}))
            }
            "/v1/playlists" => {
                return j(json!([{"id": {"uuid": "PL1", "name": "Sunday", "index": 0}, "field_type": "playlist", "children": []},
                    {"id": {"uuid": "GRP", "name": "Folder", "index": 1}, "field_type": "group",
                     "children": [{"id": {"uuid": "PL2", "name": "Youth", "index": 0}, "field_type": "playlist", "children": []}]}]))
            }
            "/v1/playlist/PL1" => {
                return j(json!({"id": {"uuid": "PL1", "name": "Sunday", "index": 0}, "items": [
                    {"id": {"uuid": "I0", "name": "Welcome", "index": 0}, "type": "header", "is_hidden": false, "header_color": {"red": 1, "green": 0, "blue": 0, "alpha": 1}},
                    {"id": {"uuid": "I1", "name": "Great Song", "index": 1}, "type": "presentation", "is_hidden": false, "presentation_info": {"presentation_uuid": "P-Grea"}}]}))
            }
            "/v1/macros" => {
                return j(json!([{"id": {"uuid": "M1", "name": "Lights up", "index": 0}, "color": {"red": 0, "green": 1, "blue": 0}},
                    {"id": {"uuid": "M2", "name": "Reboot", "index": 1}, "color": {"red": 1, "green": 0, "blue": 0}}]))
            }
            "/v1/looks" => {
                return j(json!([{"id": {"uuid": "L1", "name": "Default", "index": 0}, "screens": []}, {"id": {"uuid": "L2", "name": "Sermon", "index": 1}, "screens": []}]))
            }
            "/v1/timers/current" => return j(self.timer_rows.clone()),
            "/v1/timers" => {
                self.timer_cfg_calls += 1;
                return j(self.timer_cfg.clone().unwrap_or_else(|| json!([{"id": {"uuid": "T1", "name": "Segment", "index": 0}, "countdown": {"duration": 300}}])));
            }
            "/v1/messages" => {
                return j(json!([{"id": {"uuid": "MSG1", "name": "Nursery", "index": 0}, "message": "Room {room}",
                    "tokens": [{"name": "room", "text": {"text": "3"}}], "is_active": self.msg_active}]))
            }
            _ => {}
        }
        if base.contains("/thumbnail/") {
            self.thumb_calls += 1;
            let mut body = b"\xff\xd8JPEG".to_vec();
            body.extend_from_slice(base.as_bytes());
            return Some((200, body, "image/jpeg"));
        }
        if base.starts_with("/v1/playlist/focused/") && self.no_focus {
            return Some((404, Vec::new(), "application/json"));
        }
        if base.ends_with("/trigger") || base.contains("/clear") || base.starts_with("/v1/timer/") || base.starts_with("/v1/timers/") {
            if base.starts_with("/v1/message/") && base.ends_with("/clear") {
                self.msg_active = false;
            }
            return Some((204, Vec::new(), "application/json"));
        }
        None
    }

    fn extra_write(&mut self, method: &str, path: &str, raw: &[u8]) -> Option<(u16, Vec<u8>, &'static str)> {
        let empty = Some((204u16, Vec::new(), "application/json"));
        if method == "POST" && path == "/v1/message/MSG1/trigger" {
            self.msg_active = true;
            return empty;
        }
        if method == "PUT" && path == "/v1/transport/presentation/time" {
            return empty;
        }
        if method == "PUT" && (path == "/v1/status/audience_screens" || path == "/v1/status/stage_screens") {
            let Ok(Value::Bool(b)) = serde_json::from_slice::<Value>(raw) else { return Some((400, b"bad body".to_vec(), "text/plain")) };
            if path.ends_with("audience_screens") {
                self.screens_audience = b;
            } else {
                self.screens_stage = b;
            }
            return empty;
        }
        None
    }

    fn stream_message_for(&mut self, url: &str) -> Option<Vec<u8>> {
        let data = match url {
            "status/slide" => self.status_slide(),
            "presentation/current" => self.current_body(),
            "presentation/slide_index" => self.index_body(),
            "stage/message" => json!(self.stage),
            "timers/current" => self.timer_rows.clone(),
            "status/layers" => json!({"slide": self.presentation.is_some()}),
            "playlist/active" => json!({}),
            "look/current" => json!({"id": {"name": "Default"}}),
            "announcement/active" => self.announcement_body(),
            "announcement/slide_index" => self.announcement_index_body(),
            "status/screens" => self.screens_body(),
            "status/audience_screens" => json!(self.screens_audience),
            "status/stage_screens" => json!(self.screens_stage),
            _ => return None,
        };
        Some(format!("{}\r\n\r\n", json!({"url": url, "data": data})).into_bytes())
    }
}

impl Fake {
    pub async fn start() -> Arc<Fake> {
        let listener = TcpListener::bind("127.0.0.1:0").await.expect("bind");
        let port = listener.local_addr().expect("addr").port();
        let fake = Arc::new(Fake {
            inner: Mutex::new(Inner {
                version: json!({"name": "Studio-Mac", "platform": "mac", "os_version": "26.5.2", "host_description": "ProPresenter 22", "api_version": "v1"}),
                presentation: None,
                index: None,
                stage: String::new(),
                beat: true,
                refuse: false,
                requests: Vec::new(),
                thumb_calls: 0,
                msg_active: false,
                timer_rows: json!([]),
                timer_cfg: None,
                timer_cfg_calls: 0,
                announcement: None,
                ann_index: None,
                screens_audience: false,
                screens_stage: false,
                missing: HashSet::new(),
                notes: HashMap::new(),
                stream_count: 0,
                stream_bodies: Vec::new(),
                stale: Value::Null,
                no_focus: false,
                slide_override: None,
                media: None,
                forced: Vec::new(),
            }),
            out: Mutex::new(VecDeque::new()),
            wake: Notify::new(),
            kill: AtomicU64::new(0),
            port,
        });
        let f = Arc::clone(&fake);
        tokio::spawn(async move {
            loop {
                let Ok((sock, _)) = listener.accept().await else { return };
                let f = Arc::clone(&f);
                tokio::spawn(async move { f.serve(sock).await });
            }
        });
        fake
    }

    pub fn lock(&self) -> MutexGuard<'_, Inner> {
        self.inner.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// Send stream messages (the current state of each path), as ProPresenter pushes them.
    pub fn push(&self, urls: &[&str]) {
        let mut inner = self.lock();
        let msgs: Vec<Vec<u8>> = urls.iter().filter_map(|u| inner.stream_message_for(u)).collect();
        drop(inner);
        self.out.lock().unwrap_or_else(std::sync::PoisonError::into_inner).extend(msgs);
        self.wake.notify_waiters();
    }

    /// Drop every live stream connection now.
    pub fn kill_streams(&self) {
        self.kill.fetch_add(1, Ordering::SeqCst);
        self.wake.notify_waiters();
    }

    pub fn requests(&self, method: &str) -> Vec<(String, Vec<u8>)> {
        self.lock().requests.iter().filter(|r| r.0 == method).map(|r| (r.1.clone(), r.2.clone())).collect()
    }

    async fn serve(self: Arc<Self>, mut sock: TcpStream) {
        let mut buf = Vec::new();
        let mut tmp = [0u8; 4096];
        let head_end = loop {
            if let Some(at) = buf.windows(4).position(|w| w == b"\r\n\r\n") {
                break at;
            }
            match sock.read(&mut tmp).await {
                Ok(0) | Err(_) => return,
                Ok(n) => buf.extend_from_slice(&tmp[..n]),
            }
        };
        let head = String::from_utf8_lossy(&buf[..head_end]).into_owned();
        let mut lines = head.split("\r\n");
        let mut first = lines.next().unwrap_or("").split(' ');
        let (method, path) = (first.next().unwrap_or("").to_owned(), first.next().unwrap_or("").to_owned());
        let len: usize = lines.filter_map(|l| l.split_once(':')).find(|(k, _)| k.eq_ignore_ascii_case("content-length")).and_then(|(_, v)| v.trim().parse().ok()).unwrap_or(0);
        let mut body = buf[head_end + 4..].to_vec();
        while body.len() < len {
            match sock.read(&mut tmp).await {
                Ok(0) | Err(_) => return,
                Ok(n) => body.extend_from_slice(&tmp[..n]),
            }
        }
        if method == "POST" && path == "/v1/status/updates" {
            self.stream(sock, &body).await;
            return;
        }
        let (status, payload, ctype) = self.answer(&method, &path, &body);
        let reply = format!("HTTP/1.1 {status} X\r\nContent-Type: {ctype}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n", payload.len());
        let _ = sock.write_all(reply.as_bytes()).await;
        let _ = sock.write_all(&payload).await;
        let _ = sock.shutdown().await;
    }

    fn answer(&self, method: &str, path: &str, body: &[u8]) -> (u16, Vec<u8>, &'static str) {
        let mut inner = self.lock();
        inner.requests.push((method.to_owned(), path.to_owned(), body.to_vec()));
        if let Some((_, status, text)) = inner.forced.iter().find(|(p, _, _)| p == path).cloned() {
            return (status, text.into_bytes(), "text/plain");
        }
        let j = |o: &Value| (200u16, if o.is_null() { Vec::new() } else { format!("{o}\r\n\r\n").into_bytes() }, "application/json");
        match method {
            "GET" => {
                let x = if path == "/v1/trigger/next" || path == "/v1/trigger/previous" { None } else { inner.extra_get(path) };
                if let Some(x) = x.filter(|_| !(path.starts_with("/v1/presentation/") && !path.contains("/thumbnail/") && !path.contains("/trigger"))) {
                    return x;
                }
                match path {
                    "/version" => j(&inner.version.clone()),
                    "/v1/status/slide" => j(&inner.status_slide()),
                    "/v1/presentation/current" => j(&inner.current_body()),
                    "/v1/presentation/slide_index" => j(&inner.index_body()),
                    "/v1/stage/message" => j(&json!(inner.stage)),
                    "/v1/transport/presentation/time" => inner.media.clone().map_or((404, Vec::new(), "application/json"), |m| j(&m.0)),
                    "/v1/transport/presentation/current" => inner.media.clone().map_or((404, Vec::new(), "application/json"), |m| j(&m.1)),
                    p if p.starts_with("/v1/presentation/") && inner.missing.contains(p.rsplit('/').next().unwrap_or("")) => (404, Vec::new(), "application/json"),
                    p if p.starts_with("/v1/presentation/") && inner.presentation.is_some() => j(&inner.detail()),
                    p if p.starts_with("/v1/trigger/") || p.starts_with("/v1/clear/") => (204, Vec::new(), "application/json"),
                    _ => (404, Vec::new(), "application/json"),
                }
            }
            "PUT" => {
                if let Some(x) = inner.extra_write("PUT", path, body) {
                    return x;
                }
                if path == "/v1/stage/message" {
                    let Ok(value) = serde_json::from_slice::<Value>(body) else { return (400, b"bad json".to_vec(), "application/json") };
                    let Some(text) = value.as_str() else { return (400, b"Json deserialize error: invalid type: map, expected a string".to_vec(), "application/json") };
                    inner.stage = text.to_owned();
                    drop(inner);
                    self.push(&["stage/message"]);
                    return (204, Vec::new(), "application/json");
                }
                (404, Vec::new(), "application/json")
            }
            "DELETE" => {
                if path == "/v1/stage/message" {
                    inner.stage.clear();
                    drop(inner);
                    self.push(&["stage/message"]);
                    return (204, Vec::new(), "application/json");
                }
                (404, Vec::new(), "application/json")
            }
            _ => inner.extra_write(method, path, body).unwrap_or((404, Vec::new(), "application/json")),
        }
    }

    async fn stream(self: Arc<Self>, mut sock: TcpStream, body: &[u8]) {
        let paths: Vec<String> = serde_json::from_slice::<Vec<String>>(body).unwrap_or_default();
        let generation = self.kill.load(Ordering::SeqCst);
        enum Plan {
            Refuse,
            Bad(String),
            Go(Vec<u8>),
        }
        let plan = {
            let mut inner = self.lock();
            inner.stream_bodies.push(paths.clone());
            inner.stream_count += 1;
            if inner.refuse {
                Plan::Refuse
            } else if let Some(bad) = paths.iter().find(|p| !ACCEPTED.contains(&p.as_str())) {
                Plan::Bad(json!([format!("URL: {bad}. Error: 404 Not Found")]).to_string())
            } else {
                Plan::Go(paths.iter().filter_map(|p| inner.stream_message_for(p)).flatten().collect::<Vec<u8>>())
            }
        };
        let first = match plan {
            Plan::Bad(msg) => {
                let reply = format!("HTTP/1.1 400 X\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{msg}", msg.len());
                let _ = sock.write_all(reply.as_bytes()).await;
                return;
            }
            Plan::Refuse => Vec::new(),
            Plan::Go(first) => first,
        };
        let refuse = first.is_empty();
        if refuse {
            let reply = "HTTP/1.1 503 X\r\nContent-Length: 4\r\nConnection: close\r\n\r\nbusy";
            let _ = sock.write_all(reply.as_bytes()).await;
            return;
        }
        let head = "HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nTransfer-Encoding: chunked\r\nConnection: close\r\n\r\n";
        if sock.write_all(head.as_bytes()).await.is_err() {
            return;
        }
        // The current state of every requested path arrives at once, as one message cut across two HTTP chunks and two messages sharing a chunk.
        let cut = first.len() / 3;
        if chunk(&mut sock, &first[..cut]).await.is_err() || chunk(&mut sock, &first[cut..]).await.is_err() {
            return;
        }
        let mut last_beat = tokio::time::Instant::now() - Duration::from_secs(10);
        loop {
            if self.kill.load(Ordering::SeqCst) != generation {
                return;
            }
            let next = self.out.lock().unwrap_or_else(std::sync::PoisonError::into_inner).pop_front();
            match next {
                Some(data) => {
                    let mid = data.len() / 2;
                    if chunk(&mut sock, &data[..mid]).await.is_err() {
                        return;
                    }
                    tokio::time::sleep(Duration::from_millis(10)).await;
                    if chunk(&mut sock, &data[mid..]).await.is_err() {
                        return;
                    }
                }
                None => {
                    let _ = tokio::time::timeout(Duration::from_millis(50), self.wake.notified()).await;
                }
            }
            let beating = self.lock().beat;
            if beating && last_beat.elapsed() > Duration::from_millis(200) {
                last_beat = tokio::time::Instant::now();
                let beat = format!("{}\r\n\r\n", json!({"url": "timer/system_time", "data": 1_760_000_000}));
                if chunk(&mut sock, beat.as_bytes()).await.is_err() {
                    return;
                }
            }
        }
    }
}

async fn chunk(sock: &mut TcpStream, data: &[u8]) -> std::io::Result<()> {
    if data.is_empty() {
        return Ok(());
    }
    sock.write_all(format!("{:x}\r\n", data.len()).as_bytes()).await?;
    sock.write_all(data).await?;
    sock.write_all(b"\r\n").await?;
    sock.flush().await
}

// ------------------------------------------------------------------------------------------------------------------------ the rig
use axum::body::Body;
use axum::http::request::Parts;
use axum::http::{Method, Request, StatusCode};
use axum::response::Response;
use http_body_util::BodyExt;
use relay_core::future::BoxFuture;
use relay_core::{Authorizer, Host, Module, SettingsStore, Shutdown};
use relay_module_propresenter::follower::Timing;
use relay_module_propresenter::ProPresenter;
use tower::ServiceExt;

pub struct Store(pub Mutex<HashMap<String, Value>>);

impl SettingsStore for Store {
    fn read(&self, key: &str) -> Option<Value> {
        self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner).get(key).cloned()
    }
    fn save(&self, key: &str, changes: &Value) -> Result<(), relay_core::AppError> {
        let mut m = self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        let mut cur = m.get(key).cloned().unwrap_or_else(|| json!({}));
        for (k, v) in changes.as_object().into_iter().flatten() {
            cur[k] = v.clone();
        }
        m.insert(key.to_owned(), cur);
        Ok(())
    }
    fn replace(&self, key: &str, value: &Value) -> Result<(), relay_core::AppError> {
        self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner).insert(key.to_owned(), value.clone());
        Ok(())
    }
    fn add_secret(&self, _key: &str, _field: &str) {}
}

#[derive(Default)]
pub struct Gate {
    pub refuse: Mutex<Vec<&'static str>>,
}

impl Authorizer for Gate {
    fn require<'a>(&'a self, _r: &'a Parts, permission: &'static str) -> BoxFuture<'a, Result<(), Response>> {
        Box::pin(async move {
            if self.refuse.lock().unwrap_or_else(std::sync::PoisonError::into_inner).contains(&permission) {
                let mut r = Response::new(Body::empty());
                *r.status_mut() = StatusCode::FORBIDDEN;
                return Err(r);
            }
            Ok(())
        })
    }
}

pub struct Rig {
    pub module: Arc<ProPresenter>,
    pub host: Host,
    pub store: Arc<Store>,
    pub gate: Arc<Gate>,
    pub fake: Arc<Fake>,
}

/// Quick timings so the stall and backoff paths run in seconds, not minutes.
pub fn quick() -> Timing {
    Timing { resync: Duration::from_millis(200), stall: Duration::from_millis(1500), backoff: vec![Duration::from_millis(100), Duration::from_millis(200)] }
}

pub async fn rig() -> Rig {
    let fake = Fake::start().await;
    let store = Arc::new(Store(Mutex::new(HashMap::new())));
    let host = Host::new(Shutdown::new()).with_store(store.clone());
    let gate = Arc::new(Gate::default());
    host.set_authorizer(gate.clone());
    let module = Arc::new(ProPresenter::new().with_timing(quick()));
    module.attach(&host);
    Rig { module, host, store, gate, fake }
}

impl Rig {
    /// Save the settings a switched-on install has, with `extra` on top, in place of what was saved.
    pub fn configure(&self, extra: Value) {
        let mut s = json!({"enabled": true, "host": "127.0.0.1", "port": self.fake.port, "songs_library": "Songs"});
        for (k, v) in extra.as_object().into_iter().flatten() {
            s[k] = v.clone();
        }
        self.store.replace("propresenter", &s).expect("replace");
    }

    /// The saved settings with `extra` on top (what a page that saves one switch does).
    pub fn amend(&self, extra: Value) {
        let mut s = self.store.read("propresenter").unwrap_or_else(|| json!({}));
        for (k, v) in extra.as_object().into_iter().flatten() {
            s[k] = v.clone();
        }
        self.store.replace("propresenter", &s).expect("replace");
    }

    pub async fn start(&self) {
        self.module.start(self.host.clone(), json!({})).await.expect("start");
    }

    pub fn ctx(&self) -> &relay_module_propresenter::routes::Ctx {
        self.module.ctx().expect("attached")
    }

    pub async fn send(&self, method: Method, uri: &str, body: Option<Value>) -> (StatusCode, Value, String) {
        let req = Request::builder().method(method).uri(uri).header("content-type", "application/json").body(body.map_or_else(Body::empty, |b| Body::from(b.to_string()))).expect("request");
        let res = self.module.routes().oneshot(req).await.expect("response");
        let status = res.status();
        let bytes = res.into_body().collect().await.expect("body").to_bytes();
        (status, serde_json::from_slice(&bytes).unwrap_or(Value::Null), String::from_utf8_lossy(&bytes).into_owned())
    }
}

/// Wait up to `secs` for `cond`; polls, never a fixed sleep.
pub async fn until(secs: f64, mut cond: impl FnMut() -> bool) -> bool {
    let end = tokio::time::Instant::now() + Duration::from_secs_f64(secs);
    while tokio::time::Instant::now() < end {
        if cond() {
            return true;
        }
        tokio::time::sleep(Duration::from_millis(30)).await;
    }
    cond()
}
