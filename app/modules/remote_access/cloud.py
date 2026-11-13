"""The calls this install makes to Relay's cloud (relay-cloud, a Cloudflare Worker at cloud.relaychurchsystems.com).

Enrolling spends an enrollment code and gives this install a tunnel and a viewer address <slug>.relaychurchsystems.com.
The install makes its own secret, sends it once, and proves itself with it afterwards; the cloud stores only a hash.
The cloud holds the Cloudflare API token; this install only ever holds its own tunnel's token.

Errors come back as a kind, and the operator sees a fixed sentence for that kind (never the exception's text).
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

import certifi

TIMEOUT_S = 30
DEFAULT_URL = "https://cloud.relaychurchsystems.com"

# What the operator reads for each kind of failure.
MESSAGES = {
    "bad_code": "That enrollment code isn't right. Check it and try again.",
    "code_used": "That enrollment code has already been used. Ask for a new one.",
    "code_expired": "That enrollment code has expired. Ask for a new one.",
    "provision_failed": "Relay's cloud couldn't set up the address just now. Try again in a minute.",
    "server_error": "Relay's cloud couldn't finish that just now. Try again in a minute.",
    "unauthorised": "Relay's cloud no longer knows this install: its address was released. Enroll again with a new code.",
    "network": "Couldn't reach Relay's cloud. Check this computer's internet connection.",
    "unexpected": "Relay's cloud answered in a way this version of Relay doesn't understand. Try again later.",
}


class CloudError(Exception):
    def __init__(self, kind: str, status: int = 0):
        super().__init__(kind)
        self.kind = kind if kind in MESSAGES else "unexpected"
        self.status = status

    @property
    def message(self) -> str:
        return MESSAGES[self.kind]


def _context() -> ssl.SSLContext:
    # The frozen app has no system CA bundle it can rely on everywhere; certifi ships one.
    return ssl.create_default_context(cafile=certifi.where())


def _call(base: str, method: str, path: str, body: dict | None = None, secret: str = "") -> dict:
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json", "User-Agent": "Relay"}
    if secret:
        headers["Authorization"] = f"Bearer {secret}"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S, context=_context()) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        try:
            kind = json.loads(exc.read() or b"{}").get("error", "")
        except (ValueError, AttributeError):
            kind = ""
        raise CloudError(kind or "unexpected", exc.code) from None
    except (OSError, ValueError):
        raise CloudError("network") from None
    if not raw:
        return {}
    try:
        out = json.loads(raw)
    except ValueError:
        raise CloudError("unexpected") from None
    if not isinstance(out, dict):
        raise CloudError("unexpected")
    return out


def _install(out: dict) -> dict:
    """The fields an enroll or check-in answers with, checked."""
    got = {k: out.get(k) for k in ("install_id", "hostname", "tunnel_token")}
    if not all(isinstance(v, str) and v for v in got.values()):
        raise CloudError("unexpected")
    return got


def enroll(base: str, code: str, secret: str, viewer_port: int, version: str) -> dict:
    """Spend an enrollment code. Sending the same code and secret again answers with the same install."""
    return _install(_call(base, "POST", "/v1/enroll",
                          {"code": code, "secret": secret, "viewer_port": viewer_port, "relay_version": version}))


def check_in(base: str, secret: str, viewer_port: int, version: str) -> dict:
    """Tell the cloud where the viewer port is and which version runs; get the current tunnel token back."""
    return _install(_call(base, "PUT", "/v1/install", {"viewer_port": viewer_port, "relay_version": version},
                          secret=secret))


def release(base: str, secret: str) -> None:
    """Give the address up: the tunnel and DNS record are deleted and the slug is never reused."""
    _call(base, "DELETE", "/v1/install", secret=secret)


def health(base: str) -> bool:
    return bool(_call(base, "GET", "/health").get("ok"))
