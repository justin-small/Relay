"""ShowXpress / TheLightingController backend (#378): its Live API, a line protocol over TCP (port 7348).

Lines end with CRLF and fields are separated by `|`. The client says `HELLO|<app name>|<password>`; the controller
answers `HELLO`, or `ERROR|BAD PASSWORD` (and `ERROR|NOT LOGGED` for anything sent before a login). Then:

    BUTTON_LIST                   ->  BUTTON_LIST|<xml>: pages of buttons (name, column, line, type, flash, color,
                                      pressed) and faders (index, value), as text inside <button>/<fader>
    BUTTON_PRESS|<name>           press a button; BUTTON_RELEASE|<name> lets go of it
    FADER_CHANGE|<index>|<value>  move a fader, -100 (off) to 100; 0 is the normal maximum
    (pushed to a client) BUTTON_PRESS|name, BUTTON_RELEASE|name, FADER_CHANGE|index|value, INTERFACE_CHANGE

The protocol is from the Bitfocus Companion module for this controller (companion-module-thelightingcontroller).

A button is known only by its name, so a cue id is "b" plus ten hex digits of the name's hash (ids are short plain text,
names are not), mapped back from the last list; a rename is a new cue. A fader is "s<index>", set as 0 to 100 percent
(-100 to 0 on the controller; above 0 reads as 100). Relay never writes to a page with a button whose name holds a `|`
or a line break, since it could not be addressed: such buttons are left out.

A press toggles a button (a flash button is pressed and let go), and, like backend.py says, is sent once and never
retried. Pressing, releasing and moving a fader each open a short connection through the "lighting" traffic class.
Reading state keeps one connection open in the background that follows the controller's pushes and re-reads the list
every few seconds, reconnecting with a growing wait; `state()` and `levels()` answer from that copy, or read once
when it is down.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
from html.parser import HTMLParser

from ... import netsock
from .backend import Backend, BackendError, Cue, register

TRAFFIC = "lighting"
DEFAULT_PORT = 7348
APP = "thelightingcontrollerclient"
TIMEOUT = 5.0
REFRESH_S = 5.0
RETRY_MIN_S = 2.0
RETRY_MAX_S = 30.0
FLASH_HOLD_S = 0.15
LIST_MAX = 100_000        # the longest list we will read: 100 KB of XML


class _Auth(Exception):
    pass


class _Interface(HTMLParser):
    """The text inside a BUTTON_LIST reply. HTMLParser, not an XML library: the list comes off the network, and this
    reads only the few tags it needs and never follows an entity or a DTD."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.buttons: list[dict] = []
        self.faders: list[dict] = []
        self._cur: dict | None = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "button":
            self._cur = {"kind": "button", "name": "", "pressed": a.get("pressed") == "1",
                         "flash": a.get("flash") == "1"}
        elif tag == "fader":
            try:
                value = int(float(a.get("value") or 0))
                index = int(a.get("index") or "")
            except ValueError:
                return
            self._cur = {"kind": "fader", "name": "", "index": index, "value": value}

    def handle_data(self, data):
        if self._cur is not None:
            self._cur["name"] += data

    def handle_endtag(self, tag):
        c = self._cur
        if c is None or tag != c["kind"]:
            return
        self._cur = None
        c["name"] = c["name"].strip()
        if c["kind"] == "button":
            if c["name"] and "|" not in c["name"] and "\r" not in c["name"] and "\n" not in c["name"]:
                self.buttons.append(c)
        else:
            self.faders.append(c)


def parse_list(xml: str) -> tuple[list[dict], list[dict]]:
    p = _Interface()
    p.feed(xml[:LIST_MAX])
    p.close()
    return p.buttons, p.faders


def button_id(name: str) -> str:
    return "b" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:10]   # nosec B324: an id, not a secret


def percent(raw: int) -> int:
    return max(0, min(100, raw + 100))


class TheLightingController(Backend):
    id = "thelightingcontroller"
    label = "ShowXpress / TheLightingController (Live API)"
    can_list = True
    can_report_state = True
    can_set_level = True

    def __init__(self, settings: dict) -> None:
        super().__init__(settings)
        self._task: asyncio.Task | None = None
        self._live = False
        self._cues: list[Cue] = []
        self._names: dict[str, str] = {}      # cue id -> button name
        self._on: dict[str, bool] = {}
        self._pct: dict[str, int] = {}

    # ------------------------------------------------------------------------------------------ connection
    def _where(self) -> tuple[str, int]:
        host = self.settings.get("host") or ""
        if not host:
            raise BackendError("Enter the lighting computer's address first.")
        return host, self.settings.get("port") or DEFAULT_PORT

    @staticmethod
    async def _line(reader) -> str:
        raw = await asyncio.wait_for(reader.readuntil(b"\r\n"), TIMEOUT)
        return raw[:-2].decode("utf-8", "replace")

    async def _login(self):
        """(reader, writer) logged in, or raise _Auth / OSError."""
        host, port = self._where()
        reader, writer = await netsock.open_tcp(TRAFFIC, host, port, TIMEOUT, limit=LIST_MAX + 1024)
        try:
            writer.write(f"HELLO|{APP}|{self.settings.get('password') or ''}\r\n".encode())
            await writer.drain()
            while True:
                line = await self._line(reader)
                if line.startswith("HELLO"):
                    return reader, writer
                if line.startswith("ERROR"):
                    raise _Auth(line)
        except BaseException:
            writer.close()
            raise

    @contextlib.asynccontextmanager
    async def _session(self):
        try:
            reader, writer = await self._login()
        except BackendError:
            raise
        except _Auth:
            raise BackendError("The controller refused the password. Set the Live API password in Relay to "
                               "match the controller's.") from None
        except Exception:
            raise BackendError(netsock.failure(
                TRAFFIC, "Could not reach the lighting controller. Check that its software is running, "
                         "and the address and port.")) from None
        try:
            yield reader, writer
        except BackendError:
            raise
        except Exception:
            raise BackendError("The lighting controller did not answer.") from None
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _read_list(self, reader, writer) -> tuple[list[dict], list[dict]]:
        writer.write(b"BUTTON_LIST\r\n")
        await writer.drain()
        while True:
            line = await self._line(reader)
            if line.startswith("BUTTON_LIST|"):
                return parse_list(line[len("BUTTON_LIST|"):])

    # ------------------------------------------------------------------------------------------ reading
    def _apply(self, buttons: list[dict], faders: list[dict]) -> None:
        cues: list[Cue] = []
        names: dict[str, str] = {}
        on: dict[str, bool] = {}
        pct: dict[str, int] = {}
        for b in buttons:
            cid = button_id(b["name"])
            if cid in names:
                continue                      # two buttons with one name cannot be told apart
            names[cid] = b["name"]
            cues.append(Cue(cid, b["name"].replace("~", " ").strip() or b["name"]))
            on[cid] = b["pressed"]
        for f in faders:
            cid = f"s{f['index']}"
            cues.append(Cue(cid, f["name"].replace("~", " ").strip() or f"Fader {f['index']}", "slider"))
            pct[cid] = percent(f["value"])
        self._cues, self._names, self._on, self._pct = cues, names, on, pct

    async def _read(self) -> None:
        async with self._session() as (reader, writer):
            self._apply(*await self._read_list(reader, writer))

    def _start_live(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.get_running_loop().create_task(self._run_live())

    async def _run_live(self) -> None:
        wait = RETRY_MIN_S
        while True:
            try:
                reader, writer = await self._login()
                try:
                    self._apply(*await self._read_list(reader, writer))
                    self._live = True
                    wait = RETRY_MIN_S
                    while True:
                        try:
                            line = await asyncio.wait_for(reader.readuntil(b"\r\n"), REFRESH_S)
                        except asyncio.TimeoutError:
                            self._apply(*await self._read_list(reader, writer))
                            continue
                        self._push(line[:-2].decode("utf-8", "replace"))
                        if line.startswith(b"INTERFACE_CHANGE"):
                            self._apply(*await self._read_list(reader, writer))
                finally:
                    writer.close()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            self._live = False
            await asyncio.sleep(wait)
            wait = min(RETRY_MAX_S, wait * 2)

    def _push(self, line: str) -> None:
        """One pushed line from the controller: a button went down or up, or a fader moved."""
        parts = line.split("|")
        if parts[0] in ("BUTTON_PRESS", "BUTTON_RELEASE") and len(parts) >= 2:
            cid = button_id(parts[1])
            if cid in self._on:
                self._on[cid] = parts[0] == "BUTTON_PRESS"
        elif parts[0] == "FADER_CHANGE" and len(parts) >= 3:
            with contextlib.suppress(ValueError):
                cid = f"s{int(parts[1])}"
                if cid in self._pct:
                    self._pct[cid] = percent(int(float(parts[2])))

    async def _fresh(self) -> None:
        if not self._live:
            await self._read()
            self._start_live()

    async def list_cues(self) -> list[Cue]:
        await self._fresh()
        return list(self._cues)

    async def state(self) -> dict[str, bool]:
        await self._fresh()
        return dict(self._on)

    async def levels(self) -> dict[str, int]:
        await self._fresh()
        return dict(self._pct)

    # ------------------------------------------------------------------------------------------ acting
    async def _find(self, reader, writer, cue_id: str) -> dict:
        buttons, _ = await self._read_list(reader, writer)
        for b in buttons:
            if button_id(b["name"]) == cue_id:
                return b
        raise BackendError("The controller no longer has that button.")

    async def _send(self, writer, *parts: str) -> None:
        writer.write(("|".join(parts) + "\r\n").encode("utf-8"))
        await writer.drain()

    async def press(self, cue_id: str) -> None:
        if cue_id.startswith("s") and cue_id[1:].isdigit():
            raise BackendError("That is a fader: set its level instead.")
        async with self._session() as (reader, writer):
            b = await self._find(reader, writer, cue_id)
            if b["flash"]:
                await self._send(writer, "BUTTON_PRESS", b["name"])
                await asyncio.sleep(FLASH_HOLD_S)
                await self._send(writer, "BUTTON_RELEASE", b["name"])
            else:
                await self._send(writer, "BUTTON_RELEASE" if b["pressed"] else "BUTTON_PRESS", b["name"])
            await asyncio.sleep(0.05)

    async def release(self, cue_id: str) -> None:
        async with self._session() as (reader, writer):
            b = await self._find(reader, writer, cue_id)
            await self._send(writer, "BUTTON_RELEASE", b["name"])
            await asyncio.sleep(0.05)

    async def set_level(self, cue_id: str, percent_: int) -> None:
        if not (cue_id.startswith("s") and cue_id[1:].isdigit()):
            raise BackendError("That is not a fader.")
        async with self._session() as (reader, writer):
            await self._send(writer, "FADER_CHANGE", cue_id[1:], str(max(0, min(100, percent_)) - 100))
            await asyncio.sleep(0.05)

    async def test(self) -> dict:
        try:
            async with self._session() as (reader, writer):
                buttons, faders = await self._read_list(reader, writer)
        except BackendError as e:
            return {"ok": False, "detail": str(e)}
        except Exception:
            return {"ok": False, "detail": "The lighting controller did not answer."}
        n, f = len(buttons), len(faders)
        return {"ok": True, "detail": f"Connected to the lighting controller. Found {n} button{'s' if n != 1 else ''} "
                                      f"and {f} fader{'s' if f != 1 else ''}."}

    async def close(self) -> None:
        task, self._task = self._task, None
        self._live = False
        if task is not None:
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


register(TheLightingController)
