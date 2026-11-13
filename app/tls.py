"""The operator panel's TLS certificate: minted here, served by Relay itself (no proxy in front).

The panel carries the OpenAI key, the sign-in cookie and every account, so it is only ever served over
HTTPS. There is no public DNS name and no ACME challenge to answer on a venue LAN, so the certificate is
self-signed: the operator trusts it once, on the machine that drives the panel, after checking the SHA-256
fingerprint (printed at start-up, shown on the admin page) against the one the browser shows.

The certificate is reused while it still covers this machine's current names and has more than 30 days left,
so a restart does not change the fingerprint an operator already trusted. When the machine's LAN address
changes to one the certificate does not cover (a laptop on a new Wi-Fi network), a new certificate is minted
and loaded into the running socket; `Panel.changed` then says the fingerprint moved, and the admin page
shows it. The private key is written 0600 in `certs/` of the state folder and is never logged.

Environment:
  RELAY_CERT_DIR   where the key and certificate live (default: certs/ in the state folder)
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import logging
import os
import socket
import ssl
import threading
from dataclasses import dataclass, field
from pathlib import Path

from . import netinfo, paths

log = logging.getLogger("relay.tls")

# Apple and Chrome reject server certificates valid for more than 398 days.
VALID_DAYS = 397
# Renew this far before expiry, not at the wire, so a renewal never lands in the middle of an event.
RENEW_WITHIN_DAYS = 30
# Strict-Transport-Security for the panel: 397 days, the certificate's own life.
HSTS_MAX_AGE = 34300800


def cert_dir() -> Path:
    env = (os.environ.get("RELAY_CERT_DIR") or "").strip()
    return Path(env) if env else paths.state_dir() / "certs"


def cert_path() -> Path:
    return cert_dir() / "admin.crt"


def key_path() -> Path:
    return cert_dir() / "admin.key"


def names(fqdn: str = "") -> tuple[list[str], list[str]]:
    """(DNS names, IP addresses) the certificate must name: what an operator could type into a browser."""
    ips: list[str] = []
    for ip in [*netinfo.lan_ips(), "127.0.0.1"]:
        if ip not in ips:
            ips.append(ip)
    dns: list[str] = ["localhost"]
    host = socket.gethostname().split(".")[0]
    for cand in (host, f"{host}.local"):
        if cand and cand not in dns:
            dns.append(cand)
    fqdn = (fqdn or "").strip().strip(".")
    if fqdn and fqdn not in dns:
        dns.insert(0, fqdn)
    return dns, ips


def fingerprint(cert) -> str:
    from cryptography.hazmat.primitives import hashes

    fp = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))


def _load():
    """The certificate on disk, or None when there is no usable pair."""
    if not (cert_path().exists() and key_path().exists()):
        return None
    try:
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization

        cert = x509.load_pem_x509_certificate(cert_path().read_bytes())
        key = serialization.load_pem_private_key(key_path().read_bytes(), password=None)
        # The pair must match, or the socket could not be opened with it.
        pub = serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        if key.public_key().public_bytes(*pub) != cert.public_key().public_bytes(*pub):
            return None
        return cert
    except Exception:
        # A corrupt or unreadable certificate is replaced, not diagnosed.
        return None


def _usable(cert, dns: list[str], ips: list[str]) -> bool:
    """True when `cert` names every one of these and is not near expiry."""
    try:
        from cryptography import x509

        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_dns = {d.lower() for d in san.get_values_for_type(x509.DNSName)}
        have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        if not {d.lower() for d in dns} <= have_dns or not set(ips) <= have_ips:
            return False
        return cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc) >= dt.timedelta(days=RENEW_WITHIN_DAYS)
    except Exception:
        return False


def _write_private(path: Path, data: bytes) -> None:
    """Write `data` to `path` readable by the owner only, never at the umask default even for an instant."""
    tmp = path.with_name(path.name + ".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _mint(fqdn: str, dns: list[str], ips: list[str]):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, (fqdn or dns[-1] or "relay-panel")[:64])])
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName(d) for d in dns] + [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # Backdated a little: a laptop whose clock runs behind would reject a certificate minted seconds ago.
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(digital_signature=True, key_encipherment=False, key_agreement=False,
                          content_commitment=False, data_encipherment=False, key_cert_sign=False,
                          crl_sign=False, encipher_only=False, decipher_only=False),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(key, hashes.SHA256())
    )
    paths.make_private_dir(cert_dir())
    # The key lands first: a certificate on disk always has its key beside it.
    _write_private(key_path(), key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    ))
    cert_path().write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return cert


@dataclass
class Info:
    fingerprint: str
    dns: list[str]
    ips: list[str]
    not_after: str
    reused: bool


@dataclass
class Panel:
    """The running panel's certificate and the TLS context that serves it."""

    fqdn: str = ""
    info: Info | None = None
    # Set when a certificate was minted over an earlier one: {"from": fp, "to": fp, "at": iso time}.
    changed: dict | None = None
    context: ssl.SSLContext | None = field(default=None, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def _ensure(self, force: bool = False) -> bool:
        """Reuse the certificate on disk or mint one. True when a new one was minted."""
        dns, ips = names(self.fqdn)
        existing = _load()
        if existing is not None and not force and _usable(existing, dns, ips):
            self.info = Info(fingerprint(existing), dns, ips, existing.not_valid_after_utc.isoformat(), True)
            return False
        cert = _mint(self.fqdn, dns, ips)
        new = fingerprint(cert)
        if existing is not None:
            old = fingerprint(existing)
            self.changed = {"from": old, "to": new, "at": dt.datetime.now(dt.timezone.utc).isoformat()}
            log.warning("The panel certificate was replaced (new names or near expiry); its SHA-256 fingerprint "
                        "changed: %s -> %s", old, new)
        self.info = Info(new, dns, ips, cert.not_valid_after_utc.isoformat(), False)
        return True

    def _load_into_context(self) -> None:
        if self.context is None:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.minimum_version = ssl.TLSVersion.TLSv1_2
            self.context = ctx
        # Calling this again on the same context swaps the certificate for new handshakes; connections
        # already open keep the one they started with.
        self.context.load_cert_chain(str(cert_path()), str(key_path()))

    def start(self, fqdn: str = "", force: bool = False) -> Info:
        """Make sure there is a certificate for these names and build the TLS context that serves it."""
        with self._lock:
            self.fqdn = (fqdn or "").strip().strip(".")
            self._ensure(force)
            self._load_into_context()
            return self.info

    def refresh(self) -> bool:
        """Re-check the names (call when the LAN address changes). True when a new certificate was minted."""
        with self._lock:
            if self.context is None:
                return False
            minted = self._ensure()
            if minted:
                self._load_into_context()
            return minted

    def describe(self) -> dict:
        info = self.info
        return {
            "fingerprint": info.fingerprint if info else "",
            "names": (info.dns + info.ips) if info else [],
            "expires": info.not_after if info else "",
            "changed": self.changed,
        }


panel = Panel()
