//! The web pages and YouTube links an operator saved for signage (`app/modules/signage_web/links.py`, #210), and what the player is told
//! about each. Links live in this module's own settings (config.json `signage_web`). A link's `ref` in a playlist is its id, so editing a
//! link's address changes every playlist that uses it.

use std::collections::HashSet;

use serde_json::{json, Value};
use url::Url;

pub const MAX_LINKS: usize = 200;
pub const YOUTUBE_HOSTS: [&str; 7] =
    ["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be", "www.youtube-nocookie.com", "youtube-nocookie.com"];
pub const EMBED_HOST: &str = "https://www.youtube-nocookie.com";
const STATES: [&str; 5] = ["ok", "blocked", "unsure", "gone", "offline"];

/// A link Relay refuses: a fixed sentence for the admin page.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Invalid(pub &'static str);

/// A YouTube address's video and playlist ids; either may be empty, never both.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Youtube {
    pub video: String,
    pub list: String,
}

fn id_like(s: &str, min: usize, max: usize) -> bool {
    (min..=max).contains(&s.len()) && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
}

/// What the operator typed, as a web address: a bare `example.org/menu` gets `https://`; other schemes, a user name or password,
/// whitespace and anything over 500 characters are refused.
fn clean_url(raw: &Value) -> Result<String, Invalid> {
    let typed = match raw {
        Value::String(s) => s.trim().to_owned(),
        Value::Null | Value::Bool(false) => String::new(),
        other => other.to_string(),
    };
    let mut url = typed;
    if !url.is_empty() && !url.contains("://") {
        let (head, rest) = url.split_once(':').map_or((url.as_str(), None), |(h, r)| (h, Some(r)));
        // "javascript:x" and "mailto:y" name a scheme; "example.org:8080/x" is a host and port.
        let names_scheme = rest.is_some_and(|r| !head.is_empty() && head.chars().all(char::is_alphabetic) && !r.starts_with(|c: char| c.is_ascii_digit()));
        if !names_scheme {
            url = format!("https://{url}");
        }
    }
    let wrong = Invalid("Enter a web address starting with http:// or https://.");
    if url.chars().count() > 500 || url.chars().any(char::is_whitespace) {
        return Err(wrong);
    }
    let parsed = Url::parse(&url).map_err(|_| wrong.clone())?;
    if !matches!(parsed.scheme(), "http" | "https") || parsed.host_str().is_none_or(str::is_empty) {
        return Err(wrong);
    }
    if !parsed.username().is_empty() || parsed.password().is_some() {
        return Err(Invalid("A web address with a user name or password in it can't be used."));
    }
    Ok(url)
}

fn host_of(url: &str) -> Option<String> {
    Url::parse(url).ok()?.host_str().map(str::to_lowercase)
}

/// The ids a YouTube watch, short, embed or playlist link names, else `None`. A YouTube "mix" (`list=RD…`) is not a playlist.
pub fn parse_youtube(url: &str) -> Option<Youtube> {
    let parts = Url::parse(url).ok()?;
    let host = parts.host_str()?.to_lowercase();
    if !YOUTUBE_HOSTS.contains(&host.as_str()) {
        return None;
    }
    let query = |key: &str| parts.query_pairs().find(|(k, v)| k == key && !v.is_empty()).map(|(_, v)| v.into_owned()).unwrap_or_default();
    let path: Vec<&str> = parts.path().split('/').filter(|p| !p.is_empty()).collect();
    let mut video = String::new();
    if host == "youtu.be" && !path.is_empty() {
        video = path[0].to_owned();
    } else if path.len() > 1 && matches!(path[0], "embed" | "shorts" | "live" | "v") {
        video = path[1].to_owned();
    } else if path.first() == Some(&"watch") {
        video = query("v");
    }
    let list = query("list");
    if !id_like(&video, 11, 11) {
        video.clear();
    }
    let list = if id_like(&list, 10, 64) && !list.starts_with("RD") { list } else { String::new() };
    if path.first() == Some(&"playlist") {
        video.clear();
    }
    (!video.is_empty() || !list.is_empty()).then_some(Youtube { video, list })
}

fn random_id() -> String {
    use rand::Rng;
    let mut rng = rand::thread_rng();
    (0..4).map(|_| format!("{:02x}", rng.gen::<u8>())).collect()
}

fn is_id(s: &str) -> bool {
    s.len() == 8 && s.bytes().all(|b| matches!(b, b'0'..=b'9' | b'a'..=b'f'))
}

/// One link from what an admin sent or the file holds. `existing` are the ids already taken, so a new id is not one of them.
pub fn clean_link(raw: &Value, existing: &HashSet<String>) -> Result<Value, Invalid> {
    let Some(raw) = raw.as_object() else { return Err(Invalid("A link must be an object.")) };
    let url = clean_url(raw.get("url").unwrap_or(&Value::Null))?;
    let yt = parse_youtube(&url);
    if yt.is_none() && host_of(&url).is_some_and(|h| YOUTUBE_HOSTS.contains(&h.as_str())) {
        return Err(Invalid("That YouTube address names no video or playlist."));
    }
    let mut id = raw.get("id").and_then(Value::as_str).filter(|i| is_id(i)).map(str::to_owned).unwrap_or_default();
    if id.is_empty() {
        id = random_id();
        while existing.contains(&id) {
            id = random_id();
        }
    }
    let name: String = raw.get("name").and_then(Value::as_str).unwrap_or("").split_whitespace().collect::<Vec<_>>().join(" ").chars().take(100).collect();
    let check = raw.get("check").filter(|c| c.is_object()).cloned().unwrap_or_else(|| json!({}));
    Ok(json!({"id": id, "kind": if yt.is_some() { "youtube" } else { "web" }, "url": url, "name": name, "check": check}))
}

/// The module's saved settings: each link cleaned, duplicates and bad rows dropped, at most [`MAX_LINKS`].
pub fn clean_stored(raw: &Value) -> Value {
    let mut out = Vec::new();
    let mut seen: HashSet<String> = HashSet::new();
    for item in raw.get("links").and_then(Value::as_array).into_iter().flatten().take(MAX_LINKS) {
        let Ok(mut link) = clean_link(item, &seen) else { continue };
        let id = link["id"].as_str().unwrap_or("").to_owned();
        if !seen.insert(id) {
            continue;
        }
        if link["name"].as_str().is_some_and(str::is_empty) {
            link["name"] = link["url"].clone();
        }
        let c = &link["check"];
        let state = c["state"].as_str().filter(|s| STATES.contains(s)).unwrap_or("");
        let text = |k: &str, n: usize| c[k].as_str().unwrap_or("").chars().take(n).collect::<String>();
        let check = json!({"state": state, "note": text("note", 200), "at": text("at", 30)});
        link["check"] = check;
        out.push(link);
    }
    json!({"links": out})
}

// ---------------------------------------------------------------- what the player is told

/// The embed address of a YouTube link, muted unless the screen has sound.
pub fn embed_url(url: &str, sound: bool) -> String {
    let yt = parse_youtube(url).unwrap_or(Youtube { video: String::new(), list: String::new() });
    let mut q: Vec<(&str, &str)> = vec![("autoplay", "1"), ("controls", "0"), ("rel", "0"), ("playsinline", "1"), ("enablejsapi", "1"), ("iv_load_policy", "3"), ("modestbranding", "1")];
    if !sound {
        q.push(("mute", "1"));
    }
    let path = if yt.video.is_empty() {
        q.push(("list", &yt.list));
        q.push(("loop", "1"));
        "/embed/videoseries".to_owned()
    } else {
        if !yt.list.is_empty() {
            q.push(("list", &yt.list));
        }
        format!("/embed/{}", yt.video)
    };
    let query: Vec<String> = q.iter().map(|(k, v)| format!("{k}={v}")).collect();
    format!("{EMBED_HOST}{path}?{}", query.join("&"))
}

/// A web page: shown as it is; the player probes the site's front page to know whether the screen can reach it.
pub fn resolve_web(url: &str) -> Value {
    let scheme = url.split_once("://").map_or("", |(s, _)| s).to_lowercase();
    let rest = url.split_once("://").map_or(url, |(_, r)| r);
    let netloc = rest.split(['/', '?', '#']).next().unwrap_or("");
    json!({"type": "page", "url": url, "probe": format!("{scheme}://{netloc}/")})
}

pub fn resolve_youtube(url: &str) -> Value {
    let yt = parse_youtube(url).unwrap_or(Youtube { video: String::new(), list: String::new() });
    let mut out = json!({"type": "page", "url": embed_url(url, false), "url_sound": embed_url(url, true), "probe": format!("{EMBED_HOST}/favicon.ico")});
    if !yt.video.is_empty() && yt.list.is_empty() {
        // A single video can end the item when it ends (a duration of 0); the player listens for YouTube saying so.
        out["post_message"] = json!({"event": "listening", "id": 1, "channel": "widget"});
        out["end_when"] = json!([{"event": "onStateChange", "info": 0}, {"event": "infoDelivery", "info.playerState": 0}]);
    }
    out
}

/// What the player shows for the saved link `link`.
pub fn resolve(link: &Value) -> Value {
    let url = link["url"].as_str().unwrap_or("");
    if link["kind"] == "youtube" { resolve_youtube(url) } else { resolve_web(url) }
}

/// The saved links of one signage source kind (`web_page` lists web links, `youtube` lists YouTube ones).
pub fn of_kind<'a>(saved: &'a Value, kind: &str) -> Vec<&'a Value> {
    let want = if kind == "youtube" { "youtube" } else { "web" };
    saved["links"].as_array().into_iter().flatten().filter(|l| l["kind"] == want).collect()
}

// ---------------------------------------------------------------- framing headers

/// `("ok" | "blocked" | "unsure", sentence)` from a response's framing headers (name, value pairs).
pub fn frame_verdict(headers: &[(String, String)]) -> (&'static str, &'static str) {
    fn named<'a>(headers: &'a [(String, String)], n: &'a str) -> impl Iterator<Item = &'a str> {
        headers.iter().filter(move |(k, _)| k.eq_ignore_ascii_case(n)).map(|(_, v)| v.as_str())
    }
    if named(headers, "x-frame-options").any(|v| !v.trim().is_empty()) {
        return ("blocked", "This site refuses to be shown inside another page (X-Frame-Options). Screens will stay blank.");
    }
    let csp = named(headers, "content-security-policy").collect::<Vec<_>>().join(" ");
    let lower = csp.to_ascii_lowercase();
    if let Some(at) = lower.find("frame-ancestors") {
        let rest = &csp[at + "frame-ancestors".len()..];
        let sources: Vec<&str> = rest.split(';').next().unwrap_or("").split_whitespace().collect();
        if sources.contains(&"*") {
            return ("ok", "The site allows being shown in a frame.");
        }
        if sources.is_empty() || sources.contains(&"'none'") || sources == ["'self'"] {
            return ("blocked", "This site refuses to be shown inside another page (frame-ancestors). Screens will stay blank.");
        }
        return ("unsure", "The site only allows certain sites to frame it. It may stay blank on screens.");
    }
    ("ok", "The site allows being shown in a frame.")
}
