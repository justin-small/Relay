"""Connections (#629): the settings an operator types, the stored list (an MQTT password sealed at rest and never sent to a
browser), the live handles modules ask for, Test, and the admin routes with each role. Run: python tests/test_connections.py

The fake devices are a serial server that answers each line, and a Modbus TCP device, both on loopback in a thread; the MQTT
part runs against a real mosquitto when it is installed (skipped otherwise). The Waveshare serial server and the Furman
were not used here.
"""
import asyncio
import os
import shutil
import struct
import subprocess
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
from app import config, connections, db, secretbox, serialio  # noqa: E402
from app.main import app  # noqa: E402

from _harness import await_for, check, done, eq, section, wait_for  # noqa: E402

config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})


class Fakes:
    """A serial server (`OK <line>` for each CR-ended line) and a Modbus TCP device, on loopback in their own thread."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.serial_opened = 0
        self.writers = []
        ready = threading.Event()
        self.thread = threading.Thread(target=self._run, args=(ready,), daemon=True)
        self.thread.start()
        assert ready.wait(5)

    def _run(self, ready):
        asyncio.set_event_loop(self.loop)

        async def boot():
            self.serial = await asyncio.start_server(self._serial, "127.0.0.1", 0)
            self.modbus = await asyncio.start_server(self._modbus, "127.0.0.1", 0)
            self.serial_port = self.serial.sockets[0].getsockname()[1]
            self.modbus_port = self.modbus.sockets[0].getsockname()[1]
            ready.set()

        self.loop.run_until_complete(boot())
        self.loop.run_forever()

    async def _serial(self, reader, writer):
        self.serial_opened += 1
        self.writers.append(writer)
        try:
            while True:
                line = await reader.readuntil(b"\r")
                writer.write(b"OK " + line)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass

    async def _modbus(self, reader, writer):
        try:
            while True:
                head = await reader.readexactly(7)
                tid, _proto, length, unit = struct.unpack(">HHHB", head)
                pdu = await reader.readexactly(length - 1)
                fc, addr, count = pdu[0], *struct.unpack(">HH", pdu[1:5])
                if fc == 3 and addr + count <= 100:
                    data = struct.pack(f">{count}H", *(a * 10 + 7 for a in range(addr, addr + count)))
                    reply = bytes([3, len(data)]) + data
                else:
                    reply = bytes([fc | 0x80, 2])
                writer.write(struct.pack(">HHHB", tid, 0, len(reply) + 1, unit) + reply)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass


fakes = Fakes()
SERIAL = {"name": "Waveshare", "kind": "serial", "link": {"kind": "tcp", "host": "127.0.0.1", "port": fakes.serial_port,
                                                           "baud": 19200}}
MODBUS = {"name": "Meter", "kind": "modbus", "link": {"kind": "tcp", "host": "127.0.0.1", "port": fakes.modbus_port},
          "modbus": {"framing": "tcp", "unit": 1, "timeout": 0.5, "retries": 0}}
MQTT = {"name": "Broker", "kind": "mqtt", "mqtt": {"host": "10.0.0.20", "username": "relay", "password": "hunter2"}}


def refused(fn, *args, **kw):
    """The fixed sentence a Refused or SettingsError carries, or None."""
    try:
        fn(*args, **kw)
    except (connections.Refused, serialio.SettingsError) as exc:
        return exc.message
    return None


with section("What an operator types"):
    s = connections.clean(SERIAL)
    eq("a serial connection keeps only its link", (s["kind"], s["traffic"], list(s["settings"])),
       ("serial", "connections", ["link"]))
    m = connections.clean(MODBUS)
    eq("a Modbus connection has a link and Modbus settings", sorted(m["settings"]), ["link", "modbus"])
    q = connections.clean(MQTT)
    eq("an MQTT connection has its broker", (sorted(q["settings"]), q["settings"]["mqtt"]["port"]), (["mqtt"], 1883))
    eq("a local serial port keeps its framing", connections.clean(
        {"name": "Desk", "kind": "serial", "link": {"kind": "serial", "device": "/dev/ttyUSB0", "baud": 19200}}
    )["settings"]["link"]["baud"], 19200)
    for what, raw in (("no name", {**SERIAL, "name": "  "}), ("no kind", {"name": "x"}), ("an unknown kind", {**SERIAL, "kind": "x"}),
                      ("an unknown traffic class", {**SERIAL, "traffic": "nope"}), ("no link", {"name": "x", "kind": "serial"}),
                      ("a bad Modbus unit", {**MODBUS, "modbus": {"unit": 999}}), ("no broker", {"name": "x", "kind": "mqtt"}),
                      ("nothing", None)):
        check(f"{what} is refused with a sentence", refused(connections.clean, raw), raw)

with section("The stored list"):
    a = connections.create(SERIAL)
    eq("it gets an id and keeps what was typed", (a["id"], a["name"], a["kind"], a["settings"]["link"]["port"]),
       (a["id"], "Waveshare", "serial", fakes.serial_port))
    eq("the list has it", [c["name"] for c in connections.listing()], ["Waveshare"])
    eq("a name that differs only by case or spacing is taken",
       refused(connections.create, {**SERIAL, "name": "  waveSHARE  "}), "Another connection already has that name.")
    b = connections.create(MODBUS)
    c = connections.create(MQTT)
    eq("the list is by name", [x["name"] for x in connections.listing()], ["Broker", "Meter", "Waveshare"])
    eq("an unknown id", connections.get(9999), None)

with section("An MQTT password is sealed at rest and never sent to a browser"):
    raw = db.one("select settings from connections where id = ?", (c["id"],))["settings"]
    check("the stored row does not hold the password", "hunter2" not in raw, raw)
    check("it holds a sealed value", secretbox.PREFIX in raw, raw)
    eq("Relay reads it back", connections.get(c["id"])["settings"]["mqtt"]["password"], "hunter2")
    shown = connections.public(connections.get(c["id"]))
    check("the browser's copy has no password, only whether there is one",
          "password" not in shown["settings"]["mqtt"] and shown["settings"]["mqtt"]["has_password"], shown)
    check("and the text of it appears nowhere in it", "hunter2" not in repr(shown))
    asyncio.run(connections.update(c["id"], {**MQTT, "mqtt": {"host": "10.0.0.21", "username": "relay"}}))
    eq("an edit that leaves the password out keeps it", (connections.get(c["id"])["settings"]["mqtt"]["host"],
                                                         connections.get(c["id"])["settings"]["mqtt"]["password"]),
       ("10.0.0.21", "hunter2"))
    asyncio.run(connections.update(c["id"], {**MQTT, "mqtt": {"host": "10.0.0.21", "username": "relay", "password": ""}}))
    eq("an edit that sends an empty one clears it", connections.get(c["id"])["settings"]["mqtt"]["password"], "")
    asyncio.run(connections.update(c["id"], {**MQTT, "mqtt": {"host": "10.0.0.21", "username": "relay", "password": "new"}}))
    eq("and a new one replaces it", connections.get(c["id"])["settings"]["mqtt"]["password"], "new")

with section("Edit and remove"):
    changed = asyncio.run(connections.update(a["id"], {**SERIAL, "name": "Serial server"}))
    eq("a rename", changed["name"], "Serial server")
    eq("keeping its own name is not a clash", asyncio.run(connections.update(a["id"], {**SERIAL, "name": "Serial server"}))["id"],
       a["id"])
    try:
        asyncio.run(connections.update(a["id"], {**SERIAL, "name": "Meter"}))
        check("taking another's name is refused", False)
    except connections.Refused as exc:
        eq("taking another's name is refused", exc.message, "Another connection already has that name.")
    try:
        asyncio.run(connections.update(9999, SERIAL))
        check("editing one that is not there is refused", False)
    except connections.Refused as exc:
        eq("editing one that is not there is refused", exc.key, "no_connection")
    gone = connections.create({**SERIAL, "name": "Spare"})
    asyncio.run(connections.remove(gone["id"]))
    eq("a removed one is gone", connections.get(gone["id"]), None)


async def live() -> None:
    with section("Live handles"):
        link = await connections.open_link(a["id"])
        check("a serial connection gives a started link", await link.wait_connected(3), link.state())
        eq("it talks to the device", await link.request(b"?ID 0\r"), b"OK ?ID 0\r")
        eq("asking again gives the same link", await connections.open_link(a["id"]) is link, True)
        eq("state shows it", connections.state(a["id"])["connected"], True)
        before = fakes.serial_opened
        connections.network_changed()
        check("a network change connects it again", await await_for(lambda: fakes.serial_opened > before and link.connected))
        eq("and it still works", await link.request(b"again\r"), b"OK again\r")
        await connections.update(a["id"], {**SERIAL, "name": "Serial server", "link": {**SERIAL["link"], "host": "localhost"}})
        eq("an edit closes the old handle", link.connected, False)
        eq("and the state says nothing is open", connections.state(a["id"]), None)
        fresh = await connections.open_link(a["id"])
        check("asking again opens a new one with the new settings", fresh is not link and fresh.settings["host"] == "localhost")
        await connections.close_all()
        eq("close_all closes it", fresh.connected, False)

        modbus_link = await connections.open_link(b["id"])
        check("a Modbus connection's link connects", await modbus_link.wait_connected(3), modbus_link.state())
        client = await connections.open_modbus(b["id"])
        eq("and gives a client on that link", await client.read_holding_registers(3, 2), [37, 47])
        eq("the client's link is the same one", client.link is modbus_link, True)
        for call in (connections.open_modbus(a["id"]), connections.open_link(c["id"]), connections.open_mqtt(a["id"]),
                     connections.open_link(9999)):
            try:
                await call
                check("the wrong kind of connection is refused", False)
            except connections.Refused as exc:
                check("the wrong kind of connection, or none, is refused with a sentence", exc.message, exc.key)
        await connections.remove(b["id"])
        eq("removing a connection closes it", modbus_link.connected, False)

    with section("Test"):
        out = await connections.test(SERIAL)
        eq("a serial server that is there", (out["ok"], out["detail"]), (True, "Connected."))
        out = await connections.test(SERIAL, {"send": "?ID 0"})
        eq("with something to send, the reply comes back", (out["ok"], out["reply"], out["hex"]),
           (True, "OK ?ID 0\\r", "4f 4b 20 3f 49 44 20 30 0d"))
        out = await connections.test(SERIAL, {"send": "x", "terminator": "lf", "until": "lf"})
        eq("a device that never answers in the terminator asked for", (out["ok"], out["detail"]),
           (False, "The device did not answer in time."))
        closed = connections.clean({**SERIAL, "link": {**SERIAL["link"], "port": 1}})["settings"]["link"]
        out = await connections.test({**SERIAL, "link": closed})
        eq("a port nothing listens on fails at once, with a sentence", (out["ok"], bool(out["detail"])), (False, True))
        out = await connections.test({"name": "u", "kind": "serial", "link": {"kind": "udp", "host": "127.0.0.1", "port": 9}})
        eq("UDP says it sent nothing", (out["ok"], out["detail"].startswith("The UDP socket opened")), (True, True))
        out = await connections.test(MODBUS)
        eq("Modbus reads a register", (out["ok"], out["values"]), (True, [7]))
        out = await connections.test(MODBUS, {"address": 5})
        eq("at the address asked for", out["values"], [57])
        out = await connections.test(MODBUS, {"address": 500})
        eq("a device that says no still answered", (out["ok"], out["detail"]),
           (True, "The device answered, but not with a value: The device does not have that address."))
        out = await connections.test(MODBUS, {"address": "x"})
        eq("an address that is not a number", out["ok"], False)
        out = await connections.test({**MODBUS, "link": {**MODBUS["link"], "port": 1}})
        eq("Modbus to nothing fails with a sentence", (out["ok"], bool(out["detail"])), (False, True))
        out = await connections.test(MQTT)
        eq("an MQTT broker that is not there fails with a sentence", (out["ok"], bool(out["detail"])), (False, True))
        try:
            await connections.test({"name": "x", "kind": "serial"})
            check("settings that are not settings are refused", False)
        except serialio.SettingsError as exc:
            check("settings that are not settings are refused", exc.message)


asyncio.run(live())


async def mosquitto() -> None:
    with section("MQTT against a real mosquitto"):
        exe = shutil.which("mosquitto")
        if not exe:
            print("  (skipped: mosquitto is not installed)")
            return
        import socket

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        conf = os.path.join(TMP, "mosquitto.conf")
        Path(conf).write_text(f"listener {port} 127.0.0.1\nallow_anonymous true\npersistence false\n")
        proc = subprocess.Popen([exe, "-c", conf], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            raw = {"name": "Local broker", "kind": "mqtt", "mqtt": {"host": "127.0.0.1", "port": port}}
            def listening():
                try:
                    socket.create_connection(("127.0.0.1", port), 0.2).close()
                    return True
                except OSError:
                    return False

            check("mosquitto is listening", await await_for(listening, 5))
            out = await connections.test(raw)
            eq("Test publishes and hears its own message", (out["ok"], out["detail"]),
               (True, "Connected, and the broker passed a test message back."))
            conn = connections.create(raw)
            client = await connections.open_mqtt(conn["id"])
            check("a saved connection gives a connected client", await client.wait_connected(3), client.state())
            got = []
            await client.subscribe("relay/connections/#", lambda t, p, r: got.append((t, p)))
            await client.publish("relay/connections/x", b"hi", qos=1)
            check("and it works", await await_for(lambda: got == [("relay/connections/x", b"hi")]), got)
            await connections.remove(conn["id"])
            eq("removing it closes the client", client.connected, False)
        finally:
            proc.terminate()
            proc.wait(5)


asyncio.run(mosquitto())

with section("The admin routes"):
    A = "http://relay.local:8001"
    admin = _db.client(app, base_url=A)
    viewer = _db.client(app, role="viewer", username="viewer1", base_url=A)
    operator = _db.client(app, role="operator", username="operator1", base_url=A)
    R = "/api/admin/connections"
    for who, name in ((viewer, "a viewer"), (operator, "an operator")):
        eq(f"{name} cannot see them", who.get(R).status_code, 403)
        eq(f"{name} cannot add one", who.post(R, json=SERIAL).status_code, 403)
        eq(f"{name} cannot test one", who.post(R + "/test", json={"connection": SERIAL}).status_code, 403)
    res = admin.get(R)
    body = res.json()
    eq("an admin sees the list, the ports, the kinds and the traffic classes", (res.status_code, sorted(body)),
       (200, ["connections", "kinds", "serial_ports", "traffic"]))
    check("the list so far, with no password anywhere", "new" not in res.text and "hunter2" not in res.text, res.text[:300])
    check("the Waveshare shows its state as not open", any(c["name"] == "Serial server" and c["state"] is None
                                                           for c in body["connections"]), body["connections"])
    check("connections is one of the traffic classes", "connections" in body["traffic"])
    res = admin.post(R, json={**MQTT, "name": "Hall broker", "mqtt": {"host": "10.0.0.30", "password": "s3cret"}})
    mid = res.json()["connection"]["id"]
    eq("add an MQTT connection", (res.status_code, res.json()["connection"]["settings"]["mqtt"]["has_password"]), (200, True))
    check("its password does not come back", "s3cret" not in res.text, res.text)
    res = admin.post(R, json={**SERIAL, "name": "serial SERVER"})
    eq("the same name again is a 409 with the sentence", (res.status_code, res.json()["detail"]),
       (409, "Another connection already has that name."))
    res = admin.post(R, json={"name": "x", "kind": "serial", "link": {"kind": "tcp", "host": "h", "port": 0}})
    eq("a bad port is a 400 with the sentence", (res.status_code, bool(res.json()["detail"])), (400, True))
    eq("no kind is a 400", admin.post(R, json={"name": "x"}).status_code, 400)
    res = admin.post(f"{R}/{mid}", json={"name": "Hall broker", "kind": "mqtt", "mqtt": {"host": "10.0.0.31"}})
    eq("an edit that leaves the password out is 200", res.status_code, 200)
    eq("and kept it", connections.get(mid)["settings"]["mqtt"]["password"], "s3cret")
    eq("editing one that is not there is 404", admin.post(f"{R}/9999", json=SERIAL).status_code, 404)
    res = admin.post(R + "/test", json={"connection": SERIAL, "probe": {"send": "?ID 0"}})
    eq("Test through the route", (res.status_code, res.json()["ok"], res.json()["reply"]), (200, True, "OK ?ID 0\\r"))
    res = admin.post(R + "/test", json={"connection": {"name": "x", "kind": "serial"}})
    eq("Test with nothing to test is a 400", res.status_code, 400)
    res = admin.post(R + "/test", json={"id": mid, "connection": {"name": "Hall broker", "kind": "mqtt",
                                                                   "mqtt": {"host": "127.0.0.1", "port": 1}}})
    eq("Test with a saved id and no password does not fail on that", (res.status_code, res.json()["ok"]), (200, False))
    eq("removing one that is not there is 404", admin.delete(f"{R}/9999").status_code, 404)
    eq("remove it", admin.delete(f"{R}/{mid}").status_code, 200)
    eq("and it is gone", connections.get(mid), None)

done()
