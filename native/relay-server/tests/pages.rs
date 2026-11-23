//! Pages and static files (#654): minijinja must produce what Jinja2 produced.
//!
//! `tests/pages/*.json` are renders the Python server made (`tools/capture_pages.py` records them while the Python tests run):
//! the template's name, the context it got and the HTML that came out. Each is rendered again here from the same context and
//! must match byte for byte, except where `tests/pages_allowed.txt` lists the file, with the reason, as a reviewed difference.

use std::fs;
use std::path::{Path, PathBuf};

use relay_server::pages::{static_file, static_url, Pages};
use serde_json::Value;

fn app_dir() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../../app")
}

fn captured() -> Vec<(String, Value)> {
    let dir = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/pages");
    let mut out: Vec<(String, Value)> = fs::read_dir(dir)
        .expect("tests/pages is there")
        .filter_map(|e| e.ok())
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .map(|e| (e.file_name().to_string_lossy().into_owned(), serde_json::from_str(&fs::read_to_string(e.path()).unwrap()).unwrap()))
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

/// `static()` links end in `?v=<the file's modification time>`, which is different on every machine (a CI checkout is newer than
/// the capture). The version's format is checked by `static_urls_carry_the_files_version`; here it is one fixed word.
fn without_versions(html: &str) -> String {
    let mut out = String::with_capacity(html.len());
    let mut rest = html;
    while let Some(at) = rest.find("?v=") {
        out.push_str(&rest[..at + 3]);
        rest = &rest[at + 3..];
        let hex = rest.bytes().take_while(|b| b.is_ascii_hexdigit()).count();
        out.push('V');
        rest = &rest[hex..];
    }
    out.push_str(rest);
    out
}

/// The first line where two texts differ, with a little around it.
fn first_difference(a: &str, b: &str) -> String {
    let at = a.bytes().zip(b.bytes()).position(|(x, y)| x != y).unwrap_or(a.len().min(b.len()));
    let from = (0..=at.saturating_sub(60)).rev().find(|i| a.is_char_boundary(*i) && b.is_char_boundary(*i)).unwrap_or(0);
    let to = |s: &str| (at + 60).min(s.len());
    let to_a = (0..=to(a)).rev().find(|i| a.is_char_boundary(*i)).unwrap_or(0);
    let to_b = (0..=to(b)).rev().find(|i| b.is_char_boundary(*i)).unwrap_or(0);
    format!("python: {:?}\nrust:   {:?}", &a[from..to_a], &b[from..to_b])
}

#[test]
fn every_captured_page_renders_as_jinja_did() {
    let pages = Pages::new(&app_dir());
    let allowed = fs::read_to_string(Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/pages_allowed.txt")).unwrap_or_default();
    let allowed: Vec<&str> = allowed.lines().map(|l| l.split('#').next().unwrap_or("").trim()).filter(|l| !l.is_empty()).collect();
    let all = captured();
    assert!(!all.is_empty(), "no captured pages: run tools/capture_pages.py");
    let mut failures = Vec::new();
    for (file, page) in &all {
        let (name, context, want) = (page["template"].as_str().unwrap(), &page["context"], page["html"].as_str().unwrap());
        if allowed.iter().any(|a| file.starts_with(a)) {
            continue;
        }
        match pages.render(name, context) {
            Ok(got) if without_versions(&got) == without_versions(want) => {}
            Ok(got) => failures.push(format!("{file}\n{}", first_difference(&without_versions(want), &without_versions(&got)))),
            Err(e) => failures.push(format!("{file}\n  {e:#}")),
        }
    }
    assert!(failures.is_empty(), "{} of {} pages differ:\n\n{}", failures.len(), all.len(), failures.join("\n\n"));
}

#[test]
fn every_template_is_covered_by_a_captured_page() {
    // A page is covered when a captured render used it: it was the page itself, or one the page included (a module's
    // admin page comes in through `admin.html`).
    let have: std::collections::BTreeSet<String> = captured()
        .iter()
        .flat_map(|(_, p)| p["uses"].as_array().unwrap().iter().map(|u| u.as_str().unwrap().to_owned()).collect::<Vec<_>>())
        .collect();
    let mut missing = Vec::new();
    let root = app_dir();
    let mut dirs = vec![(root.join("templates"), String::new())];
    for m in fs::read_dir(root.join("modules")).unwrap().flatten() {
        dirs.push((m.path().join("templates"), format!("{}/", m.file_name().to_string_lossy())));
    }
    for (dir, prefix) in dirs {
        let Ok(entries) = fs::read_dir(dir) else { continue };
        for e in entries.flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            // `_name.html` files are included by others, so they are covered by the pages that include them.
            // `crew/offline.html` has no Jinja in it: the Python route sends the file as it is, and so will the Rust one.
            let raw = prefix == "crew/" && n == "offline.html";
            if n.ends_with(".html") && !n.starts_with('_') && !raw && !have.contains(&format!("{prefix}{n}")) {
                missing.push(format!("{prefix}{n}"));
            }
        }
    }
    assert!(missing.is_empty(), "no captured render for: {missing:?}");
}

#[test]
fn static_urls_carry_the_files_version() {
    let root = app_dir();
    let url = static_url(&root, "relay-ui.js");
    assert!(url.starts_with("/static/relay-ui.js?v="), "{url}");
    assert!(url["/static/relay-ui.js?v=".len()..].chars().all(|c| c.is_ascii_hexdigit()), "{url}");
    assert_eq!(static_url(&root, "no-such-file.js"), "/static/no-such-file.js");
    assert!(static_file(&root, "../templates/admin.html").is_none());
    assert!(static_file(&root, "modules/../x").is_none());
}

mod serving {
    use super::app_dir;
    use axum::body::Body;
    use axum::http::{Request, StatusCode};
    use relay_server::statics;
    use tower::ServiceExt;

    async fn get(path: &str, inm: Option<&str>) -> axum::response::Response {
        let mut req = Request::builder().uri(path);
        if let Some(t) = inm {
            req = req.header("if-none-match", t);
        }
        statics::routes(&app_dir()).oneshot(req.body(Body::empty()).unwrap()).await.unwrap()
    }

    #[tokio::test]
    async fn a_static_file_is_served_with_revalidation_headers() {
        let res = get("/static/relay-ui.js", None).await;
        assert_eq!(res.status(), StatusCode::OK);
        assert_eq!(res.headers()["cache-control"], "no-cache");
        assert!(res.headers()["content-type"].to_str().unwrap().contains("javascript"));
        assert!(res.headers().contains_key("etag") && res.headers().contains_key("last-modified"));
    }

    #[tokio::test]
    async fn an_unchanged_file_is_a_304_by_its_etag() {
        let etag = get("/static/tokens.css", None).await.headers()["etag"].to_str().unwrap().to_owned();
        assert_eq!(get("/static/tokens.css", Some(&etag)).await.status(), StatusCode::NOT_MODIFIED);
        assert_eq!(get("/static/tokens.css", Some("\"other\"")).await.status(), StatusCode::OK);
    }

    #[tokio::test]
    async fn a_module_serves_its_own_static_files_and_nothing_outside() {
        assert_eq!(get("/static/modules/propresenter/ppsongs.js", None).await.status(), StatusCode::OK);
        assert_eq!(get("/static/modules/propresenter/nothing.js", None).await.status(), StatusCode::NOT_FOUND);
        assert_eq!(get("/static/modules/nope/x.js", None).await.status(), StatusCode::NOT_FOUND);
        assert_eq!(get("/static/..%2Ftemplates%2Fadmin.html", None).await.status(), StatusCode::NOT_FOUND);
        assert_eq!(get("/static/", None).await.status(), StatusCode::NOT_FOUND);
    }
}
