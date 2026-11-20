"""The shared layout schema (#421): looks and places as every grid surface (boards, lighting cue pages) stores them.
Run: python tests/test_layout.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from app import layout  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

with section("a look"):
    s = layout.clean_style({"label": "  Walk \n in ", "shape": "hexagon", "bg": "#ABCDEF", "fg": "red",
                            "pressed": "#123456;", "icon": "<svg>"})
    eq("label tidied, bad values fall back", (s["label"], s["shape"], s["bg"], s["fg"], s["pressed"], s["icon"]),
       ("Walk in", "rounded", "#abcdef", "#fafafa", "#3f3f46", ""))
    eq("the owner's defaults fill what is missing", layout.clean_style({}, {"bg": "#2b5fd9", "icon": "light"})["bg"], "#2b5fd9")
    eq("the older lighting names are read", (layout.clean_style({"color": "#111111", "text_color": "#222222"})["bg"],
                                             layout.clean_style({"color": "#111111", "text_color": "#222222"})["fg"]),
       ("#111111", "#222222"))
    check("only the keys of the schema come back", set(layout.clean_style({"x": 1, "onclick": "a"})) ==
          {"label", "shape", "bg", "fg", "pressed", "icon"})
    eq("a long label is cut", len(layout.clean_style({"label": "x" * 500})["label"]), layout.LABEL_MAX)
    for bad in ("#fff", "url(x)", "#12345g", 12, None, "#1234567"):
        eq(f"colour {bad!r} refused", layout.clean_style({"bg": bad})["bg"], layout.DEFAULT_STYLE["bg"])
    eq("every shape the editor offers is kept", [layout.clean_style({"shape": s})["shape"] for s in layout.SHAPES],
       list(layout.SHAPES))

with section("a grid"):
    eq("bounded", layout.clean_grid(0, 99), (1, layout.MAX_CELLS))
    eq("not numbers fall back", layout.clean_grid("4", True, (4, 3)), (4, 3))

with section("places"):
    got = layout.place([{"id": "a", "x": 0, "y": 0, "w": 2, "h": 2}, {"id": "b", "x": 1, "y": 1, "w": 1, "h": 1},
                        {"id": "c", "x": 9, "y": 9, "w": 9, "h": 1}], 4, 3)
    eq("an overlapping item moves to the first free spot", [(i["id"], i["x"], i["y"], i["w"], i["h"]) for i in got],
       [("a", 0, 0, 2, 2), ("b", 2, 0, 1, 1), ("c", 0, 2, 4, 1)])
    full = layout.place([{"x": 0, "y": 0, "w": 2, "h": 1}, {"x": 0, "y": 0, "w": 2, "h": 1}, {"x": 0, "y": 0}], 2, 1)
    eq("with no room left an item is dropped", len(full), 1)
    shrink = layout.place([{"x": 0, "y": 0, "w": 1, "h": 1}, {"x": 0, "y": 0, "w": 2, "h": 2}], 2, 2)
    eq("an item too big for any free spot becomes one cell", [(i["x"], i["y"], i["w"], i["h"]) for i in shrink],
       [(0, 0, 1, 1), (1, 0, 1, 1)])
    eq("other keys are kept", layout.place([{"id": "z", "props": {"k": 1}}], 2, 2)[0]["props"], {"k": 1})
    eq("non-dicts are skipped", layout.place(["x", None, 3], 2, 2), [])
    eq("floats and bools are not trusted as places", [(i["x"], i["w"]) for i in layout.place([{"x": True, "w": 1.9}], 4, 1)],
       [(0, 1)])

with section("from a percentage of the page"):
    eq("the middle quarter of an 8 by 6 grid", layout.from_percent(25, 50, 50, 50, 8, 6), {"x": 2, "y": 3, "w": 4, "h": 3})
    eq("a box at the far edge stays inside", layout.from_percent(100, 100, 50, 50, 8, 6), {"x": 7, "y": 5, "w": 1, "h": 1})
    eq("a tiny box is still one cell", layout.from_percent(0, 0, 1, 1, 8, 6), {"x": 0, "y": 0, "w": 1, "h": 1})

done()
