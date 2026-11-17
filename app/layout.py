"""The shared layout schema (#421): a grid of items, each with a place on the grid and a look.

One editor in core (`app/static/layout-editor.js`) and one renderer (`app/static/layout-render.js`) draw every
surface that lays items out on a grid: boards (`app/boards.py`, #156) and the lighting module's cue pages first, then
panel button surfaces (#223) and crew pages. Each of them stores its items next to its own data and passes them through
here, so a look and a place are checked the same way everywhere, and nothing a browser sends reaches a page as CSS.

A grid is `cols` x `rows` whole cells (1 to MAX_CELLS each). An item is

    {"x": 0, "y": 0, "w": 2, "h": 1,                    cells, from the top left, inside the grid
     "style": {"label": "Walk-in", "shape": "rounded",  one of SHAPES
               "bg": "#2b5fd9", "fg": "#ffffff",        #rrggbb only
               "pressed": "#5b8bff", "icon": "light"}}  one of ICONS ("" for none)

plus whatever the owner keeps on it (an id, a widget type, a cue). Items never overlap: `place` keeps the first of
two that would, and moves the later one to the first free spot, or drops it when the grid is full.

Pure functions: this file imports nothing from Relay, so config.py and any module can use it.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

MAX_CELLS = 24
SHAPES = ("rounded", "rect", "pill", "circle")
# The icons the renderer can draw (layout-render.js has the same list); "" is none.
ICONS = ("", "light", "mic", "cam", "star", "music", "cc", "clock", "arrow", "bell", "play", "stop")
LABEL_MAX = 60
_COLOR = re.compile(r"#[0-9a-fA-F]{6}")

DEFAULT_STYLE = {"label": "", "shape": "rounded", "bg": "#18181b", "fg": "#fafafa", "pressed": "#3f3f46", "icon": ""}


def text(value: Any, limit: int) -> str:
    """Plain text on one line: whitespace squeezed, cut to `limit`, "" for anything that is not a string."""
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def color(value: Any, default: str) -> str:
    return value.lower() if isinstance(value, str) and _COLOR.fullmatch(value) else default


def number(value: Any, lo: int, hi: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value:
        return default
    return max(lo, min(hi, int(value)))


def clean_style(raw: Any, defaults: dict | None = None) -> dict:
    """A look that is safe to put in a style attribute. `defaults` fills what `raw` lacks or gets wrong. The older
    lighting names (`color`, `text_color`) are still read, so a page saved before #421 keeps its colours."""
    d = {**DEFAULT_STYLE, **(defaults or {})}
    r = raw if isinstance(raw, dict) else {}
    return {
        "label": text(r.get("label"), LABEL_MAX),
        "shape": r.get("shape") if r.get("shape") in SHAPES else d["shape"],
        "bg": color(r.get("bg", r.get("color")), d["bg"]),
        "fg": color(r.get("fg", r.get("text_color")), d["fg"]),
        "pressed": color(r.get("pressed"), d["pressed"]),
        "icon": r.get("icon") if r.get("icon") in ICONS else d["icon"],
    }


def clean_grid(cols: Any, rows: Any, default: tuple[int, int] = (12, 8)) -> tuple[int, int]:
    return number(cols, 1, MAX_CELLS, default[0]), number(rows, 1, MAX_CELLS, default[1])


def _free(taken: set[tuple[int, int]], x: int, y: int, w: int, h: int) -> bool:
    return all((cx, cy) not in taken for cx in range(x, x + w) for cy in range(y, y + h))


def first_free(taken: set[tuple[int, int]], cols: int, rows: int, w: int, h: int) -> tuple[int, int] | None:
    for y in range(rows - h + 1):
        for x in range(cols - w + 1):
            if _free(taken, x, y, w, h):
                return x, y
    return None


def place(items: Iterable[dict], cols: int, rows: int) -> list[dict]:
    """Each item with whole-cell `x`, `y`, `w`, `h` inside the grid and over no other item. An item that does not fit
    where it asks is moved to the first free spot of its size, then of one cell; with no room left it is dropped.
    The item's other keys are kept as they are; the caller cleans them."""
    out: list[dict] = []
    taken: set[tuple[int, int]] = set()
    for it in items:
        if not isinstance(it, dict):
            continue
        w = number(it.get("w"), 1, cols, 1)
        h = number(it.get("h"), 1, rows, 1)
        x = number(it.get("x"), 0, cols - w, 0)
        y = number(it.get("y"), 0, rows - h, 0)
        if not _free(taken, x, y, w, h):
            spot = first_free(taken, cols, rows, w, h)
            if spot is None:
                w = h = 1
                spot = first_free(taken, cols, rows, 1, 1)
            if spot is None:
                continue
            x, y = spot
        taken.update((cx, cy) for cx in range(x, x + w) for cy in range(y, y + h))
        out.append({**it, "x": x, "y": y, "w": w, "h": h})
    return out


def from_percent(x: Any, y: Any, w: Any, h: Any, cols: int, rows: int) -> dict:
    """A box given as a percentage of the page (left, top, width, height) as the nearest whole cells: how a lighting
    page saved with free placement (#379) moves onto the grid."""
    def cell(v: Any, n: int) -> int:
        return round(number(v, 0, 100, 0) / 100 * n)
    cx, cy = min(cell(x, cols), cols - 1), min(cell(y, rows), rows - 1)
    return {"x": cx, "y": cy, "w": max(1, min(cols - cx, cell(w, cols))), "h": max(1, min(rows - cy, cell(h, rows)))}
