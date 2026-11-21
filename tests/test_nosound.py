"""The "No sound?" walk (#263): core's steps, a module's steps, and the Dante steps with each fault injected in turn.
Run: .venv/bin/python tests/test_nosound.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import netinfo, nosound  # noqa: E402
from app.modules.dante import walk as dante_walk  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

LIVE = {"running": True, "device": "Core Audio: Dante Virtual Soundcard", "channel": 0, "rms_dbfs": -20.0}
SILENT = {**LIVE, "rms_dbfs": -80.0}
HERE = next((a for p in netinfo.interfaces() for a in p.addresses), "10.9.9.9")


def ids(w):
    return [s["id"] for s in w["steps"]]


def states(w):
    return {s["id"]: s["state"] for s in w["steps"]}


def dante(subs, devices=None, device="Core Audio: Dante Virtual Soundcard"):
    mine = {"name": "DVS-PC", "key": "dvs-pc", "ips": [HERE], "online": True, "subscriptions": subs,
            "clock": {"locked": True}}
    stage = {"name": "STAGE", "key": "stage", "ips": ["10.9.9.2"], "online": True, "clock": {"locked": True}}
    return {"dante": lambda audio: dante_walk.steps({**audio, "device": device}, devices if devices is not None else [mine, stage])}


OK_SUBS = [{"channel": 1, "status": "ok", "source": "Lead Vox@STAGE"}]

with section("core steps"):
    w = nosound.walk({}, False, {})
    eq("not capturing", (w["verdict"], w["stopped_at"]), ("off", "capture"))
    w = nosound.walk(LIVE, True, {})
    eq("sound arriving ends the walk", (w["verdict"], ids(w)), ("sound", ["capture", "input"]))
    w = nosound.walk({**LIVE, "error": "x"}, True, {})
    eq("lost input", (w["verdict"], w["stopped_at"]), ("stopped", "input"))
    w = nosound.walk(SILENT, True, {})
    eq("silent with nothing upstream to check", (w["verdict"], w["stopped_at"]), ("unknown", "source"))

with section("module steps"):
    got = nosound.module_steps(SILENT, {"x": lambda a: (_ for _ in ()).throw(RuntimeError("boom"))})
    eq("a failing module becomes one unknown step", [s["state"] for s in got], ["unknown"])
    got = nosound.module_steps(SILENT, {"x": lambda a: [{"id": "a", "title": "A", "state": "weird"}, "junk"]})
    eq("a bad state becomes unknown and junk is dropped", [s["state"] for s in got], ["unknown"])

with section("Dante: each fault stops the walk at the right step"):
    w = nosound.walk(SILENT, True, dante(OK_SUBS))
    eq("all fine upstream: the fault is the source", (w["verdict"], w["stopped_at"]), ("unknown", "source"))
    check("every Dante step passed", all(v == "ok" for k, v in states(w).items() if k.startswith("dante_")), str(states(w)))

    w = nosound.walk(SILENT, True, dante(OK_SUBS, devices=[]))
    eq("no Dante device on this computer", (w["verdict"], w["stopped_at"]), ("stopped", "dante_here"))

    w = nosound.walk(SILENT, True, dante([{"channel": 1, "status": "none", "source": ""}]))
    eq("unsubscribed", (w["verdict"], w["stopped_at"]), ("stopped", "dante_sub"))
    check("steps after the fault are skipped", "dante_source" not in states(w) or states(w)["dante_source"] == "skipped")

    w = nosound.walk(SILENT, True, dante([{"channel": 1, "status": "unresolved", "source": "Lead Vox@GONE"}]))
    eq("subscription that does not resolve", (w["verdict"], w["stopped_at"]), ("stopped", "dante_sub"))

    mine = {"name": "DVS-PC", "key": "dvs-pc", "ips": [HERE], "online": True, "subscriptions": OK_SUBS}
    w = nosound.walk(SILENT, True, dante(OK_SUBS, devices=[mine]))
    eq("source device offline (not seen)", (w["verdict"], w["stopped_at"]), ("stopped", "dante_source"))
    stage = {"name": "STAGE", "key": "stage", "ips": ["10.9.9.2"], "online": False}
    w = nosound.walk(SILENT, True, dante(OK_SUBS, devices=[mine, stage]))
    eq("source device does not answer", (w["verdict"], w["stopped_at"]), ("stopped", "dante_source"))

    stage = {"name": "STAGE", "key": "stage", "ips": ["10.9.9.2"], "online": True, "clock": {"locked": False}}
    w = nosound.walk(SILENT, True, dante(OK_SUBS, devices=[mine, stage]))
    eq("clock unlocked", (w["verdict"], w["stopped_at"]), ("stopped", "dante_clock"))

    w = nosound.walk(SILENT, True, dante(OK_SUBS, device="MacBook Microphone"))
    eq("a non-Dante input adds no Dante steps", [i for i in ids(w) if i.startswith("dante")], [])

    mine["subscriptions"] = None
    w = nosound.walk(SILENT, True, dante(OK_SUBS, devices=[mine, {"name": "STAGE", "ips": ["10.9.9.2"], "online": True}]))
    eq("unreadable subscriptions are unknown, not a fault", states(w)["dante_sub"], "unknown")

with section("route"):
    check("route exists", any(r.path == "/api/admin/nosound" for r in main.app.routes))

done()
