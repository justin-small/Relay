#![no_main]
//! Any request path: decoding never panics, and the port rules answer the same whether the path is written plainly or with every
//! byte percent-encoded (an encoded `/admin` must never get past the viewer port).

use std::sync::Arc;

use libfuzzer_sys::fuzz_target;
use relay_server::gate::{decoded_path, refuses, Kind, Prefixes};

fuzz_target!(|data: &[u8]| {
    let Ok(path) = std::str::from_utf8(data) else { return };
    let prefixes = Prefixes { listener: vec!["/signage".to_owned()], panel: vec!["/crew".to_owned()] };
    let own = Kind::Module(Arc::new(vec!["/signage".to_owned()]));
    let encoded: String = path.bytes().map(|b| format!("%{b:02X}")).collect();
    assert_eq!(decoded_path(&encoded), path);
    for kind in [Kind::Viewer, Kind::Panel, own] {
        assert_eq!(refuses(&kind, &prefixes, &decoded_path(&encoded)), refuses(&kind, &prefixes, path));
        let _ = refuses(&kind, &prefixes, &decoded_path(path));
    }
});
