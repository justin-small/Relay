#!/usr/bin/env python3
"""Generate the operator panel's TLS certificate and a matching Caddyfile.

Runs inside the container: setup.command / setup.bat call it once through
`docker compose run` so the operator sees the fingerprint while they are still
at the keyboard, and docker/entrypoint.sh calls it again on every start. Both
write to the state volume (docker-config/ on the host), so the certificate
outlives the container and the fingerprint stays put.

Relay itself only ever speaks plain HTTP on loopback. Caddy is the process that
faces the network, and it is the only one holding the private key.

Split by design (see issue #15):

  viewers  plain HTTP, every interface, no certificate  -> 127.0.0.1:<port>
  panel    HTTPS with the certificate written here      -> 127.0.0.1:<admin_port>

Caddy binds 8080 and 8443 inside the network namespace and the host publishes
80 and 443 in front of them, so the container can keep cap_drop: ALL -- binding
a privileged port directly would need CAP_NET_BIND_SERVICE.

The viewer pages carry no secret and are read by a room full of phones that
will not install a root certificate, so they stay on plain HTTP. The panel
carries the OpenAI key, the admin token and a session cookie, so it never goes
out in cleartext.

The certificate is self-signed. There is no public DNS name and no ACME
challenge to answer on a venue LAN, so a real CA is not an option; the operator
trusts this one certificate once, on the one machine that drives the panel, and
checks the fingerprint printed below against the fingerprint the browser shows.

An existing certificate is reused while it still covers the current names and
has more than 30 days left -- a container restart must not silently change the
fingerprint out from under an operator who has already trusted it. Pass
--force to mint a new one regardless.

Environment:
  RELAY_ADMIN_FQDN    optional hostname to add to the certificate ("" for none)
  RELAY_ADMIN_IPS     extra IPs to certify, comma-separated. Needed in Docker:
                      the address this process can see is the container's, and
                      browsers connect to the host's, so the launcher passes it
  RELAY_CADDY_DIR     where to write the Caddyfile      (image: /app/config)
  RELAY_CERT_DIR      where to write the key and cert   (image: /app/config/certs)
  RELAY_HTTP_PORT     port Caddy serves viewers on      (default 8080)
  RELAY_HTTPS_PORT    port Caddy serves the panel on    (default 8443)
  RELAY_PUBLIC_HTTP   port the host publishes for viewers, for the URLs printed
                      below only                        (default 80)
  RELAY_PUBLIC_HTTPS  same, for the panel                (default 443)
"""
from __future__ import annotations

import datetime as dt
import ipaddress
import os
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

CADDY_DIR = Path(os.environ.get("RELAY_CADDY_DIR") or (ROOT / "caddy"))
CERT_DIR = Path(os.environ.get("RELAY_CERT_DIR") or (CADDY_DIR / "certs"))
CERT_PATH = CERT_DIR / "admin.crt"
KEY_PATH = CERT_DIR / "admin.key"
CADDYFILE = CADDY_DIR / "Caddyfile"

# Apple and Chrome reject server certificates valid for more than 398 days, so
# this one lasts 397 and re-running setup is the way to renew it.
VALID_DAYS = 397
# Renew this far before expiry rather than at the wire, so a renewal never
# lands in the middle of an event.
RENEW_WITHIN_DAYS = 30


def lan_ip() -> str:
    """The address of the interface that carries the default route.

    No packet is sent -- connect() on a UDP socket only picks a route -- so this
    works on a venue LAN with no internet behind it.
    """
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def _extra_ips() -> list[str]:
    """RELAY_ADMIN_IPS, parsed and validated.

    In Docker `lan_ip()` sees the container's bridge address (172.17.x.x),
    never the host's -- but the browser connects to the host's, and a
    certificate that does not name it is a certificate the browser rejects for
    the wrong reason. The start scripts pass the host address here.
    """
    out: list[str] = []
    for raw in (os.environ.get("RELAY_ADMIN_IPS") or "").replace(";", ",").split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            ipaddress.ip_address(raw)
        except ValueError:
            print(f"  Ignoring RELAY_ADMIN_IPS entry {raw!r}: not an IP address.")
            continue
        if raw not in out:
            out.append(raw)
    return out


def _names(fqdn: str) -> tuple[list[str], list[str]]:
    """(dns names, ip addresses) to put in the certificate's SAN extension."""
    ips: list[str] = []
    for cand in _extra_ips() + [lan_ip(), "127.0.0.1"]:
        if cand not in ips:
            ips.append(cand)

    dns: list[str] = ["localhost"]
    host = socket.gethostname().split(".")[0]
    for cand in (host, f"{host}.local"):
        if cand and cand not in dns:
            dns.append(cand)
    if fqdn and fqdn not in dns:
        dns.insert(0, fqdn)
    return dns, ips


def _required_names(fqdn: str) -> tuple[list[str], list[str]]:
    """The subset of the SAN list that an existing certificate must cover.

    Not every name in `_names` is worth a regeneration. The machine hostname is
    a convenience -- and in Docker it is the container id, which is new on every
    single start, so requiring it would mint a fresh certificate (and a fresh
    fingerprint) on every restart. Likewise `lan_ip()` inside a container is the
    bridge address, noise next to the host address passed in RELAY_ADMIN_IPS.

    What must be covered: the hostname the operator was told to use, and the
    address they will actually dial.
    """
    dns = ["localhost"]
    if fqdn:
        dns.insert(0, fqdn)
    ips = _extra_ips() or [lan_ip()]
    if "127.0.0.1" not in ips:
        ips.append("127.0.0.1")
    return dns, ips


def _fingerprint(cert) -> str:
    from cryptography.hazmat.primitives import hashes

    fp = cert.fingerprint(hashes.SHA256()).hex().upper()
    return ":".join(fp[i : i + 2] for i in range(0, len(fp), 2))


def _reusable(fqdn: str):
    """The existing certificate, if it still covers these names and is fresh.

    In Docker this runs on every container start. Minting a new certificate
    each time would change the fingerprint the operator trusted, training them
    to click through the warning -- which is the whole value of the warning.
    """
    if not (CERT_PATH.exists() and KEY_PATH.exists()):
        return None
    try:
        from cryptography import x509

        cert = x509.load_pem_x509_certificate(CERT_PATH.read_bytes())
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_dns = set(san.get_values_for_type(x509.DNSName))
        have_ips = {str(i) for i in san.get_values_for_type(x509.IPAddress)}
        need_dns, need_ips = _required_names(fqdn)
        if not set(need_dns).issubset(have_dns) or not set(need_ips).issubset(have_ips):
            return None
        left = cert.not_valid_after_utc - dt.datetime.now(dt.timezone.utc)
        if left < dt.timedelta(days=RENEW_WITHIN_DAYS):
            return None
        return cert
    except Exception:
        # A corrupt or unreadable certificate is replaced, not diagnosed.
        return None


def write_cert(fqdn: str, force: bool = False) -> tuple[str, list[str], list[str], bool]:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    dns, ips = _names(fqdn)

    if not force:
        existing = _reusable(fqdn)
        if existing is not None:
            return _fingerprint(existing), dns, ips, True

    subject = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, (fqdn or dns[-1] or "relay-panel")[:64])]
    )
    key = ec.generate_private_key(ec.SECP256R1())
    now = dt.datetime.now(dt.timezone.utc)
    san = [x509.DNSName(d) for d in dns]
    san += [x509.IPAddress(ipaddress.ip_address(i)) for i in ips]

    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        # Backdated a little: a laptop whose clock has drifted behind the host
        # would otherwise reject a certificate minted seconds ago.
        .not_valid_before(now - dt.timedelta(hours=1))
        .not_valid_after(now + dt.timedelta(days=VALID_DAYS))
        .add_extension(x509.SubjectAlternativeName(san), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=False,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    CERT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(CERT_DIR, 0o700)
    except OSError:
        pass  # Windows; the ACL is what matters there and we do not touch it.

    # Written 0600 before any bytes land in it: the key must never exist on
    # disk, even briefly, at the umask default.
    fd = os.open(KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.PKCS8,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )
    CERT_PATH.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    return _fingerprint(cert), dns, ips, False


CADDYFILE_TEMPLATE = """\
# Relay front end -- generated by tools/setup_caddy.py. Re-run setup to rebuild.
#
# Relay's own sockets stay on loopback (see `admin_host` in config.json). Caddy
# is the process that faces the network:
#
#   :{http_port:<6} viewer pages, plain HTTP, no certificate -- the link the room opens
#   :{https_port:<6} operator panel over HTTPS, cert at {cert}
#
# Run it with:  caddy run --config {caddyfile}

{{
	# No admin API: it is an unauthenticated control socket on 127.0.0.1:2019
	# that can rewrite this whole configuration, and nothing here needs it.
	admin off
	# Nothing is public and there is no resolvable name to validate, so never
	# reach for ACME, and never redirect the viewer port to HTTPS -- the room
	# has no way to trust this certificate and should not be asked to.
	auto_https off
}}

# ---------------------------------------------------------------- viewers
:{http_port} {{
	# The app already 404s admin routes on the viewer port; this is the same
	# refusal one hop earlier, so a misconfigured upstream cannot leak the
	# panel onto the link handed to the room.
	@panel path /admin /admin/* /api/admin /api/admin/*
	respond @panel 404

	reverse_proxy 127.0.0.1:{viewer_upstream} {{
		# Captions are server-sent events: buffering them would hold every
		# line back until the buffer filled.
		flush_interval -1
	}}
}}

# ----------------------------------------------------------- operator panel
:{https_port} {{
	tls {cert} {key}

	reverse_proxy 127.0.0.1:{admin_upstream} {{
		flush_interval -1
	}}

	header {{
		# The panel is HTTPS from here on. 397 days matches the certificate.
		Strict-Transport-Security "max-age=34300800"
		X-Content-Type-Options "nosniff"
		X-Frame-Options "DENY"
		Referrer-Policy "no-referrer"
	}}
}}
"""


def _rel(path: Path) -> str:
    """Repo-relative when it is inside the repo, absolute otherwise.

    Native setup runs Caddy from the repo root, so short paths read better
    there. In Docker the cert lives on the state volume, outside the repo.
    """
    try:
        return path.relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def write_caddyfile(cfg: dict) -> tuple[int, int]:
    http_port = int(os.environ.get("RELAY_HTTP_PORT") or 8080)
    https_port = int(os.environ.get("RELAY_HTTPS_PORT") or 8443)
    CADDY_DIR.mkdir(parents=True, exist_ok=True)
    CADDYFILE.write_text(
        CADDYFILE_TEMPLATE.format(
            http_port=http_port,
            https_port=https_port,
            viewer_upstream=int(cfg.get("port") or 8000),
            admin_upstream=int(cfg.get("admin_port") or 8001),
            cert=_rel(CERT_PATH),
            key=_rel(KEY_PATH),
            caddyfile=_rel(CADDYFILE),
        )
    )
    return http_port, https_port


def main(argv: list[str]) -> int:
    try:
        import cryptography  # noqa: F401
    except ImportError:
        print(
            "  The `cryptography` package is missing -- re-run setup so it can\n"
            "  install dependencies, then try again.",
            file=sys.stderr,
        )
        return 1

    from app import config  # noqa: E402

    config.ensure_file()
    cfg = config.load()

    # The env var wins so a one-off run can override, but the operator's answer
    # at setup time lives in config.json -- the container is recreated on every
    # start and an env-only answer would not survive that.
    fqdn = (os.environ.get("RELAY_ADMIN_FQDN") or cfg.get("admin_fqdn") or "").strip().strip(".")
    fp, dns, ips, reused = write_cert(fqdn, force="--force" in argv)
    write_caddyfile(cfg)
    pub_http = int(os.environ.get("RELAY_PUBLIC_HTTP") or 80)
    pub_https = int(os.environ.get("RELAY_PUBLIC_HTTPS") or 443)

    primary = fqdn or ips[0]
    verb = "Reusing" if reused else "Wrote"
    print(f"{verb} {_rel(CERT_PATH)} and {_rel(KEY_PATH)} (key 0600).")
    print(f"Wrote {_rel(CADDYFILE)}.")
    print()
    print(f"  Certificate names : {', '.join(dns + ips)}")
    print(f"  SHA-256           : {fp}")
    print()
    _h = "" if pub_https == 443 else f":{pub_https}"
    _v = "" if pub_http == 80 else f":{pub_http}"
    print(f"  Panel   : https://{primary}{_h}/admin")
    print(f"  Viewers : http://{ips[0]}{_v}/")
    print()
    print("  The certificate is self-signed, so the browser will warn once. Check")
    print("  the fingerprint it shows against the SHA-256 above before you accept")
    print("  it -- that check is what makes this connection worth anything.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
