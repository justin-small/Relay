"""MQTT client for modules (#629): MQTT 3.1.1 over a TCP or TLS connection that netsock pins to the chosen network port.

    client = MqttClient(clean_mqtt({"host": "10.0.0.20", "username": "relay", "password": "..."}), traffic="mymodule")
    await client.start()
    await client.subscribe("stage/+/state", on_message)          # async or plain function: (topic, payload, retain)
    await client.publish("stage/lights/cmd", b"on", qos=1)

It connects by itself, reconnects with a backoff, and subscribes again after a reconnect, so a module sets its
subscriptions once. QoS 0 and 1 are supported (a subscription asks for at most 1; QoS 2 is refused). The last will,
retained messages, user name and password, a client id and TLS (system trust, or a CA the operator pastes in for a broker
with its own certificate) are settings.

Written here, not taken from paho-mqtt or aiomqtt, because they open their own sockets, which would bypass the routing
that keeps this traffic on the network port the operator chose (#345). Every error is a fixed sentence.
"""
from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import os
import ssl
import struct
import time
from typing import Any, Awaitable, Callable

from . import netsock, serialio

log = logging.getLogger("relay.mqtt")

DEFAULT_TRAFFIC = serialio.DEFAULT_TRAFFIC
BACKOFF = serialio.BACKOFF
CONNECT_TIMEOUT = 10.0
ACK_TIMEOUT = 10.0
MAX_PAYLOAD = 1 << 20
MAX_PACKET = 4 * MAX_PAYLOAD

CONNECT, CONNACK, PUBLISH, PUBACK = 1, 2, 3, 4
SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ, PINGRESP, DISCONNECT = 8, 9, 10, 11, 12, 13, 14

REFUSED = {1: "The broker does not accept MQTT 3.1.1.", 2: "The broker refused this client id.",
           3: "The broker is not available.", 4: "The broker refused the user name or password.",
           5: "The broker did not authorise this client."}

Handler = Callable[[str, bytes, bool], "Awaitable[None] | None"]


class MqttError(OSError):
    """An MQTT operation that failed. `message` is a fixed sentence."""

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


# ------------------------------------------------------------------ settings and topics

def _text(raw: Any, limit: int) -> str:
    return str(raw or "").strip()[:limit]


def valid_topic(topic: Any) -> bool:
    """A topic name to publish to: no wildcards, no NUL, 1 to 65535 bytes of UTF-8."""
    return (isinstance(topic, str) and 0 < len(topic.encode("utf-8", "replace")) <= 65535
            and not any(c in topic for c in "+#\x00"))


def valid_filter(filt: Any) -> bool:
    """A topic filter to subscribe to: `+` stands for one whole level, `#` only as the last level."""
    if not isinstance(filt, str) or not 0 < len(filt.encode("utf-8", "replace")) <= 65535 or "\x00" in filt:
        return False
    levels = filt.split("/")
    for i, level in enumerate(levels):
        if "#" in level and (level != "#" or i != len(levels) - 1):
            return False
        if "+" in level and level != "+":
            return False
    return True


def topic_matches(filt: str, topic: str) -> bool:
    """Whether `topic` is covered by `filt`. A topic starting with $ (the broker's own) is not matched by a filter that
    starts with a wildcard."""
    f, t = filt.split("/"), topic.split("/")
    if t[0].startswith("$") and f[0] in ("+", "#"):
        return False
    for i, level in enumerate(f):
        if level == "#":
            return True
        if i >= len(t) or (level != "+" and level != t[i]):
            return False
    return len(f) == len(t)


def clean_mqtt(raw: Any) -> dict:
    """MQTT settings from what an operator typed, or serialio.SettingsError. `password` is a secret."""
    raw = raw if isinstance(raw, dict) else {}
    host = " ".join(str(raw.get("host") or "").split())
    if not host or len(host) > 253 or any(c in host for c in "/\\ @"):
        raise serialio.SettingsError("Enter the address of the MQTT broker.")
    tls = bool(raw.get("tls"))
    try:
        port = int(raw.get("port") or (8883 if tls else 1883))
        keepalive = int(raw.get("keepalive") or 60)
    except (TypeError, ValueError):
        raise serialio.SettingsError("Port and keep-alive must be numbers.") from None
    if not 1 <= port <= 65535:
        raise serialio.SettingsError("Port must be from 1 to 65535.")
    if not 5 <= keepalive <= 3600:
        raise serialio.SettingsError("Keep-alive is from 5 to 3600 seconds.")
    out: dict = {"host": host, "port": port, "tls": tls, "username": _text(raw.get("username"), 200),
                 "password": str(raw.get("password") or "")[:200], "client_id": _text(raw.get("client_id"), 100),
                 "keepalive": keepalive, "ca": str(raw.get("ca") or "").strip()[:20000]}
    if out["ca"] and "BEGIN CERTIFICATE" not in out["ca"]:
        raise serialio.SettingsError("The broker's certificate must be in PEM form.")
    will = raw.get("will")
    if isinstance(will, dict) and will.get("topic"):
        topic = str(will["topic"])
        if not valid_topic(topic):
            raise serialio.SettingsError("The last-will topic cannot hold + or #.")
        try:
            qos = int(will.get("qos") or 0)
        except (TypeError, ValueError):
            raise serialio.SettingsError("The last-will QoS is 0 or 1.") from None
        if qos not in (0, 1):
            raise serialio.SettingsError("The last-will QoS is 0 or 1.")
        out["will"] = {"topic": topic, "payload": str(will.get("payload") or "")[:2000], "qos": qos,
                       "retain": bool(will.get("retain"))}
    return out


# ------------------------------------------------------------------ packets

def _string(text: str) -> bytes:
    raw = text.encode("utf-8")
    return struct.pack(">H", len(raw)) + raw


def _length(n: int) -> bytes:
    out = bytearray()
    while True:
        digit, n = n % 128, n // 128
        out.append(digit | (0x80 if n else 0))
        if not n:
            return bytes(out)


def packet(kind: int, flags: int, body: bytes = b"") -> bytes:
    return bytes([kind << 4 | flags]) + _length(len(body)) + body


def connect_packet(s: dict, client_id: str) -> bytes:
    flags = 0x02                                  # a clean session: the client keeps its own subscriptions
    payload = _string(client_id)
    will = s.get("will")
    if will:
        flags |= 0x04 | will["qos"] << 3 | (0x20 if will["retain"] else 0)
        payload += _string(will["topic"]) + struct.pack(">H", len(will["payload"].encode())) + will["payload"].encode()
    if s["username"]:
        flags |= 0x80
        payload += _string(s["username"])
        if s["password"]:
            flags |= 0x40
            payload += _string(s["password"])
    return packet(CONNECT, 0, _string("MQTT") + bytes([4, flags]) + struct.pack(">H", int(s["keepalive"])) + payload)


def publish_packet(topic: str, payload: bytes, qos: int, retain: bool, packet_id: int) -> bytes:
    body = _string(topic) + (struct.pack(">H", packet_id) if qos else b"") + payload
    return packet(PUBLISH, qos << 1 | int(retain), body)


async def read_packet(reader: asyncio.StreamReader) -> tuple[int, int, bytes]:
    """One packet: (type, flags, body). Raises MqttError for a packet that is not MQTT, asyncio.IncompleteReadError at EOF."""
    first = (await reader.readexactly(1))[0]
    length, shift = 0, 0
    for _ in range(4):
        digit = (await reader.readexactly(1))[0]
        length |= (digit & 0x7F) << shift
        shift += 7
        if not digit & 0x80:
            break
    else:
        raise MqttError("The broker sent something that is not MQTT.")
    if length > MAX_PACKET:
        raise MqttError("The broker sent a message that is too large.")
    return first >> 4, first & 0x0F, await reader.readexactly(length) if length else b""


# ------------------------------------------------------------------ the client

class MqttClient:
    """One connection to a broker, kept up. `settings` come from clean_mqtt(); `traffic` is the module's network traffic
    class. `on_state()` runs when it connects or loses the connection."""

    def __init__(self, settings: dict, *, traffic: str = DEFAULT_TRAFFIC, name: str = "",
                 on_state: Callable[[], None] | None = None, backoff: tuple[float, float] = BACKOFF):
        self.settings = settings
        self.traffic = traffic
        self.name = name
        self.on_state = on_state
        self._backoff = backoff
        self.client_id = settings.get("client_id") or "relay-" + os.urandom(4).hex()
        self._task: asyncio.Task | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._wlock: asyncio.Lock | None = None
        self._up: asyncio.Event | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._next_id = 0
        self._subs: dict[str, tuple[int, Handler]] = {}
        self.connected = False
        self.last_error = ""
        self.last_error_at = 0.0
        self.since = 0.0
        self.attempts = 0
        self.received = 0
        self.sent = 0

    # -- lifecycle

    async def start(self) -> None:
        if self._task is not None:
            return
        self._wlock, self._up = asyncio.Lock(), asyncio.Event()
        self._task = asyncio.create_task(self._run(), name=f"mqtt-{self.name or self.settings['host']}")

    async def close(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            if self.connected:
                with contextlib.suppress(OSError, asyncio.TimeoutError, MqttError):
                    await self._write(packet(DISCONNECT, 0))
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        await self._drop()

    def reconnect(self) -> None:
        """Drop the connection and connect again now (the network port it uses changed)."""
        if self._task is None:
            return
        self._task.cancel()
        self._task = asyncio.create_task(self._run(), name=f"mqtt-{self.name or self.settings['host']}")

    async def wait_connected(self, timeout: float = 5.0) -> bool:
        if self.connected:
            return True
        if self._up is None:
            return False
        try:
            await asyncio.wait_for(self._up.wait(), timeout)
        except asyncio.TimeoutError:
            return False
        return self.connected

    def state(self) -> dict:
        return {"connected": self.connected, "last_error": self.last_error, "last_error_at": self.last_error_at or None,
                "connected_since": self.since if self.connected else None, "attempts": self.attempts,
                "received": self.received, "sent": self.sent, "client_id": self.client_id,
                "subscriptions": sorted(self._subs)}

    def _changed(self) -> None:
        if self.on_state is not None:
            try:
                self.on_state()
            except Exception:  # noqa: BLE001 - a module's callback must not stop the client
                log.exception("mqtt state callback failed")

    def _fail(self, sentence: str) -> None:
        self.last_error, self.last_error_at = sentence, time.time()

    async def _drop(self) -> None:
        writer, self._writer = self._writer, None
        was, self.connected = self.connected, False
        if self._up is not None:
            self._up.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MqttError("The connection was lost."))
        self._pending.clear()
        if writer is not None:
            writer.close()
            with contextlib.suppress(OSError, asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(), 1.0)
        if was:
            self._changed()

    # -- the wire

    def _ssl(self) -> Any:
        if not self.settings["tls"]:
            return None
        ca = self.settings.get("ca")
        if not ca:
            return True                            # netsock builds the default context
        try:
            return ssl.create_default_context(cadata=ca)
        except ssl.SSLError:
            raise MqttError("The broker certificate you pasted could not be read.") from None

    async def _write(self, data: bytes) -> None:
        writer, lock = self._writer, self._wlock
        if writer is None or lock is None:
            raise MqttError("Not connected.")
        async with lock:
            try:
                writer.write(data)
                await asyncio.wait_for(writer.drain(), ACK_TIMEOUT)
            except (OSError, asyncio.TimeoutError):
                raise MqttError("Could not send to the broker.") from None

    def _id(self) -> int:
        for _ in range(65535):
            self._next_id = self._next_id % 65535 + 1
            if self._next_id not in self._pending:
                return self._next_id
        raise MqttError("Too many requests are waiting for the broker.")

    async def _ask(self, make: Callable[[int], bytes], timeout: float = ACK_TIMEOUT) -> bytes:
        """Send a packet that carries an id and wait for the broker's acknowledgement of it."""
        if not self.connected:
            raise MqttError("Not connected.")
        pid = self._id()
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[pid] = fut
        try:
            await self._write(make(pid))
            return await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            raise MqttError("The broker did not answer in time.") from None
        finally:
            self._pending.pop(pid, None)

    async def _handle(self, kind: int, flags: int, body: bytes) -> None:
        if kind == PUBLISH:
            qos, retain = (flags >> 1) & 3, bool(flags & 1)
            if len(body) < 2:
                raise MqttError("The broker sent something that is not MQTT.")
            n = struct.unpack(">H", body[:2])[0]
            topic, rest = body[2:2 + n].decode("utf-8", "replace"), body[2 + n:]
            if qos:
                pid, rest = struct.unpack(">H", rest[:2])[0], rest[2:]
                if qos == 1:
                    await self._write(packet(PUBACK, 0, struct.pack(">H", pid)))
            self.received += 1
            for filt, (_, handler) in list(self._subs.items()):
                if topic_matches(filt, topic):
                    await self._deliver(handler, topic, rest, retain)
        elif kind in (PUBACK, SUBACK, UNSUBACK):
            if len(body) >= 2:
                fut = self._pending.get(struct.unpack(">H", body[:2])[0])
                if fut is not None and not fut.done():
                    fut.set_result(body[2:])
        elif kind == PINGRESP:
            pass
        else:
            raise MqttError("The broker sent something that is not MQTT.")

    @staticmethod
    async def _deliver(handler: Handler, topic: str, payload: bytes, retain: bool) -> None:
        try:
            result = handler(topic, payload, retain)
            if inspect.isawaitable(result):
                await result
        except Exception:  # noqa: BLE001 - one module's handler must not stop the others or the connection
            log.exception("mqtt message handler failed for %s", topic)

    async def _session(self) -> None:
        s = self.settings
        reader, writer = await netsock.open_tcp(self.traffic, s["host"], s["port"], CONNECT_TIMEOUT, ssl=self._ssl())
        self._writer = writer
        try:
            await self._write(connect_packet(s, self.client_id))
            try:
                kind, _flags, body = await asyncio.wait_for(read_packet(reader), CONNECT_TIMEOUT)
            except asyncio.TimeoutError:
                raise MqttError("The broker did not answer in time.") from None
            if kind != CONNACK or len(body) != 2:
                raise MqttError("The broker did not answer like an MQTT broker.")
            if body[1]:
                raise MqttError(REFUSED.get(body[1], "The broker refused the connection."))
        except (asyncio.IncompleteReadError, ConnectionError):
            raise MqttError("The broker closed the connection.") from None
        self.connected, self.since, self.attempts, self.last_error = True, time.time(), 0, ""
        if self._up is not None:
            self._up.set()
        self._changed()
        keepalive = s["keepalive"]
        ping = asyncio.create_task(self._pinger(keepalive))
        resub = asyncio.create_task(self._resubscribe())
        try:
            while True:
                try:
                    kind, flags, body = await asyncio.wait_for(read_packet(reader), keepalive * 1.5)
                except asyncio.TimeoutError:
                    raise MqttError("The broker stopped answering.") from None
                except (asyncio.IncompleteReadError, ConnectionError):
                    raise MqttError("The broker closed the connection.") from None
                await self._handle(kind, flags, body)
        finally:
            ping.cancel()
            resub.cancel()

    async def _pinger(self, keepalive: int) -> None:
        while True:
            await asyncio.sleep(keepalive / 2)
            await self._write(packet(PINGREQ, 0))

    async def _resubscribe(self) -> None:
        try:
            for filt, (qos, _) in list(self._subs.items()):
                await self._subscribe_wire(filt, qos)
        except MqttError:
            pass                                   # the connection dropped; the next session does it again

    async def _run(self) -> None:
        low, high = self._backoff
        wait = low
        try:
            while True:
                self.attempts += 1
                try:
                    await self._session()
                except netsock.RouteError as exc:
                    self._fail(exc.message)
                except MqttError as exc:
                    self._fail(exc.message)
                except (OSError, asyncio.TimeoutError):
                    self._fail("Could not connect to the broker.")
                except KeyError:
                    log.error("mqtt client %s names a traffic class Relay does not have", self.name)
                    self._fail("This connection uses a kind of traffic Relay does not know.")
                was = self.connected
                await self._drop()
                if was:
                    wait = low
                await asyncio.sleep(wait)
                wait = min(high, wait * 2)
        finally:
            await self._drop()

    # -- what a module calls

    async def publish(self, topic: str, payload: bytes | str = b"", *, qos: int = 0, retain: bool = False) -> None:
        """Publish a message. QoS 1 waits for the broker to acknowledge it."""
        if not valid_topic(topic):
            raise MqttError("That is not a topic Relay can publish to.")
        if qos not in (0, 1):
            raise MqttError("Relay publishes with QoS 0 or 1.")
        data = payload.encode("utf-8") if isinstance(payload, str) else bytes(payload)
        if len(data) > MAX_PAYLOAD:
            raise MqttError("That message is too large.")
        if not self.connected:
            raise MqttError("Not connected.")
        if qos:
            await self._ask(lambda pid: publish_packet(topic, data, 1, retain, pid))
        else:
            await self._write(publish_packet(topic, data, 0, retain, 0))
        self.sent += 1

    async def _subscribe_wire(self, filt: str, qos: int) -> None:
        reply = await self._ask(lambda pid: packet(SUBSCRIBE, 2, struct.pack(">H", pid) + _string(filt) + bytes([qos])))
        if not reply or reply[0] == 0x80:
            raise MqttError("The broker refused that subscription.")

    async def subscribe(self, topic_filter: str, handler: Handler, *, qos: int = 0) -> None:
        """Call `handler(topic, payload, retain)` for every message on `topic_filter` (`+` and `#` wildcards allowed),
        now and after every reconnect. The handler may be async. Subscribing again to a filter replaces its handler."""
        if not valid_filter(topic_filter):
            raise MqttError("That is not a topic filter.")
        if qos not in (0, 1):
            raise MqttError("Relay subscribes with QoS 0 or 1.")
        self._subs[topic_filter] = (qos, handler)
        if self.connected:
            try:
                await self._subscribe_wire(topic_filter, qos)
            except MqttError:
                if not self.connected:
                    return                         # it will subscribe when the connection is back
                self._subs.pop(topic_filter, None)
                raise

    async def unsubscribe(self, topic_filter: str) -> None:
        if self._subs.pop(topic_filter, None) is None or not self.connected:
            return
        await self._ask(lambda pid: packet(UNSUBSCRIBE, 2, struct.pack(">H", pid) + _string(topic_filter)))
