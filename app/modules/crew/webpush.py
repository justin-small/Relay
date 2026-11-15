"""Web push for the crew page (#228), written on `cryptography` alone.

pywebpush was ruled out: its http-ece dependency has no wheel, and the release build installs wheel-only with
hashes. What a push needs is small: a VAPID signature (RFC 8292) and the aes128gcm message encryption (RFC 8291).

A subscription's endpoint is typed by whoever is signed in, so Relay only ever posts to the push services that
browsers use (ALLOWED_HOSTS), over HTTPS, and follows no redirect: otherwise a crew account could make Relay
call any address on the network.
"""
from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

# The browsers' push services: Chrome and Edge (FCM, Windows), Firefox (Mozilla), Safari (Apple).
ALLOWED_HOSTS = ("fcm.googleapis.com", "updates.push.services.mozilla.com", "push.apple.com",
                 "notify.windows.com", "push.services.mozilla.com")
SUBJECT = "mailto:relay@example.com"      # VAPID needs one; it names no real person
TTL_S = 120
TIMEOUT_S = 6
RECORD_SIZE = 4096
MAX_PAYLOAD = 3000


def b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def b64d(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def public_bytes(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)


def new_key() -> ec.EllipticCurvePrivateKey:
    return ec.generate_private_key(ec.SECP256R1())


def key_to_pem(key: ec.EllipticCurvePrivateKey) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption()).decode()


def key_from_pem(pem: str) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key(pem.encode(), None)


def allowed(endpoint) -> bool:
    """An https address on one of the browsers' push services, with no credentials or odd port."""
    if not isinstance(endpoint, str) or len(endpoint) > 1000:
        return False
    try:
        u = urllib.parse.urlsplit(endpoint)
        host = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return False
    if u.scheme != "https" or u.username or u.password or port not in (None, 443) or not host:
        return False
    return any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS)


def encrypt(plaintext: bytes, ua_public: bytes, auth_secret: bytes, *, as_key=None, salt: bytes | None = None) -> bytes:
    """The aes128gcm body for one subscription (RFC 8291): salt, record size, sender key, then the ciphertext."""
    as_key = as_key or new_key()
    salt = salt or os.urandom(16)
    as_public = public_bytes(as_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = as_key.exchange(ec.ECDH(), ua_key)
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(shared)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    sealed = AESGCM(cek).encrypt(nonce, plaintext + b"\x02", None)      # 0x02 ends the only record
    return salt + RECORD_SIZE.to_bytes(4, "big") + bytes([len(as_public)]) + as_public + sealed


def vapid_header(key: ec.EllipticCurvePrivateKey, endpoint: str, now: float | None = None) -> str:
    """The Authorization value (RFC 8292): a short-lived ES256 token naming the push service."""
    u = urllib.parse.urlsplit(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int(now or time.time()) + 12 * 3600, "sub": SUBJECT}
    head = b64e(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    body = b64e(json.dumps(claims, separators=(",", ":")).encode())
    r, s = decode_dss_signature(key.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256())))
    sig = r.to_bytes(32, "big") + s.to_bytes(32, "big")
    return f"vapid t={head}.{body}.{b64e(sig)}, k={b64e(public_bytes(key))}"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class Gone(Exception):
    """The push service says this subscription no longer exists (404 or 410): forget it."""


class Failed(Exception):
    pass


def post(endpoint: str, headers: dict, body: bytes) -> int:
    """One POST to a push service. Returns the status; raises Failed on a network error. Tests replace this."""
    req = urllib.request.Request(endpoint, data=body, headers=headers, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect).open(req, timeout=TIMEOUT_S) as res:
            return res.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise Failed(type(exc).__name__) from exc


def send(key: ec.EllipticCurvePrivateKey, sub: dict, payload: dict, *, sender: Callable = None) -> None:
    """Push `payload` (a small dict) to one subscription {endpoint, p256dh, auth}. Raises Gone or Failed."""
    if not allowed(sub["endpoint"]):
        raise Gone("not a push service")
    data = json.dumps(payload, separators=(",", ":")).encode()
    if len(data) > MAX_PAYLOAD:
        raise Failed("too large")
    body = encrypt(data, b64d(sub["p256dh"]), b64d(sub["auth"]))
    headers = {"Authorization": vapid_header(key, sub["endpoint"]), "Content-Encoding": "aes128gcm",
               "Content-Type": "application/octet-stream", "TTL": str(TTL_S), "Urgency": "high"}
    status = (sender or post)(sub["endpoint"], headers, body)
    if status in (404, 410):
        raise Gone(str(status))
    if not 200 <= status < 300:
        raise Failed(str(status))
