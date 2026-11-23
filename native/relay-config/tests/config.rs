//! What relay-config promises (#656): config.json read, normalised and saved as `app/config.py` does, and secrets sealed in the
//! format of `app/secretbox.py`. Each test runs real code on a real folder. The Python side is checked with a value its own
//! `secretbox.seal` made (the golden below), so a drift in either server shows here.

use std::path::{Path, PathBuf};

use relay_config::secretbox::{is_sealed, kid};
use relay_config::{SecretBox, Settings, Store};
use serde_json::{json, Value};

/// Made by Python: `RELAY_KEY_STORE=file`, then `secretbox.seal("hunter-two é", "config:admin_token")`.
const GOLDEN_KEY: &str = "qWW0gSU4zZAFHiXEgI_TiQ54qoVtJkdnVSRLnXzqML8";
const GOLDEN_SEALED: &str = "enc:v1:2df06e2a:-t7L1arSYWFr3dFaAov9e2B4hPWw1YvTdmGLNjtFWiJt5rXhEqQeBqk";
const GOLDEN_PLAIN: &str = "hunter-two é";

struct Dir(PathBuf);

impl Dir {
    fn new(name: &str) -> Self {
        let dir = std::env::temp_dir().join(format!("relay-config-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        Dir(dir)
    }
    fn path(&self) -> &Path {
        &self.0
    }
    fn config(&self) -> PathBuf {
        self.0.join("config.json")
    }
}

impl Drop for Dir {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

fn settings(dir: &Dir) -> Settings {
    Settings::with_secretbox(&dir.config(), SecretBox::with_store(dir.path(), Store::File))
}

fn read(dir: &Dir) -> Value {
    serde_json::from_str(&std::fs::read_to_string(dir.config()).unwrap()).unwrap()
}

// ---- the key store ----------------------------------------------------------------------------------------------------------

#[test]
fn opens_what_python_sealed() {
    let dir = Dir::new("golden");
    std::fs::write(dir.path().join("secret.key"), format!("{GOLDEN_KEY}\n")).unwrap();
    let sb = SecretBox::with_store(dir.path(), Store::File);
    assert_eq!(sb.unseal(GOLDEN_SEALED, "config:admin_token").as_deref(), Some(GOLDEN_PLAIN));
    assert_eq!(sb.location(), "key file");
}

#[test]
fn a_value_is_bound_to_its_field() {
    let dir = Dir::new("purpose");
    std::fs::write(dir.path().join("secret.key"), format!("{GOLDEN_KEY}\n")).unwrap();
    let sb = SecretBox::with_store(dir.path(), Store::File);
    assert_eq!(sb.unseal(GOLDEN_SEALED, "config:openai_api_key"), None, "moved to another field");
}

#[test]
fn key_ids_match_pythons() {
    let key = relay_config_test_key();
    assert_eq!(kid(&key), "2df06e2a");
}

fn relay_config_test_key() -> [u8; 32] {
    use base64::Engine;
    let raw = base64::engine::general_purpose::URL_SAFE_NO_PAD.decode(GOLDEN_KEY).unwrap();
    <[u8; 32]>::try_from(raw).unwrap()
}

#[test]
fn seals_and_opens_its_own_and_makes_the_key_file_private() {
    let dir = Dir::new("own");
    let sb = SecretBox::with_store(dir.path(), Store::File);
    let sealed = sb.seal("a secret", "config:vercel_api_key").unwrap();
    assert!(is_sealed(&sealed) && sealed.starts_with("enc:v1:"));
    assert_ne!(sealed, sb.seal("a secret", "config:vercel_api_key").unwrap(), "a fresh nonce each time");
    assert_eq!(sb.unseal(&sealed, "config:vercel_api_key").as_deref(), Some("a secret"));
    assert_eq!(sb.seal("", "x").unwrap(), "");
    assert_eq!(sb.seal(&sealed, "config:vercel_api_key").unwrap(), sealed, "sealing a sealed value changes nothing");
    assert_eq!(sb.unseal("plain text", "x").as_deref(), Some("plain text"), "a hand-edited value reads as it is");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let mode = std::fs::metadata(dir.path().join("secret.key")).unwrap().permissions().mode() & 0o777;
        assert_eq!(mode, 0o600);
    }
    // A second SecretBox on the same folder reads the same key.
    let again = SecretBox::with_store(dir.path(), Store::File);
    assert_eq!(again.unseal(&sealed, "config:vercel_api_key").as_deref(), Some("a secret"));
}

#[test]
fn what_it_cannot_open_reads_as_not_set() {
    let dir = Dir::new("lost");
    let sb = SecretBox::with_store(dir.path(), Store::File);
    // sealed under a key this folder has never had
    assert_eq!(sb.unseal(GOLDEN_SEALED, "config:admin_token"), None);
    assert_eq!(sb.unseal("enc:v1:nonsense", "config:admin_token"), None);
    assert_eq!(sb.unseal("enc:v1:2df06e2a:!!!", "config:admin_token"), None);
}

#[test]
fn the_store_follows_the_environment_setting() {
    assert_eq!(Store::choose(Some("file")), Store::File);
    assert_eq!(Store::choose(Some(" KEYRING ")), Store::Keyring);
    let platform = if cfg!(any(target_os = "macos", target_os = "windows")) { Store::Keyring } else { Store::File };
    assert_eq!(Store::choose(None), platform);
    assert_eq!(Store::choose(Some("nonsense")), platform);
}

// ---- config.json ------------------------------------------------------------------------------------------------------------

#[test]
fn a_missing_file_is_the_defaults() {
    let dir = Dir::new("fresh");
    let cfg = settings(&dir).load().unwrap();
    assert_eq!(cfg["port"], 80);
    assert_eq!(cfg["admin_port"], 443);
    assert_eq!(cfg["host"], "0.0.0.0");
    assert_eq!(cfg["history_lines"], 40);
    assert_eq!(cfg["stream_captions"], true);
    assert_eq!(cfg["realtime"]["backend"], "scribe");
    assert_eq!(cfg["targets"]["SPANISH"]["enabled"], true);
    assert_eq!(cfg["targets"]["FRENCH"]["enabled"], false);
    assert!(cfg["targets"].get("ENGLISH").is_none(), "the source language is never a target");
    assert!(!dir.config().exists(), "reading writes nothing");
}

#[test]
fn clamps_as_python_does() {
    let dir = Dir::new("clamp");
    std::fs::write(
        dir.config(),
        json!({
            "default_font_px": 5, "history_lines": 9999, "audio_lane": 99, "port": "8080", "admin_port": "8080",
            "host": "  ", "admin_fqdn": " .relay.example. ", "source_language": "KLINGON",
            "realtime": {"segment_idle_s": 50, "segment_max_idle_s": 0.5, "noise_reduction": "sideways", "backend": "x".repeat(60),
                         "translation_mode": "realtime", "vad_mode": "server", "transcribe_model": null},
            "recording": {"enabled": 1, "dir": "  ", "keep_runs": 0},
            "targets": {"spanish": {"enabled": 1}, "KLINGON": {"enabled": true}, "GERMAN": null}
        })
        .to_string(),
    )
    .unwrap();
    let cfg = settings(&dir).load().unwrap();
    assert_eq!(cfg["default_font_px"], 16);
    assert_eq!(cfg["history_lines"], 500);
    assert_eq!(cfg["audio_lane"], 32);
    assert_eq!((cfg["port"].clone(), cfg["admin_port"].clone()), (json!(8080), json!(443)), "the panel is never on the viewer port");
    assert_eq!(cfg["host"], "0.0.0.0");
    assert_eq!(cfg["admin_fqdn"], "relay.example");
    assert_eq!(cfg["source_language"], "ENGLISH");
    let rt = &cfg["realtime"];
    assert_eq!(rt["segment_idle_s"], 10.0);
    assert_eq!(rt["segment_max_idle_s"], 10.0, "never below the idle time");
    assert!(rt["noise_reduction"].is_null());
    assert_eq!(rt["backend"].as_str().unwrap().len(), 40);
    assert!(rt.get("translation_mode").is_none() && rt.get("vad_mode").is_none());
    assert_eq!(rt["transcribe_model"], "", "an explicit null turns the source transcript off");
    assert_eq!(cfg["recording"], json!({"enabled": true, "dir": "recordings", "keep_runs": 20}), "0 is falsy, so the default");
    assert_eq!(cfg["targets"]["SPANISH"]["enabled"], true);
    assert_eq!(cfg["targets"]["GERMAN"]["enabled"], false);
    assert!(cfg["targets"].get("KLINGON").is_none());
    let order: Vec<&str> = cfg["targets"].as_object().unwrap().keys().map(String::as_str).take(3).collect();
    assert_eq!(order, ["SPANISH", "GERMAN", "PORTUGUESE"], "the file's targets first, then the rest in list order");
}

#[test]
fn the_admin_port_moves_off_a_viewer_port_of_443() {
    let dir = Dir::new("ports");
    std::fs::write(dir.config(), r#"{"port": 443, "admin_port": 443}"#).unwrap();
    let cfg = settings(&dir).load().unwrap();
    assert_eq!((cfg["port"].clone(), cfg["admin_port"].clone()), (json!(443), json!(8443)));
}

#[test]
fn a_file_that_is_not_an_object_stops_the_load_and_is_left_alone() {
    let dir = Dir::new("bad");
    std::fs::write(dir.config(), "[1, 2]").unwrap();
    assert!(settings(&dir).load().is_err());
    std::fs::write(dir.config(), "{ nope").unwrap();
    assert!(settings(&dir).load().is_err());
    assert_eq!(std::fs::read_to_string(dir.config()).unwrap(), "{ nope");
}

#[test]
fn blocks_other_modules_own_are_kept_as_stored() {
    let dir = Dir::new("kept");
    let boards = json!([{"slug": "main", "widgets": [1, 2, 3]}]);
    std::fs::write(
        dir.config(),
        json!({"boards": boards, "propresenter": {"host": "10.0.0.9", "future": true}, "something_newer": {"a": [1]}}).to_string(),
    )
    .unwrap();
    let s = settings(&dir);
    s.save(&json!({"port": 8000}), &[]).unwrap();
    let after = read(&dir);
    assert_eq!(after["boards"], boards);
    assert_eq!(after["propresenter"], json!({"host": "10.0.0.9", "future": true}));
    assert_eq!(after["something_newer"], json!({"a": [1]}));
    assert_eq!(after["port"], 8000);
}

#[test]
fn saves_atomically_and_privately() {
    let dir = Dir::new("save");
    let s = settings(&dir);
    s.ensure_file(None).unwrap();
    s.save(&json!({"history_lines": 80, "realtime": {"gate_silence": false}}), &[]).unwrap();
    let on_disk = read(&dir);
    assert_eq!(on_disk["history_lines"], 80);
    assert_eq!(on_disk["realtime"]["gate_silence"], false);
    assert_eq!(on_disk["realtime"]["backend"], "scribe", "nested objects merge");
    let names: Vec<String> = std::fs::read_dir(dir.path()).unwrap().map(|e| e.unwrap().file_name().to_string_lossy().into_owned()).collect();
    assert!(!names.iter().any(|n| n.ends_with(".tmp")), "no temp file is left: {names:?}");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(dir.config()).unwrap().permissions().mode() & 0o777, 0o600);
    }
}

#[test]
fn replace_lets_an_entry_be_removed() {
    let dir = Dir::new("replace");
    let s = settings(&dir);
    s.save(&json!({"panels": {"a": {"name": "A"}, "b": {"name": "B"}}}), &[]).unwrap();
    s.save(&json!({"panels": {"a": {"name": "A"}}}), &[]).unwrap();
    assert!(read(&dir)["panels"].get("b").is_some(), "a merge keeps what it is not told about");
    s.save(&json!({"panels": {"a": {"name": "A"}}}), &["panels"]).unwrap();
    assert!(read(&dir)["panels"].get("b").is_none());
}

#[test]
fn an_edit_on_disk_takes_effect_without_a_restart() {
    let dir = Dir::new("reload");
    let s = settings(&dir);
    s.save(&json!({"history_lines": 60}), &[]).unwrap();
    assert_eq!(s.get().unwrap()["history_lines"], 60);
    // the documented way to set the first admin token: edit the file by hand
    std::thread::sleep(std::time::Duration::from_millis(1100)); // allow-sleep: file times have a one second grain on some filesystems
    let mut edited = read(&dir);
    edited["history_lines"] = json!(70);
    std::fs::write(dir.config(), edited.to_string()).unwrap();
    assert_eq!(s.get().unwrap()["history_lines"], 70);
}

// ---- secrets in config.json -------------------------------------------------------------------------------------------------

#[test]
fn secrets_are_sealed_on_disk_and_plain_in_memory() {
    let dir = Dir::new("seal");
    let s = settings(&dir);
    s.add_secret("planningcenter", "app_secret");
    s.save(&json!({"openai_api_key": "sk-abcdef1234", "admin_token": "tok", "planningcenter": {"app_secret": "pc-secret", "app_id": "id"}}), &[]).unwrap();
    let disk = read(&dir);
    for (got, plain) in [(&disk["openai_api_key"], "sk-abcdef1234"), (&disk["admin_token"], "tok"), (&disk["planningcenter"]["app_secret"], "pc-secret")] {
        let text = got.as_str().unwrap();
        assert!(is_sealed(text) && !text.contains(plain), "{text}");
    }
    assert_eq!(disk["planningcenter"]["app_id"], "id", "a field that is not a secret stays readable");
    assert_eq!(disk["vercel_api_key"], "", "an empty secret stays empty");
    // a new Settings (a restart) opens them with the key file
    let restarted = settings(&dir);
    restarted.add_secret("planningcenter", "app_secret");
    let again = restarted.get().unwrap();
    assert_eq!(again["openai_api_key"], "sk-abcdef1234");
    assert_eq!(again["planningcenter"]["app_secret"], "pc-secret");
}

#[test]
fn a_secret_typed_by_hand_is_sealed_on_the_next_load() {
    let dir = Dir::new("hand");
    std::fs::write(dir.config(), r#"{"admin_token": "first-token"}"#).unwrap();
    let cfg = settings(&dir).get().unwrap();
    assert_eq!(cfg["admin_token"], "first-token");
    assert!(is_sealed(read(&dir)["admin_token"].as_str().unwrap()));
}

#[test]
fn opens_a_config_python_wrote() {
    let dir = Dir::new("pyconfig");
    std::fs::write(dir.path().join("secret.key"), format!("{GOLDEN_KEY}\n")).unwrap();
    std::fs::write(dir.config(), json!({"admin_token": GOLDEN_SEALED, "port": 8000}).to_string()).unwrap();
    let cfg = settings(&dir).get().unwrap();
    assert_eq!(cfg["admin_token"], GOLDEN_PLAIN);
}

#[test]
fn a_sealed_value_the_key_cannot_open_survives_a_save() {
    let dir = Dir::new("unopened");
    // sealed by another computer's key: this folder has a key of its own
    std::fs::write(dir.config(), json!({"admin_token": GOLDEN_SEALED}).to_string()).unwrap();
    let s = settings(&dir);
    assert_eq!(s.get().unwrap()["admin_token"], "", "it reads as not set");
    s.save(&json!({"history_lines": 50}), &[]).unwrap();
    assert_eq!(read(&dir)["admin_token"], GOLDEN_SEALED, "but a save does not wipe it");
    s.save(&json!({"admin_token": "new"}), &[]).unwrap();
    assert_ne!(read(&dir)["admin_token"], GOLDEN_SEALED, "a new value replaces it");
    assert_eq!(settings(&dir).get().unwrap()["admin_token"], "new");
}

#[test]
fn the_browser_view_has_no_secret() {
    let dir = Dir::new("redacted");
    let s = settings(&dir);
    s.save(&json!({"openai_api_key": "sk-abcdef1234", "admin_token": "tok", "backup_key": "bk"}), &[]).unwrap();
    let view = s.redacted().unwrap();
    for gone in ["openai_api_key", "vercel_api_key", "admin_token", "backup_key"] {
        assert!(view.get(gone).is_none(), "{gone}");
    }
    assert_eq!(view["openai_api_key_set"], true);
    assert_eq!(view["openai_api_key_hint"], "…1234");
    assert_eq!(view["vercel_api_key_set"], false);
    assert_eq!(view["vercel_api_key_hint"], "");
    assert_eq!(view["backup_passphrase_set"], true);
    assert_eq!(view["secret_store"], "key file");
    assert!(!view.to_string().contains("sk-abcdef1234"));
}

#[test]
fn first_run_makes_the_file_from_the_example_or_the_defaults() {
    let dir = Dir::new("first");
    let example = dir.path().join("config.example.json");
    std::fs::write(&example, r#"{"port": 8123}"#).unwrap();
    let s = settings(&dir);
    s.ensure_file(Some(&example)).unwrap();
    assert_eq!(read(&dir), json!({"port": 8123}));
    let dir2 = Dir::new("first2");
    settings(&dir2).ensure_file(None).unwrap();
    assert_eq!(read(&dir2)["history_lines"], 40);
}
