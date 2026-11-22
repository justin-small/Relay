"""Connections (#629): the serial, Modbus and MQTT connections an operator sets up once, for any module to use.

An operator adds a connection on the admin page (a Waveshare serial server at 10.10.0.181:4196 at 19200 8N1, a Modbus
meter, an MQTT broker), presses Test, and a module is then told which connection to use. The module asks for it by id:

    link = await connections.open_link(conn_id, traffic="furman")        # serial, or the link under a Modbus connection
    modbus = await connections.open_modbus(conn_id)                      # a ModbusClient on the connection's link
    mqtt = await connections.open_mqtt(conn_id)                          # a started MqttClient

A handle is started once and shared by whoever asks for the same connection and traffic class; editing or deleting the
connection closes it, so ask again when you need it rather than keeping one across an edit. `traffic` is the module's own
network traffic class (so its traffic leaves through the port the operator routed it to); without it the connection's
own class, "connections", is used.

Kinds and what `settings` holds:
  serial   {"link": serialio.clean_link(...)}
  modbus   {"link": serialio.clean_link(...), "modbus": modbus.clean_modbus(...)}
  mqtt     {"mqtt": mqttclient.clean_mqtt(...)}   (the password is sealed at rest and never sent to a browser)

Table: app/migrations/0016_connections.sql.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

from . import db, modbus, mqttclient, netroute, secretbox, serialio, text

log = logging.getLogger("relay.connections")

KINDS = ("serial", "modbus", "mqtt")
DEFAULT_TRAFFIC = serialio.DEFAULT_TRAFFIC
NAME_MAX = 60
MAX_CONNECTIONS = 200
PASSWORD_PURPOSE = "connections:mqtt-password"
TEST_WAIT = 6.0

REFUSALS = {"no_connection": "That connection does not exist.", "name": "Give the connection a name.",
            "exists": "Another connection already has that name.", "too_many": "That is as many connections as Relay keeps.",
            "kind": "Choose serial, Modbus or MQTT.", "traffic": "Relay has no such kind of traffic.",
            "no_probe": "Enter something to send."}


class Refused(ValueError):
    """A change that cannot be made. `key` picks the fixed sentence in REFUSALS; `message` is that sentence."""

    def __init__(self, key: str):
        super().__init__(REFUSALS[key])
        self.key = key
        self.message = REFUSALS[key]


# ------------------------------------------------------------------ settings

def _name(raw: Any) -> str:
    name = text.squeeze(raw)[:NAME_MAX]
    if not name:
        raise Refused("name")
    return name


def _traffic(raw: Any) -> str:
    traffic = str(raw or DEFAULT_TRAFFIC)
    if traffic not in netroute.classes():
        raise Refused("traffic")
    return traffic


def clean(raw: Any, keep: dict | None = None) -> dict:
    """A connection from what an operator typed: {"name", "kind", "traffic", "settings"} or Refused / SettingsError.
    `keep` is the connection being edited: an MQTT password that is left out keeps its old value."""
    raw = raw if isinstance(raw, dict) else {}
    kind = str(raw.get("kind") or "")
    if kind not in KINDS:
        raise Refused("kind")
    name, traffic = _name(raw.get("name")), _traffic(raw.get("traffic"))
    if kind == "serial":
        settings: dict = {"link": serialio.clean_link(raw.get("link"))}
    elif kind == "modbus":
        settings = {"link": serialio.clean_link(raw.get("link")), "modbus": modbus.clean_modbus(raw.get("modbus"))}
    else:
        given = raw.get("mqtt") if isinstance(raw.get("mqtt"), dict) else {}
        if keep and "password" not in given:
            given = {**given, "password": keep["settings"]["mqtt"].get("password", "")}
        settings = {"mqtt": mqttclient.clean_mqtt(given)}
    return {"name": name, "kind": kind, "traffic": traffic, "settings": settings}


def _pack(settings: dict) -> str:
    stored = json.loads(json.dumps(settings))
    if "mqtt" in stored:
        stored["mqtt"]["password"] = secretbox.seal(stored["mqtt"].get("password", ""), PASSWORD_PURPOSE)
    return json.dumps(stored, sort_keys=True)


def _unpack(blob: str) -> dict:
    settings = json.loads(blob)
    if "mqtt" in settings:
        opened = secretbox.unseal(settings["mqtt"].get("password", ""), PASSWORD_PURPOSE)
        settings["mqtt"]["password"] = opened or ""
    return settings


def _row(r: dict | None) -> dict | None:
    if r is None:
        return None
    return {"id": r["id"], "name": r["name"], "kind": r["kind"], "traffic": r["traffic"],
            "settings": _unpack(r["settings"]), "created_at": r["created_at"], "updated_at": r["updated_at"]}


def public(conn: dict) -> dict:
    """The connection as a browser may see it: an MQTT password is only whether there is one."""
    settings = json.loads(json.dumps(conn["settings"]))
    if "mqtt" in settings:
        settings["mqtt"]["has_password"] = bool(settings["mqtt"].pop("password", ""))
    return {**conn, "settings": settings}


# ------------------------------------------------------------------ the list

def get(cid: int) -> dict | None:
    return _row(db.one("select * from connections where id = ?", (cid,)))


def listing() -> list[dict]:
    return [_row(r) for r in db.query("select * from connections order by lower(name)")]  # type: ignore[misc]


def _taken(tx: Any, name: str, except_id: int | None) -> bool:
    return any(text.same_name(r["name"], name) and r["id"] != except_id
               for r in tx.query("select id, name from connections"))


def create(raw: Any) -> dict:
    new = clean(raw)
    with db.transaction() as tx:
        if tx.one("select count(*) n from connections")["n"] >= MAX_CONNECTIONS:
            raise Refused("too_many")
        if _taken(tx, new["name"], None):
            raise Refused("exists")
        now = db.now()
        tx.execute("insert into connections (name, kind, traffic, settings, created_at, updated_at) values (?, ?, ?, ?, ?, ?)",
                   (new["name"], new["kind"], new["traffic"], _pack(new["settings"]), now, now))
        cid = tx.one("select last_insert_rowid() id")["id"]
    return get(cid)  # type: ignore[return-value]


async def update(cid: int, raw: Any) -> dict:
    old = get(cid)
    if old is None:
        raise Refused("no_connection")
    new = clean(raw, keep=old)
    with db.transaction() as tx:
        if _taken(tx, new["name"], cid):
            raise Refused("exists")
        tx.execute("update connections set name = ?, kind = ?, traffic = ?, settings = ?, updated_at = ? where id = ?",
                   (new["name"], new["kind"], new["traffic"], _pack(new["settings"]), db.now(), cid))
    await close(cid)
    return get(cid)  # type: ignore[return-value]


async def remove(cid: int) -> dict:
    old = get(cid)
    if old is None:
        raise Refused("no_connection")
    db.execute("delete from connections where id = ?", (cid,))
    await close(cid)
    return old


# ------------------------------------------------------------------ live handles

# (connection id, traffic class) -> {"link": Link | None, "modbus": ModbusClient | None, "mqtt": MqttClient | None}
_live: dict[tuple[int, str], dict] = {}
_lock: asyncio.Lock | None = None


def _guard() -> asyncio.Lock:
    global _lock
    if _lock is None:
        _lock = asyncio.Lock()
    return _lock


def _need(cid: int, *kinds: str) -> dict:
    conn = get(cid)
    if conn is None:
        raise Refused("no_connection")
    if conn["kind"] not in kinds:
        raise Refused("kind")
    return conn


async def _handle(cid: int, traffic: str | None, *kinds: str) -> tuple[dict, dict]:
    conn = _need(cid, *kinds)
    key = (cid, traffic or conn["traffic"])
    async with _guard():
        entry = _live.get(key)
        if entry is None:
            entry = _live[key] = {}
        if not entry:
            settings = conn["settings"]
            if conn["kind"] == "mqtt":
                entry["mqtt"] = mqttclient.MqttClient(settings["mqtt"], traffic=key[1], name=conn["name"])
                await entry["mqtt"].start()
            else:
                entry["link"] = serialio.Link(settings["link"], traffic=key[1], name=conn["name"])
                await entry["link"].start()
                if conn["kind"] == "modbus":
                    m = settings["modbus"]
                    entry["modbus"] = modbus.ModbusClient(entry["link"], framing=m["framing"], unit=m["unit"],
                                                          timeout=m["timeout"], retries=m["retries"])
    return conn, entry


async def open_link(cid: int, traffic: str | None = None) -> serialio.Link:
    """The started Link of a serial connection, or the link under a Modbus connection."""
    return (await _handle(cid, traffic, "serial", "modbus"))[1]["link"]


async def open_modbus(cid: int, traffic: str | None = None) -> modbus.ModbusClient:
    return (await _handle(cid, traffic, "modbus"))[1]["modbus"]


async def open_mqtt(cid: int, traffic: str | None = None) -> mqttclient.MqttClient:
    return (await _handle(cid, traffic, "mqtt"))[1]["mqtt"]


def state(cid: int) -> dict | None:
    """How the live handle of a connection is doing (the first one open), or None when nothing is open."""
    for (open_id, _), entry in _live.items():
        if open_id == cid and entry:
            return (entry.get("mqtt") or entry["link"]).state()
    return None


async def close(cid: int) -> None:
    async with _guard():
        gone = [key for key in _live if key[0] == cid]
        entries = [_live.pop(key) for key in gone]
    for entry in entries:
        for handle in (entry.get("mqtt"), entry.get("link")):
            if handle is not None:
                await handle.close()


async def close_all() -> None:
    for cid in {key[0] for key in list(_live)}:
        await close(cid)


def network_changed() -> None:
    """A network port changed: every open connection connects again."""
    for entry in _live.values():
        for handle in (entry.get("mqtt"), entry.get("link")):
            if handle is not None:
                handle.reconnect()


# ------------------------------------------------------------------ Test

_ESCAPES = {13: "\\r", 10: "\\n", 9: "\\t", 0: "\\0"}


def _shown(data: bytes) -> str:
    """Bytes a device sent, as text an operator can read: printable characters, \\r and \\n, the rest as \\xNN."""
    return "".join(chr(b) if 32 <= b < 127 else _ESCAPES.get(b, f"\\x{b:02x}") for b in data[:200])


async def _settle(handle: Any, timeout: float = TEST_WAIT) -> bool:
    """Wait until a link or client has connected or its first try has failed (it keeps retrying; a Test must not wait for that)."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not handle.connected and not handle.last_error and loop.time() < deadline:
        await asyncio.sleep(0.02)
    return handle.connected


async def _test_link(link: serialio.Link, probe: dict) -> dict:
    if not await _settle(link):
        return {"ok": False, "detail": link.last_error or "Could not connect."}
    if link.settings["kind"] == "udp" and not probe.get("send"):
        return {"ok": True, "detail": "The UDP socket opened. UDP has no connection, so nothing was sent."}
    if not probe.get("send"):
        return {"ok": True, "detail": "Connected."}
    terminator = serialio.TERMINATORS.get(str(probe.get("terminator") or "cr"), b"\r")
    until = serialio.TERMINATORS.get(str(probe.get("until") or probe.get("terminator") or "cr"), b"\r")
    try:
        reply = await link.request(str(probe["send"]).encode("utf-8", "replace")[:200] + terminator, until=until,
                                   timeout=3.0)
    except serialio.LinkError as exc:
        return {"ok": False, "detail": exc.message}
    return {"ok": True, "detail": "The device answered.", "reply": _shown(reply), "hex": reply[:200].hex(" ")}


async def _test_modbus(client: modbus.ModbusClient, link: serialio.Link, probe: dict) -> dict:
    if not await _settle(link):
        return {"ok": False, "detail": link.last_error or "Could not connect."}
    try:
        address = int(probe.get("address") or 0)
    except (TypeError, ValueError):
        return {"ok": False, "detail": "The address to read must be a number."}
    try:
        values = await client.read_holding_registers(address, 1)
    except modbus.ModbusError as exc:
        if exc.code is not None:
            return {"ok": True, "detail": "The device answered, but not with a value: " + exc.message}
        return {"ok": False, "detail": exc.message}
    return {"ok": True, "detail": f"The device answered: register {address} is {values[0]}.", "values": values}


async def _test_mqtt(client: mqttclient.MqttClient) -> dict:
    if not await _settle(client):
        return {"ok": False, "detail": client.last_error or "Could not connect."}
    topic = "relay/test/" + os.urandom(6).hex()
    heard: asyncio.Future = asyncio.get_running_loop().create_future()

    def got(t: str, payload: bytes, retain: bool) -> None:
        if not heard.done():
            heard.set_result(payload)

    try:
        await client.subscribe(topic, got, qos=1)
        await client.publish(topic, b"relay", qos=1)
        await asyncio.wait_for(heard, 3.0)
    except mqttclient.MqttError as exc:
        return {"ok": False, "detail": "Connected, but the broker would not take a test message: " + exc.message}
    except asyncio.TimeoutError:
        return {"ok": False, "detail": "Connected, but the broker did not pass a test message back. Check its access rules."}
    finally:
        await client.unsubscribe(topic)
    return {"ok": True, "detail": "Connected, and the broker passed a test message back."}


async def test(raw: Any, probe: Any = None, *, cid: int | None = None) -> dict:
    """Try a connection for the operator: {"ok": bool, "detail": sentence, ...}. `raw` is settings as typed (not saved); with
    `cid` an MQTT password left out is the saved one. `probe` is what to send: {"send", "terminator", "until"} for serial,
    {"address"} for Modbus. A temporary handle is opened and closed; nothing is saved."""
    probe = probe if isinstance(probe, dict) else {}
    new = clean(raw, keep=get(cid) if cid is not None else None)
    s = new["settings"]
    if new["kind"] == "mqtt":
        client = mqttclient.MqttClient(s["mqtt"], traffic=new["traffic"], name=new["name"])
        await client.start()
        try:
            return await _test_mqtt(client)
        finally:
            await client.close()
    link = serialio.Link(s["link"], traffic=new["traffic"], name=new["name"])
    await link.start()
    try:
        if new["kind"] == "serial":
            return await _test_link(link, probe)
        m = s["modbus"]
        client_m = modbus.ModbusClient(link, framing=m["framing"], unit=m["unit"], timeout=m["timeout"],
                                       retries=m["retries"])
        return await _test_modbus(client_m, link, probe)
    finally:
        await link.close()
