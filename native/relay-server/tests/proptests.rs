//! The port rules against a plain model, on paths proptest builds from a grammar (quickcheck's random strings rarely look like
//! URLs). A failing case is shrunk to the smallest path that fails and saved in `proptest-regressions/`, to be committed.

use std::sync::Arc;

use proptest::prelude::*;
use relay_server::gate::{decoded_path, refuses, Kind, Prefixes};

fn prefixes() -> Prefixes {
    Prefixes { listener: vec!["/signage".to_owned()], panel: vec!["/crew".to_owned()] }
}

/// A path of one to four segments, each a few letters, digits, dots or escapes.
fn path() -> impl Strategy<Value = String> {
    proptest::collection::vec("[a-z0-9.]{0,6}|%[0-9A-F]{2}", 1..4).prop_map(|segs| format!("/{}", segs.join("/")))
}

/// The same path with its first letter written as an escape.
fn escape_first_letter(p: &str) -> String {
    match p.char_indices().find(|(_, c)| c.is_ascii_lowercase()) {
        Some((i, c)) => format!("{}%{:02X}{}", &p[..i], c as u32, &p[i + 1..]),
        None => p.to_owned(),
    }
}

proptest! {
    #[test]
    fn writing_a_letter_as_an_escape_changes_no_answer(p in path()) {
        let escaped = escape_first_letter(&p);
        for kind in [Kind::Viewer, Kind::Panel, Kind::Module(Arc::new(vec!["/signage".to_owned()]))] {
            prop_assert_eq!(refuses(&kind, &prefixes(), &decoded_path(&p)), refuses(&kind, &prefixes(), &decoded_path(&escaped)));
        }
    }

    #[test]
    fn the_viewer_port_refuses_admin_however_the_path_is_written(rest in "(/[a-z0-9]{0,6}){0,3}", which in 0usize..3) {
        let base = ["/admin", "/api/admin", "/home"][which];
        let plain = format!("{base}{rest}");
        prop_assert!(refuses(&Kind::Viewer, &prefixes(), &decoded_path(&plain)));
        prop_assert!(refuses(&Kind::Viewer, &prefixes(), &decoded_path(&escape_first_letter(&plain))));
    }

    #[test]
    fn decoding_twice_only_changes_a_path_that_still_holds_an_escape(p in path()) {
        let once = decoded_path(&p);
        if !once.contains('%') {
            prop_assert_eq!(decoded_path(&once), once);
        }
    }
}
