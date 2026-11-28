//! Properties that hold for any input: what a saved link may be, and which addresses are never fetched.

use std::collections::HashSet;
use std::net::{IpAddr, Ipv4Addr};

use quickcheck::{quickcheck, TestResult};
use relay_module_signage_web::check::is_public;
use relay_module_signage_web::links::{clean_link, clean_stored, frame_verdict, parse_youtube, YOUTUBE_HOSTS};
use serde_json::{json, Value};

quickcheck! {
    /// A link that survives cleaning is a plain web address with no user name, and cleaning it again changes nothing.
    fn a_cleaned_link_is_web_and_stays_cleaned(url: String, name: String) -> TestResult {
        let Ok(link) = clean_link(&json!({"url": url, "name": name}), &HashSet::new()) else { return TestResult::discard() };
        let u = link["url"].as_str().unwrap_or("");
        let plain = (u.starts_with("http://") || u.starts_with("https://")) && !u.contains(char::is_whitespace) && u.chars().count() <= 500;
        let again = clean_stored(&json!({"links": [link.clone()]}));
        TestResult::from_bool(plain && link["name"].as_str().unwrap_or("").chars().count() <= 100 && again["links"][0]["id"] == link["id"] && again["links"][0]["url"] == link["url"])
    }

    /// The saved list is never longer than the limit and holds no id twice, whatever the file says.
    fn the_saved_list_is_bounded_and_has_unique_ids(urls: Vec<String>) -> bool {
        let rows: Vec<Value> = urls.iter().map(|u| json!({"url": u, "id": "aaaaaaaa"})).collect();
        let out = clean_stored(&json!({"links": rows}));
        let ids: Vec<&str> = out["links"].as_array().unwrap().iter().map(|l| l["id"].as_str().unwrap()).collect();
        ids.len() <= 200 && ids.iter().collect::<HashSet<_>>().len() == ids.len()
    }

    /// A video id is eleven characters and a playlist id is never a YouTube mix.
    fn youtube_ids_are_well_formed(path: String, query: String) -> bool {
        match parse_youtube(&format!("https://www.youtube.com/{path}?{query}")) {
            None => true,
            Some(y) => (y.video.is_empty() || y.video.len() == 11) && !y.list.starts_with("RD") && !(y.video.is_empty() && y.list.is_empty()),
        }
    }

    /// Another host is never YouTube.
    fn another_host_is_not_youtube(host: String) -> TestResult {
        if YOUTUBE_HOSTS.contains(&host.to_lowercase().as_str()) {
            return TestResult::discard();
        }
        TestResult::from_bool(parse_youtube(&format!("https://{host}/watch?v=dQw4w9WgXcQ")).is_none())
    }

    /// Private, loopback, link-local and shared addresses are never public.
    fn private_addresses_are_never_fetched(b: u8, c: u8, d: u8) -> bool {
        [Ipv4Addr::new(10, b, c, d), Ipv4Addr::new(127, b, c, d), Ipv4Addr::new(192, 168, c, d), Ipv4Addr::new(169, 254, c, d), Ipv4Addr::new(172, 16 + b % 16, c, d), Ipv4Addr::new(100, 64 + b % 64, c, d)]
            .into_iter()
            .all(|ip| !is_public(IpAddr::V4(ip)) && !is_public(IpAddr::V6(ip.to_ipv6_mapped())))
    }

    /// Whatever headers arrive, the verdict is one of the three.
    fn a_framing_verdict_is_always_one_of_three(name: String, value: String) -> bool {
        ["ok", "blocked", "unsure"].contains(&frame_verdict(&[(name, value)]).0)
    }
}
