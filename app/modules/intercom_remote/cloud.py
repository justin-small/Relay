"""The Cloudflare Realtime calls the bridge makes, through Relay's cloud (relay-cloud, `/v1/realtime/*`).

The Realtime app secret lives only in relay-cloud; this install proves itself with the secret it made when it enrolled
(remote_access) and can act only on sessions it opened there. Only session control and SDP pass through. Blocking:
callers use `run`, which keeps the event loop free. Errors are `CloudError`, whose text is a fixed sentence.
"""
from __future__ import annotations

import asyncio
from typing import Any

from ..remote_access import cloud as rcloud
from ..remote_access.service import enrolled, settings

CloudError = rcloud.CloudError


def _base_and_secret() -> tuple[str, str]:
    s = settings()
    if not enrolled(s):
        raise CloudError("unauthorised")
    return s["cloud_url"], s["secret"]


def _call(method: str, path: str, body: dict | None = None) -> dict:
    base, secret = _base_and_secret()
    try:
        return rcloud.call(base, method, path, body, secret=secret)
    except CloudError as exc:
        if exc.status == 429 and exc.kind != "too_many_sessions":      # whatever the cloud called it, it is busy
            raise CloudError("rate_limited", 429) from None
        raise


async def run(fn, *args: Any):
    return await asyncio.get_running_loop().run_in_executor(None, fn, *args)


def open_session() -> str:
    out = _call("POST", "/v1/realtime/sessions")
    sid = out.get("sessionId")
    if not isinstance(sid, str) or not sid:
        raise CloudError("unexpected")
    return sid


def tracks_new(session_id: str, body: dict) -> dict:
    return _call("POST", f"/v1/realtime/sessions/{session_id}/tracks/new", body)


def renegotiate(session_id: str, body: dict) -> dict:
    return _call("PUT", f"/v1/realtime/sessions/{session_id}/renegotiate", body)


def tracks_close(session_id: str, body: dict) -> dict:
    return _call("PUT", f"/v1/realtime/sessions/{session_id}/tracks/close", body)


def close_session(session_id: str) -> None:
    _call("DELETE", f"/v1/realtime/sessions/{session_id}")


def reachable() -> bool:
    """Relay's cloud answers its health check. False for anything else; never raises."""
    try:
        base, _ = _base_and_secret()
        return rcloud.health(base)
    except CloudError:
        return False
