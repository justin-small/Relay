"""The Dante module (#257): the inventory folded from mDNS answers (as the field survey found them), the control-port
check against a real UDP socket on loopback, the gear it offers, and its admin routes with the module on and off.
Run: .venv/bin/python tests/test_dante.py"""
import os
import socket
import struct
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import config, mdns  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.dante import service as dante  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402

ARC, CMC, CHAN = "_netaudio-arc._udp.local", "_netaudio-cmc._udp.local", "_netaudio-chan._udp.local"


def found(service, label, host, port, ips, **txt):
    return mdns.Found(service, f"{label}.{service}", host, port, list(ips), dict(txt))


with section("the inventory is keyed by the Dante name"):
    inv = dante.inventory([
        found(ARC, "DT168-0a1b2c", "dt168-0a1b2c.local", 4440, ["10.9.0.21"], arcp_vers="2.8.12"),
        found(CMC, "DT168-0a1b2c", "dt168-0a1b2c.local", 8800, ["10.9.0.21"]),
        found(CHAN, "Lead Vox@DT168-0a1b2c", "dt168-0a1b2c.local", 4455, ["10.9.0.21"]),
        found(CHAN, "Pastor @ Pulpit@DT168-0a1b2c", "dt168-0a1b2c.local", 4455, ["10.9.0.21"]),
        found(ARC, "STAGE-MAC", "stage-mac.local", 4440, ["10.9.0.40"], model="DvsMacOs"),
        found(ARC, "stage-mac", "stage-mac-lan.local", 4440, ["192.168.1.40", "fe80::1"], model="DvsMacOs"),
        found(ARC, "BOOTH-PC", "booth-pc.local", 24440, ["10.9.0.50"], model="ViaWin", router_info="Dante Via"),
        found("_ndi._tcp.local", "CAM 1", "cam1.local", 5961, ["10.0.0.30"]),
    ])
    eq("one record per Dante name, NDI left out", [d["name"] for d in inv], ["BOOTH-PC", "DT168-0a1b2c", "STAGE-MAC"])
    box = inv[1]
    eq("ports come from SRV", box["ports"], {"arc": 4440, "cmc": 8800, "chan": 4455})
    eq("channels, with an @ in a name kept", box["transmit"], ["Lead Vox", "Pastor @ Pulpit"])
    eq("the ARC version", box["arc_version"], "2.8.12")
    mac = inv[2]
    eq("a computer on two ports is one device (case ignored), IPv4 only", mac["ips"], ["10.9.0.40", "192.168.1.40"])
    eq("Via uses its own control port and is marked", (inv[0]["ports"]["arc"], inv[0]["via"]), (24440, True))
    eq("a stage box is not Via", box["via"], False)
    eq("channel split", dante.split_channel("A@B@Box"), ("A@B", "Box"))
    eq("no channel", dante.split_channel("Box"), ("", "Box"))

with section("the control-port check talks to a real socket"):
    eq("the probe packet is the ARC header", dante.probe_packet(7), struct.pack(">HHHHH", 0x27FF, 10, 7, 0x1000, 0))
    answering = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    answering.bind(("127.0.0.1", 0))
    answering.settimeout(2)
    silent = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    silent.bind(("127.0.0.1", 0))
    heard = []

    def answer():
        try:
            data, addr = answering.recvfrom(2048)
            heard.append(data)
            answering.sendto(data[:8] + b"\x10\x00\x00\x00", addr)
        except OSError:
            pass

    t = threading.Thread(target=answer, daemon=True)
    t.start()
    eq("an answering device is reachable", dante.probe("127.0.0.1", answering.getsockname()[1], 3, wait=2.0), True)
    t.join(2)
    eq("it was sent the probe", heard, [dante.probe_packet(3)])
    eq("a silent device is not", dante.probe("127.0.0.1", silent.getsockname()[1], 4, wait=0.3), False)
    answering.close()
    silent.close()

with section("the gear it offers"):
    eq("nothing before the first scan", dante.service.gear_list(), None)
    dante.service.record(inv)
    offer = dante.service.gear_list()
    eq("a box per device", [d["name"] for d in offer["devices"]], ["BOOTH-PC", "DT168-0a1b2c", "STAGE-MAC"])
    check("Via is tagged", "Dante Via" in offer["devices"][0]["tags"])
    box_channels = offer["channels"][1]
    eq("channels are audio sources keyed under the device", [(c["key"], c["kind"], c["direction"]) for c in box_channels["items"]],
       [("dt168-0a1b2c:tx:Lead Vox", "audio", "source"), ("dt168-0a1b2c:tx:Pastor @ Pulpit", "audio", "source")])
    eq("limited to that device's keys", box_channels["within"], "dt168-0a1b2c:")
    dante.service.record([])

with section("admin routes, module on and off"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"dante": {"enabled": True}}})
    _modules.attach("dante")
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    eq("it reads", admin.get("/api/admin/dante").json()["devices"], [])
    eq("a viewer can look", viewer.get("/api/admin/dante").status_code, 200)
    eq("a viewer cannot scan", viewer.post("/api/admin/dante/scan").status_code, 403)
    eq("a scan with the loop off answers", admin.post("/api/admin/dante/scan").status_code, 200)
    check("health says something", _modules.get("dante").health().detail != "")
    config.save({"modules": {"dante": {"enabled": False}}})
    _modules.get("dante").detach()
    eq("it answers 404 when off", admin.get("/api/admin/dante").status_code, 404)

done()
