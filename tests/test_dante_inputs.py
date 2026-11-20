"""Dante and AES67 inputs named in the input picker (#264): the name rules, the feeds from this computer's Dante device,
and core's hook with the Dante module on and off. Run: .venv/bin/python tests/test_dante_inputs.py"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import audio, config, netinfo  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.dante import inputs, service as dante  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402


def dev(index, name, channels=2):
    return {"index": index, "name": name, "label": f"Core Audio: {name}", "channels": channels, "hostapi": "x",
            "default_samplerate": 48000}


with section("names"):
    for name, want in [("Dante Virtual Soundcard (x64)", "Dante"), ("Dante Via", "Dante"), ("Dante AVIO USB", "Dante"),
                       ("openaes67 Sound Card", "AES67"), ("Inferno", "AES67"), ("MacBook Pro Microphone", ""),
                       ("Dante AES67 Bridge", "Dante")]:
        eq(name, inputs.kind_of(name), want)

with section("feeds come from the device on this computer"):
    here = next((a for p in netinfo.interfaces() for a in p.addresses), "")
    subs = [{"channel": 1, "status": "ok", "source": "Lead Vox@STAGE"}, {"channel": 2, "status": "none", "source": ""},
            {"channel": 3, "status": "unresolved", "source": "Gone@BOX"}]
    mine = {"ips": [here], "subscriptions": subs}
    other = {"ips": ["203.0.113.9"], "subscriptions": subs}
    if here:
        eq("this computer's device", inputs.local_feeds([other, mine]), ["Lead Vox@STAGE", "", "Gone@BOX"])
    eq("a device elsewhere is not it", inputs.local_feeds([other]), [])
    eq("an unreadable device gives no feeds", inputs.local_feeds([{"ips": [here], "subscriptions": None}]), [])
    out = inputs.labels([dev(0, "Dante Virtual Soundcard"), dev(1, "openaes67"), dev(2, "Built-in Mic")],
                        [mine] if here else [])
    eq("only Dante and AES67 are labelled", {i: o["kind"] for i, o in out.items()}, {0: "Dante", 1: "AES67"})
    eq("AES67 inputs get no Dante feeds", out[1]["feeds"], [])

with section("core's hook: nothing named with the module off, labels with it on"):
    devices = [dev(0, "Dante Virtual Soundcard"), dev(1, "Built-in Mic")]
    eq("off: no kind", [(d["kind"], d["feeds"]) for d in audio.label_inputs(devices)], [("", []), ("", [])])
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"dante": {"enabled": True}}})
    _modules.attach("dante")
    got = audio.label_inputs(devices)
    eq("on: kinds", [d["kind"] for d in got], ["Dante", ""])
    eq("the saved label is untouched", [d["label"] for d in got], [d["label"] for d in devices])
    config.save({"modules": {"dante": {"enabled": False}}})
    _modules.get("dante").detach()
    eq("off again", [d["kind"] for d in audio.label_inputs(devices)], ["", ""])

done()
