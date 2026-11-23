//! Properties of the port rules that must hold for any input, checked on random ones by quickcheck.

use std::sync::Arc;

use quickcheck::quickcheck;
use relay_server::announce::{clean_label, host_label};
use relay_server::gate::{decoded_path, is_admin_path, refuses, under, Kind, Prefixes, ADMIN_PREFIXES, INTERNAL_PREFIX};
use relay_server::listen::link;
use relay_server::netroute::route_key;
use serde_json::json;

fn prefixes() -> Prefixes {
    Prefixes { listener: vec!["/signage".to_owned()], panel: vec!["/crew".to_owned()] }
}

/// Every byte as `%XX`.
fn encode_all(s: &str) -> String {
    s.bytes().map(|b| format!("%{b:02X}")).collect()
}

quickcheck! {
    fn a_path_with_no_percent_is_read_as_it_is(s: String) -> bool {
        let plain = s.replace('%', "");
        decoded_path(&plain) == plain
    }

    fn a_fully_encoded_path_reads_back_as_the_original(s: String) -> bool {
        decoded_path(&encode_all(&s)) == s
    }

    fn a_prefix_is_under_itself_and_below_itself_but_not_a_longer_name(name: String) -> bool {
        let name: String = name.chars().filter(char::is_ascii_alphanumeric).collect();
        if name.is_empty() {
            return true; // "/" is not a prefix a module reserves; as in Python it would match every path
        }
        let p = vec![format!("/{name}")];
        under(&format!("/{name}"), &p) && under(&format!("/{name}/x"), &p) && !under(&format!("/{name}x"), &p)
    }

    fn the_apps_own_calls_are_refused_on_every_kind_of_socket(suffix: String) -> bool {
        let path = format!("{INTERNAL_PREFIX}/{suffix}");
        let module = Kind::Module(Arc::new(vec![INTERNAL_PREFIX.to_owned()]));
        [Kind::Viewer, Kind::Panel, module].iter().all(|k| refuses(k, &prefixes(), &path))
    }

    fn an_admin_path_never_answers_on_the_viewer_port(which: u8, suffix: String) -> bool {
        let base = ADMIN_PREFIXES[usize::from(which) % ADMIN_PREFIXES.len()];
        let path = format!("{base}/{suffix}");
        is_admin_path(&path) && refuses(&Kind::Viewer, &prefixes(), &path)
    }

    fn a_module_port_answers_exactly_its_own_prefix(path: String) -> bool {
        let own = vec!["/signage".to_owned()];
        let kind = Kind::Module(Arc::new(own.clone()));
        let answers = under(&path, &own) && !under(&path, &[INTERNAL_PREFIX.to_owned()]);
        refuses(&kind, &prefixes(), &path) == !answers
    }

    fn the_panel_never_serves_what_belongs_to_a_module_port(suffix: String) -> bool {
        refuses(&Kind::Panel, &prefixes(), &format!("/signage/{suffix}"))
    }

    fn a_route_key_is_a_mac_or_name_id_without_control_characters(s: String) -> bool {
        match route_key(&json!(s)) {
            Some(k) => (k.starts_with("mac:") || k.starts_with("name:")) && k.len() <= 205 && !k.chars().any(char::is_control),
            None => true,
        }
    }

    fn a_link_leaves_out_only_the_default_port(port: u16) -> bool {
        let https = link("https", "relay.local", port, "/");
        let http = link("http", "relay.local", port, "/");
        (port == 443) == !https.contains(&format!(":{port}/")) && (port == 80) == !http.contains(&format!(":{port}/"))
    }

    fn an_install_name_is_one_short_printable_label(s: String) -> bool {
        let label = clean_label(&s);
        label.len() <= 63 && !label.contains('.') && !label.chars().any(char::is_control)
    }

    fn a_host_label_is_letters_digits_and_hyphens(s: String) -> bool {
        let label = host_label(&s);
        label.starts_with("relay-") && label.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
    }
}
