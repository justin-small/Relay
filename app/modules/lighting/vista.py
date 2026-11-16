"""Chroma-Q Vista backend (#382): fires a Vista playback button over OSC (UDP).

Vista 3 R5 and later: turn OSC on under File > Preferences > OSC (OSC Server Enabled) and give Relay's address as an
OSC client. Vista listens on port 8000 unless the operator changed it. The addresses and argument format are the ones
the Bitfocus Companion module for Vista (companion-module-chromaq-vista) sends, which is what is known to work:

    /V3/console/<type>/<console>/playbackButton/panel/<p>/bank/<b>/row/<r>/col/<c>    OSC string "true", then "false"

Vista has no OSC "jump to cue" (R6 plans Skip/Jump to Step), so a cue here is a playback button or action grid cell
that runs the cuelist, defined by hand on a cue page. There is no button list and no state: nothing comes back that
Relay uses, so `can_list` and `can_report_state` are off and a Vista button never shows on or off.

A cue id says which button: `<type>.<console>.<panel>.<bank>.<row>.<col>` (`EX.1.0.1.0.0`), or without the type
(`1.0.1.0.0`) to use the Console type setting. Companion warns that an out-of-range number has crashed Vista, so every
number is checked against the console's own limits (panel 0-10, bank 0-100, row 0-11, column 0-59) before anything
is sent. A press is a tap: "true", 20 ms, "false", sent once and never retried (UDP has no answer to wait for).
"""
from __future__ import annotations

import asyncio
import re

from ... import netsock
from .backend import Backend, BackendError, register

TRAFFIC = "lighting"
DEFAULT_PORT = 8000
DEFAULT_TYPE = "EX"
TAP_S = 0.02
_TYPE_RE = re.compile(r"[A-Za-z0-9]{1,8}")
LIMITS = (("console", 1, 9), ("panel", 0, 10), ("bank", 0, 100), ("row", 0, 11), ("col", 0, 59))


def _pad(b: bytes) -> bytes:
    return b + b"\0" * (4 - len(b) % 4)


def osc_message(address: str, *strings: str) -> bytes:
    """An OSC 1.0 message whose arguments are all strings."""
    out = _pad(address.encode("ascii")) + _pad(("," + "s" * len(strings)).encode("ascii"))
    for s in strings:
        out += _pad(s.encode("utf-8"))
    return out


def address_for(cue_id: str, default_type: str = DEFAULT_TYPE) -> str:
    """The playback button's OSC address for a cue id, or BackendError saying what is wrong with the id."""
    parts = cue_id.split(".")
    if len(parts) == 5:
        parts = [default_type or DEFAULT_TYPE] + parts
    if len(parts) != 6:
        raise BackendError("A Vista cue id is type.console.panel.bank.row.column, like EX.1.0.1.0.0.")
    ctype, nums = parts[0], parts[1:]
    if not _TYPE_RE.fullmatch(ctype):
        raise BackendError("The console type is letters and digits only, like EX.")
    values = []
    for (name, lo, hi), text in zip(LIMITS, nums):
        if not text.isdigit() or not lo <= int(text) <= hi:
            raise BackendError(f"The Vista {name} must be a number from {lo} to {hi}.")
        values.append(int(text))
    c, p, b, r, col = values
    return f"/V3/console/{ctype}/{c}/playbackButton/panel/{p}/bank/{b}/row/{r}/col/{col}"


@register
class Vista(Backend):
    id = "vista"
    label = "Chroma-Q Vista (OSC)"

    def _target(self) -> tuple[str, int]:
        host = self.settings.get("host") or ""
        if not host:
            raise BackendError("Enter the Vista console's address first.")
        return host, self.settings.get("port") or DEFAULT_PORT

    async def _send(self, *packets: tuple[bytes, float]) -> None:
        """Send each packet, then wait its delay, on one UDP socket through the lighting network port."""
        host, port = self._target()
        try:
            sock = await asyncio.to_thread(netsock.udp_socket, TRAFFIC)
            try:
                for data, delay in packets:
                    await asyncio.to_thread(sock.sendto, data, (host, port))
                    if delay:
                        await asyncio.sleep(delay)
            finally:
                sock.close()
        except (OSError, netsock.RouteError):      # RouteError is an OSError; gaierror too
            raise BackendError(netsock.failure(
                TRAFFIC, "Could not send to the Vista console. Check its address and port.")) from None

    async def press(self, cue_id: str) -> None:
        addr = address_for(cue_id, self.settings.get("console") or DEFAULT_TYPE)
        await self._send((osc_message(addr, "true"), TAP_S), (osc_message(addr, "false"), 0))

    async def release(self, cue_id: str) -> None:
        addr = address_for(cue_id, self.settings.get("console") or DEFAULT_TYPE)
        await self._send((osc_message(addr, "false"), 0))

    async def test(self) -> dict:
        try:
            host, port = self._target()
            await self._send((osc_message("/relay/test"), 0))
        except BackendError as e:
            return {"ok": False, "detail": str(e)}
        except Exception:
            return {"ok": False, "detail": "The test message could not be sent."}
        return {"ok": True, "detail": f"Sent a test message to {host} port {port}. Vista does not answer OSC, so Relay "
                                      "cannot tell it arrived: in Vista, check File > Preferences > OSC has OSC Server "
                                      "Enabled and this computer as an OSC client."}
