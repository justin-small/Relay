//! The operator panel's TLS certificate (`app/tls.py`): minted here, served by Relay itself, no proxy in front.
//!
//! The panel carries the OpenAI key, the sign-in cookie and every account, so it is only ever served over HTTPS. There is no
//! public DNS name and no ACME challenge on a venue LAN, so the certificate is self-signed: the operator trusts it once, on
//! the machine that drives the panel, after checking the SHA-256 fingerprint (printed at start-up, shown on the admin page).
//!
//! The certificate is reused while it still covers this machine's current names and has more than 30 days left, so a restart
//! does not change the fingerprint an operator already trusted. When the machine's LAN address changes to one the certificate
//! does not cover, a new one is minted and swapped into the running socket; `Panel::changed` then says the fingerprint moved.
//! The files are the Python server's (`admin.crt`, `admin.key` in `certs/` of the state folder, an ECDSA P-256 key in PKCS#8),
//! so either server reads what the other wrote. The private key is written 0600 and never logged.
//!
//! `RELAY_CERT_DIR` moves the folder.

use std::path::{Path, PathBuf};
use std::sync::{Arc, RwLock};

use rcgen::{CertificateParams, DistinguishedName, DnType, ExtendedKeyUsagePurpose, IsCa, KeyPair, KeyUsagePurpose, SanType};
use rustls::pki_types::{CertificateDer, PrivateKeyDer, PrivatePkcs8KeyDer};
use rustls::server::{ClientHello, ResolvesServerCert};
use rustls::sign::CertifiedKey;
use sha2::{Digest, Sha256};
use time::format_description::well_known::Rfc3339;
use time::{Duration, OffsetDateTime};
use x509_parser::extensions::GeneralName;
use x509_parser::prelude::{FromDer, X509Certificate};

/// Apple and Chrome reject server certificates valid for more than 398 days.
pub const VALID_DAYS: i64 = 397;
/// Renew this far before expiry, not at the wire, so a renewal never lands in the middle of an event.
pub const RENEW_WITHIN_DAYS: i64 = 30;
/// A new certificate keeps the names the one it replaces covered, up to this many of each kind (current names first).
pub const MAX_KEPT_NAMES: usize = 16;
/// Strict-Transport-Security for the panel: 397 days, the certificate's own life.
pub const HSTS_MAX_AGE: u64 = 34_300_800;

pub fn cert_dir() -> PathBuf {
    match std::env::var("RELAY_CERT_DIR").ok().map(|v| v.trim().to_owned()).filter(|v| !v.is_empty()) {
        Some(dir) => PathBuf::from(dir),
        None => relay_core::paths::state_dir().join("certs"),
    }
}

/// The names the certificate must carry: what an operator could type into a browser.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct Names {
    pub dns: Vec<String>,
    pub ips: Vec<String>,
}

/// The names for these addresses: `localhost`, the host name and `<host>.local`, the optional `admin_fqdn` first, and every
/// address the panel is reached on (LAN addresses, the crew route's address, 127.0.0.1).
pub fn names(fqdn: &str, lan_ips: &[String], crew_addresses: &[String]) -> Names {
    let mut ips: Vec<String> = Vec::new();
    for ip in lan_ips.iter().chain(crew_addresses).map(String::as_str).chain(std::iter::once("127.0.0.1")) {
        if !ips.iter().any(|i| i == ip) {
            ips.push(ip.to_owned());
        }
    }
    let mut dns: Vec<String> = vec!["localhost".to_owned()];
    let host = gethostname::gethostname().to_string_lossy().split('.').next().unwrap_or_default().to_owned();
    for cand in [host.clone(), format!("{host}.local")] {
        if !cand.is_empty() && cand != ".local" && !dns.contains(&cand) {
            dns.push(cand);
        }
    }
    let fqdn = fqdn.trim().trim_matches('.');
    if !fqdn.is_empty() && !dns.iter().any(|d| d == fqdn) {
        dns.insert(0, fqdn.to_owned());
    }
    Names { dns, ips }
}

/// `AA:BB:...` of the certificate's DER, upper case.
pub fn fingerprint(der: &[u8]) -> String {
    Sha256::digest(der).iter().map(|b| format!("{b:02X}")).collect::<Vec<_>>().join(":")
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Info {
    pub fingerprint: String,
    pub dns: Vec<String>,
    pub ips: Vec<String>,
    pub not_after: String,
    pub reused: bool,
}

/// A certificate minted over an earlier one.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Changed {
    pub from: String,
    pub to: String,
    pub at: String,
}

struct Loaded {
    der: Vec<u8>,
    key_pem: String,
    dns: Vec<String>,
    ips: Vec<String>,
    not_after: OffsetDateTime,
}

fn read_pair(dir: &Path) -> Option<Loaded> {
    let cert_pem = std::fs::read_to_string(dir.join("admin.crt")).ok()?;
    let key_pem = std::fs::read_to_string(dir.join("admin.key")).ok()?;
    let der = pem_to_der(&cert_pem)?;
    let key = KeyPair::from_pem(&key_pem).ok()?;
    let (_, cert) = X509Certificate::from_der(&der).ok()?;
    // The pair must match, or the socket could not be opened with it.
    if cert.public_key().subject_public_key.data.as_ref() != key.public_key_raw() {
        return None;
    }
    let (mut dns, mut ips) = (Vec::new(), Vec::new());
    if let Ok(Some(san)) = cert.subject_alternative_name() {
        for name in &san.value.general_names {
            match name {
                GeneralName::DNSName(d) => dns.push((*d).to_owned()),
                GeneralName::IPAddress(b) => {
                    if let Ok(four) = <[u8; 4]>::try_from(*b) {
                        ips.push(std::net::Ipv4Addr::from(four).to_string());
                    } else if let Ok(sixteen) = <[u8; 16]>::try_from(*b) {
                        ips.push(std::net::Ipv6Addr::from(sixteen).to_string());
                    }
                }
                _ => {}
            }
        }
    }
    let not_after = OffsetDateTime::from_unix_timestamp(cert.validity().not_after.timestamp()).ok()?;
    Some(Loaded { der, key_pem, dns, ips, not_after })
}

fn pem_to_der(pem: &str) -> Option<Vec<u8>> {
    let mut body = String::new();
    let mut inside = false;
    for line in pem.lines() {
        if line.starts_with("-----BEGIN") {
            if inside {
                break;
            }
            inside = true;
        } else if line.starts_with("-----END") {
            break;
        } else if inside {
            body.push_str(line.trim());
        }
    }
    base64_decode(&body)
}

/// Standard base64 with padding (the only kind a PEM holds).
fn base64_decode(text: &str) -> Option<Vec<u8>> {
    let mut out = Vec::with_capacity(text.len() * 3 / 4);
    let (mut acc, mut bits) = (0u32, 0u32);
    for b in text.bytes() {
        let v = match b {
            b'A'..=b'Z' => b - b'A',
            b'a'..=b'z' => b - b'a' + 26,
            b'0'..=b'9' => b - b'0' + 52,
            b'+' => 62,
            b'/' => 63,
            b'=' => break,
            _ => return None,
        };
        acc = (acc << 6) | u32::from(v);
        bits += 6;
        if bits >= 8 {
            bits -= 8;
            out.push((acc >> bits) as u8);
            acc &= (1 << bits) - 1;
        }
    }
    Some(out)
}

/// True when the certificate names every one of these and is not near expiry.
fn usable(loaded: &Loaded, want: &Names, now: OffsetDateTime) -> bool {
    let have_dns: Vec<String> = loaded.dns.iter().map(|d| d.to_ascii_lowercase()).collect();
    want.dns.iter().all(|d| have_dns.contains(&d.to_ascii_lowercase()))
        && want.ips.iter().all(|i| loaded.ips.contains(i))
        && loaded.not_after - now >= Duration::days(RENEW_WITHIN_DAYS)
}

/// `current`, then the `old` names it lacks, at most `MAX_KEPT_NAMES` of them (current ones are never dropped).
fn keep(current: &[String], old: &[String]) -> Vec<String> {
    let mut out: Vec<String> = current.to_vec();
    let limit = MAX_KEPT_NAMES.max(current.len());
    for n in old {
        if out.len() >= limit {
            break;
        }
        if !out.iter().any(|o| o.eq_ignore_ascii_case(n)) {
            out.push(n.clone());
        }
    }
    out
}

/// Write `data` to `path` readable by the owner only, never at the umask default even for an instant.
fn write_private(path: &Path, data: &[u8]) -> std::io::Result<()> {
    use std::io::Write;
    let tmp = path.with_file_name(format!("{}.tmp", path.file_name().and_then(|n| n.to_str()).unwrap_or("key")));
    let mut options = std::fs::OpenOptions::new();
    options.write(true).create(true).truncate(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&tmp)?;
    file.write_all(data)?;
    drop(file);
    std::fs::rename(&tmp, path)
}

fn mint(dir: &Path, fqdn: &str, dns: &[String], ips: &[String], now: OffsetDateTime) -> Result<(Vec<u8>, String), String> {
    let mut params = CertificateParams::default();
    let mut subject = DistinguishedName::new();
    let cn = if !fqdn.is_empty() { fqdn } else { dns.last().map(String::as_str).filter(|d| !d.is_empty()).unwrap_or("relay-panel") };
    subject.push(DnType::CommonName, cn.chars().take(64).collect::<String>());
    params.distinguished_name = subject;
    params.subject_alt_names = dns
        .iter()
        .filter_map(|d| rcgen::Ia5String::try_from(d.as_str()).ok().map(SanType::DnsName))
        .chain(ips.iter().filter_map(|i| i.parse().ok().map(SanType::IpAddress)))
        .collect();
    // Backdated a little: a laptop whose clock runs behind would reject a certificate minted seconds ago.
    params.not_before = now - Duration::hours(1);
    params.not_after = now + Duration::days(VALID_DAYS);
    params.is_ca = IsCa::ExplicitNoCa;
    params.key_usages = vec![KeyUsagePurpose::DigitalSignature];
    params.extended_key_usages = vec![ExtendedKeyUsagePurpose::ServerAuth];
    let key = KeyPair::generate().map_err(|e| format!("key: {e}"))?;
    let cert = params.self_signed(&key).map_err(|e| format!("certificate: {e}"))?;
    std::fs::create_dir_all(dir).map_err(|e| format!("certificate folder: {e}"))?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700));
    }
    let key_pem = key.serialize_pem();
    // The key lands first: a certificate on disk always has its key beside it.
    write_private(&dir.join("admin.key"), key_pem.as_bytes()).map_err(|e| format!("key file: {e}"))?;
    std::fs::write(dir.join("admin.crt"), cert.pem()).map_err(|e| format!("certificate file: {e}"))?;
    Ok((cert.der().to_vec(), key_pem))
}

/// What the TLS acceptors ask for a certificate: the one in force, swapped for new handshakes when it is replaced
/// (connections already open keep the one they started with).
#[derive(Debug)]
pub struct Resolver(RwLock<Arc<CertifiedKey>>);

impl Resolver {
    fn set(&self, key: Arc<CertifiedKey>) {
        if let Ok(mut slot) = self.0.write() {
            *slot = key;
        }
    }
}

impl ResolvesServerCert for Resolver {
    fn resolve(&self, _hello: ClientHello<'_>) -> Option<Arc<CertifiedKey>> {
        self.0.read().ok().map(|k| Arc::clone(&k))
    }
}

fn certified(der: &[u8], key_pem: &str) -> Result<Arc<CertifiedKey>, String> {
    let key = KeyPair::from_pem(key_pem).map_err(|e| format!("key: {e}"))?;
    let key_der = PrivateKeyDer::Pkcs8(PrivatePkcs8KeyDer::from(key.serialize_der()));
    let signing = rustls::crypto::ring::sign::any_supported_type(&key_der).map_err(|e| format!("signing key: {e}"))?;
    Ok(Arc::new(CertifiedKey::new(vec![CertificateDer::from(der.to_vec())], signing)))
}

/// The running panel's certificate, and the resolver that serves it.
pub struct Panel {
    dir: PathBuf,
    fqdn: String,
    pub info: Info,
    pub changed: Option<Changed>,
    resolver: Arc<Resolver>,
}

impl Panel {
    /// The lines the start-up banner prints about the certificate and where to open the panel and the viewer pages. `fqdn` names the
    /// panel when one is set, else its first address does.
    pub fn banner(&self, fqdn: Option<&str>, admin_port: u16, viewer_port: u16) -> Vec<String> {
        let mut out = Vec::new();
        if self.changed.is_some() {
            out.push("  The panel certificate was replaced, so its fingerprint changed. Trust the new one below.".to_owned());
        }
        let info = &self.info;
        let first = info.ips.first().cloned().unwrap_or_else(|| "127.0.0.1".to_owned());
        let primary = fqdn.map_or_else(|| first.clone(), str::to_owned);
        out.push(String::new());
        out.push(format!("  Certificate names : {}", info.dns.iter().chain(info.ips.iter()).cloned().collect::<Vec<_>>().join(", ")));
        out.push(format!("  SHA-256           : {}", info.fingerprint));
        out.push(String::new());
        out.push(format!("  Panel   : {}", crate::listen::link("https", &primary, admin_port, "/admin")));
        out.push(format!("  Viewers : {}", crate::listen::link("http", &first, viewer_port, "/")));
        out.push(String::new());
        out.push("  The certificate is self-signed, so the browser will warn once. Check the fingerprint it".to_owned());
        out.push("  shows against the SHA-256 above before you accept it.".to_owned());
        out
    }

    /// The SHA-256 fingerprint of the certificate in use.
    pub fn fingerprint(&self) -> &str {
        &self.info.fingerprint
    }

    /// What the app behind this server is told about the certificate.
    pub fn report(&self) -> crate::front::CertReport {
        crate::front::CertReport {
            fingerprint: self.info.fingerprint.clone(),
            names: self.info.dns.iter().chain(self.info.ips.iter()).cloned().collect(),
            expires: self.info.not_after.clone(),
            changed: self.changed.as_ref().map(|c| crate::front::ChangedReport { from: c.from.clone(), to: c.to.clone(), at: c.at.clone() }),
        }
    }

    /// Make sure there is a certificate for these names in `dir` and load it.
    pub fn start(dir: &Path, fqdn: &str, want: &Names) -> Result<Self, String> {
        let fqdn = fqdn.trim().trim_matches('.').to_owned();
        let (info, changed, der, key_pem) = Self::ensure(dir, &fqdn, want)?;
        let resolver = Arc::new(Resolver(RwLock::new(certified(&der, &key_pem)?)));
        Ok(Self { dir: dir.to_path_buf(), fqdn, info, changed, resolver })
    }

    /// Reuse the certificate on disk or mint one: (info, what changed, DER, key PEM).
    fn ensure(dir: &Path, fqdn: &str, want: &Names) -> Result<(Info, Option<Changed>, Vec<u8>, String), String> {
        let now = OffsetDateTime::now_utc();
        let existing = read_pair(dir);
        if let Some(e) = &existing {
            if usable(e, want, now) {
                // What it names, kept names included, not only today's.
                let info = Info {
                    fingerprint: fingerprint(&e.der),
                    dns: if e.dns.is_empty() { want.dns.clone() } else { e.dns.clone() },
                    ips: if e.ips.is_empty() { want.ips.clone() } else { e.ips.clone() },
                    not_after: e.not_after.format(&Rfc3339).unwrap_or_default(),
                    reused: true,
                };
                return Ok((info, None, e.der.clone(), e.key_pem.clone()));
            }
        }
        // Keep what the old certificate named. On a Mac the host name comes from DHCP and falls back to the local name while
        // the network is down, so without this a 5-second Wi-Fi drop minted twice, and every operator who had trusted the
        // fingerprint had to trust a new one.
        let (dns, ips) = match &existing {
            Some(e) => (keep(&want.dns, &e.dns), keep(&want.ips, &e.ips)),
            None => (want.dns.clone(), want.ips.clone()),
        };
        let (der, key_pem) = mint(dir, fqdn, &dns, &ips, now)?;
        let new = fingerprint(&der);
        let changed = existing.as_ref().map(|e| {
            let from = fingerprint(&e.der);
            tracing::warn!(from = %from, to = %new, "The panel certificate was replaced (new names or near expiry); its SHA-256 fingerprint changed");
            Changed { from, to: new.clone(), at: now.format(&Rfc3339).unwrap_or_default() }
        });
        let not_after = (now + Duration::days(VALID_DAYS)).format(&Rfc3339).unwrap_or_default();
        Ok((Info { fingerprint: new, dns, ips, not_after, reused: false }, changed, der, key_pem))
    }

    /// Re-check the names (call when the LAN address changes). True when a new certificate was minted and swapped in.
    pub fn refresh(&mut self, want: &Names) -> Result<bool, String> {
        let (info, changed, der, key_pem) = Self::ensure(&self.dir, &self.fqdn, want)?;
        if info.reused {
            self.info = info;
            return Ok(false);
        }
        self.resolver.set(certified(&der, &key_pem)?);
        self.info = info;
        self.changed = changed;
        Ok(true)
    }

    /// The resolver for a TLS server config.
    pub fn resolver(&self) -> Arc<Resolver> {
        Arc::clone(&self.resolver)
    }

    /// The server config the panel's acceptors use: TLS 1.2 and up, HTTP/1.1.
    pub fn server_config(&self) -> Arc<rustls::ServerConfig> {
        let provider = Arc::new(rustls::crypto::ring::default_provider());
        let mut cfg = rustls::ServerConfig::builder_with_provider(provider)
            .with_safe_default_protocol_versions()
            .map(|b| b.with_no_client_auth().with_cert_resolver(self.resolver()))
            .unwrap_or_else(|_| unreachable!("the ring provider supports the default protocol versions"));
        cfg.alpn_protocols = vec![b"http/1.1".to_vec()];
        Arc::new(cfg)
    }
}
