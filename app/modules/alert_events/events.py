"""What leaves Relay for an alert event (#219): the webhook body, the OSC packet, and the settings that say where.

Every error text here is a fixed sentence, never an exception's own, since it reaches the admin page.
"""
from __future__ import annotations

import json
import socket
import struct
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

WEBHOOK_TIMEOUT_S = 5
URL_MAX = 500
HOST_MAX = 253

class Invalid(Exception):
    """Settings from admin that are refused. `message` is a fixed sentence, safe to show."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


EVENTS = ("alert.sent", "alert.acknowledged", "alert.ended", "message.sent")


def clean_stored(raw: Any) -> dict:
    """The saved settings, malformed parts dropped so a hand edit cannot stop Relay starting."""
    raw = raw if isinstance(raw, dict) else {}
    hook = raw.get("webhook") if isinstance(raw.get("webhook"), dict) else {}
    osc = raw.get("osc") if isinstance(raw.get("osc"), dict) else {}
    url = hook.get("url") if isinstance(hook.get("url"), str) else ""
    host = osc.get("host") if isinstance(osc.get("host"), str) else ""
    port = osc.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = 8000
    return {"webhook": {"enabled": hook.get("enabled") is True and bool(_url_ok(url.strip())),
                        "url": url.strip()[:URL_MAX]},
            "osc": {"enabled": osc.get("enabled") is True and bool(host.strip()), "host": host.strip()[:HOST_MAX],
                    "port": port}}


def _url_ok(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return parts.scheme in ("http", "https") and bool(parts.hostname) and len(url) <= URL_MAX


def validate(raw: Any) -> dict:
    """Settings from admin, refused with a sentence when something is wrong."""
    if not isinstance(raw, dict):
        raise Invalid("The settings must be an object.")
    hook = raw.get("webhook") if isinstance(raw.get("webhook"), dict) else {}
    osc = raw.get("osc") if isinstance(raw.get("osc"), dict) else {}
    url = str(hook.get("url") or "").strip()
    if url and not _url_ok(url):
        raise Invalid("The webhook address must start with http:// or https://.")
    if hook.get("enabled") is True and not url:
        raise Invalid("Enter the webhook address, or switch the webhook off.")
    host = str(osc.get("host") or "").strip()
    if len(host) > HOST_MAX or any(c.isspace() or c in "/\\" for c in host):
        raise Invalid("The OSC address must be a host name or an IP address.")
    if osc.get("enabled") is True and not host:
        raise Invalid("Enter the OSC address, or switch OSC off.")
    port = osc.get("port", 8000)
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        raise Invalid("The OSC port must be 1 to 65535.")
    return {"webhook": {"enabled": hook.get("enabled") is True, "url": url},
            "osc": {"enabled": osc.get("enabled") is True, "host": host, "port": port}}


def test_event() -> dict:
    return {"event": "test", "id": "test", "button": "Test", "level": "info", "from": "Relay", "targets": []}


def body(event: dict) -> bytes:
    """The webhook's JSON: the event as core gave it, plus when."""
    return json.dumps({**event, "at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                      ensure_ascii=False).encode("utf-8")


# ------------------------------------------------------------------ OSC
def _pad(raw: bytes) -> bytes:
    raw += b"\0"
    return raw + b"\0" * (-len(raw) % 4)


def osc_message(address: str, *args: Any) -> bytes:
    """An OSC 1.0 message with string, int and float arguments."""
    tags = ","
    data = b""
    for a in args:
        if isinstance(a, bool):
            a = int(a)
        if isinstance(a, int):
            tags += "i"
            data += struct.pack(">i", a)
        elif isinstance(a, float):
            tags += "f"
            data += struct.pack(">f", a)
        else:
            tags += "s"
            data += _pad(str(a).encode("utf-8"))
    return _pad(address.encode("utf-8")) + _pad(tags.encode("ascii")) + data


def osc_for(event: dict) -> bytes:
    """/relay/alert/sent <button> <from> <level>, /relay/alert/acknowledged <button> <seen_on> <reply>,
    /relay/alert/ended <button> <state>, /relay/message/sent <text> <from> <level>."""
    kind = event.get("event", "")
    if kind == "alert.sent":
        return osc_message("/relay/alert/sent", event.get("button", ""), event.get("from", ""), event.get("level", ""))
    if kind == "alert.acknowledged":
        return osc_message("/relay/alert/acknowledged", event.get("button", ""), event.get("seen_on", ""),
                           event.get("reply", ""))
    if kind == "alert.ended":
        return osc_message("/relay/alert/ended", event.get("button", ""), event.get("state", ""))
    if kind == "message.sent":
        return osc_message("/relay/message/sent", event.get("text", ""), event.get("from", ""), event.get("level", ""))
    return osc_message("/relay/test", event.get("from", ""))


# ------------------------------------------------------------------ sending (blocking: run in a thread)
def send_webhook(url: str, event: dict) -> str:
    """POST the event. Returns "" when it went, or a fixed sentence."""
    req = urllib.request.Request(url, data=body(event), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": "Relay"})
    try:
        with urllib.request.urlopen(req, timeout=WEBHOOK_TIMEOUT_S) as resp:   # noqa: S310 (http and https only)
            resp.read(1024)
        return ""
    except urllib.error.HTTPError:
        return "The webhook answered with an error."
    except (urllib.error.URLError, OSError, ValueError):
        return "The webhook could not be reached."


def send_osc(host: str, port: int, event: dict) -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(WEBHOOK_TIMEOUT_S)
            s.sendto(osc_for(event), (host, port))
        return ""
    except (OSError, ValueError):
        return "The OSC message could not be sent."


def deliver(cfg: dict, event: dict) -> list[str]:
    """Send `event` everywhere the settings say. Returns the fixed sentences for what failed."""
    errors = []
    if cfg["webhook"]["enabled"] and cfg["webhook"]["url"]:
        err = send_webhook(cfg["webhook"]["url"], event)
        if err:
            errors.append(err)
    if cfg["osc"]["enabled"] and cfg["osc"]["host"]:
        err = send_osc(cfg["osc"]["host"], cfg["osc"]["port"], event)
        if err:
            errors.append(err)
    return errors
