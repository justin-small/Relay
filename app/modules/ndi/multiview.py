"""NDI multiview (#297): many NDI sources on one page, composed on the server into a single MJPEG picture.

An operator saves named **multiviews**, each a layout (2x2, 3x3, 4x4, or one big box with 5 or 7 small ones) and a
list of boxes. A box is a source (with a label, an optional audio meter and a tally border), a clock, or a countdown
timer (to a time of day, or to the next service start when Planning Center knows one). Each multiview has its own
address, `/ndi/multiview/<id>`.

A multiview is composed once however many browsers watch it: it holds a lease on the shared receiver pool for each
source in it, draws the boxes with Pillow at most `view_fps` times a second, and hands the same JPEG to every
browser, the way a single-source feed does (viewer.py). Nothing is composed, and every lease is given back, while
nobody is watching. Sending the composed picture out as an NDI source is not built yet.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Any, Callable

from ... import timewin
from ...text import slug
from . import viewer

log = logging.getLogger("relay.ndi.multiview")

MAX_MULTIVIEWS = 20
SIGNAL_LOST_S = 3.0
GAP = 4
# layout -> (columns, rows, the big box as (col, row, cols, rows) or None)
LAYOUTS: dict[str, tuple[int, int, tuple[int, int, int, int] | None]] = {
    "2x2": (2, 2, None), "3x3": (3, 3, None), "4x4": (4, 4, None),
    "1+5": (3, 3, (0, 0, 2, 2)), "1+7": (4, 4, (0, 0, 3, 3)),
}
LAYOUT_NAMES = {"2x2": "2 x 2", "3x3": "3 x 3", "4x4": "4 x 4", "1+5": "1 big, 5 small", "1+7": "1 big, 7 small"}
KINDS = ("empty", "source", "clock", "timer")
_ID = re.compile(r"[a-z0-9][a-z0-9\-]{0,39}")


def cells(layout: str) -> list[tuple[int, int, int, int]]:
    """The boxes of a layout as (column, row, columns, rows), the big one first."""
    cols, rows, big = LAYOUTS[layout]
    out = [big] if big else []
    taken = set()
    if big:
        taken = {(c, r) for c in range(big[0], big[0] + big[2]) for r in range(big[1], big[1] + big[3])}
    out += [(c, r, 1, 1) for r in range(rows) for c in range(cols) if (c, r) not in taken]
    return out


def clean_box(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    kind = raw.get("kind") if raw.get("kind") in KINDS else "empty"
    src = raw.get("source")
    label = raw.get("label")
    at = raw.get("at")
    return {"kind": kind,
            "source": src[:160] if isinstance(src, str) else "",
            "label": " ".join(label.split())[:40] if isinstance(label, str) else "",
            "meter": raw.get("meter") is True,
            "mode": raw.get("mode") if raw.get("mode") in ("time", "service") else "time",
            "at": at if timewin.is_clock(at) else "10:30"}


def clean_multiview(raw: Any) -> dict | None:
    """One saved multiview, or None when it is not usable (no name). Boxes are padded or cut to the layout."""
    if not isinstance(raw, dict):
        return None
    name = " ".join(raw["name"].split())[:60] if isinstance(raw.get("name"), str) else ""
    if not name:
        return None
    layout = raw.get("layout") if raw.get("layout") in LAYOUTS else "2x2"
    mid = raw.get("id") if isinstance(raw.get("id"), str) and _ID.fullmatch(raw["id"]) else ""
    boxes = [clean_box(b) for b in (raw.get("boxes") if isinstance(raw.get("boxes"), list) else [])]
    n = len(cells(layout))
    boxes = (boxes + [clean_box(None) for _ in range(n)])[:n]
    return {"id": mid, "name": name, "layout": layout, "boxes": boxes}


def clean_multiviews(raw: Any) -> list[dict]:
    """The saved list: usable entries only, each with a unique id (made from the name when it has none)."""
    out: list[dict] = []
    used: set[str] = set()
    for r in raw if isinstance(raw, list) else []:
        mv = clean_multiview(r)
        if mv is None or len(out) >= MAX_MULTIVIEWS:
            continue
        base = mv["id"] or slug(mv["name"], limit=30, fallback="multiview")
        mid, n = base, 2
        while mid in used:
            mid, n = f"{base}-{n}", n + 1
        mv["id"] = mid
        used.add(mid)
        out.append(mv)
    return out


def countdown_text(seconds: float) -> str:
    if seconds <= 0:
        return "Starting"
    s = int(seconds)
    return f"{s // 3600}:{s // 60 % 60:02d}:{s % 60:02d}"


def timer_target(at: str, now: float) -> float:
    """The next time the clock reads `at` (HH:MM), today or tomorrow, as a Unix time."""
    h, m = int(at[:2]), int(at[3:])
    lt = time.localtime(now)
    t = time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, h, m, 0, 0, 0, -1))
    return t if t > now else t + 86400


class Compositor(viewer.Feed):
    """One multiview being watched. Browsers read it through `next()` exactly as they read a single-source feed."""

    def __init__(self, mv: dict, owner: "Multiviews") -> None:
        super().__init__(mv["id"], None)
        self.mv, self.mgr = mv, owner
        self._leases: list[Any] = []
        self._tiles: dict[int, tuple[Any, float]] = {}
        self._tile_last: dict[int, float] = {}
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        s = owner.settings()
        self.width = max(320, s["view_width"] // 2 * 2)
        self.height = self.width * 9 // 16 // 2 * 2
        self.rects = self._rects()

    def _rects(self) -> list[tuple[int, int, int, int]]:
        cols, rows, _ = LAYOUTS[self.mv["layout"]]
        cw, ch = self.width / cols, self.height / rows
        out = []
        for c, r, w, h in cells(self.mv["layout"]):
            x0, y0 = round(c * cw) + GAP // 2, round(r * ch) + GAP // 2
            x1, y1 = round((c + w) * cw) - GAP // 2, round((r + h) * ch) - GAP // 2
            out.append((x0, y0, x1 - x0, y1 - y0))
        return out

    # ---- start and stop
    def start(self) -> bool:
        for i, box in enumerate(self.mv["boxes"]):
            if box["kind"] != "source" or not box["source"]:
                continue
            lease = self.mgr.subscribe(box["source"], lambda f, i=i: self._on_frame(i, f))
            if lease is not None:
                self._leases.append(lease)
        self._thread = threading.Thread(target=self._run, name="ndi-multiview", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        self._stop.set()
        for lease in self._leases:
            try:
                lease()
            except Exception:
                log.exception("releasing a multiview source failed")
        self._leases = []
        if self._thread is not None and self._thread is not threading.current_thread():
            self._thread.join(3)

    # ---- the receivers' threads
    def _on_frame(self, i: int, frame: dict) -> None:
        now = time.monotonic()
        if now - self._tile_last.get(i, 0.0) < 1.0 / self.mgr.settings()["view_fps"]:
            return
        self._tile_last[i] = now
        _, _, w, h = self.rects[i]
        try:
            img = viewer.to_rgb(frame, w)
            if img is not None:
                img.thumbnail((w, h))
                self._tiles[i] = (img, now)
        except Exception:
            log.exception("scaling a multiview frame failed")

    # ---- the compose thread
    def _run(self) -> None:
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                jpeg = self.compose()
                self.publish(jpeg, (self.width, self.height))
            except Exception:
                log.exception("composing a multiview failed")
            self._stop.wait(max(0.01, 1.0 / self.mgr.settings()["view_fps"] - (time.monotonic() - t0)))

    def compose(self) -> bytes:
        import io

        from PIL import Image, ImageDraw, ImageFont

        s = self.mgr.settings()
        canvas = Image.new("RGB", (self.width, self.height), (14, 14, 16))
        d = ImageDraw.Draw(canvas)
        big = ImageFont.load_default(size=max(14, self.height // 14))
        small = ImageFont.load_default(size=max(11, self.height // 40))
        now = time.time()
        cams = {c["n"]: c for c in self.mgr.cameras()}
        for i, box in enumerate(self.mv["boxes"]):
            x, y, w, h = self.rects[i]
            d.rectangle((x, y, x + w - 1, y + h - 1), fill=(0, 0, 0), outline=(40, 40, 44))
            kind = box["kind"]
            if kind == "source":
                self._source(canvas, d, box, i, (x, y, w, h), small, cams, s)
            elif kind == "clock":
                self._centre(d, time.strftime("%I:%M:%S %p", time.localtime(now)).lstrip("0"), (x, y, w, h), big)
                self._label(d, box["label"] or "Clock", (x, y, w, h), small)
            elif kind == "timer":
                if box["mode"] == "service":
                    start = self.mgr.service_start()
                    text = countdown_text(start - now) if start else "No service time"
                    label = box["label"] or "Next service"
                else:
                    text = countdown_text(timer_target(box["at"], now) - now)
                    label = box["label"] or f"Starts {box['at']}"
                self._centre(d, text, (x, y, w, h), big)
                self._label(d, label, (x, y, w, h), small)
        buf = io.BytesIO()
        canvas.save(buf, "JPEG", quality=s["view_quality"])
        return buf.getvalue()

    def _source(self, canvas, d, box, i, rect, font, cams, s) -> None:
        x, y, w, h = rect
        tile = self._tiles.get(i)
        if tile is not None and time.monotonic() - tile[1] < SIGNAL_LOST_S:
            img = tile[0]
            canvas.paste(img, (x + (w - img.width) // 2, y + (h - img.height) // 2))
        else:
            self._centre(d, "No signal" if box["source"] else "No source", rect, font)
        self._label(d, box["label"] or box["source"], rect, font)
        if box["meter"]:
            peaks = self.mgr.levels(box["source"]) if self.mgr.levels is not None else None
            if not peaks:
                db = self.mgr.level(box["source"])
                peaks = [] if db is None else [db]
            peaks = peaks[:MAX_METER_BARS] or [None]
            bw, bh = max(40, w // 4), max(3, h // 60)
            mx, my = x + w - bw - 8, y + h - (bh + 2) * len(peaks) - 6
            for k, db in enumerate(peaks):
                frac = 0.0 if db is None else max(0.0, min(1.0, (db + 60) / 60))
                by = my + k * (bh + 2)
                d.rectangle((mx, by, mx + bw, by + bh), fill=(50, 50, 54))
                if frac:
                    d.rectangle((mx, by, mx + int(bw * frac), by + bh), fill=(64, 192, 87) if frac < 0.92 else (224, 49, 49))
        cam = s["tally"].get(box["source"], 0)
        c = cams.get(cam) if cam else None
        colour = (224, 49, 49) if c and c["program"] else ((47, 158, 68) if c and c["preview"] else None)
        if colour:
            for k in range(6):
                d.rectangle((x + k, y + k, x + w - 1 - k, y + h - 1 - k), outline=colour)

    @staticmethod
    def _centre(d, text, rect, font) -> None:
        x, y, w, h = rect
        box = d.textbbox((0, 0), text, font=font)
        d.text((x + (w - (box[2] - box[0])) // 2, y + (h - (box[3] - box[1])) // 2 - box[1]), text, fill=(235, 235, 235), font=font)

    @staticmethod
    def _label(d, text, rect, font) -> None:
        if not text:
            return
        x, y, w, h = rect
        box = d.textbbox((0, 0), text, font=font)
        th = box[3] - box[1]
        d.rectangle((x, y, x + min(w, box[2] - box[0] + 14), y + th + 8), fill=(0, 0, 0))
        d.text((x + 6, y + 3 - box[1]), text, fill=(255, 255, 255), font=font)


MAX_METER_BARS = 8       # channels drawn in a box's meter


class Multiviews:
    """The open compositors. `subscribe` is core's `ndi_subscribe`; the rest read settings, audio levels, the shared
    tally state and the next service start, so this file imports nothing from another module."""

    def __init__(self, subscribe: Callable, settings: Callable[[], dict], level: Callable[[str], float | None],
                 cameras: Callable[[], list[dict]], service_start: Callable[[], float | None],
                 levels: Callable[[str], list[float] | None] | None = None) -> None:
        self.subscribe, self.settings, self.level = subscribe, settings, level
        self.levels = levels          # each channel's peak in dBFS (#301); without it a box shows one bar
        self.cameras, self.service_start = cameras, service_start
        self.open_: dict[str, Compositor] = {}
        self._lock = threading.Lock()

    def get(self, mid: str) -> dict | None:
        return next((m for m in self.settings()["multiviews"] if m["id"] == mid), None)

    def open(self, mid: str) -> Compositor | None:
        with self._lock:
            c = self.open_.get(mid)
            if c is not None:
                c.viewers += 1
                return c
            mv = self.get(mid)
            if mv is None:
                return None
            c = Compositor(mv, self)
            c.viewers = 1
            c.start()
            self.open_[mid] = c
            return c

    def close(self, c: Compositor) -> None:
        with self._lock:
            c.viewers -= 1
            if c.viewers > 0:
                return
            if self.open_.get(c.source) is c:
                del self.open_[c.source]
        c.stop()

    def count(self) -> dict[str, int]:
        with self._lock:
            return {k: c.viewers for k, c in self.open_.items()}

    def close_all(self) -> None:
        with self._lock:
            comps, self.open_ = list(self.open_.values()), {}
        for c in comps:
            c.viewers = 0
            c.stop()
