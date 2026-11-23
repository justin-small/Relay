#![no_main]
//! Any JSON a saved route could be: reading its key never panics, and a key that is accepted is one Python would accept
//! (`mac:` or `name:` then 1 to 200 characters, none of them control characters).

use libfuzzer_sys::fuzz_target;
use relay_server::netroute::route_key;

fuzz_target!(|data: &[u8]| {
    let Ok(value) = serde_json::from_slice::<serde_json::Value>(data) else { return };
    if let Some(key) = route_key(&value) {
        let rest = key.strip_prefix("mac:").or_else(|| key.strip_prefix("name:")).expect("a known prefix");
        assert!((1..=200).contains(&rest.chars().count()));
        assert!(!rest.chars().any(char::is_control));
    }
});
