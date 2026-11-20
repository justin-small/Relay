"""The Open Sound Meter and ProdMesh RTA modules (#149): tolerant parsers, a real UDP packet and a real websocket
against fakes, publishing through core's metering seam, and the admin routes with the modules on and off.
Run: .venv/bin/python tests/test_sound_modules.py"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import time
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

import websockets  # noqa: E402

import app.main as main  # noqa: E402,F401
from app import boards, config, metering  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.open_sound_meter import listener as osm  # noqa: E402
from app.modules.prodmesh_rta import client as pm  # noqa: E402

from _harness import check, done, eq, section, wait_for  # noqa: E402

BANDS = [60.0 + i for i in range(31)]

with section("Open Sound Meter reads many packet shapes"):
    eq("named JSON", osm.parse_packet(b'{"LAF": 71.2, "LAS": 69.9, "LCS": 74.0, "LAeq": 70.1}'),
       {"a_fast": 71.2, "a_slow": 69.9, "c_slow": 74.0, "leq_a": 70.1})
    eq("key=value text", osm.parse_packet(b"LAF=71.2 LCF=75.5\n"), {"a_fast": 71.2, "c_fast": 75.5})
    eq("key: value text", osm.parse_packet(b"dBA: 68.5"), {"a_slow": 68.5})
    eq("a bare value takes the setting", osm.parse_packet(b'{"spl": 80}', "C", "fast"), {"c_fast": 80.0})
    eq("nested JSON", osm.parse_packet(b'{"meter": {"values": {"LAS": 66}}}'), {"a_slow": 66.0})
    eq("leq is A-only when bare", osm.parse_packet(b"leq=70", "C", "slow"), None)
    eq("unknown keys are not levels", osm.parse_packet(b'{"freq": 1000, "gain": 3}'), None)
    eq("an absurd number is dropped", osm.parse_packet(b"LAF=9999"), None)
    eq("junk bytes", osm.parse_packet(b"\xff\xfe\x00junk"), None)
    eq("empty", osm.parse_packet(b""), None)
    eq("settings are cleaned", osm.clean_settings({"group": "10.0.0.1", "port": 0, "weighting": "Z"}),
       {"group": "", "port": 9999, "weighting": "A", "response": "slow", "warn_db": 85.0, "limit_db": 95.0})
    eq("a multicast address is kept", osm.clean_settings({"group": "239.1.1.1", "port": 5000})["group"], "239.1.1.1")

with section("ProdMesh reads the analyser's messages"):
    got = pm.parse_message(json.dumps({"spl": 82.5, "lufs": -14.2, "signal": "Present", "rta": BANDS}))
    eq("level", got["reading"]["a_slow"], 82.5)
    eq("31 bands", len(got["reading"]["bands"]), 31)
    eq("loudness", got["lufs"], -14.2)
    eq("signal", got["signal"], "Present")
    got = pm.parse_message(json.dumps({"data": {"analyser": BANDS[:30]}}))
    eq("30 bands is not an analyser", got, None)
    eq("nested fields are found", pm.parse_message('{"a": {"b": {"dBA": 70}}}')["reading"]["a_slow"], 70.0)
    eq("not JSON", pm.parse_message("hello"), None)
    eq("a list is not a message", pm.parse_message("[1,2]"), None)
    eq("the signal text is cleaned", pm.parse_message('{"signal": "<b>ok</b>"}')["signal"], "bokb")
    eq("settings", pm.clean_settings({"url": "http://x"})["url"], "")
    eq("a ws address is kept", pm.clean_settings({"url": "ws://10.0.0.5:8080/rta"})["url"], "ws://10.0.0.5:8080/rta")
    eq("credentials in the address are refused", pm.clean_settings({"url": "ws://u:p@h/x"})["url"], "")

with section("the seam keeps only sound, labelled sources"):
    metering.register_source("fake", "Fake")
    metering.publish("fake", {"a_slow": 70, "bands": [1] * 30, "junk": 5})
    ext = metering.meter.public()
    check("a source alone is enough for the feed", ext is not None and "fake" in ext["sources"])
    check("a short bands list is dropped", "bands" not in ext["sources"]["fake"])
    metering.publish("nobody", {"a_slow": 1})
    check("an unknown id is ignored", "nobody" not in metering.meter.public()["sources"])
    metering.publish("fake", {"a_slow": "NaN"})
    eq("a bad number keeps the last good reading", metering.meter.public()["sources"]["fake"]["a_slow"], 70.0)
    metering.unregister_source("fake")
    eq("an unregistered source is gone", metering.meter.public(), None)

with section("a real UDP packet reaches the seam"):
    async def udp():
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        metering.register_source(osm.SOURCE, "Open Sound Meter")
        osm.service.settings = osm.clean_settings({"port": port})
        await asyncio.to_thread(osm.service._open, osm.clean_settings({"port": port}))
        osm.service._running = True
        eq("listening", osm.service.snapshot()["listening"], True)
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        target = osm.service._sock.getsockname()
        tx.sendto(b'{"LAF": 72.5}', target)
        tx.sendto(b"what is this", target)
        tx.close()
        for _ in range(100):
            if osm.service.packets >= 2:
                break
            await asyncio.sleep(0.02)
        snap = osm.service.snapshot()
        eq("two packets", snap["packets"], 2)
        eq("one not understood", snap["unknown"], 1)
        eq("kept for the page", snap["samples"], ["what is this"])
        eq("published", metering.meter.public()["sources"][osm.SOURCE]["a_fast"], 72.5)
        await osm.service.stop()
        eq("stopped", osm.service.snapshot()["listening"], False)
        metering.unregister_source(osm.SOURCE)
    asyncio.run(udp())

with section("a real websocket reaches the seam, and reconnects"):
    async def ws():
        conns = []

        async def handler(sock):
            conns.append(sock)
            await sock.send(json.dumps({"spl": 77.0, "signal": "Present", "rta": BANDS}))
            await sock.send("not json")
            await sock.wait_closed()

        server = await websockets.serve(handler, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        metering.register_source(pm.SOURCE, "ProdMesh RTA")
        c = pm.Client(f"ws://127.0.0.1:{port}/rta", 85.0, 95.0)
        c.start()

        async def until(cond, t=5.0):
            end = time.time() + t
            while time.time() < end:
                if cond():
                    return True
                await asyncio.sleep(0.02)
            return False

        check("connects and reads", await until(lambda: c.connected and c.messages >= 2))
        eq("signal", c.signal, "Present")
        r = metering.meter.public()["sources"][pm.SOURCE]
        eq("level", r["a_slow"], 77.0)
        eq("its own thresholds", (r["warn_db"], r["limit_db"]), (85.0, 95.0))
        eq("31 bands", len(r["bands"]), 31)
        eq("the odd message is counted", c.unknown, 1)
        for s in list(conns):
            await s.close()
        check("a drop is noticed", await until(lambda: not c.connected))
        check("it reconnects", await until(lambda: c.connected, 6.0))
        await c.close()
        server.close()
        await server.wait_closed()
        metering.unregister_source(pm.SOURCE)
    asyncio.run(ws())

with section("boards offer the registered sources"):
    metering.register_source("fake", "Fake meter")
    cat = boards.catalogue()
    opts = [o[0] for f in cat["spl"]["fields"] if f["key"] == "source" for o in f["options"]]
    eq("own meter and the source", opts, ["", "fake"])
    props = boards.clean_props({"source": "nothing"}, cat["rta"])
    eq("a source that is not offered falls back", props["source"], "")
    metering.unregister_source("fake")

with section("admin routes, modules on and off"):
    config.ensure_file()
    config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
                 "modules": {"open_sound_meter": {"enabled": True}, "prodmesh_rta": {"enabled": True}}})
    _modules.attach("open_sound_meter")
    _modules.attach("prodmesh_rta")
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    eq("osm reads", admin.get("/api/admin/open-sound-meter").status_code, 200)
    eq("a viewer can look", viewer.get("/api/admin/open-sound-meter").status_code, 200)
    eq("a viewer cannot change", viewer.put("/api/admin/open-sound-meter/settings", json={"port": 5000}).status_code, 403)
    eq("a bad group is a 400", admin.put("/api/admin/open-sound-meter/settings", json={"group": "10.0.0.1"}).status_code, 400)
    eq("pm reads", admin.get("/api/admin/prodmesh-rta").json()["configured"], False)
    eq("a bad address is a 400", admin.put("/api/admin/prodmesh-rta/settings", json={"url": "http://x"}).status_code, 400)
    eq("a viewer cannot change pm", viewer.put("/api/admin/prodmesh-rta/settings", json={"url": ""}).status_code, 403)
    eq("pm says it is not set up", _modules.get("prodmesh_rta").health().state, "warn")
    config.save({"modules": {"open_sound_meter": {"enabled": False}, "prodmesh_rta": {"enabled": False}}})
    _modules.get("open_sound_meter").detach()
    _modules.get("prodmesh_rta").detach()
    eq("osm answers 404 when off", admin.get("/api/admin/open-sound-meter").status_code, 404)
    eq("pm answers 404 when off", admin.get("/api/admin/prodmesh-rta").status_code, 404)

done()
