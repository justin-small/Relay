use relay_module_ndi::discovery::{collect, parse_records, question, split_instance, visible, A, NDI, PTR, SRV, TXT};
use relay_module_ndi::settings::{clean_server, clean_settings, defaults, same_name, sdk_config, slug};
use serde_json::{json, Value};

fn name(n: &str) -> Vec<u8> {
    let mut o = Vec::new();
    for l in n.split('.') {
        o.push(l.len() as u8);
        o.extend(l.as_bytes());
    }
    o.push(0);
    o
}

fn rr(n: &str, t: u16, rdata: &[u8]) -> Vec<u8> {
    let mut o = name(n);
    o.extend(t.to_be_bytes());
    o.extend([0, 1, 0, 0, 0, 120]);
    o.extend((rdata.len() as u16).to_be_bytes());
    o.extend(rdata);
    o
}

fn message(records: Vec<Vec<u8>>) -> Vec<u8> {
    let mut m = vec![0, 0, 0x84, 0, 0, 0, 0, records.len() as u8, 0, 0, 0, 0];
    for r in records {
        m.extend(r);
    }
    m
}

#[test]
fn settings_defaults_match_python() {
    let d = defaults();
    assert_eq!(d["interval"], 15);
    assert_eq!(d["view_fps"], 15);
    assert_eq!(d["view_quality"], 60);
    assert_eq!(d["view_width"], 1280);
    assert_eq!(d["view_max"], 4);
    assert_eq!(d["mdns"], true);
    assert_eq!(d["out_size"], "1280x720");
    assert_eq!(d["watch"], json!({"on": false, "auto": true, "sources": [], "hold": 10, "schedule": "", "sound": true}));
    assert_eq!(d["routes"], json!([]));
}

#[test]
fn settings_are_clamped_and_deduplicated() {
    let c = clean_settings(&json!({
        "interval": 1, "view_fps": 99, "view_quality": 5, "view_width": 100, "view_max": 100,
        "extra_ips": ["10.0.0.5", " 10.0.0.5 ", "nope", "::1"], "groups": ["Cam A", "cam  a", "bad/group"],
        "servers": ["host", "host:5959", "host:6000", "bad host", "x:99999"], "mdns": "yes",
        "tally": {"Cam": 3, "": 1, "Other": 0, "Big": 1000}, "out_fps": 99, "out_size": "4k"
    }));
    assert_eq!(c["interval"], 5);
    assert_eq!((c["view_fps"].clone(), c["view_quality"].clone(), c["view_width"].clone(), c["view_max"].clone()), (json!(30), json!(20), json!(320), json!(8)));
    assert_eq!(c["extra_ips"], json!(["10.0.0.5", "::1"]));
    assert_eq!(c["groups"], json!(["Cam A"]));
    assert_eq!(c["servers"], json!(["host", "host:6000"]));
    assert_eq!(c["mdns"], true);
    assert_eq!(c["tally"], json!({"Cam": 3}));
    assert_eq!(c["out_fps"], 30);
    assert_eq!(c["out_size"], "1280x720");
}

#[test]
fn cleaning_twice_changes_nothing() {
    let raw = json!({"multiviews": [{"name": "  Main  wall ", "layout": "1+5", "boxes": [{"kind": "source", "source": "A (B)", "meter": true}]}],
                     "tests": [{"id": "abcd", "name": "Bars", "pattern": "grid", "level": -20, "fps": 90}],
                     "tests_on": ["abcd", "zzzz"], "router_outputs": [{"id": "ab12", "name": "Out 1", "audio": " src "}],
                     "bridge": [{"id": "ab12", "name": "Site", "address": "1.2.3.4", "role": "host", "port": 7000}],
                     "watch": {"on": true, "hold": 1000, "sources": ["a", "a", "b"]}});
    let once = clean_settings(&raw);
    assert_eq!(clean_settings(&once), once);
    assert_eq!(once["multiviews"][0]["id"], "main-wall");
    assert_eq!(once["multiviews"][0]["boxes"].as_array().map(Vec::len), Some(6));
    assert_eq!(once["tests"][0]["fps"], 60);
    assert_eq!(once["tests_on"], json!(["abcd"]));
    assert_eq!(once["bridge"][0]["port"], 7000);
    assert_eq!(once["watch"]["hold"], 300);
    assert_eq!(once["watch"]["sources"], json!(["a", "b"]));
}

#[test]
fn multiview_ids_are_unique() {
    let c = clean_settings(&json!({"multiviews": [{"name": "Wall"}, {"name": "Wall"}, {"name": ""}]}));
    assert_eq!(c["multiviews"].as_array().map(Vec::len), Some(2));
    assert_eq!(c["multiviews"][1]["id"], "wall-2");
}

#[test]
fn names_slugs_and_servers() {
    assert!(same_name("  Cam   One", "cam one"));
    assert!(!same_name("", ""));
    assert_eq!(slug("Main  Wall!", 30, "x"), "main-wall");
    assert_eq!(slug("!!!", 30, "multiview"), "multiview");
    assert_eq!(clean_server(&json!("a.b:5959")), "a.b");
    assert_eq!(clean_server(&json!("a.b:0")), "");
    assert_eq!(clean_server(&Value::Null), "");
}

#[test]
fn sdk_config_names_the_networks() {
    let c = sdk_config(&json!({"servers": ["a", "b:1"], "mdns": false, "groups": ["g"], "extra_ips": ["10.0.0.1"]}));
    assert_eq!(c["ndi"]["networks"]["discovery"], "a,b:1");
    assert_eq!(c["ndi"]["mdns"]["enable"], false);
    assert_eq!(c["ndi"]["groups"]["recv"], "g");
    assert_eq!(c["ndi"]["networks"]["ips"], "10.0.0.1");
}

#[test]
fn instance_names_are_read_loosely() {
    assert_eq!(split_instance("PTZ-1 (NDI HX2, )"), ("PTZ-1".into(), "NDI HX2".into()));
    assert_eq!(split_instance("Laptop (OBS)"), ("Laptop".into(), "OBS".into()));
    assert_eq!(split_instance("NoBrackets"), ("NoBrackets".into(), String::new()));
}

#[test]
fn an_announcement_becomes_a_source() {
    let inst = "STUDIO (Cam 1)._ndi._tcp.local";
    let mut srv = vec![0, 0, 0, 0, 0x17, 0x49];
    srv.extend(name("studio.local"));
    let mut txt = Vec::new();
    for t in ["groups=public,Booth", "discovery=5960"] {
        txt.push(t.len() as u8);
        txt.extend(t.as_bytes());
    }
    let msg = message(vec![rr(NDI, PTR, &name(inst)), rr(inst, SRV, &srv), rr(inst, TXT, &txt), rr("studio.local", A, &[192, 168, 1, 7])]);
    let records = parse_records(&msg).expect("well formed");
    let found = collect(&records);
    assert_eq!(found.len(), 1);
    let f = &found[0];
    assert_eq!((f.machine.as_str(), f.source.as_str(), f.address.as_str(), f.port), ("STUDIO", "Cam 1", "192.168.1.7", 5961));
    assert_eq!(f.groups, vec!["public".to_owned(), "Booth".to_owned()]);
    assert_eq!(visible(found.clone(), &["Other".to_owned()]).len(), 1, "public is always watched");
    assert!(visible(vec![relay_module_ndi::discovery::Found { groups: vec!["Booth".into()], ..f.clone() }], &["Other".to_owned()]).is_empty());
    assert!(visible(found.clone(), &[]).len() == 1);
}

#[test]
fn garbage_is_refused_not_trusted() {
    assert!(parse_records(&[0; 5]).is_none());
    let mut bad = message(vec![rr(NDI, PTR, &name("x._ndi._tcp.local"))]);
    bad.truncate(bad.len() - 3);
    assert!(parse_records(&bad).is_none());
    let looping = [0u8, 0, 0, 0, 0, 0, 0, 1, 0, 0, 0, 0, 0xC0, 12];
    assert!(parse_records(&looping).is_none());
    assert_eq!(&question(NDI, PTR, false)[..4], &[0, 0, 0, 0]);
}
