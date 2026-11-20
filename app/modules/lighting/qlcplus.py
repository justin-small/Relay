"""QLC+ v4 backend (#381): the virtual console over QLC+'s web API (WebSocket, QLC+ started with `-w`).

Messages are `|`-separated text. `QLC+API|getWidgetsList` answers `...|id|name|id|name`; `getWidgetType|id` says
"Button" or "Slider"; `getWidgetStatus|id` is 255 when a button is on (a slider's own value is 0 to 255); a bare
`id|255` presses a button or moves a slider. A button that is a toggle in QLC+ toggles. Functions (scenes, chases)
are `getFunctionsList`, `getFunctionStatus|id` ("Running" or "Stopped") and `setFunctionStatus|id|1/0`.

Cue ids: a button is its widget id ("7"), a slider is "s14", a function is "f12", so one id says what it is.

Presses and level changes each open a short connection through the "lighting" traffic class and are never retried
(backend.py). Reading state keeps one connection open in the background: QLC+ pushes `FUNCTION|id|Running` to other
clients but not widget changes, so a push or a few seconds' quiet makes it re-read every status, and `state()` and
`levels()` answer from that copy. If the connection is down they read once instead. QLC+'s OSC and MIDI routes are
deliberately not used: they need every button mapped by hand and report state poorly. The web API has no command for
the virtual console's frames and pages or for a blackout, so a blackout is a button in the QLC+ project.
"""
from __future__ import annotations

import asyncio
import contextlib

from ... import netsock
from .backend import Backend, BackendError, Cue, register

TRAFFIC = "lighting"
DEFAULT_PORT = 9999
PATH = "/qlcplusWS"
TIMEOUT = 5.0
REFRESH_S = 5.0        # re-read every status this often when QLC+ is quiet
RETRY_S = 3.0          # wait before reconnecting the background connection


class QLCPlus(Backend):
    id = "qlcplus"
    label = "QLC+ (web API)"
    about = "QLC+ started with -w, on any computer."
    fields = ("host", "port", "functions")
    default_port = DEFAULT_PORT
    can_list = True
    can_report_state = True
    can_set_level = True

    def __init__(self, settings: dict) -> None:
        super().__init__(settings)
        self._task: asyncio.Task | None = None
        self._live = False                       # the background copy below is current
        self._cues: list[Cue] = []
        self._on: dict[str, bool] = {}
        self._pct: dict[str, int] = {}

    def _url(self) -> str:
        host = self.settings.get("host") or ""
        if not host:
            raise BackendError("Enter QLC+'s address first.")
        if ":" in host:                      # an IPv6 literal
            host = f"[{host}]"
        return f"ws://{host}:{self.settings.get('port') or DEFAULT_PORT}{PATH}"

    @contextlib.asynccontextmanager
    async def _session(self):
        url = self._url()
        try:
            async with netsock.websocket_session(TRAFFIC, url, open_timeout=TIMEOUT) as ws:
                yield ws
        except BackendError:
            raise
        except Exception:
            raise BackendError(netsock.failure(
                TRAFFIC, "Could not reach QLC+. Start it with -w and check the address and port.")) from None

    @staticmethod
    async def _ask(ws, *parts: str) -> list[str]:
        """Send `QLC+API|<parts>` and return the reply's fields after the command name."""
        await ws.send("QLC+API|" + "|".join(parts))
        while True:
            msg = await asyncio.wait_for(ws.recv(), TIMEOUT)
            if isinstance(msg, str) and msg.split("|", 2)[:2] == ["QLC+API", parts[0]]:
                return msg.split("|")[2:]

    # ------------------------------------------------------------------------------------------ reading
    async def _scan(self, ws) -> list[Cue]:
        """Every button and slider on the virtual console, then (when the setting is on) every function."""
        f = await self._ask(ws, "getWidgetsList")
        out: list[Cue] = []
        for wid, name in zip(f[0::2], f[1::2]):
            if not wid.isdigit():
                continue
            kind = (await self._ask(ws, "getWidgetType", wid))[1:2]
            if kind == ["Button"]:
                out.append(Cue(wid, name.strip() or wid))
            elif kind == ["Slider"]:
                out.append(Cue("s" + wid, name.strip() or "Slider " + wid, "slider"))
        if self.settings.get("functions"):
            f = await self._ask(ws, "getFunctionsList")
            for fid, name in zip(f[0::2], f[1::2]):
                if fid.isdigit():
                    out.append(Cue("f" + fid, name.strip() or "Function " + fid, "function"))
        return out

    async def _statuses(self, ws, cues: list[Cue]) -> tuple[dict[str, bool], dict[str, int]]:
        on: dict[str, bool] = {}
        pct: dict[str, int] = {}
        for c in cues:
            if c.kind == "button":
                on[c.id] = (await self._ask(ws, "getWidgetStatus", c.id))[1:2] == ["255"]
            elif c.kind == "slider":
                raw = (await self._ask(ws, "getWidgetStatus", c.id[1:]))[1:2]
                pct[c.id] = round(min(255, max(0, int(raw[0]))) * 100 / 255) if raw and raw[0].isdigit() else 0
            else:
                on[c.id] = (await self._ask(ws, "getFunctionStatus", c.id[1:]))[0:1] == ["Running"]
        return on, pct

    async def _read(self) -> None:
        """Read everything once on a new short connection."""
        async with self._session() as ws:
            self._cues = await self._scan(ws)
            self._on, self._pct = await self._statuses(ws, self._cues)

    def _start_live(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run_live())

    async def _run_live(self) -> None:
        """The held connection: scan, then re-read every status on a FUNCTION push or after REFRESH_S of quiet."""
        while True:
            try:
                async with netsock.websocket_session(TRAFFIC, self._url(), open_timeout=TIMEOUT) as ws:
                    self._cues = await self._scan(ws)
                    while True:
                        self._on, self._pct = await self._statuses(ws, self._cues)
                        self._live = True
                        try:
                            msg = await asyncio.wait_for(ws.recv(), REFRESH_S)
                            while True:     # drain a burst of pushes into one re-read
                                msg = await asyncio.wait_for(ws.recv(), 0.1)
                        except asyncio.TimeoutError:
                            pass
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            self._live = False
            await asyncio.sleep(RETRY_S)

    async def list_cues(self) -> list[Cue]:
        if not self._live:
            await self._read()
            self._start_live()
        return list(self._cues)

    async def state(self) -> dict[str, bool]:
        if not self._live:
            await self._read()
            self._start_live()
        return dict(self._on)

    async def levels(self) -> dict[str, int]:
        if not self._live:
            await self._read()
            self._start_live()
        return dict(self._pct)

    # ------------------------------------------------------------------------------------------ acting
    async def press(self, cue_id: str) -> None:
        if cue_id.startswith("s") and cue_id[1:].isdigit():
            raise BackendError("That is a slider: set its level instead.")
        if cue_id.startswith("f") and cue_id[1:].isdigit():
            await self._toggle_function(cue_id[1:])
            return
        if not cue_id.isdigit():
            raise BackendError("That is not a QLC+ button.")
        async with self._session() as ws:
            await ws.send(f"{cue_id}|255")
            await asyncio.sleep(0.05)       # let QLC+ read it before we close

    async def _toggle_function(self, fid: str) -> None:
        async with self._session() as ws:
            running = (await self._ask(ws, "getFunctionStatus", fid))[0:1] == ["Running"]
            await ws.send(f"QLC+API|setFunctionStatus|{fid}|{0 if running else 1}")
            await asyncio.sleep(0.05)

    async def set_level(self, cue_id: str, percent: int) -> None:
        if not (cue_id.startswith("s") and cue_id[1:].isdigit()):
            raise BackendError("That is not a slider.")
        async with self._session() as ws:
            await ws.send(f"{cue_id[1:]}|{round(min(100, max(0, percent)) * 255 / 100)}")
            await asyncio.sleep(0.05)

    async def test(self) -> dict:
        try:
            async with self._session() as ws:
                cues = await self._scan(ws)
        except BackendError as e:
            return {"ok": False, "detail": str(e)}
        except Exception:
            return {"ok": False, "detail": "QLC+ did not answer."}
        n = sum(1 for c in cues if c.kind == "button")
        sl = sum(1 for c in cues if c.kind == "slider")
        fn = sum(1 for c in cues if c.kind == "function")
        bits = [f"{n} button{'s' if n != 1 else ''}", f"{sl} slider{'s' if sl != 1 else ''}"]
        if self.settings.get("functions"):
            bits.append(f"{fn} function{'s' if fn != 1 else ''}")
        return {"ok": True, "detail": "Connected to QLC+. Found " + ", ".join(bits) + "."}

    async def close(self) -> None:
        task, self._task = self._task, None
        self._live = False
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


register(QLCPlus)
