//! Secrets at rest (#278, ported by #656): one machine key kept outside relay.db and config.json, and AES-256-GCM with it.
//! The format is `app/secretbox.py`'s byte for byte, so either server opens what the other sealed:
//!
//! * a sealed value is `enc:v1:<kid>:<data>`, `data` being url-safe base64 without padding of `nonce(12) || ciphertext || tag(16)`;
//! * the purpose (`config:openai_api_key`) is the GCM associated data, so a value cannot be moved to another field;
//! * `kid` names the key: the first 4 bytes of the key's own GCM tag over nothing, under a zero nonce and the data
//!   `relay-key-id` (never a hash of the key: CodeQL reads any hash of a secret as weak password hashing);
//! * the key is 32 bytes, url-safe base64 without padding, kept in the login Keychain or Windows Credential Manager (service
//!   `Relay`, account `at-rest-key-<id>`, the id in `secret-key.id` beside config.json), or in `secret.key` (0600) in the state folder.
//!
//! `RELAY_KEY_STORE=file` forces the file, `keyring` forces the keyring; otherwise macOS and Windows use the keyring and
//! everything else the file. When the keyring cannot be used the file is, and [`SecretBox::location`] says so. A value that is
//! still plain text (a hand-edited config.json) is read as it is. A sealed value this computer cannot open reads as not set.

use std::collections::{HashMap, HashSet};
use std::path::{Path, PathBuf};
use std::sync::Mutex;

use aes_gcm::aead::{Aead, KeyInit, Payload};
use aes_gcm::{Aes256Gcm, Nonce};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine;
use rand::RngCore;

use crate::private;

pub const PREFIX: &str = "enc:v1:";
pub const KEY_FILE: &str = "secret.key";
pub const ID_FILE: &str = "secret-key.id";
#[cfg(any(target_os = "macos", target_os = "windows"))]
const SERVICE: &str = "Relay";
const KEY_ID_DATA: &[u8] = b"relay-key-id";

/// No key could be read or made.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Unavailable(pub &'static str);

impl std::fmt::Display for Unavailable {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(self.0)
    }
}

impl std::error::Error for Unavailable {}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Store {
    File,
    Keyring,
}

impl Store {
    /// `RELAY_KEY_STORE`, else the platform's own store (`secretbox.choice`).
    pub fn from_env() -> Self {
        Self::choose(std::env::var("RELAY_KEY_STORE").ok().as_deref())
    }

    pub fn choose(env: Option<&str>) -> Self {
        match env.map(|v| v.trim().to_lowercase()).as_deref() {
            Some("file") => Self::File,
            Some("keyring") => Self::Keyring,
            _ if cfg!(any(target_os = "macos", target_os = "windows")) => Self::Keyring,
            _ => Self::File,
        }
    }
}

struct Loaded {
    keys: HashMap<String, [u8; 32]>,
    primary: String,
    location: String,
}

pub struct SecretBox {
    dir: PathBuf,
    store: Store,
    loaded: Mutex<Option<Loaded>>,
    warned: Mutex<HashSet<String>>,
}

fn b64e(bytes: &[u8]) -> String {
    URL_SAFE_NO_PAD.encode(bytes)
}

/// Python's `urlsafe_b64decode(s + "=" * (-len(s) % 4))`: padding is optional on the way in.
fn b64d(text: &str) -> Option<Vec<u8>> {
    URL_SAFE_NO_PAD.decode(text.trim_end_matches('=')).ok()
}

/// A short, non-secret name for a key, so a sealed value says which key opens it.
pub fn kid(key: &[u8; 32]) -> String {
    let cipher = Aes256Gcm::new(key.into());
    let out = cipher.encrypt(Nonce::from_slice(&[0u8; 12]), Payload { msg: b"", aad: KEY_ID_DATA }).unwrap_or_default();
    out.iter().take(4).map(|b| format!("{b:02x}")).collect()
}

pub fn is_sealed(value: &str) -> bool {
    value.starts_with(PREFIX)
}

fn new_key() -> [u8; 32] {
    let mut key = [0u8; 32];
    rand::rngs::OsRng.fill_bytes(&mut key);
    key
}

fn random_hex(bytes: usize) -> String {
    let mut buf = vec![0u8; bytes];
    rand::rngs::OsRng.fill_bytes(&mut buf);
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

fn write_private(path: &Path, text: &str) -> std::io::Result<()> {
    if let Some(dir) = path.parent().filter(|d| !d.as_os_str().is_empty()) {
        private::make_private_dir(dir)?;
    }
    let name = path.file_name().map(|n| n.to_string_lossy().into_owned()).unwrap_or_default();
    let tmp = path.with_file_name(format!(".{name}.{}.tmp", random_hex(4)));
    private::write_private(path, &tmp, text)?;
    private::restrict(path);
    Ok(())
}

impl SecretBox {
    pub fn new(state_dir: &Path) -> Self {
        Self::with_store(state_dir, Store::from_env())
    }

    pub fn with_store(state_dir: &Path, store: Store) -> Self {
        Self { dir: state_dir.to_path_buf(), store, loaded: Mutex::new(None), warned: Mutex::new(HashSet::new()) }
    }

    fn file_key(&self, create: bool) -> Option<[u8; 32]> {
        let path = self.dir.join(KEY_FILE);
        match std::fs::read_to_string(&path) {
            Ok(text) => match b64d(text.trim()).and_then(|b| <[u8; 32]>::try_from(b).ok()) {
                Some(key) => return Some(key),
                None => tracing::warn!("secret store: {} does not hold a 32-byte key", path.display()),
            },
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(e) => tracing::warn!("secret store: could not read {}: {e}", path.display()),
        }
        if !create {
            return None;
        }
        let key = new_key();
        match write_private(&path, &format!("{}\n", b64e(&key))) {
            Ok(()) => {
                tracing::info!("secret store: made a new key file {}", path.display());
                Some(key)
            }
            Err(e) => {
                tracing::warn!("secret store: could not write {}: {e}", path.display());
                None
            }
        }
    }

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    fn keyring_name(&self) -> std::io::Result<String> {
        let path = self.dir.join(ID_FILE);
        if let Ok(text) = std::fs::read_to_string(&path) {
            let got = text.trim();
            if !got.is_empty() {
                return Ok(format!("at-rest-key-{got}"));
            }
        }
        let got = random_hex(16);
        write_private(&path, &format!("{got}\n"))?;
        Ok(format!("at-rest-key-{got}"))
    }

    #[cfg(any(target_os = "macos", target_os = "windows"))]
    fn keyring_key(&self) -> Result<([u8; 32], &'static str), Unavailable> {
        let name = self.keyring_name().map_err(|_| Unavailable("the key id could not be kept"))?;
        // Python's keyring names a Windows credential `<user>@<service>`; macOS uses the service and account as they are.
        let entry = if cfg!(windows) {
            keyring::Entry::new_with_target(&format!("{name}@{SERVICE}"), SERVICE, &name)
        } else {
            keyring::Entry::new(SERVICE, &name)
        }
        .map_err(|_| Unavailable("no keyring backend on this computer"))?;
        let label = if cfg!(windows) { "Windows Credential Manager" } else { "macOS Keychain" };
        if let Ok(got) = entry.get_password() {
            if let Some(key) = b64d(&got).and_then(|b| <[u8; 32]>::try_from(b).ok()) {
                return Ok((key, label));
            }
        }
        let key = new_key();
        entry.set_password(&b64e(&key)).map_err(|_| Unavailable("the keyring refused the key"))?;
        if entry.get_password().ok().as_deref() != Some(b64e(&key).as_str()) {
            return Err(Unavailable("the keyring did not keep the key"));
        }
        tracing::info!("secret store: made a new key in the {label}");
        Ok((key, label))
    }

    fn load(&self) -> Result<std::sync::MutexGuard<'_, Option<Loaded>>, Unavailable> {
        let mut guard = self.loaded.lock().map_err(|_| Unavailable("the key store lock failed"))?;
        if guard.is_some() {
            return Ok(guard);
        }
        let mut keys: HashMap<String, [u8; 32]> = HashMap::new();
        let mut primary: Option<[u8; 32]> = None;
        let mut location = String::new();
        if self.store == Store::Keyring {
            #[cfg(any(target_os = "macos", target_os = "windows"))]
            match self.keyring_key() {
                Ok((key, label)) => {
                    primary = Some(key);
                    location = label.to_owned();
                }
                Err(e) => tracing::warn!("secret store: the keyring could not be used ({e}); using the key file instead"),
            }
            #[cfg(not(any(target_os = "macos", target_os = "windows")))]
            tracing::warn!("secret store: there is no keyring on this platform; using the key file instead");
        }
        match primary {
            None => {
                primary = self.file_key(true);
                location = if self.store == Store::Keyring { "key file (the keyring could not be used)" } else { "key file" }.to_owned();
            }
            Some(_) => {
                // Values sealed while the keyring was down still open.
                if let Some(old) = self.file_key(false) {
                    keys.insert(kid(&old), old);
                }
            }
        }
        let primary = primary.ok_or(Unavailable("no key"))?;
        let id = kid(&primary);
        keys.insert(id.clone(), primary);
        *guard = Some(Loaded { keys, primary: id, location });
        Ok(guard)
    }

    /// Where the key is kept, for health and the admin page ("" when no key could be made).
    pub fn location(&self) -> String {
        self.load().ok().and_then(|g| g.as_ref().map(|l| l.location.clone())).unwrap_or_default()
    }

    /// `text` sealed for `purpose`. "" stays "", and a value already sealed is returned as it is.
    pub fn seal(&self, text: &str, purpose: &str) -> Result<String, Unavailable> {
        if text.is_empty() || is_sealed(text) {
            return Ok(text.to_owned());
        }
        let guard = self.load()?;
        let loaded = guard.as_ref().ok_or(Unavailable("no key"))?;
        let key = loaded.keys.get(&loaded.primary).ok_or(Unavailable("no key"))?;
        let mut nonce = [0u8; 12];
        rand::rngs::OsRng.fill_bytes(&mut nonce);
        let data = Aes256Gcm::new(key.into())
            .encrypt(Nonce::from_slice(&nonce), Payload { msg: text.as_bytes(), aad: purpose.as_bytes() })
            .map_err(|_| Unavailable("sealing failed"))?;
        let mut blob = nonce.to_vec();
        blob.extend_from_slice(&data);
        Ok(format!("{PREFIX}{}:{}", loaded.primary, b64e(&blob)))
    }

    /// The plain text of a sealed value, a plain-text value as it is, `None` when it cannot be opened.
    pub fn unseal(&self, value: &str, purpose: &str) -> Option<String> {
        if !is_sealed(value) {
            return Some(value.to_owned());
        }
        let Some((key_id, data)) = value[PREFIX.len()..].split_once(':') else {
            self.warn(purpose, "is not a sealed value");
            return None;
        };
        let Some(raw) = b64d(data) else {
            self.warn(purpose, "is not a sealed value");
            return None;
        };
        let key = match self.load() {
            Ok(guard) => guard.as_ref().and_then(|l| l.keys.get(key_id).copied()),
            Err(e) => {
                self.warn(purpose, &format!("cannot be opened: no key ({e})"));
                return None;
            }
        };
        let Some(key) = key.filter(|_| raw.len() >= 13) else {
            self.warn(purpose, "was sealed with a key this computer no longer has");
            return None;
        };
        let opened = Aes256Gcm::new((&key).into())
            .decrypt(Nonce::from_slice(&raw[..12]), Payload { msg: &raw[12..], aad: purpose.as_bytes() })
            .ok()
            .and_then(|b| String::from_utf8(b).ok());
        if opened.is_none() {
            self.warn(purpose, "does not open with this computer's key");
        }
        opened
    }

    fn warn(&self, purpose: &str, what: &str) {
        let first = self.warned.lock().map(|mut w| w.insert(purpose.to_owned())).unwrap_or(true);
        if first {
            tracing::warn!("secret store: the stored {purpose} {what}; it is treated as not set");
        }
    }
}
