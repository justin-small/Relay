"""Runs the ATEM client for the module and publishes its tally into core's shared state (#243)."""
from __future__ import annotations

import asyncio
import re

from ... import config, tally
from . import protocol as p
from .client import AtemClient

SOURCE = "atem"
KEY = "atem"
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")


def clean_settings(raw) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    host = raw.get("host") if isinstance(raw.get("host"), str) else ""
    host = host.strip()
    if not _HOST_RE.fullmatch(host):
        host = ""
    port = raw.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        port = p.PORT
    return {"host": host, "port": port}


class Service:
    def __init__(self) -> None:
        self.client: AtemClient | None = None
        self._running = False

    def publish(self) -> None:
        c = self.client
        if c is not None and c.connected:
            tally.tally.publish(SOURCE, c.cameras())
        else:
            tally.tally.clear(SOURCE)

    def start(self) -> None:
        self._running = True
        self._open()

    async def stop(self) -> None:
        self._running = False
        await self._close()
        tally.tally.clear(SOURCE)

    def _open(self) -> None:
        s = clean_settings(config.get().get(KEY))
        if self._running and s["host"]:
            self.client = AtemClient(s["host"], s["port"], on_change=self.publish)
            self.client.start()

    async def _close(self) -> None:
        if self.client is not None:
            client, self.client = self.client, None
            await client.close()

    async def restart(self) -> None:
        await self._close()
        tally.tally.clear(SOURCE)
        self._open()

    # ------------------------------------------------------------------------------------------ what the page shows
    def snapshot(self) -> dict:
        s = clean_settings(config.get().get(KEY))
        c = self.client
        out = {"host": s["host"], "port": s["port"], "configured": bool(s["host"]), "connected": False,
               "error": c.error if c else "", "product": "", "inputs": [], "mix_effects": [], "macros": [],
               "downstream_keyers": 0}
        if c is None or not c.connected:
            return out
        st = c.state
        out.update(connected=True, product=st.product, downstream_keyers=st.downstream_keyers,
                   inputs=[{"id": i, "name": v["long"] or v["short"] or f"Input {i}"}
                           for i, v in sorted(st.inputs.items()) if i <= 40 or i in (1000, 2001, 2002)],
                   mix_effects=[{"me": m, "program": st.program.get(m), "preview": st.preview.get(m)}
                                for m in range(st.mix_effects)],
                   macros=[{"slot": k, "name": v} for k, v in sorted(st.macros.items())])
        return out

    # ------------------------------------------------------------------------------------------ control
    def _send(self, *cmds: bytes) -> bool:
        return bool(self.client and self.client.send(*cmds))

    def cut(self, me: int) -> bool:
        return self._send(p.cut(me))

    def auto(self, me: int) -> bool:
        return self._send(p.auto(me))

    def fade_to_black(self, me: int) -> bool:
        return self._send(p.fade_to_black(me))

    def set_program(self, me: int, source: int) -> bool:
        return self._send(p.set_program(me, source))

    def set_preview(self, me: int, source: int) -> bool:
        return self._send(p.set_preview(me, source))

    def downstream(self, keyer: int, action: str) -> bool:
        version = self.client.state.version if self.client else 0
        if action == "auto":
            return self._send(p.downstream_auto(keyer, version))
        return self._send(p.downstream_on_air(keyer, action == "on"))

    def macro(self, slot: int, action: str) -> bool:
        return self._send(p.macro_run(slot) if action == "run" else p.macro_stop())

    def limits(self) -> dict:
        st = self.client.state if self.client else None
        return {"mix_effects": st.mix_effects if st else 0, "keyers": st.downstream_keyers if st else 0}


service = Service()
