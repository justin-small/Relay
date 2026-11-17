"""The NDI router (#298): named outputs the booth points at any source, like a video matrix.

A **routed output** is an NDI source "Relay - <name>" that the NDI library itself points at another source (no decoding,
no re-encoding), so a TV or decoder set to receive "Relay - Lobby TV" once can be switched from here without touching it.
A **salvo** is a saved set of routes taken in one click. Both live in the module's settings, so routes survive a
restart: on start every output is created and pointed back where it was.

`routes` is [{"id", "name", "source"}] (source "" is nothing: the output shows no video) and `salvos` is
[{"id", "name", "routes": {output id: source}}]. A source is the full NDI name, "MACHINE (Source)", and may be offline.
Relay's own page outputs (#299) are NDI sources like any other, so they are valid inputs.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
from typing import Any, Callable

from .outputs import ndi_name

log = logging.getLogger("relay.ndi.router")

MAX_OUTPUTS = 16
MAX_SALVOS = 24
_ID = re.compile(r"[a-z0-9]{4,16}")


def _name(v: Any, limit: int = 60) -> str:
    return " ".join(v.split())[:limit] if isinstance(v, str) else ""


def _source(v: Any) -> str:
    return v.strip()[:160] if isinstance(v, str) else ""


def clean_routes(raw: Any) -> list[dict]:
    out: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not _ID.fullmatch(r["id"]):
            continue
        name = _name(r.get("name"))
        if name and r["id"] not in [x["id"] for x in out] and name.lower() not in [x["name"].lower() for x in out]:
            out.append({"id": r["id"], "name": name, "source": _source(r.get("source"))})
    return out[:MAX_OUTPUTS]


def clean_salvos(raw: Any, routes: list[dict]) -> list[dict]:
    known = {r["id"] for r in routes}
    out: list[dict] = []
    for s in raw if isinstance(raw, list) else []:
        if not isinstance(s, dict) or not isinstance(s.get("id"), str) or not _ID.fullmatch(s["id"]):
            continue
        name = _name(s.get("name"))
        if not name or s["id"] in [x["id"] for x in out]:
            continue
        rt = s.get("routes") if isinstance(s.get("routes"), dict) else {}
        out.append({"id": s["id"], "name": name, "routes": {k: _source(v) for k, v in rt.items() if k in known}})
    return out[:MAX_SALVOS]


def new_id() -> str:
    return secrets.token_hex(4)


class Router:
    def __init__(self, backend: Callable[[], Any], settings: Callable[[], dict], save: Callable[[dict], None],
                 notify: Callable[[], None], running: Callable[[], bool]) -> None:
        self._backend, self._settings, self._save, self._notify, self._running = backend, settings, save, notify, running
        self._live: dict[str, Any] = {}        # output id -> the NDI router instance
        self._err: dict[str, str] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Create every routed output and point it at its saved source."""
        for r in self._settings()["routes"]:
            self._open(r)

    def stop(self) -> None:
        with self._lock:
            live, self._live = list(self._live.values()), {}
        for inst in live:
            try:
                inst.close()
            except Exception:
                pass

    def _open(self, r: dict) -> bool:
        backend = self._backend()
        if backend is None or not self._running():
            return False
        with self._lock:
            if r["id"] in self._live:
                return True
        try:
            inst = backend.open_router(ndi_name(r["name"]))
        except Exception as exc:
            log.warning("NDI routed output %s could not open: %s", r["name"], exc)
            self._err[r["id"]] = "NDI would not create this output."
            return False
        self._err.pop(r["id"], None)
        with self._lock:
            self._live[r["id"]] = inst
        self._apply(inst, r["source"])
        return True

    @staticmethod
    def _apply(inst: Any, source: str) -> bool:
        try:
            return bool(inst.change(source) if source else inst.clear())
        except Exception:
            log.exception("NDI route change failed")
            return False

    # -------------------------------------------------------------------------------------------- editing
    def _write(self, routes: list[dict], salvos: list[dict]) -> None:
        self._save({"routes": routes, "salvos": clean_salvos(salvos, routes)})

    def add_output(self, name: str) -> dict | str:
        """The new output, or the sentence saying why not."""
        s = self._settings()
        name = _name(name)
        if not name:
            return "Give the output a name."
        if len(s["routes"]) >= MAX_OUTPUTS:
            return f"At most {MAX_OUTPUTS} routed outputs."
        if name.lower() in [r["name"].lower() for r in s["routes"]]:
            return "There is already an output with that name."
        r = {"id": new_id(), "name": name, "source": ""}
        self._write(s["routes"] + [r], s["salvos"])
        self._open(r)
        self._notify()
        return r

    def remove_output(self, oid: str) -> bool:
        s = self._settings()
        if oid not in [r["id"] for r in s["routes"]]:
            return False
        with self._lock:
            inst = self._live.pop(oid, None)
        if inst:
            inst.close()
        self._err.pop(oid, None)
        self._write([r for r in s["routes"] if r["id"] != oid], s["salvos"])
        self._notify()
        return True

    def route(self, oid: str, source: str) -> str:
        """'' on success, else the sentence saying why not."""
        s = self._settings()
        r = next((x for x in s["routes"] if x["id"] == oid), None)
        if r is None:
            return "That output does not exist."
        source = _source(source)
        if source.endswith("(" + ndi_name(r["name"]) + ")") or source == ndi_name(r["name"]):
            return "An output cannot show itself."
        self._set(s, {oid: source})
        self._notify()
        if not self._running():
            return ""        # saved; it applies when NDI is on
        if oid not in self._live and not self._open({**r, "source": source}):
            return self._err.get(oid, "NDI is not running.")
        return "" if self._apply(self._live[oid], source) else "NDI would not switch that output."

    def _set(self, s: dict, changes: dict[str, str]) -> None:
        routes = [{**r, "source": changes.get(r["id"], r["source"])} for r in s["routes"]]
        self._write(routes, s["salvos"])

    # -------------------------------------------------------------------------------------------- salvos
    def save_salvo(self, name: str, sid: str = "") -> dict | str:
        """Save the current routes as a salvo; with `sid` an existing one is replaced."""
        s = self._settings()
        name = _name(name)
        if not name:
            return "Give the preset a name."
        cur = {r["id"]: r["source"] for r in s["routes"]}
        old = next((x for x in s["salvos"] if x["id"] == sid), None)
        if sid and old is None:
            return "That preset does not exist."
        if not old and len(s["salvos"]) >= MAX_SALVOS:
            return f"At most {MAX_SALVOS} presets."
        if name.lower() in [x["name"].lower() for x in s["salvos"] if x["id"] != sid]:
            return "There is already a preset with that name."
        sv = {"id": sid or new_id(), "name": name, "routes": cur}
        self._write(s["routes"], [sv if x["id"] == sv["id"] else x for x in s["salvos"]] if old else s["salvos"] + [sv])
        self._notify()
        return sv

    def delete_salvo(self, sid: str) -> bool:
        s = self._settings()
        if sid not in [x["id"] for x in s["salvos"]]:
            return False
        self._write(s["routes"], [x for x in s["salvos"] if x["id"] != sid])
        self._notify()
        return True

    def take(self, sid: str) -> str:
        """Apply a salvo: every output it names is pointed at its saved source. '' on success."""
        s = self._settings()
        sv = next((x for x in s["salvos"] if x["id"] == sid), None)
        if sv is None:
            return "That preset does not exist."
        changes = {k: v for k, v in sv["routes"].items() if k in [r["id"] for r in s["routes"]]}
        self._set(s, changes)
        self._notify()
        bad = []
        if self._running():
            for r in s["routes"]:
                if r["id"] not in changes:
                    continue
                if r["id"] not in self._live and not self._open({**r, "source": changes[r["id"]]}):
                    bad.append(r["name"])
                elif not self._apply(self._live[r["id"]], changes[r["id"]]):
                    bad.append(r["name"])
        return "NDI would not switch: " + ", ".join(bad) + "." if bad else ""

    # -------------------------------------------------------------------------------------------- state
    def snapshot(self) -> dict:
        s = self._settings()
        return {"routes": [{**r, "ndi": ndi_name(r["name"]), "live": r["id"] in self._live,
                            "error": self._err.get(r["id"], "")} for r in s["routes"]],
                "salvos": s["salvos"]}
