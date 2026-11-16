"""Settings, cue pages and the one active backend for the lighting module (#377)."""
from __future__ import annotations

import asyncio
import hmac
import re
import secrets
import uuid
from typing import Any

from ... import config
from . import lightkey, qlcplus, thelightingcontroller, vista  # noqa: F401  (each registers its backend)
from .backend import BACKENDS, Backend, BackendError, Cue

KEY = "lighting"
MAX_PAGES = 20
MAX_CUES = 200
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
_ID_RE = re.compile(r"[A-Za-z0-9_.:\-]{1,64}")


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


SHAPES = ("rounded", "rect", "pill", "circle")
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}")
_KEY_RE = re.compile(r"[0-9a-f]{32}")
MAX_SPAN = 6


def _color(value: Any, default: str) -> str:
    return value.lower() if isinstance(value, str) and _COLOR_RE.fullmatch(value) else default


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return max(lo, min(hi, int(value)))


def clean_style(c: dict) -> dict:
    """How one cue's button looks: the label it shows, its shape, colours and size. `w` and `h` are grid cells (1 to
    6); `x`, `y`, `fw`, `fh` are a percentage of the page, used only when the page is free-positioned. Always safe
    to put in a style attribute: a colour is #rrggbb and every number is bounded."""
    fw = _int(c.get("fw"), 4, 100, 25)
    fh = _int(c.get("fh"), 4, 100, 20)
    return {"label": _text(c.get("label"), 60), "shape": c.get("shape") if c.get("shape") in SHAPES else "rounded",
            "color": _color(c.get("color"), "#2b5fd9"), "text_color": _color(c.get("text_color"), "#ffffff"),
            "w": _int(c.get("w"), 1, MAX_SPAN, 1), "h": _int(c.get("h"), 1, MAX_SPAN, 1),
            "x": _int(c.get("x"), 0, 100 - fw, 0), "y": _int(c.get("y"), 0, 100 - fh, 0), "fw": fw, "fh": fh}


def new_key() -> str:
    """A page's screen key: 128 random bits, as 32 hex digits."""
    return secrets.token_hex(16)


def clean_pages(raw: Any, keys: dict[str, str] | None = None) -> list[dict]:
    """Pages as [{"id", "name", "free", "key_on", "key", "cues": [{"id", "name", + clean_style}]}]: bounded, unique
    page ids, a cue once per page. A page's key is never taken from `raw` when `keys` (the saved ones, by page id)
    is given: only the key routes change it."""
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
            cues.append({"id": cid, "name": _text(c.get("name"), 60) or cid, **clean_style(c)})
        out.append({"id": pid, "name": name, "free": p.get("free") is True, "key_on": bool(key) and p.get("key_on") is True,
                    "key": key, "cues": cues})
    return out


def clean_settings(raw: Any) -> dict:
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
    return {"backend": backend if _ID_RE.fullmatch(backend) else "", "host": host, "port": port,
            "password": password[:200], "functions": raw.get("functions") is True, "console": console, "fade": fade,
            "pages": clean_pages(raw.get("pages"))}


def public(settings: dict) -> dict:
    s = clean_settings(settings)
    s["has_password"] = bool(s["password"])
    s["password"] = ""
    for p in s["pages"]:     # a key reaches only its own route (manage), never a viewer or a config read
        p["has_key"] = bool(p.pop("key"))
    return s


class Service:
    def __init__(self) -> None:
        self.backend: Backend | None = None
        self._running = False
        self.error = ""

    def settings(self) -> dict:
        return clean_settings(config.get().get(KEY))

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
        s = self.settings()
        cls = BACKENDS.get(s["backend"])
        self.error = ""
        if self._running and cls is not None:
            self.backend = cls(s)

    async def _close(self) -> None:
        backend, self.backend = self.backend, None
        if backend is not None:
            try:
                await backend.close()
            except Exception:
                pass

    # ------------------------------------------------------------------------------------------ cues
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
        want = " ".join(name.split()).casefold()
        if not want:
            return None
        for p in self.settings()["pages"]:
            for c in p["cues"]:
                if " ".join(c["name"].split()).casefold() == want:
                    return p, c
        return None

    async def listed(self) -> list[Cue]:
        b = self.backend
        if b is None or not b.can_list:
            return []
        try:
            return list(await b.list_cues())
        except BackendError as e:
            self.error = str(e)
        except Exception:
            self.error = "The lighting program did not answer."
        return []

    async def find(self, cue_id: str, *, page_only: bool) -> tuple[str, str] | None:
        """(label for the audit trail, cue id) when `cue_id` may be fired, else None."""
        found = self.page_cue(cue_id)
        if found is not None:
            return f"{found[0]['name']}, {found[1]['name']}", cue_id
        if page_only:
            return None
        for c in await self.listed():
            if c.id == cue_id:
                return c.name, cue_id
        return None

    async def act(self, verb: str, cue_id: str, *args: Any) -> None:
        """One try, never repeated: a dropped connection may already have done it."""
        b = self.backend
        if b is None:
            raise BackendError("No lighting program is connected.")
        try:
            await getattr(b, verb)(cue_id, *args)
        except BackendError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception:
            raise BackendError("The lighting program did not answer. The cue may or may not have happened.")

    async def snapshot(self) -> dict:
        s = self.settings()
        b = self.backend
        out = public(s)
        out.update(backends=[{"id": k, "label": v.label or k} for k, v in sorted(BACKENDS.items())],
                   active=bool(b), can_list=bool(b and b.can_list), can_report_state=bool(b and b.can_report_state),
                   can_set_level=bool(b and b.can_set_level), cues=[], state={}, levels={}, error=self.error)
        if b is not None:
            out["cues"] = [{"id": c.id, "name": c.name, "kind": c.kind} for c in await self.listed()]
            if b.can_set_level:
                try:
                    out["levels"] = {str(k): int(v) for k, v in (await b.levels()).items()}
                except Exception:
                    out["error"] = out["error"] or "The lighting program did not report its levels."
            if b.can_report_state:
                try:
                    out["state"] = {str(k): bool(v) for k, v in (await b.state()).items()}
                except Exception:
                    out["error"] = out["error"] or "The lighting program did not report its state."
        return out


service = Service()
