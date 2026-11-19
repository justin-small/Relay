"""The front and back of each Blackmagic device, drawn from a model spec (the rack redesign), and its connectors (#540).

A spec lists a face's buttons, screens and connectors with their places. A button Relay can press carries an action; the
rest are drawn dimmed and say why. The server builds the spec from what the device reports, so the server knows every
connector a model has: `connectors(spec)` lists them as core ports (app/ports.py) on the device's gear, and the page
(static/bmdraw.js) only draws what it is given, with the live state it reads itself.

A drawing is chosen by the operator (gear's `drawing`) or picked from the product name (`auto`).
"""
from __future__ import annotations

import math
from typing import Any

NAMES = {"tvs-hd8": "ATEM Television Studio HD8", "atem-rack": "ATEM rack switcher", "mini-extreme": "ATEM Mini Extreme",
         "mini-pro": "ATEM Mini / Mini Pro", "hyperdeck-studio": "HyperDeck Studio", "videohub": "Videohub"}
DRAWINGS = tuple(NAMES)
NOT_AUDIO = "Audio is mixed on the switcher; Relay does not change it."
# The drawing's connector kinds, as core port kinds. A 3.5 mm jack is a TRS jack; a 12 V barrel is DC power.
PORT_KINDS = {"sdi": "sdi", "hdmi": "hdmi", "eth": "eth", "usb": "usb", "xlr": "xlr", "mini": "trs", "iec": "iec",
              "barrel": "dc"}
DIRECTIONS = {"in": "in", "out": "out", "": "both"}


def btn(id_: str, x: float, y: float, w: float, h: float, label: str, act: str | None = None, **extra: Any) -> dict:
    return {"t": "btn", "id": id_, "x": x, "y": y, "w": w, "h": h, "label": label, "act": act, **extra}


def port(id_: str, kind: str, x: float, y: float, label: str, dir_: str = "", **extra: Any) -> dict:
    return {"t": "port", "id": id_, "kind": kind, "x": x, "y": y, "label": label, "dir": dir_, **extra}


def cap(x: float, y: float, label: str, anchor: str = "start") -> dict:
    return {"t": "text", "x": x, "y": y, "label": label, "anchor": anchor, "cls": "hw-cap"}


# ------------------------------------------------------------------------------------------------ ATEM
def _source_rows(items: list, x0: float, step: float, w: float, y_p: float, y_v: float, srcs: list[dict]) -> None:
    items += [cap(x0 - 10, y_p + 21, "PROGRAM", "end"), cap(x0 - 10, y_v + 21, "PREVIEW", "end")]
    for i, src in enumerate(srcs):
        items.append(btn(f"pgm{src['n']}", x0 + i * step, y_p, w, 32, src["k"], f"pgm:{src['n']}", src=src["n"]))
        items.append(btn(f"pvw{src['n']}", x0 + i * step, y_v, w, 32, src["k"], f"pvw:{src['n']}", src=src["n"], name=1))


def _atem_sources(v: dict, count: int, extra_id: int, extra_k: str) -> list[dict]:
    out = [{"n": n, "k": str(n)} for n in range(1, count + 1)]
    out.append({"n": 0, "k": "BLK"})
    if extra_id in v["inputs"]:
        out.append({"n": extra_id, "k": extra_k})
    return out


def _dsk_buttons(items: list, v: dict, x: float, y1: float, y2: float) -> None:
    if v["dsk"] >= 1:
        items.append(btn("dsk1", x, y1, 46, 32, "DSK 1", "dsk:0", tipText="Downstream key 1: take it on or off with its own transition"))
    else:
        items.append(btn("dsk1", x, y1, 46, 32, "DSK", None, why="This switcher has no downstream key."))
    if v["dsk"] >= 2:
        items.append(btn("dsk2", x, y2, 46, 32, "DSK 2", "dsk:1", tipText="Downstream key 2: take it on or off with its own transition"))


def _macro_buttons(items: list, v: dict, x0: float, y: float, step: float, w: float, h: float, count: int) -> None:
    for m in range(count):
        mac = v["macros"][m] if m < len(v["macros"]) else None
        extra = ({"tipText": f"Run macro {mac['slot'] + 1}: {mac.get('name') or 'Macro'}"} if mac
                 else {"why": "No macro recorded in this slot."})
        items.append(btn(f"mac{m}", x0 + m * step, y, w, h, str(m + 1), f"macro:{mac['slot']}" if mac else None, **extra))


def tvs(v: dict, inputs: int, title: str) -> dict:
    f: list[dict] = []
    b: list[dict] = []
    n = min(inputs, 12)
    step = 36 if n > 10 else 44
    bw = step - 6
    f.append({"t": "lcd", "x": 40, "y": 22, "w": 168, "h": 98, "key": "atem"})
    srcs = _atem_sources(v, n, 3010, "MP1")
    _source_rows(f, 300, step, bw, 26, 70, srcs)
    xr = 300 + len(srcs) * step + 14
    _dsk_buttons(f, v, xr, 26, 70)
    f.append(btn("cut", xr + 62, 26, 56, 32, "CUT", "cut", big=1))
    f.append(btn("auto", xr + 62, 70, 56, 32, "AUTO", "auto", big=1))
    f.append(cap(290, 142, "MACRO", "end"))
    _macro_buttons(f, v, 300, 124, 44, 38, 28, 6)
    f.append(btn("ftb", 588, 124, 56, 28, "FTB", "ftb"))
    f.append(btn("rec", 656, 124, 46, 28, "REC", None, why="Recording is started on the switcher or in ATEM Software Control."))
    f.append(btn("stream", 712, 124, 60, 28, "STREAM", None, why="Streaming is started on the switcher."))
    f.append({"t": "text", "x": 40, "y": 150, "label": title, "cls": "hw-brand"})
    bstep = 34 if n > 8 else 50
    for i in range(1, n + 1):
        b.append(port(f"in{i}", "sdi", 40 + i * bstep, 56, f"IN {i}", "in", input=i))
    for k, label in enumerate(("PGM", "AUX 1", "AUX 2", "AUX 3", "AUX 4")):
        b.append(port(f"out{k}", "sdi", 90 + k * 50, 128, label, "out", out=k))
    xa = max(490, 40 + (n + 1) * bstep)
    b.append(port("mv", "hdmi", 380, 128, "MULTIVIEW", "out"))
    b.append(port("ref", "sdi", xa, 56, "REF IN", "in"))
    b.append(port("xlr1", "xlr", xa + 70, 56, "AUDIO 1", "in"))
    b.append(port("xlr2", "xlr", xa + 120, 56, "AUDIO 2", "in"))
    b.append(port("tb", "eth", 560, 128, "TALKBACK", ""))
    b.append(port("eth", "eth", max(680, xa + 190), 56, "ETHERNET", "", net=1))
    b.append(port("usb", "usb", max(740, xa + 250), 56, "USB-C", ""))
    b.append(port("pwr", "iec", 870, 88, "AC 100–240V", "", power=1))
    b.append({"t": "zone", "x": 52, "y": 22, "w": xa - 72, "h": 152, "label": "VIDEO"})
    return {"form": "rack", "u": 2, "front": {"w": 960, "h": 176, "items": f}, "back": {"w": 960, "h": 176, "items": b}}


def mini(v: dict, inputs: int, title: str) -> dict:
    f: list[dict] = []
    b: list[dict] = []
    f.append({"t": "zone", "x": 24, "y": 28, "w": 236, "h": 230, "label": "AUDIO"})
    for i, c in enumerate(["M1", "M2"] + [str(k) for k in range(1, inputs + 1)]):
        col, row = i % 5, i // 5
        f.append(btn(f"a{c}", 40 + col * 42, 56 + row * 96, 34, 22, c, None, small=1, why=NOT_AUDIO))
        f.append(btn(f"afv{c}", 40 + col * 42, 84 + row * 96, 34, 22, "AFV", None, small=1, why=NOT_AUDIO))
    f.append({"t": "lcd", "x": 300, "y": 28, "w": 140, "h": 62, "key": "atem"})
    _source_rows(f, 370, 40, 34, 112, 160, _atem_sources(v, inputs, 6000, "SS"))
    f.append(cap(360, 236, "TRANSITION", "end"))
    for i, label in enumerate(("MIX", "DIP", "WIPE", "DVE", "STING")):
        f.append(btn(f"tr{label}", 370 + i * 46, 216, 40, 28, label, None, small=1,
                     why="Relay uses the transition already chosen on the switcher."))
    _dsk_buttons(f, v, 790, 112, 160)
    f.append(btn("cut", 850, 112, 58, 32, "CUT", "cut", big=1))
    f.append(btn("auto", 850, 160, 58, 32, "AUTO", "auto", big=1))
    f.append(btn("ftb", 850, 216, 58, 28, "FTB", "ftb"))
    mac = v["macros"][0] if v["macros"] else None
    f.append(btn("macro", 790, 216, 46, 28, "MACRO", f"macro:{mac['slot']}" if mac else None,
                 **({"tipText": f"Run macro {mac['slot'] + 1}: {mac.get('name') or 'Macro'}"} if mac
                    else {"why": "No macro recorded on the switcher."})))
    f.append({"t": "knob", "x": 660, "y": 58, "r": 30, "why": "Moves the DVE; not from Relay."})
    f.append(btn("rec", 720, 34, 56, 26, "REC", None, why="Recording is started on the switcher."))
    f.append(btn("onair", 786, 34, 56, 26, "ON AIR", None, why="Streaming is started on the switcher."))
    f.append(btn("stream", 852, 34, 56, 26, "STREAM", None, why="Streaming is started on the switcher."))
    f.append({"t": "text", "x": 24, "y": 300, "label": title, "cls": "hw-brand"})
    step = 50 if inputs > 4 else 60
    for i in range(1, inputs + 1):
        b.append(port(f"in{i}", "hdmi", 14 + i * step, 54, f"HDMI {i}", "in", input=i))
    b.append(port("out0", "hdmi", 490, 54, "OUT 1", "out", out=0))
    if inputs > 4:
        b.append(port("out1", "hdmi", 545, 54, "OUT 2", "out", out=1))
    b.append(port("mic1", "mini", 610, 54, "MIC 1", "in"))
    b.append(port("mic2", "mini", 660, 54, "MIC 2", "in"))
    if inputs > 4:
        b.append(port("hs", "mini", 714, 54, "HEADSET", ""))
    b.append(port("eth", "eth", 776, 54, "ETHERNET", "", net=1))
    b.append(port("usb1", "usb", 834, 54, "USB-C 1", ""))
    if inputs > 4:
        b.append(port("usb2", "usb", 882, 54, "USB-C 2", ""))
    b.append(port("pwr", "barrel", 930, 54, "12V", "", power=1))
    return {"form": "desk", "front": {"w": 960, "h": 320, "items": f}, "back": {"w": 960, "h": 110, "items": b}}


# ------------------------------------------------------------------------------------------------ HyperDeck
def deck(v: dict) -> dict:
    f: list[dict] = []
    b: list[dict] = []
    elsewhere = "Relay records, stops and plays; pick clips on the deck."
    f.append({"t": "lcd", "x": 40, "y": 12, "w": 168, "h": 64, "key": "deck"})
    f.append({"t": "slot", "x": 228, "y": 18, "w": 128, "h": 14, "label": "SLOT 1", "slot": 1})
    f.append({"t": "slot", "x": 228, "y": 48, "w": 128, "h": 14, "label": "SLOT 2", "slot": 2})
    f.append(btn("prev", 420, 26, 46, 34, "⏮", None, why=elsewhere))
    f.append(btn("rec", 476, 26, 46, 34, "REC", "record", tone="rec"))
    f.append(btn("stop", 532, 26, 46, 34, "STOP", "stop"))
    f.append(btn("play", 588, 26, 46, 34, "PLAY", "play"))
    f.append(btn("next", 644, 26, 46, 34, "⏭", None, why=elsewhere))
    f.append(btn("rem", 712, 26, 46, 34, "REM", None, lit=v["remote"],
                 why="Remote is on, so Relay can control the deck." if v["remote"]
                 else "Remote is off on the deck: Relay can look but not control it."))
    f.append({"t": "knob", "x": 860, "y": 44, "r": 30, "why": "Jog and shuttle are on the deck."})
    f.append({"t": "text", "x": 228, "y": 80, "label": v["product"] or "HyperDeck", "cls": "hw-brand"})
    b.append(port("sdiin", "sdi", 90, 44, "SDI IN", "in", rec=1))
    b.append(port("sdiout", "sdi", 140, 44, "SDI OUT", "out"))
    b.append(port("hdmiin", "hdmi", 210, 44, "HDMI IN", "in"))
    b.append(port("hdmiout", "hdmi", 270, 44, "HDMI OUT", "out"))
    b.append(port("ref", "sdi", 340, 44, "REF IN", "in"))
    b.append(port("tcin", "xlr", 400, 44, "TC IN", "in"))
    b.append(port("tcout", "xlr", 450, 44, "TC OUT", "out"))
    b.append(port("eth", "eth", 560, 44, "ETHERNET", "", net=1))
    b.append(port("usb", "usb", 620, 44, "USB-C", ""))
    b.append(port("pwr", "iec", 860, 44, "AC", "", power=1))
    return {"form": "rack", "u": 1, "front": {"w": 960, "h": 88, "items": f}, "back": {"w": 960, "h": 88, "items": b}}


# ------------------------------------------------------------------------------------------------ Videohub
def hub(v: dict) -> dict:
    f: list[dict] = []
    b: list[dict] = []
    nin, nout = max(1, v["n_inputs"]), max(1, v["n_outputs"])
    cols, step, x0 = (10, 44, 250) if nout > 12 else (12, 38, 250)
    rows = math.ceil(nout / cols)
    u = max(math.ceil((rows * 40 + 24) / 88) if rows > 2 else 1, 2 if max(nin, nout) > 12 else 1)
    h = 88 * u
    f.append({"t": "lcd", "x": 40, "y": 12, "w": 160, "h": h - 24, "key": "hub"})
    f.append(cap(x0 - 8, 32, "OUT", "end"))
    for i in range(1, nout + 1):
        row, col = (i - 1) // cols, (i - 1) % cols
        f.append(btn(f"dst{i}", x0 + col * step, 12 + row * 40, step - 6, 30, str(i), f"dst:{i}", dst=i))
    x1 = x0 + cols * step + 18
    f.append(btn("take", x1, 12, 56, 30, "TAKE", "take", big=1))
    f.append(btn("lock", x1, 52, 56, 30, "LOCK", "lock", manage=1))
    f.append({"t": "knob", "x": min(x1 + 110, 900), "y": h / 2, "r": 26,
              "why": "Choose the input in Relay: click it on the back, or pick it in the side panel."})
    bx0, bstep = 70, min(40, 760 / max(nin, nout))
    compact = bstep < 30
    for k in range(1, nin + 1):
        b.append(port(f"in{k}", "sdi", bx0 + (k - 1) * bstep, h * 0.3, str(k), "in", input=k, compact=1, tiny=compact))
    for k in range(1, nout + 1):
        b.append(port(f"out{k}", "sdi", bx0 + (k - 1) * bstep, h * 0.74, str(k), "out", out=k, compact=1, tiny=compact))
    b += [cap(40, h * 0.3 + 4, "IN", "end"), cap(40, h * 0.74 + 4, "OUT", "end")]
    b.append(port("ref", "sdi", 860, h * 0.3, "REF", "in", compact=1))
    b.append(port("eth", "eth", 860, h * 0.74, "ETH", "", compact=1, net=1))
    b.append(port("pwr", "iec", 920, h / 2, "AC", "", compact=1, power=1))
    return {"form": "rack", "u": u, "front": {"w": 960, "h": h, "items": f}, "back": {"w": 960, "h": h, "items": b}}


# ------------------------------------------------------------------------------------------------ picking a drawing
def auto(dev: dict) -> str:
    """The drawing a device gets when none is chosen, from its kind and product name."""
    text = f"{dev.get('product') or ''} {dev.get('model') or ''}".lower()
    if dev.get("kind") == "hyperdeck":
        return "hyperdeck-studio"
    if dev.get("kind") == "videohub":
        return "videohub"
    if "television studio hd8" in text or "tvs hd8" in text:
        return "tvs-hd8"
    if "mini extreme" in text:
        return "mini-extreme"
    if "atem mini" in text:
        return "mini-pro"
    return "atem-rack"


def drawing_of(dev: dict) -> str:
    return dev["drawing"] if dev.get("drawing") in NAMES else auto(dev)


def view(dev: dict) -> dict:
    """What a drawing needs from a device snapshot (service.device_snapshot)."""
    inputs = {i["id"]: i["name"] for i in dev.get("inputs") or []}
    hubbed = dev.get("videohub") or {}
    return {"kind": dev.get("kind"), "product": dev.get("product") or dev.get("model") or "", "inputs": inputs,
            "dsk": dev.get("downstream_keyers") or 0, "macros": dev.get("macros") or [],
            "external": sum(1 for i in inputs if 1 <= i <= 40),
            "remote": (dev.get("hyperdeck") or {}).get("remote_enabled") is not False,
            "n_inputs": len(hubbed.get("inputs") or []), "n_outputs": len(hubbed.get("outputs") or [])}


def spec(dev: dict) -> dict:
    """Both faces of a device, from its snapshot."""
    v, drawing = view(dev), drawing_of(dev)
    if drawing == "tvs-hd8":
        return tvs(v, 8, "ATEM Television Studio HD8")
    if drawing == "atem-rack":
        return tvs(v, max(4, min(v["external"] or 8, 12)), v["product"] or "ATEM switcher")
    if drawing == "mini-extreme":
        return mini(v, 8, "ATEM Mini Extreme")
    if drawing == "mini-pro":
        return mini(v, 4, v["product"] or "ATEM Mini")
    if drawing == "hyperdeck-studio":
        return deck(v)
    return hub(v)


def connectors(face: dict) -> list[dict]:
    """Every connector on the back, as core ports report them (app/ports.py `sync`)."""
    return [{"key": it["id"], "kind": PORT_KINDS.get(it["kind"], "other"), "direction": DIRECTIONS.get(it["dir"], "both"),
             "gear_name": it["label"]}
            for it in face["back"]["items"] if it["t"] == "port"]
