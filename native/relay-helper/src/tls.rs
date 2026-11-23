//! TLS for a Relay that serves its own certificate (#636). Relay's panel is self-signed (app/tls.py), so no
//! public authority vouches for it; the operator checks its SHA-256 fingerprint once, the helper remembers it
//! (`relay-helper pair ... --pin <fingerprint>`), and every later connection must present exactly that certificate.
//! The same idea as the desktop app's pinning proxy (desktop/src-tauri/src/proxy.rs).

use std::sync::Arc;

use rustls::client::danger::{HandshakeSignatureValid, ServerCertVerified, ServerCertVerifier};
use rustls::crypto::{ring, verify_tls12_signature, verify_tls13_signature, CryptoProvider};
use rustls::pki_types::{CertificateDer, ServerName, UnixTime};
use rustls::{DigitallySignedStruct, SignatureScheme};
use sha2::{Digest, Sha256};

/// Upper-case hex with no separators, so `AB:CD`, `abcd` and `AB CD` compare equal.
pub fn normalise(fingerprint: &str) -> String {
    fingerprint.chars().filter(|c| c.is_ascii_hexdigit()).collect::<String>().to_ascii_uppercase()
}

/// The fingerprint of a certificate in DER form: 64 upper-case hex digits.
pub fn fingerprint_of(cert: &[u8]) -> String {
    Sha256::digest(cert).iter().map(|b| format!("{b:02X}")).collect()
}

/// A SHA-256 fingerprint as 64 hex digits, from any spacing or colons; an error sentence when it is not one.
pub fn clean_pin(raw: &str) -> Result<String, String> {
    let pin = normalise(raw);
    if pin.len() == 64 && raw.chars().all(|c| c.is_ascii_hexdigit() || c == ':' || c == ' ') {
        Ok(pin)
    } else {
        Err("The fingerprint is 64 hex digits (SHA-256), as Relay's admin page shows it.".to_string())
    }
}

/// Accepts a server certificate only if its fingerprint is the pinned one. The name is not checked: the
/// fingerprint already names exactly one certificate. Signatures are still verified.
#[derive(Debug)]
struct PinVerifier {
    pin: String,
    provider: Arc<CryptoProvider>,
}

impl ServerCertVerifier for PinVerifier {
    fn verify_server_cert(
        &self,
        end_entity: &CertificateDer<'_>,
        _intermediates: &[CertificateDer<'_>],
        _name: &ServerName<'_>,
        _ocsp: &[u8],
        _now: UnixTime,
    ) -> Result<ServerCertVerified, rustls::Error> {
        if fingerprint_of(end_entity.as_ref()) == self.pin {
            Ok(ServerCertVerified::assertion())
        } else {
            Err(rustls::Error::General("certificate does not match the pinned fingerprint".into()))
        }
    }

    fn verify_tls12_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls12_signature(m, c, d, &self.provider.signature_verification_algorithms)
    }

    fn verify_tls13_signature(&self, m: &[u8], c: &CertificateDer<'_>, d: &DigitallySignedStruct) -> Result<HandshakeSignatureValid, rustls::Error> {
        verify_tls13_signature(m, c, d, &self.provider.signature_verification_algorithms)
    }

    fn supported_verify_schemes(&self) -> Vec<SignatureScheme> {
        self.provider.signature_verification_algorithms.supported_schemes()
    }
}

/// A TLS client setup that trusts one certificate and nothing else. `None` when no pin is set, so the connection
/// uses the usual public roots (a Relay behind a proxy with a real certificate).
pub fn pinned_connector(pin: &str) -> Option<tokio_tungstenite::Connector> {
    let pin = normalise(pin);
    if pin.is_empty() {
        return None;
    }
    let provider = Arc::new(ring::default_provider());
    let config = rustls::ClientConfig::builder_with_provider(provider.clone())
        .with_safe_default_protocol_versions()
        .expect("TLS versions")
        .dangerous() // only to install PinVerifier, which accepts one fingerprint and nothing else
        .with_custom_certificate_verifier(Arc::new(PinVerifier { pin, provider }))
        .with_no_client_auth();
    Some(tokio_tungstenite::Connector::Rustls(Arc::new(config)))
}
