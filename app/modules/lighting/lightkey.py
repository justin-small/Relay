"""Lightkey backend (#383): fires a Lightkey cue over OSC (UDP, port 21600). Lightkey is macOS only.

Lightkey 4 takes OSC over UDP or TCP on 21600 (what it listens on by default). The address Relay sends is the one
Lightkey's "External Control > Copy OSC Address" writes and the Bitfocus Companion module for Lightkey uses:

    /live/<page>/cue/<cue>/activate     (or deactivate)    optional argument: a float, the fade time in seconds

Names have their spaces written as underscores. `<page>` may be `selected` for the page open in Lightkey. Lightkey's
OSC has no list of cues and nothing Relay can read back (its feedback is MIDI), so `can_list` and `can_report_state`
are off and a cue never shows on or off.

A cue id is `<page>:<cue>` in that underscore form (`My_Control_Panel:Walk-in`). Only letters, digits and `_ . -` are
allowed in either name: the characters OSC treats as patterns (`*` `?` `[` `]` `{` `}`) are refused so a press can
never hit every cue. Press activates and release deactivates; a press is sent once and never retried.
"""
from __future__ import annotations

import asyncio
import re

from ... import netsock
from . import osc
from .backend import Backend, BackendError, register

TRAFFIC = "lighting"
DEFAULT_PORT = 21600
_NAME_RE = re.compile(r"[A-Za-z0-9_.\-]{1,64}")


def address_for(cue_id: str, action: str = "activate") -> str:
    page, sep, cue = cue_id.partition(":")
    if not sep or not _NAME_RE.fullmatch(page) or not _NAME_RE.fullmatch(cue):
        raise BackendError("A Lightkey cue id is Page:Cue with spaces written as underscores, like "
                           "My_Control_Panel:Walk-in. Use letters, digits, _ . and - only.")
    return f"/live/{page}/cue/{cue}/{action}"


@register
class Lightkey(Backend):
    id = "lightkey"
    label = "Lightkey (OSC, macOS)"

    def _target(self) -> tuple[str, int]:
        host = self.settings.get("host") or ""
        if not host:
            raise BackendError("Enter the address of the Mac running Lightkey first.")
        return host, self.settings.get("port") or DEFAULT_PORT

    def _packet(self, address: str) -> bytes:
        fade = self.settings.get("fade")
        if isinstance(fade, (int, float)) and not isinstance(fade, bool):
            return osc.message(address, float(fade))
        return osc.message(address)

    async def _send(self, data: bytes) -> None:
        host, port = self._target()
        try:
            sock = await asyncio.to_thread(netsock.udp_socket, TRAFFIC)
            try:
                await asyncio.to_thread(sock.sendto, data, (host, port))
            finally:
                sock.close()
        except (OSError, netsock.RouteError):      # RouteError and gaierror are OSErrors
            raise BackendError(netsock.failure(
                TRAFFIC, "Could not send to Lightkey. Check the address and port.")) from None

    async def press(self, cue_id: str) -> None:
        await self._send(self._packet(address_for(cue_id, "activate")))

    async def release(self, cue_id: str) -> None:
        await self._send(self._packet(address_for(cue_id, "deactivate")))

    async def test(self) -> dict:
        try:
            host, port = self._target()
            await self._send(osc.message("/relay/test"))
        except BackendError as e:
            return {"ok": False, "detail": str(e)}
        except Exception:
            return {"ok": False, "detail": "The test message could not be sent."}
        return {"ok": True, "detail": f"Sent a test message to {host} port {port}. Lightkey does not answer OSC, so "
                                      "Relay cannot tell it arrived: check Lightkey is running on that Mac and its "
                                      "OSC log (Window menu) shows the message."}
