"""MQTT client (#629): topics and settings, packets, and a client against a fake MQTT 3.1.1 broker (plain and TLS) and,
when it is installed, a real mosquitto. Run: python tests/test_mqttclient.py

The fake broker is written here from the MQTT 3.1.1 spec and matches topics with its own code, not the client's, so a
mistake in the client's matching is not repeated in the broker. The real mosquitto run (skipped without the binary)
is the check that the client speaks to a broker nobody on this project wrote.
"""
import asyncio
import datetime
import ipaddress
import os
import shutil
import ssl
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import mqttclient as mq, serialio  # noqa: E402
from _harness import await_for, check, done, eq, section  # noqa: E402

FAST = (0.02, 0.05)


def settings(port, **extra):
    return mq.clean_mqtt({"host": "127.0.0.1", "port": port, **extra})


async def raises(coro):
    try:
        await coro
    except mq.MqttError as exc:
        return str(exc)
    return None


# ------------------------------------------------------------------ a fake broker

def broker_match(filt, topic):
    """Topic matching written the other way round from the client's: by walking the topic, not the filter."""
    f, t = filt.split("/"), topic.split("/")
    if t[0].startswith("$") and f[0] in ("#", "+"):
        return False
    for i, part in enumerate(t):
        if i >= len(f):
            return False
        if f[i] == "#":
            return True
        if f[i] != "+" and f[i] != part:
            return False
    return len(f) == len(t) or (len(f) == len(t) + 1 and f[-1] == "#")


def varlen(n):
    out = bytearray()
    while True:
        d, n = n % 128, n // 128
        out.append(d | (0x80 if n else 0))
        if not n:
            return bytes(out)


def pkt(first, body=b""):
    return bytes([first]) + varlen(len(body)) + body


def text(body, at):
    n = struct.unpack(">H", body[at:at + 2])[0]
    return body[at + 2:at + 2 + n], at + 2 + n


class Broker:
    def __init__(self, users=None, ssl_context=None):
        self.users = users               # {name: password}, or None to let anyone in
        self.ssl_context = ssl_context
        self.sessions = []               # one dict per live connection
        self.connects = []               # what each CONNECT said
        self.retained = {}
        self.pings = 0
        self.mute = False                # stop answering anything
        self.server = None
        self.port = 0

    async def start(self, port=0):
        self.server = await asyncio.start_server(self._client, "127.0.0.1", port, ssl=self.ssl_context)
        self.port = self.server.sockets[0].getsockname()[1]

    async def stop(self):
        for s in list(self.sessions):
            s["w"].close()
        self.server.close()
        await self.server.wait_closed()

    def drop(self, client_id):
        for s in list(self.sessions):
            if s["id"] == client_id:
                s["w"].transport.abort()    # no DISCONNECT, no goodbye: like a cable pulled

    async def send_publish(self, session, topic, payload, qos, retain=False):
        q = min(qos, session["granted"].get(topic, 1))
        body = struct.pack(">H", len(topic.encode())) + topic.encode()
        if q:
            body += struct.pack(">H", 7)
        session["w"].write(pkt(0x30 | q << 1 | int(retain), body + payload))

    async def route(self, topic, payload, qos, retain):
        for s in list(self.sessions):
            for filt, granted in s["subs"].items():
                if broker_match(filt, topic):
                    q = min(qos, granted)
                    body = struct.pack(">H", len(topic.encode())) + topic.encode() + (struct.pack(">H", 7) if q else b"")
                    s["w"].write(pkt(0x30 | q << 1, body + payload))
                    break

    async def _client(self, reader, writer):
        session = {"w": writer, "subs": {}, "granted": {}, "will": None, "clean": False, "id": ""}
        try:
            first = (await reader.readexactly(1))[0]
            length, shift = 0, 0
            while True:
                d = (await reader.readexactly(1))[0]
                length |= (d & 0x7F) << shift
                shift += 7
                if not d & 0x80:
                    break
            body = await reader.readexactly(length)
            assert first >> 4 == 1
            name, at = text(body, 0)
            level, flags = body[at], body[at + 1]
            keepalive = struct.unpack(">H", body[at + 2:at + 4])[0]
            at += 4
            cid, at = text(body, at)
            rec = {"proto": name, "level": level, "flags": flags, "keepalive": keepalive, "id": cid.decode(),
                   "username": None, "password": None, "will": None}
            if flags & 0x04:
                wt, at = text(body, at)
                wm, at = text(body, at)
                rec["will"] = {"topic": wt.decode(), "payload": wm, "qos": flags >> 3 & 3, "retain": bool(flags & 0x20)}
            if flags & 0x80:
                u, at = text(body, at)
                rec["username"] = u.decode()
            if flags & 0x40:
                p, at = text(body, at)
                rec["password"] = p.decode()
            self.connects.append(rec)
            if self.users is not None and self.users.get(rec["username"]) != rec["password"]:
                writer.write(pkt(0x20, bytes([0, 4])))
                await writer.drain()
                return
            session["will"], session["id"] = rec["will"], rec["id"]
            self.sessions.append(session)
            writer.write(pkt(0x20, bytes([0, 0])))
            await writer.drain()
            while True:
                first = (await reader.readexactly(1))[0]
                length, shift = 0, 0
                while True:
                    d = (await reader.readexactly(1))[0]
                    length |= (d & 0x7F) << shift
                    shift += 7
                    if not d & 0x80:
                        break
                body = await reader.readexactly(length) if length else b""
                kind, fl = first >> 4, first & 15
                if self.mute:
                    continue
                if kind == 12:
                    self.pings += 1
                    writer.write(pkt(0xD0))
                elif kind == 14:
                    session["clean"] = True
                    return
                elif kind == 8:
                    pid = body[:2]
                    at, granted = 2, b""
                    while at < len(body):
                        filt, at = text(body, at)
                        qos = min(body[at], 1)
                        at += 1
                        session["subs"][filt.decode()] = qos
                        granted += bytes([qos])
                        for topic, (payload, rq) in list(self.retained.items()):
                            if broker_match(filt.decode(), topic):
                                await self.send_publish(session, topic, payload, rq, retain=True)
                    writer.write(pkt(0x90, pid + granted))
                elif kind == 10:
                    pid, at = body[:2], 2
                    while at < len(body):
                        filt, at = text(body, at)
                        session["subs"].pop(filt.decode(), None)
                    writer.write(pkt(0xB0, pid))
                elif kind == 3:
                    qos, retain = fl >> 1 & 3, bool(fl & 1)
                    topic, at = text(body, 0)
                    pid = b""
                    if qos:
                        pid, at = body[at:at + 2], at + 2
                    payload = body[at:]
                    if qos:
                        writer.write(pkt(0x40, pid))
                    if retain:
                        if payload:
                            self.retained[topic.decode()] = (payload, qos)
                        else:
                            self.retained.pop(topic.decode(), None)
                    await self.route(topic.decode(), payload, qos, retain)
                await writer.drain()
        except (asyncio.IncompleteReadError, ConnectionError):
            pass
        finally:
            if session in self.sessions:
                self.sessions.remove(session)
                will = session["will"]
                if will and not session["clean"]:
                    if will["retain"]:
                        self.retained[will["topic"]] = (will["payload"], will["qos"])
                    await self.route(will["topic"], will["payload"], will["qos"], will["retain"])
            writer.close()


class Inbox:
    def __init__(self):
        self.got = []

    def __call__(self, topic, payload, retain):
        self.got.append((topic, payload, retain))


async def connected(port, **extra):
    client = mq.MqttClient(settings(port, **extra), backoff=FAST)
    await client.start()
    assert await client.wait_connected(3), client.state()
    return client


with section("Topics"):
    table = [("a/b", "a/b", True), ("a/b", "a/c", False), ("a/+", "a/b", True), ("a/+", "a/b/c", False),
             ("a/+/c", "a/x/c", True), ("a/#", "a/b/c/d", True), ("a/#", "a", True), ("#", "a/b", True),
             ("+/b", "a/b", True), ("a/b/#", "a/b", True), ("a", "a/b", False), ("a/b", "a", False),
             ("#", "$SYS/x", False), ("+/x", "$SYS/x", False), ("$SYS/#", "$SYS/x", True), ("/a", "/a", True),
             ("a//b", "a//b", True), ("a/+", "a/", True)]
    for filt, topic, want in table:
        eq(f"{filt!r} on {topic!r}", mq.topic_matches(filt, topic), want)
        eq(f"   the broker's own matcher agrees on {filt!r} and {topic!r}", broker_match(filt, topic), want)
    for f, ok in (("a/b", True), ("a/+/c", True), ("#", True), ("a/#", True), ("a/#/c", False), ("a+", False),
                  ("a/b#", False), ("", False), ("a\x00", False), (5, False)):
        eq(f"filter {f!r} valid", mq.valid_filter(f), ok)
    for t, ok in (("a/b", True), ("a/+", False), ("#", False), ("", False), ("a\x00b", False), ("é/ü", True)):
        eq(f"topic {t!r} valid", mq.valid_topic(t), ok)

with section("Settings"):
    s = mq.clean_mqtt({"host": " broker.local ", "username": "u", "password": "p"})
    eq("defaults", (s["host"], s["port"], s["tls"], s["keepalive"], s["client_id"], "will" in s),
       ("broker.local", 1883, False, 60, "", False))
    eq("TLS defaults to 8883", mq.clean_mqtt({"host": "h", "tls": True})["port"], 8883)
    eq("a will", mq.clean_mqtt({"host": "h", "will": {"topic": "t/s", "payload": "gone", "qos": "1", "retain": 1}})["will"],
       {"topic": "t/s", "payload": "gone", "qos": 1, "retain": True})
    for bad in ({}, {"host": "a/b"}, {"host": "h", "port": 70000}, {"host": "h", "port": "x"}, {"host": "h", "keepalive": 1},
                {"host": "h", "will": {"topic": "a/+"}}, {"host": "h", "will": {"topic": "a", "qos": 2}},
                {"host": "h", "ca": "not a certificate"}):
        try:
            mq.clean_mqtt(bad)
            check(f"{bad} is refused", False)
        except serialio.SettingsError as exc:
            check(f"{bad} is refused with a sentence", str(exc))

with section("Packets"):
    eq("remaining length 0", mq._length(0), b"\x00")
    eq("remaining length 127 and 128", (mq._length(127), mq._length(128)), (b"\x7f", b"\x80\x01"))
    eq("remaining length 16383 and 16384", (mq._length(16383), mq._length(16384)), (b"\xff\x7f", b"\x80\x80\x01"))
    check("the spec's CONNECT: clean session, client id 'c', keep-alive 60",
          mq.connect_packet({"username": "", "password": "", "keepalive": 60}, "c") == bytes.fromhex("100d00044d5154540402003c000163"))
    eq("a QoS 0 publish", mq.publish_packet("a/b", b"hi", 0, False, 0), bytes.fromhex("30070003612f626869"))
    eq("a retained QoS 1 publish carries its id", mq.publish_packet("t", b"x", 1, True, 5),
       bytes.fromhex("3306000174000578"))


async def with_broker(**kw):
    broker = Broker(**kw)
    await broker.start()
    return broker


async def fake_broker_tests():
    broker = await with_broker()
    a, b = Inbox(), Inbox()
    ca = await connected(broker.port, client_id="alice", username="u", password="pw",
                         will={"topic": "status/alice", "payload": "gone", "qos": 1, "retain": False})
    cb = await connected(broker.port)

    with section("Fake broker: what the client says"):
        first = broker.connects[0]
        eq("MQTT 3.1.1, the id, the user and the will",
           (first["proto"], first["level"], first["id"], first["username"], first["will"]),
           (b"MQTT", 4, "alice", "u", {"topic": "status/alice", "payload": b"gone", "qos": 1, "retain": False}))
        check("the password reaches the broker", first["password"] == "pw")
        eq("flags: clean session, will, will QoS 1, user, password", first["flags"], 0x02 | 0x04 | 0x08 | 0x80 | 0x40)
        check("a client with no id gets one of its own", broker.connects[1]["id"].startswith("relay-"))
        check("state says connected", ca.state()["connected"] is True and ca.state()["last_error"] == "")

    with section("Fake broker: publish and subscribe"):
        await ca.subscribe("stage/+/state", a)
        await ca.subscribe("all/#", a, qos=1)
        await cb.publish("stage/lights/state", b"on")
        await cb.publish("stage/lights/other", b"nope")
        await cb.publish("all/x/y", "text", qos=1)
        check("matching messages arrive", await await_for(lambda: len(a.got) == 2), a.got)
        eq("with topic, payload and the retain flag", a.got, [("stage/lights/state", b"on", False),
                                                              ("all/x/y", b"text", False)])
        await cb.publish("stage/a/state", b"\x00\xff\x10 binary", qos=1)
        check("a binary payload arrives intact", await await_for(lambda: len(a.got) == 3) and
              a.got[2][1] == b"\x00\xff\x10 binary", a.got)
        big = os.urandom(200000)
        await cb.publish("all/big", big, qos=1)
        check("a 200 KB message arrives intact", await await_for(lambda: len(a.got) == 4) and a.got[3][1] == big)
        await ca.unsubscribe("all/#")
        await cb.publish("all/after", b"x", qos=1)
        await cb.publish("stage/z/state", b"fence")
        check("an unsubscribed filter hears nothing more", await await_for(lambda: len(a.got) == 5)
              and a.got[4][0] == "stage/z/state", a.got)
        check("the counters", cb.state()["sent"] == 7 and ca.state()["received"] == 5)

    with section("Fake broker: handlers"):
        async def slow(topic, payload, retain):
            b.got.append(("async", topic, payload))

        def broken(topic, payload, retain):
            raise RuntimeError("a module bug")

        await cb.subscribe("h/async", slow)
        await cb.subscribe("h/broken", broken)
        await cb.subscribe("h/after", b)
        await ca.publish("h/async", b"1")
        await ca.publish("h/broken", b"2")
        await ca.publish("h/after", b"3")
        check("an async handler is awaited", await await_for(lambda: ("async", "h/async", b"1") in b.got), b.got)
        check("a handler that raises does not stop the connection or the next message",
              await await_for(lambda: ("h/after", b"3", False) in b.got) and cb.connected, b.got)

    with section("Fake broker: retained messages"):
        await ca.publish("r/last", b"first", qos=1, retain=True)
        late = Inbox()
        await cb.subscribe("r/#", late)
        check("a late subscriber is sent the retained message, marked retained",
              await await_for(lambda: late.got == [("r/last", b"first", True)]), late.got)
        await ca.publish("r/last", b"", qos=1, retain=True)
        eq("an empty retained message clears it", "r/last" in broker.retained, False)

    with section("Fake broker: what it refuses"):
        eq("a topic with a wildcard", await raises(ca.publish("a/+", b"x")), "That is not a topic Relay can publish to.")
        eq("QoS 2", await raises(ca.publish("a/b", b"x", qos=2)), "Relay publishes with QoS 0 or 1.")
        eq("a bad filter", await raises(ca.subscribe("a/#/b", b)), "That is not a topic filter.")
        eq("QoS 2 subscription", await raises(ca.subscribe("a/b", b, qos=2)), "Relay subscribes with QoS 0 or 1.")
        eq("a message that is too large", await raises(ca.publish("a/b", b"x" * (mq.MAX_PAYLOAD + 1))),
           "That message is too large.")

    with section("Fake broker: a pulled cable: reconnect, resubscribe, will"):
        wills = Inbox()
        await cb.subscribe("status/#", wills)
        count = len(broker.connects)
        broker.drop("alice")
        check("alice connects again by herself", await await_for(lambda: len(broker.connects) > count and ca.connected))
        check("her will was published when she vanished", await await_for(lambda: wills.got == [
            ("status/alice", b"gone", False)]), wills.got)
        a.got.clear()
        await cb.publish("stage/back/state", b"again")
        check("alice's subscriptions came back with the connection", await await_for(lambda: len(a.got) == 1), a.got)
        check("and the state says so", ca.state()["subscriptions"] == ["stage/+/state"])

    with section("Fake broker: a clean close sends no will"):
        wills.got.clear()
        await ca.close()
        await cb.publish("fence/x", b"1")
        await cb.subscribe("fence/#", wills)
        await cb.publish("fence/y", b"2")
        check("only the fence arrives, no will", await await_for(lambda: wills.got == [("fence/y", b"2", False)]),
              wills.got)
        eq("a closed client is not connected", ca.connected, False)
        eq("and refuses to publish", await raises(ca.publish("a/b", b"x")), "Not connected.")

    with section("Fake broker: keep-alive"):
        errors = []
        fast = mq.MqttClient(settings(broker.port, keepalive=5), backoff=FAST,
                             on_state=lambda: errors.append((fast.connected, fast.last_error)))
        fast.settings["keepalive"] = 0.2          # a test cannot wait seconds: the client reads the setting each time
        before = broker.pings
        await fast.start()
        check("it connects", await fast.wait_connected(3))
        check("it pings while idle", await await_for(lambda: broker.pings >= before + 2), broker.pings)
        count = len(broker.connects)
        broker.mute = True
        check("a broker that goes silent is noticed", await await_for(
            lambda: (False, "The broker stopped answering.") in errors), errors)
        broker.mute = False
        check("and it connects again once the broker answers", await await_for(
            lambda: fast.connected and len(broker.connects) > count))
        await fast.close()

    await cb.close()
    await broker.stop()

    with section("Fake broker: sign-in"):
        locked = await with_broker(users={"relay": "secret"})
        bad = mq.MqttClient(settings(locked.port, username="relay", password="wrong"), backoff=FAST)
        await bad.start()
        check("a wrong password says so", await await_for(lambda: bad.state()["last_error"] ==
                                                           "The broker refused the user name or password."))
        eq("and is not connected", bad.connected, False)
        await bad.close()
        good = await connected(locked.port, username="relay", password="secret")
        check("the right one connects", good.state()["last_error"] == "")
        await good.close()
        await locked.stop()

    with section("Fake broker: not there, then there"):
        spare = await with_broker()
        port = spare.port
        await spare.stop()
        late_client = mq.MqttClient(settings(port), backoff=FAST)
        await late_client.start()
        check("it says it could not connect", await await_for(lambda: late_client.state()["last_error"]
                                                               .startswith("Could not connect")))
        eq("a publish says not connected", await raises(late_client.publish("a/b", b"x")), "Not connected.")
        again = Broker()
        await again.start(port)
        check("the broker appears: it connects by itself", await late_client.wait_connected(3))
        await late_client.close()
        await again.stop()


def make_certificate():
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    now = datetime.datetime.utcnow()
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    pem = cert.public_bytes(serialization.Encoding.PEM)
    keypem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                               serialization.NoEncryption())
    return pem, keypem


async def tls_tests():
    with section("TLS"):
        pem, keypem = make_certificate()
        cert_file, key_file = os.path.join(TMP, "broker.pem"), os.path.join(TMP, "broker.key")
        Path(cert_file).write_bytes(pem)
        Path(key_file).write_bytes(keypem)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert_file, key_file)
        broker = await with_broker(ssl_context=context)
        inbox = Inbox()
        client = await connected(broker.port, tls=True, ca=pem.decode())
        await client.subscribe("t/#", inbox)
        await client.publish("t/x", b"over tls", qos=1)
        check("a broker with its own certificate works once its certificate is pasted in",
              await await_for(lambda: inbox.got == [("t/x", b"over tls", False)]), inbox.got)
        await client.close()
        untrusted = mq.MqttClient(settings(broker.port, tls=True), backoff=FAST)
        await untrusted.start()
        check("without the certificate it is refused, with a sentence",
              await await_for(lambda: untrusted.state()["last_error"]
                              .startswith("The TLS connection to 127.0.0.1 port")))
        await untrusted.close()
        broken = mq.MqttClient(mq.clean_mqtt({"host": "127.0.0.1", "port": broker.port, "tls": True,
                                              "ca": "-----BEGIN CERTIFICATE-----\nnope\n-----END CERTIFICATE-----"}),
                               backoff=FAST)
        await broken.start()
        check("a certificate that cannot be read says so", await await_for(
            lambda: broken.state()["last_error"] == "The broker certificate you pasted could not be read."))
        await broken.close()
        await broker.stop()


async def real_mosquitto():
    with section("A real mosquitto"):
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
            one = mq.MqttClient(settings(port, client_id="relay-test-a"), backoff=FAST)
            two = mq.MqttClient(settings(port, client_id="relay-test-b",
                                         will={"topic": "relay/test/will", "payload": "gone"}), backoff=FAST)
            await one.start()
            await two.start()
            check("both connect to the real broker (it was starting up: they retry)",
                  await await_for(lambda: one.connected and two.connected, 5))
            inbox = Inbox()
            await one.subscribe("relay/test/#", inbox, qos=1)
            await two.publish("relay/test/a", b"hello", qos=1)
            await two.publish("relay/test/r", b"kept", qos=1, retain=True)
            check("it routes our messages", await await_for(lambda: len(inbox.got) == 2), inbox.got)
            eq("with topic and payload", sorted((t, p) for t, p, _ in inbox.got),
               [("relay/test/a", b"hello"), ("relay/test/r", b"kept")])
            late = Inbox()
            await two.subscribe("relay/test/r", late)
            check("and keeps the retained one for a late subscriber",
                  await await_for(lambda: late.got == [("relay/test/r", b"kept", True)]), late.got)
            await two.publish("relay/test/r", b"", retain=True)
            await one.close()
            await two.close()
        finally:
            proc.terminate()
            proc.wait(5)


async def main():
    await fake_broker_tests()
    await tls_tests()
    await real_mosquitto()


asyncio.run(main())
done()
