"""The NDI router (#298): named outputs the booth points at any source, like a video matrix.

A **routed output** is an NDI source "Relay - <name>" that the NDI library itself points at another source (no decoding,
no re-encoding), so a TV or decoder set to receive "Relay - Lobby TV" once can be switched from here without touching it.

Since #544 the routes are core routes (app/routing.py): each output is a video channel (an NDI destination end, key
"route:<id>") and what it shows is a route to the channel of an NDI source (key "src:<full NDI name>"), so the same output can
be routed from the Blackmagic page or a board button. This module keeps only the outputs it creates, `router_outputs`
[{"id", "name"}], applies a route when core asks (`provide("routes.ndi")`), and says what each output really shows. A
**salvo** is a core preset with scope "ndi". Routes and presets saved in settings before #544 move to core once.
"""
from __future__ import annotations

import logging
import re
import secrets
import threading
from typing import Any, Callable

from ... import channels, config, db, routing
from .outputs import ndi_name

log = logging.getLogger("relay.ndi.router")

MAX_OUTPUTS = 16
MAX_SALVOS = 24
SCOPE = "ndi"
KEY = "ndi"
OUT = "route:"
SRC = "src:"
_ID = re.compile(r"[a-z0-9]{4,16}")
DOWN = "The database is down, so routes cannot be changed right now."


def _name(v: Any, limit: int = 60) -> str:
    return " ".join(v.split())[:limit] if isinstance(v, str) else ""


def _source(v: Any) -> str:
    return v.strip()[:160] if isinstance(v, str) else ""


def clean_outputs(raw: Any) -> list[dict]:
    out: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict) or not isinstance(r.get("id"), str) or not _ID.fullmatch(r["id"]):
            continue
        name = _name(r.get("name"))
        if name and r["id"] not in [x["id"] for x in out] and name.lower() not in [x["name"].lower() for x in out]:
            out.append({"id": r["id"], "name": name, "audio": _source(r.get("audio"))})
    return out[:MAX_OUTPUTS]


def new_id() -> str:
    return secrets.token_hex(4)


def _source_name(chan: dict | None) -> str:
    """The full NDI name of the source a channel stands for ("" when it has no NDI source end)."""
    for e in (chan or {}).get("ends", []):
        if e["provider"] == KEY and e["key"].startswith(SRC):
            return e["key"][len(SRC):]
    return ""


class _Combined:
    """A routed output whose sound comes from another source than its picture (#301). The NDI routing call switches a whole
    source, so this output is a Rust combiner (`open_combiner`) instead, restarted with the picture each time the route
    changes. It answers the same calls as a routing instance."""

    def __init__(self, backend: Any, name: str, audio: str) -> None:
        self._backend, self._name, self._audio = backend, name, audio
        self._inst: Any = None
        self._lock = threading.Lock()
        self.change("")                          # the sound alone until a picture is chosen

    def change(self, source: str) -> bool:
        inst = self._backend.open_combiner(self._name, source, self._audio)     # may raise: the caller says so
        with self._lock:
            old, self._inst = self._inst, inst
        if old is not None:
            old.close()
        return True

    def clear(self) -> bool:
        return self.change("")

    def close(self) -> None:
        with self._lock:
            inst, self._inst = self._inst, None
        if inst is not None:
            inst.close()


class Router:
    def __init__(self, backend: Callable[[], Any], settings: Callable[[], dict], save: Callable[[dict], None],
                 notify: Callable[[], None], running: Callable[[], bool], report: Callable[[], None] = lambda: None) -> None:
        self._backend, self._settings, self._save, self._notify, self._running = backend, settings, save, notify, running
        self._report = report
        self._live: dict[str, Any] = {}        # output id -> the NDI router instance
        self._target: dict[str, str] = {}      # output id -> the source it was last pointed at (what NDI was told)
        self._err: dict[str, str] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------------------------------------- lifecycle
    def start(self) -> None:
        """Create every routed output, then have core point each at the source asked for."""
        self.migrate()
        for r in self._settings()["router_outputs"]:
            self._open(r)
        self._report()
        try:
            for problem in routing.reapply(KEY):
                log.info("NDI route: %s", problem)
        except db.Unavailable:
            log.info("NDI routes were not applied: the database is down")

    def stop(self) -> None:
        with self._lock:
            live, self._live, self._target = list(self._live.values()), {}, {}
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
            inst = _Combined(backend, ndi_name(r["name"]), r["audio"]) if r.get("audio") else backend.open_router(ndi_name(r["name"]))
        except Exception as exc:
            log.warning("NDI routed output %s could not open: %s", r["name"], exc)
            self._err[r["id"]] = "NDI would not create this output."
            return False
        self._err.pop(r["id"], None)
        with self._lock:
            self._live[r["id"]] = inst
        return True

    @staticmethod
    def _apply(inst: Any, source: str) -> bool:
        try:
            return bool(inst.change(source) if source else inst.clear())
        except Exception:
            log.exception("NDI route change failed")
            return False

    # -------------------------------------------------------------------------------------------- core's side
    def api(self) -> dict:
        """What core's routes ask of this module (app/routing.py)."""
        return {"exclusive": True, "gain": False, "apply": self._core_apply, "read": self._core_read, "permission": "ndi.manage"}

    def _core_apply(self, end: dict, wanted: list[dict]) -> str:
        oid = end["key"][len(OUT):]
        r = next((x for x in self._settings()["router_outputs"] if x["id"] == oid), None)
        if r is None:
            return "That output does not exist."
        names = [e["key"][len(SRC):] for w in wanted for e in w["ends"] if e["provider"] == KEY and e["key"].startswith(SRC)]
        if wanted and not names:
            return "NDI can only show an NDI source."
        source = names[0] if names else ""
        if source.endswith("(" + ndi_name(r["name"]) + ")") or source == ndi_name(r["name"]):
            return "An output cannot show itself."
        if not self._running():
            return ""            # asked for; core applies it when NDI is on
        if oid not in self._live and not self._open(r):
            return self._err.get(oid, "NDI is not running.")
        if not self._apply(self._live[oid], source):
            return "NDI would not switch that output."
        self._target[oid] = source
        return ""

    def _core_read(self, end: dict) -> list[dict] | None:
        oid = end["key"][len(OUT):]
        if oid not in self._live:
            return None
        t = self._target.get(oid, "")
        return [{"provider": KEY, "key": SRC + t, "gain_db": None}] if t else []

    # -------------------------------------------------------------------------------------------- editing
    def _chans(self) -> dict[str, dict]:
        """{output id: its channel} for the outputs core knows."""
        return {e["key"][len(OUT):]: c for c in channels.of_provider(KEY) for e in c["ends"]
                if e["provider"] == KEY and e["key"].startswith(OUT)}

    def add_output(self, name: str) -> dict | str:
        """The new output, or the sentence saying why not."""
        s = self._settings()
        name = _name(name)
        if not name:
            return "Give the output a name."
        if len(s["router_outputs"]) >= MAX_OUTPUTS:
            return f"At most {MAX_OUTPUTS} routed outputs."
        if name.lower() in [r["name"].lower() for r in s["router_outputs"]]:
            return "There is already an output with that name."
        r = {"id": new_id(), "name": name, "audio": ""}
        self._save({"router_outputs": s["router_outputs"] + [r]})
        self._open(r)
        self._report()
        self._notify()
        return r

    def remove_output(self, oid: str) -> bool:
        s = self._settings()
        if oid not in [r["id"] for r in s["router_outputs"]]:
            return False
        with self._lock:
            inst = self._live.pop(oid, None)
            self._target.pop(oid, None)
        if inst:
            inst.close()
        self._err.pop(oid, None)
        self._save({"router_outputs": [r for r in s["router_outputs"] if r["id"] != oid]})
        try:
            self._report()                         # it is missing now, so core lets it go with its routes
            chan = self._chans().get(oid)
            if chan is not None:
                channels.forget(chan["id"])
        except (db.Unavailable, channels.Refused):
            pass
        self._notify()
        return True

    def set_audio(self, oid: str, source: str) -> str:
        """Take this output's sound from `source` ("" puts it back with the picture). '' on success."""
        s = self._settings()
        r = next((x for x in s["router_outputs"] if x["id"] == oid), None)
        if r is None:
            return "That output does not exist."
        source = _source(source)
        if source.endswith("(" + ndi_name(r["name"]) + ")") or source == ndi_name(r["name"]):
            return "An output cannot take sound from itself."
        shown = self._target.get(oid, "")
        with self._lock:
            old = self._live.pop(oid, None)
        if old is not None:
            old.close()
        r = {**r, "audio": source}
        self._save({"router_outputs": [r if x["id"] == oid else x for x in s["router_outputs"]]})
        if self._running() and self._open(r):
            if shown and not self._apply(self._live[oid], shown):
                self._notify()
                return "NDI would not switch that output."
        self._notify()
        return self._err.get(oid, "")

    def route(self, oid: str, source: str) -> str:
        """'' on success, else the sentence saying why not."""
        r = next((x for x in self._settings()["router_outputs"] if x["id"] == oid), None)
        if r is None:
            return "That output does not exist."
        source = _source(source)
        if source.endswith("(" + ndi_name(r["name"]) + ")") or source == ndi_name(r["name"]):
            return "An output cannot show itself."
        try:
            dest = self._chans().get(oid)
            if dest is None:
                self._report()
                dest = self._chans().get(oid)
            if dest is None:
                return "NDI has not listed that output yet."
            if source:
                got = routing.set(dest["ref"], channels.ensure(KEY, SRC + source, "video", "source", source))
            else:
                got = routing.clear(dest["ref"])
        except routing.Refused as exc:
            return routing.REFUSALS[exc.key]
        except db.Unavailable:
            return DOWN
        self._notify()
        return "; ".join(got["problems"])

    # -------------------------------------------------------------------------------------------- salvos
    def save_salvo(self, name: str, sid: str = "") -> dict | str:
        """Save what every output shows now as a preset; with `sid` an existing one is replaced."""
        try:
            held = routing.salvos(SCOPE)
            if not sid and len(held) >= MAX_SALVOS:
                return f"At most {MAX_SALVOS} presets."
            got = routing.save_salvo(name, [c["ref"] for c in self._chans().values()], SCOPE,
                                     int(sid) if sid.isdigit() else None)
        except routing.Refused as exc:
            return routing.REFUSALS[exc.key]
        except db.Unavailable:
            return DOWN
        self._notify()
        return {"id": str(got["id"]), "name": got["name"]}

    def delete_salvo(self, sid: str) -> bool:
        try:
            routing.delete_salvo(int(sid))
        except (routing.Refused, ValueError, db.Unavailable):
            return False
        self._notify()
        return True

    def take(self, sid: str) -> str:
        """Apply a preset: every output it names is pointed at its saved source. '' on success."""
        try:
            problems = routing.take_salvo(int(sid))
        except (routing.Refused, ValueError):
            return "That preset does not exist."
        except db.Unavailable:
            return DOWN
        self._notify()
        return "; ".join(problems)

    # -------------------------------------------------------------------------------------------- old data
    def migrate(self) -> None:
        """Routes and presets saved in settings before #544 become core routes, once: the outputs stay here, the sources
        they showed and the presets go to core, and the old keys are gone after."""
        raw = config.get().get(KEY)
        if not isinstance(raw, dict) or not (raw.get("routes") or raw.get("salvos")):
            return
        old = clean_legacy(raw.get("routes"))
        outputs = clean_outputs(raw.get("router_outputs")) or [{"id": r["id"], "name": r["name"]} for r in old]
        try:
            refs = {}
            for r in old:
                refs[r["id"]] = channels.ensure(KEY, OUT + r["id"], "video", "destination", ndi_name(r["name"]))
            for r in old:
                if r["source"] and refs.get(r["id"]):
                    src = channels.ensure(KEY, SRC + r["source"], "video", "source", r["source"])
                    if src:
                        routing.replace(refs[r["id"]], [(src, None)], apply=False)
            for sv in raw.get("salvos") if isinstance(raw.get("salvos"), list) else []:
                if not isinstance(sv, dict) or not isinstance(sv.get("routes"), dict):
                    continue
                cells = []
                for oid, source in sv["routes"].items():
                    if oid in refs and refs[oid]:
                        src = channels.ensure(KEY, SRC + _source(source), "video", "source", _source(source)) if _source(source) else None
                        cells.append((refs[oid], src, None))
                routing.import_salvo(sv.get("name"), SCOPE, cells)
        except db.Unavailable:
            return                                   # try again when the database is up
        kept = {**raw, "routes": [], "salvos": [], "router_outputs": outputs}  # allow-shared: emptied once moved
        config.save({KEY: kept}, replace=(KEY,))

    # -------------------------------------------------------------------------------------------- state
    def snapshot(self) -> dict:
        s = self._settings()
        try:
            world = channels.listing()
            chans = {e["key"][len(OUT):]: c for c in world for e in c["ends"]
                     if e["provider"] == KEY and e["key"].startswith(OUT)}
            seen = {v["destination"]: v for v in routing.listing(KEY)}
            names = {c["ref"]: _source_name(c) or c["shown"] for c in world}
            held = routing.salvos(SCOPE)
        except db.Unavailable:
            chans, seen, names, held = {}, {}, {}, []
        by_ref = {c["ref"]: oid for oid, c in chans.items()}
        routes = []
        for r in s["router_outputs"]:
            c = chans.get(r["id"])
            v = seen.get(c["ref"]) if c else None
            shown = names.get(v["sources"][0]["ref"], "") if v and v["sources"] else ""
            asked = names.get(v["wanted"][0]["ref"], "") if v and v["wanted"] else ""
            routes.append({**r, "audio": r.get("audio", ""), "ndi": ndi_name(r["name"]), "live": r["id"] in self._live, "error": self._err.get(r["id"], ""),
                           "source": shown, "asked": asked, "state": v["state"] if v else "none",
                           "ref": c["ref"] if c else ""})
        salvos = [{"id": str(sv["id"]), "name": sv["name"],
                   "routes": {by_ref[x["destination"]]: names.get(x["source"], "") if x["source"] else ""
                              for x in sv["routes"] if x["destination"] in by_ref}} for sv in held]
        return {"routes": routes, "salvos": salvos}


def clean_legacy(raw: Any) -> list[dict]:
    """The routes saved in settings before #544: [{"id", "name", "source"}]."""
    out: list[dict] = []
    for r in clean_outputs(raw):
        src = next((x.get("source") for x in raw if isinstance(x, dict) and x.get("id") == r["id"]), "")
        out.append({**r, "source": _source(src)})
    return out
