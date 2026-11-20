"""Settings, cue pages and the one active backend for the lighting module (#377)."""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import secrets
import uuid
from typing import Any

from ... import config, layout
from ...text import same_name
from . import lightkey, qlcplus, thelightingcontroller, vista  # noqa: F401  (each registers its backend)
from .backend import BACKENDS, Backend, BackendError, Cue

KEY = "lighting"
MAX_PAGES = 20
MAX_CUES = 200
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
_ID_RE = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


_KEY_RE = re.compile(r"[0-9a-f]{32}")
# A cue button's look when nothing is set: the colours the cue pages had before #421.
CUE_STYLE = {"shape": "rounded", "bg": "#2b5fd9", "fg": "#ffffff", "pressed": "#5b8bff", "icon": ""}
GRID = (4, 3)            # a new page's grid
FREE_GRID = (8, 6)       # the grid a page saved with free placement (#379) moves onto: fine enough to keep its shape


def new_key() -> str:
    """A page's screen key: 128 random bits, as 32 hex digits."""
    return secrets.token_hex(16)


def _legacy_places(p: dict, cues: list[dict]) -> tuple[int, int, list[dict]]:
    """(cols, rows, cues placed) for a page saved before #421, which has no grid of its own. A free page's buttons
    (x/y/fw/fh, percent of the page) go to the nearest cells of FREE_GRID; a grid page's (w/h cell spans, laid out by
    the browser in reading order) go one after another into four columns, as many rows as they need."""
    raws = {c.get("id"): c for c in (p.get("cues") if isinstance(p.get("cues"), list) else []) if isinstance(c, dict)}
    if p.get("free") is True:
        cols, rows = FREE_GRID
        placed = [{**c, **layout.from_percent(raws[c["id"]].get("x"), raws[c["id"]].get("y"), raws[c["id"]].get("fw", 25),
                                                raws[c["id"]].get("fh", 20), cols, rows)} for c in cues]
        return cols, rows, layout.place(placed, cols, rows)
    cols = GRID[0]
    spans = [{**c, "x": 0, "y": 0, "w": layout.number(raws[c["id"]].get("w"), 1, cols, 1),
              "h": layout.number(raws[c["id"]].get("h"), 1, layout.MAX_CELLS, 1)} for c in cues]
    placed = layout.place(spans, cols, layout.MAX_CELLS)
    rows = max([GRID[1]] + [c["y"] + c["h"] for c in placed])
    return cols, rows, placed


def clean_pages(raw: Any, keys: dict[str, str] | None = None) -> list[dict]:
    """Pages as [{"id", "name", "key_on", "key", "cols", "rows", "cues": [{"id", "name", "x", "y", "w", "h",
    "style"}]}]: bounded, unique page ids, a cue once per page, laid out on the page's grid (app/layout.py). A page's
    key is never taken from `raw` when `keys` (the saved ones, by page id) is given: only the key routes change it."""
    out: list[dict] = []
    seen: set[str] = set()
    for p in (raw if isinstance(raw, list) else [])[:MAX_PAGES]:
        if not isinstance(p, dict):
            continue
        name = _text(p.get("name"), 40)
        if not name:
            continue
        pid = p.get("id") if isinstance(p.get("id"), str) and _ID_RE.fullmatch(p["id"]) else ""
        if not pid or pid in seen:
            pid = uuid.uuid4().hex[:8]
        seen.add(pid)
        key = (keys or {}).get(pid, "") if keys is not None else p.get("key")
        key = key if isinstance(key, str) and _KEY_RE.fullmatch(key) else ""
        cues: list[dict] = []
        ids: set[str] = set()
        for c in (p.get("cues") if isinstance(p.get("cues"), list) else [])[:MAX_CUES]:
            if not isinstance(c, dict):
                continue
            cid = c.get("id") if isinstance(c.get("id"), str) else ""
            cid = cid.strip()
            if not _ID_RE.fullmatch(cid) or cid in ids:
                continue
            ids.add(cid)
            # Before #421 a cue kept its look flat on itself (label, shape, color, text_color).
            style = layout.clean_style(c.get("style") if isinstance(c.get("style"), dict) else c, CUE_STYLE)
            cues.append({"id": cid, "name": _text(c.get("name"), 60) or cid, "x": c.get("x"), "y": c.get("y"),
                         "w": c.get("w"), "h": c.get("h"), "style": style})
        if "cols" in p or "rows" in p:
            cols, rows = layout.clean_grid(p.get("cols"), p.get("rows"), GRID)
            cues = layout.place(cues, cols, rows)
        else:
            cols, rows, cues = _legacy_places(p, cues)
        out.append({"id": pid, "name": name, "key_on": bool(key) and p.get("key_on") is True, "key": key,
                    "cols": cols, "rows": rows, "cues": cues})
    return out


MAX_PROGRAMS = 8
_PROGRAM_ID_RE = re.compile(r"[a-z0-9_\-]{1,24}")


def clean_program(raw: Any) -> dict:
    """One lighting program's connection: {"id", "name", "backend", "host", "port", "password", "functions",
    "console", "fade"}."""
    raw = raw if isinstance(raw, dict) else {}
    backend = raw.get("backend") if isinstance(raw.get("backend"), str) else ""
    host = raw.get("host").strip() if isinstance(raw.get("host"), str) else ""
    if not _HOST_RE.fullmatch(host):
        host = ""
    port = raw.get("port")
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        port = 0
    password = raw.get("password") if isinstance(raw.get("password"), str) else ""
    fade = raw.get("fade")
    fade = round(float(fade), 3) if isinstance(fade, (int, float)) and not isinstance(fade, bool) \
        and 0 <= fade <= 3600 else ""
    console = raw.get("console") if isinstance(raw.get("console"), str) else ""
    console = console.strip() if re.fullmatch(r"[A-Za-z0-9]{1,8}", console.strip()) else "EX"
    pid = raw.get("id") if isinstance(raw.get("id"), str) and _PROGRAM_ID_RE.fullmatch(raw["id"]) else ""
    return {"id": pid, "name": _text(raw.get("name"), 40),
            "backend": backend if _ID_RE.fullmatch(backend) else "", "host": host, "port": port,
            "password": password[:200], "functions": raw.get("functions") is True, "console": console, "fade": fade}


def clean_programs(raw: Any) -> list[dict]:
    """Programs as a bounded list with unique ids; a program with no id gets one, and none has an empty name."""
    out: list[dict] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, list) else [])[:MAX_PROGRAMS]:
        if not isinstance(item, dict):
            continue
        prog = clean_program(item)
        if not prog["id"] or prog["id"] in seen:
            prog["id"] = next(i for i in ("main", *(f"p{n}" for n in range(2, MAX_PROGRAMS + 2))) if i not in seen)
        seen.add(prog["id"])
        label = BACKENDS[prog["backend"]].label if prog["backend"] in BACKENDS else ""
        prog["name"] = prog["name"] or label or prog["backend"] or "Lighting program"
        out.append(prog)
    return out


def _passwords(raw: Any) -> dict[str, str]:
    """The `passwords` setting: one sealed string holding {program id: password} as JSON, because config.json seals
    only top-level string fields."""
    if isinstance(raw, dict):
        d = raw
    else:
        try:
            d = json.loads(raw) if isinstance(raw, str) and raw else {}
        except ValueError:
            d = {}
    return {k: v[:200] for k, v in (d.items() if isinstance(d, dict) else ())
            if isinstance(k, str) and _PROGRAM_ID_RE.fullmatch(k) and isinstance(v, str) and v}


def runtime_programs(raw: Any) -> list[dict]:
    """The programs with each one's password put back in, for opening a backend. Before #484 the program's settings
    sat flat beside the pages; they become the first program, "main"."""
    raw = raw if isinstance(raw, dict) else {}
    programs = raw.get("programs")
    if not isinstance(programs, list) and raw.get("backend"):
        programs = [{**raw, "id": "main"}]
    saved = _passwords(raw.get("passwords"))
    out = clean_programs(programs)
    for prog, item in zip(out, [i for i in (programs or []) if isinstance(i, dict)][:len(out)]):
        sent = item.get("password") if isinstance(item.get("password"), str) else ""
        old = saved.get(item.get("id") if isinstance(item.get("id"), str) else "", "")
        prog["password"] = (sent or old)[:200]
    return out


def clean_settings(raw: Any) -> dict:
    """The stored form: {"programs": [...], "passwords": "<sealed JSON>", "pages": [...]}."""
    raw = raw if isinstance(raw, dict) else {}
    programs = runtime_programs(raw)
    pw = {p["id"]: p["password"] for p in programs if p["password"]}
    return {"programs": [{k: v for k, v in p.items() if k != "password"} for p in programs],
            "passwords": json.dumps(pw, sort_keys=True) if pw else "", "pages": clean_pages(raw.get("pages"))}


def public(settings: dict) -> dict:
    s = clean_settings(settings)
    pw = _passwords(s.pop("passwords"))
    for prog in s["programs"]:
        prog["has_password"] = prog["id"] in pw
    for p in s["pages"]:     # a key reaches only its own route (manage), never a viewer or a config read
        p["has_key"] = bool(p.pop("key"))
    return s


def cue_ref(program_id: str, cue_id: str, first: bool) -> str:
    """The id Relay uses for a program's own cue: bare for the first program (what pages saved before #484 hold),
    `<program>:<cue>` for the others, so two programs can both have a cue "1"."""
    return cue_id if first else f"{program_id}:{cue_id}"


class Service:
    def __init__(self) -> None:
        self.backends: dict[str, Backend] = {}     # program id -> its open backend
        self.errors: dict[str, str] = {}           # program id -> the last thing it could not do
        self._running = False

    @property
    def backend(self) -> Backend | None:
        """The first program's backend: what a cue with no program prefix goes to."""
        first = self.programs()
        return self.backends.get(first[0]["id"]) if first else None

    @property
    def error(self) -> str:
        return next((e for e in self.errors.values() if e), "")

    def settings(self) -> dict:
        return clean_settings(config.get().get(KEY))

    def programs(self) -> list[dict]:
        """The programs as backends are opened with them: passwords included."""
        return runtime_programs(config.get().get(KEY))

    # ------------------------------------------------------------------------------------------ life cycle
    async def start(self) -> None:
        self._running = True
        self._open()

    async def stop(self) -> None:
        self._running = False
        await self._close()

    async def restart(self) -> None:
        await self._close()
        self._open()

    def _open(self) -> None:
        self.errors = {}
        if not self._running:
            return
        for prog in self.programs():
            cls = BACKENDS.get(prog["backend"])
            if cls is not None:
                self.backends[prog["id"]] = cls(prog)

    async def _close(self) -> None:
        backends, self.backends = list(self.backends.values()), {}
        for backend in backends:
            try:
                await backend.close()
            except Exception:
                pass

    # ------------------------------------------------------------------------------------------ programs and cues
    def resolve(self, cue_ref_id: str) -> tuple[str, str]:
        """(program id, the program's own cue id) for a Relay cue id: a `<program>:<cue>` whose prefix is a program
        goes to that program, anything else to the first."""
        programs = self.programs()
        head, sep, rest = cue_ref_id.partition(":")
        if sep and any(p["id"] == head for p in programs[1:]):
            return head, rest
        return (programs[0]["id"] if programs else ""), cue_ref_id

    def backend_for(self, cue_ref_id: str) -> tuple[Backend | None, str]:
        pid, own = self.resolve(cue_ref_id)
        return self.backends.get(pid), own

    def screen_page(self, page_id: str, key: str) -> dict | None:
        """The page whose screen key is `key`, else None. The key is checked in constant time, and only while the
        page's screen is switched on."""
        for p in self.settings()["pages"]:
            if p["id"] == page_id:
                ok = p["key_on"] and bool(p["key"]) and hmac.compare_digest(p["key"].encode(), str(key).encode())
                return p if ok else None
        return None

    def page_cue(self, cue_id: str) -> tuple[dict, dict] | None:
        """(page, cue) for the first page holding `cue_id`: only these may be fired from a screen or panel."""
        for p in self.settings()["pages"]:
            for c in p["cues"]:
                if c["id"] == cue_id:
                    return p, c
        return None

    def page_cue_named(self, name: str) -> tuple[dict, dict] | None:
        """(page, cue) for the first cue called `name` (any case, spaces squeezed) on any page, in page order."""
        for p in self.settings()["pages"]:
            for c in p["cues"]:
                if same_name(c["name"], name):
                    return p, c
        return None

    async def listed(self) -> list[tuple[str, Cue]]:
        """[(Relay cue id, cue)] from every program that lists its cues."""
        out: list[tuple[str, Cue]] = []
        for index, prog in enumerate(self.programs()):
            b = self.backends.get(prog["id"])
            if b is None or not b.can_list:
                continue
            try:
                cues = list(await b.list_cues())
            except BackendError as e:
                self.errors[prog["id"]] = str(e)
                continue
            except Exception:
                self.errors[prog["id"]] = "The lighting program did not answer."
                continue
            out.extend((cue_ref(prog["id"], c.id, index == 0), c) for c in cues)
        return out

    async def find(self, cue_id: str, *, page_only: bool) -> tuple[str, str] | None:
        """(label for the audit trail, cue id) when `cue_id` may be fired, else None."""
        found = self.page_cue(cue_id)
        if found is not None:
            return f"{found[0]['name']}, {found[1]['name']}", cue_id
        if page_only:
            return None
        for ref, c in await self.listed():
            if ref == cue_id:
                return c.name, cue_id
        return None

    async def act(self, verb: str, cue_id: str, *args: Any) -> None:
        """One try, never repeated: a dropped connection may already have done it. `cue_id` is Relay's id for the cue
        (see `resolve`)."""
        b, own = self.backend_for(cue_id)
        if b is None:
            raise BackendError("No lighting program is connected.")
        try:
            await getattr(b, verb)(own, *args)
        except BackendError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BackendError("The lighting program did not answer. The cue may or may not have happened.")

    async def states(self) -> dict[str, bool]:
        """Relay cue id -> is it on, from every program that reports state; a program that does not answer is left out."""
        out: dict[str, bool] = {}
        for index, prog in enumerate(self.programs()):
            b = self.backends.get(prog["id"])
            if b is None or not b.can_report_state:
                continue
            try:
                lit = await b.state()
            except Exception:
                self.errors[prog["id"]] = self.errors.get(prog["id"]) or "The lighting program did not report its state."
                continue
            out.update({cue_ref(prog["id"], str(k), index == 0): bool(v) for k, v in lit.items()})
        return out

    async def snapshot(self) -> dict:
        out = public(config.get().get(KEY))
        programs = self.programs()
        out.update(backends=[{"id": k, "label": v.label or k, "about": v.about, "fields": list(v.fields),
                             "default_port": v.default_port} for k, v in sorted(BACKENDS.items())],
                   active=bool(self.backends), can_list=False, can_report_state=False, can_set_level=False,
                   cues=[], state={}, levels={}, error="")
        for index, prog in enumerate(programs):
            b = self.backends.get(prog["id"])
            row = next(p for p in out["programs"] if p["id"] == prog["id"])
            row["active"] = b is not None
            if b is None:
                continue
            out["can_list"] = out["can_list"] or b.can_list
            out["can_report_state"] = out["can_report_state"] or b.can_report_state
            out["can_set_level"] = out["can_set_level"] or b.can_set_level
            if b.can_set_level:
                try:
                    out["levels"].update({cue_ref(prog["id"], str(k), index == 0): int(v)
                                          for k, v in (await b.levels()).items()})
                except Exception:
                    self.errors[prog["id"]] = self.errors.get(prog["id"]) or "The lighting program did not report its levels."
        out["cues"] = [{"id": ref, "name": c.name, "kind": c.kind, "program": self.resolve(ref)[0]}
                       for ref, c in await self.listed()]
        out["state"] = await self.states()
        for prog in out["programs"]:
            prog["error"] = self.errors.get(prog["id"], "")
        out["error"] = self.error
        return out


service = Service()
