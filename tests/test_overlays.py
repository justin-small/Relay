"""Broadcast overlay builder (#139): option checking, presets, named overlays, and the page they render.

Offline: no OpenAI, no audio. Run: .venv/bin/python tests/test_overlays.py
"""
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pg  # noqa: E402  (a throwaway Postgres for the user system; must come before app.main)

_pg.start()
_pg.reset()

from fastapi.testclient import TestClient

from app import config, languages, overlays
from app.main import app

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


T = languages.TARGETS

print("options")
d = overlays.resolve({}, T)
eq("no options gives the old overlay: bottom, centred, plated, roll-up",
   {k: d[k] for k in ("pos", "align", "edge", "mode", "lines", "font", "plate", "bg", "finals")},
   {"pos": "bottom", "align": "center", "edge": "plate", "mode": "rollup", "lines": 2, "font": 54, "plate": 0.55,
    "bg": "transparent", "finals": False})
eq("numbers are clamped", [overlays.resolve({"lines": "99", "font": "1", "plate": "7", "safe": "-3"}, T)[k]
                           for k in ("lines", "font", "plate", "safe")], [12, 8, 1.0, 0.0])
eq("a number that is not one keeps the default", overlays.resolve({"font": "big", "plate": "nan"}, T)["font"], 54)
eq("NaN plate keeps the default", overlays.resolve({"plate": "nan"}, T)["plate"], 0.55)
eq("enums take only their values", [overlays.resolve({"pos": "TOP"}, T)["pos"],
                                    overlays.resolve({"pos": "sideways"}, T)["pos"]], ["top", "bottom"])
eq("colours take 6-digit hex, with or without #", [overlays.resolve({"color": "#ff00aa"}, T)["color"],
                                                   overlays.resolve({"color": "ffcc00"}, T)["color"]],
   ["FF00AA", "FFCC00"])
eq("a colour that is not hex keeps the default", overlays.resolve({"color": "red;}x{"}, T)["color"], "FFFFFF")
eq("languages must be targets", [overlays.resolve({"lang": "spanish"}, T)["lang"],
                                 overlays.resolve({"lang": "KLINGON"}, T)["lang"]], ["SPANISH", ""])
eq("background: hex gets its #, names pass, junk is transparent",
   [overlays.resolve({"bg": v}, T)["bg"] for v in ("00B140", "#0f0", "black", "red;}body{", "#12345")],
   ["#00B140", "#0F0", "black", "transparent", "transparent"])
eq("pop-on is finished lines only", overlays.resolve({"mode": "popon", "finals": "0"}, T)["finals"], True)

print("\npresets")
slate = overlays.resolve({"preset": "slate"}, T)
eq("the slate preset", {k: slate[k] for k in ("pos", "family", "bg", "lines", "edge", "hold")},
   {"pos": "middle", "family": "hyper", "bg": "#000000", "lines": 6, "edge": "none", "hold": 0})
eq("anything set with a preset wins", overlays.resolve({"preset": "slate", "lines": "3"}, T)["lines"], 3)
eq("an unknown preset is ignored", overlays.resolve({"preset": "fancy"}, T)["preset"], "")
eq("the dual preset stacks two feeds", overlays.resolve({"preset": "dual"}, T)["stream"], "both")
for k, p in overlays.PRESETS.items():
    vals = {kk: v for kk, v in p.items() if kk != "label"}
    eq(f"preset {k} holds only valid values", overlays.clean(vals, T), vals)

print("\npage")
c = _pg.client(app, base_url="http://testserver")


def body(html):
    return re.search(r"<body[^>]*>", html).group(0)


page = c.get("/overlay?lang=SPANISH&pos=top&align=left&family=serif&edge=outline&color=ff0000&platec=102030"
             "&maxw=70&mode=popon").text
eq("layout lands as body classes", body(page),
   '<body class=" overlay ov-hidden ov-pos-top ov-align-left ov-font-serif ov-edge-outline ov-popon">')
check("colours land as CSS values", "--ov-color: #FF0000;" in page and "--ov-platec: 16,32,48;" in page, page[:600])
check("max width lands", "--ov-maxw: 70%;" in page)
check("pop-on subscribes to finished lines", 'data-finals="1"' in page)
check("the bundled fonts are linked", "overlay-fonts.css" in page)
check("one feed has no language label", "ov-label" not in page)
check("the font files are served", c.get("/static/fonts/atkinson-hyperlegible-latin-700-normal.woff2").status_code == 200)

two = c.get("/overlay?stream=both&lang=SPANISH&lang2=FRENCH").text
feeds = re.findall(r'<div class="feed" data-stream="(\w+)" data-lang="(\w*)"', two)
eq("two languages stack in order", feeds, [("translation", "SPANISH"), ("translation", "FRENCH")])
eq("each carries its own name", re.findall(r'class="ov-label"[^>]*>([^<]+)<', two), ["Español", "Français"])
orig = c.get("/overlay?stream=both&lang=SPANISH").text
eq("no second language stacks the original speech", re.findall(r'data-stream="(\w+)"', orig),
   ["translation", "transcription"])
same = c.get("/overlay?stream=both&lang=SPANISH&lang2=SPANISH").text
eq("the same language twice falls back to the original", re.findall(r'data-stream="(\w+)"', same),
   ["translation", "transcription"])
check("labels can be turned off", "ov-label" not in c.get("/overlay?stream=both&lang=SPANISH&labels=0").text)
check("roll-up without finals subscribes to every word", 'data-finals' not in c.get("/overlay").text)

for field in ("bg", "color", "platec", "pos", "family", "lang", "font", "maxw"):
    html = c.get("/overlay", params={field: "x;}body{display:none}<script>"}).text
    check(f"{field} cannot inject CSS or markup", "display:none" not in html and "<script>x" not in html)

demo = c.get("/overlay?lang=SPANISH&demo=1").text
cfg = json.loads(re.search(r"overlayDemo: (.*?)};</script>", demo).group(1))
eq("the demo plays the language's own samples", cfg["SPANISH"][0], overlays.SAMPLES["es"][0])
check("no demo unless asked", "overlayDemo: null" in c.get("/overlay?lang=SPANISH").text)
demo_ko = json.loads(re.search(r"overlayDemo: (.*?)};</script>", c.get("/overlay?lang=HMONG&demo=1").text).group(1))
eq("a language with no samples plays English", demo_ko["HMONG"][0], overlays.SAMPLES["en"][0])

print("\nnamed overlays")
check("the list needs a sign-in", TestClient(app).get("/api/admin/overlays").status_code == 401)
info = c.get("/api/admin/overlays").json()
eq("none saved at first", info["overlays"], [])
check("the builder gets presets and defaults", "slate" in info["presets"] and info["defaults"]["pos"] == "bottom")
check("the source language is not offered as a target", all(t["target"] != "ENGLISH" for t in info["targets"]))

r = c.post("/api/admin/overlays", json={"name": "Stream – Spanish",
                                                   "settings": {"preset": "lower", "lang": "SPANISH", "pos": "top",
                                                                "color": "ffcc00", "junk": "1", "bg": "red;}"}})
eq("saving answers with its id", r.json().get("saved"), "stream-spanish")
saved = r.json()["overlays"][0]
eq("only valid settings are kept", saved["settings"],
   {"preset": "lower", "lang": "SPANISH", "pos": "top", "color": "FFCC00"})
check("it is in config", config.get()["overlays"][0]["id"] == "stream-spanish")

page = c.get("/overlay/stream-spanish").text
check("the named link renders its settings", "ov-pos-top" in page and "--ov-color: #FFCC00;" in page)
check("and its preset under them", "--ov-maxw: 80%;" in page)
check("the query still applies on top", "ov-pos-middle" in c.get("/overlay/stream-spanish?pos=middle").text)
eq("an unknown name is 404", c.get("/overlay/nope").status_code, 404)

r = c.post("/api/admin/overlays", json={"name": "Stream – Spanish", "settings": {}})
eq("the same name gets a numbered link", r.json()["saved"], "stream-spanish-2")
r = c.post("/api/admin/overlays", json={"name": "Other", "id": "stream-spanish", "settings": {}})
eq("asking for a taken link name is refused", (r.status_code, r.json()["error"]),
   (400, overlays.MESSAGES["id_taken"]))
r = c.post("/api/admin/overlays", json={"name": "  ", "settings": {}})
eq("a name is needed", r.json()["error"], overlays.MESSAGES["name"])
r = c.post("/api/admin/overlays", json={"name": "x", "id": "Bad Id!", "settings": {}})
eq("a bad link name is refused", r.json()["error"], overlays.MESSAGES["id"])

r = c.put("/api/admin/overlays/stream-spanish",
          json={"name": "Stream – Spanish", "settings": {"preset": "top", "lang": "FRENCH"}})
eq("an edit keeps the link", r.json()["saved"], "stream-spanish")
check("and the link shows the change on its next load", 'data-lang="FRENCH"' in c.get("/overlay/stream-spanish").text)
eq("editing an unknown overlay is 404",
   c.put("/api/admin/overlays/nope", json={"name": "x"}).status_code, 404)

r = c.delete("/api/admin/overlays/stream-spanish-2")
eq("delete removes it", [o["id"] for o in r.json()["overlays"]], ["stream-spanish"])
eq("deleting again is 404", c.delete("/api/admin/overlays/stream-spanish-2").status_code, 404)

print("\nconfig")
cleaned = overlays.clean_list([{"id": "a", "name": "A", "settings": {"pos": "top"}}, {"id": "a", "name": "Dup"},
                               {"name": ""}, "junk", {"id": "BAD ID", "name": "B"}], T)
eq("a hand-edited list keeps what is valid, first of a repeated id", [(o["id"], o["name"]) for o in cleaned],
   [("a", "A")])
eq("a list that is not one is empty", overlays.clean_list({"x": 1}, T), [])

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all overlay checks passed")
