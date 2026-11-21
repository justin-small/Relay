"""AES67 (#266): SDP and SAP parsing, RTP decoding, the stream table, loss and drift counting, the audio source feeding
capture, and the module switching on and off. Streams are built the way a Dante device in AES67 mode sends them. No
hardware: the one test through a real multicast socket is skipped where the machine cannot do multicast.
Run: .venv/bin/python tests/test_aes67.py
"""
import asyncio
import os
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import await_for, check, done, eq, section, tmpdir, wait_for  # noqa: E402

os.environ["RELAY_STATE_DIR"] = tmpdir()
os.environ["RELAY_CONFIG"] = os.path.join(tmpdir(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DEMO", None)

import numpy as np  # noqa: E402

from app import audio, config  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.aes67 import MODULE, rtp, sdp  # noqa: E402
from app.modules.aes67 import sender  # noqa: E402
from app.modules.aes67.output import AES67Output, Invalid, Output, clean as output_clean, validate  # noqa: E402
from app.modules.aes67.receiver import Receiver  # noqa: E402
from app.modules.aes67.service import MAX_STREAMS, STALE_S, Service, clean_settings  # noqa: E402
from app.modules.aes67.source import AES67Source, pick  # noqa: E402

def _raises(fn) -> bool:
    try:
        fn()
    except audio.SourceError:
        return True
    return False


DANTE_SDP = """v=0
o=- 1234 5678 IN IP4 192.168.1.20
s=AVIO-Out-ab12cd : 2
i=2 channels: Left, Right
c=IN IP4 239.69.1.2/32
t=0 0
a=keywds:Dante
m=audio 5004 RTP/AVP 97
i=Left, Right
c=IN IP4 239.69.1.2/32
a=rtpmap:97 L24/48000/2
a=ptime:1
a=ts-refclk:ptp=IEEE1588-2008:00-1D-C1-FF-FE-12-34-56:0
a=source-filter: incl IN IP4 239.69.1.2 192.168.1.20
a=label:1 Left
a=label:2 Right
"""
L16_MONO = DANTE_SDP.replace("L24/48000/2", "L16/44100/1").replace("AVIO-Out-ab12cd : 2", "Livewire desk").replace(
    "239.69.1.2", "239.192.5.6").replace("a=keywds:Dante\n", "").replace("1234 5678", "99 1")


def sap(sdp_text, withdraw=False, msg_id=7, origin="192.168.1.20", typed=True):
    flags = 0x20 | (0x04 if withdraw else 0)
    head = struct.pack("!BBH", flags, 0, msg_id) + socket.inet_aton(origin)
    return head + (b"application/sdp\0" if typed else b"") + sdp_text.encode()


def packet(seq, samples, ssrc=0x1234, pt=97, ts=0, channels=2):
    """An L24 RTP packet carrying `samples` (frames, channels) of integers in the 24-bit range."""
    raw = b"".join((int(v) & 0xFFFFFF).to_bytes(3, "big") for v in np.asarray(samples).reshape(-1))
    return struct.pack("!BBHII", 0x80, pt, seq & 0xFFFF, ts, ssrc) + raw


def frames(n, level=1000, channels=2):
    return np.full((n, channels), level)


with section("an SDP from a Dante device in AES67 mode"):
    s = sdp.parse(DANTE_SDP)
    check("it parses", s is not None)
    eq("the id is the group and port", s.id, "239.69.1.2:5004")
    eq("two channels of L24 at 48 kHz", (s.channels, s.encoding, s.rate, s.payload_type), (2, "L24", 48000, 97))
    eq("the name is the session name", s.name, "AVIO-Out-ab12cd : 2")
    check("it is marked as Dante's", s.dante)
    eq("the sender comes from the source filter", s.source, "192.168.1.20")
    eq("channel labels are kept", s.labels, ("Left", "Right"))
    check("it can be received", s.usable, s.problem)
    check("the clock line is shown as stated", s.clock.startswith("ptp=IEEE1588-2008"))
    m = sdp.parse(L16_MONO)
    eq("a Livewire-style mono L16 stream", (m.channels, m.encoding, m.rate, m.dante, m.usable), (1, "L16", 44100, False, True))

with section("an SDP Relay cannot receive is still listed, with why"):
    unicast = sdp.parse(DANTE_SDP.replace("239.69.1.2", "192.168.9.9"))
    check("unicast is refused", unicast is not None and "multicast" in unicast.problem, unicast.problem)
    am824 = sdp.parse(DANTE_SDP.replace("L24/48000/2", "AM824/48000/2"))
    check("another encoding is refused by name", "AM824" in am824.problem, am824.problem)
    odd = sdp.parse(DANTE_SDP.replace("48000", "32000"))
    check("an unsupported rate is refused", "32000" in odd.problem, odd.problem)
    many = sdp.parse(DANTE_SDP.replace("L24/48000/2", "L24/48000/200"))
    check("too many channels is refused", "channels" in many.problem, many.problem)

with section("text that is not an audio SDP is ignored"):
    for label, text in (("not SDP", "hello"), ("no media", "v=0\ns=x\n"), ("video only", "v=0\nm=video 5004 RTP/AVP 96\n"),
                        ("an unmapped payload", DANTE_SDP.replace("a=rtpmap:97 L24/48000/2\n", "")),
                        ("a bad port", DANTE_SDP.replace("5004", "99999")), ("too long", "v=0\n" + "a=x\n" * 3000),
                        ("not text", None)):
        eq(label, sdp.parse(text), None)
    eq("IPv6 is not read", sdp.parse(DANTE_SDP.replace("IN IP4", "IN IP6")), None)

with section("SAP packets"):
    a = sdp.parse_sap(sap(DANTE_SDP))
    eq("an announcement carries the SDP, the sender and the id", (a.withdraw, a.msg_id, a.origin, a.sdp == DANTE_SDP),
       (False, 7, "192.168.1.20", True))
    eq("a deletion is flagged", sdp.parse_sap(sap(DANTE_SDP, withdraw=True)).withdraw, True)
    eq("a packet with no payload type string still reads", sdp.parse_sap(sap(DANTE_SDP, typed=False)).sdp, DANTE_SDP)
    for label, raw in (("too short", b"\x20\x00"), ("another version", b"\x40" + sap(DANTE_SDP)[1:]),
                       ("IPv6", b"\x30" + sap(DANTE_SDP)[1:]), ("encrypted", b"\x22" + sap(DANTE_SDP)[1:]),
                       ("compressed", b"\x21" + sap(DANTE_SDP)[1:]),
                       ("another payload type", sap(DANTE_SDP).replace(b"application/sdp", b"application/xxx"))):
        eq(label + " is ignored", sdp.parse_sap(raw), None)
    skip_auth = b"\x20\x01" + sap(DANTE_SDP)[2:8] + b"AUTH" + sap(DANTE_SDP)[8:]
    eq("authentication data is skipped", sdp.parse_sap(skip_auth).sdp, DANTE_SDP)

with section("RTP and L24 / L16 decoding"):
    p = rtp.parse(packet(65535, frames(4), ssrc=9, ts=480))
    eq("header fields", (p.payload_type, p.seq, p.timestamp, p.ssrc, len(p.payload)), (97, 65535, 480, 9, 24))
    eq("not version 2 is ignored", rtp.parse(b"\x40" + packet(1, frames(1))[1:]), None)
    eq("an empty payload is ignored", rtp.parse(packet(1, frames(0))), None)
    ext = struct.pack("!BBHII", 0x90, 97, 1, 0, 1) + struct.pack("!HH", 0, 1) + b"\0\0\0\0" + b"\0\0\x01" * 2
    eq("a header extension is stepped over", len(rtp.parse(ext).payload), 6)
    pad = struct.pack("!BBHII", 0xA0, 97, 1, 0, 1) + b"\0\0\x01" * 2 + b"\0\0\x03"
    eq("padding is removed", len(rtp.parse(pad).payload), 6)
    edge = rtp.decode(b"\x7f\xff\xff\x80\x00\x00\x00\x00\x00\xc0\x00\x00", "L24", 1)
    eq("L24 reaches full scale at both ends, zero in the middle", [round(float(v), 4) for v in edge[:, 0]],
       [1.0, -1.0, 0.0, -0.5])
    eq("L16 is big endian", [round(float(v), 4) for v in rtp.decode(b"\x40\x00\xc0\x00", "L16", 1)[:, 0]], [0.5, -0.5])
    eq("the block is frames by channels", rtp.decode(b"\0" * 12, "L24", 2).shape, (2, 2))
    eq("a payload that is not whole frames is ignored", rtp.decode(b"\0" * 7, "L24", 2), None)
    eq("sequence gaps wrap at 16 bits", [rtp.seq_gap(65535, 0), rtp.seq_gap(10, 13), rtp.seq_gap(10, 9), rtp.seq_gap(10, 10)],
       [1, 3, -1, 0])

with section("channel pick"):
    blk = np.array([[0.2, 0.4], [0.6, 0.8]], dtype=np.float32)
    eq("a channel", pick(blk, 1).tolist(), [np.float32(0.4), np.float32(0.8)])
    eq("mix averages", [round(float(v), 3) for v in pick(blk, "mix")], [0.3, 0.7])
    eq("a channel past the end is the last", pick(blk, 9).tolist(), pick(blk, 1).tolist())
    eq("junk is channel 0", pick(blk, "x").tolist(), pick(blk, 0).tolist())
    eq("mix of one channel is that channel", pick(blk[:, :1], "mix").tolist(), pick(blk, 0).tolist())

with section("the receiver counts loss, late packets and strangers"):
    got = []
    rx = Receiver(sdp.parse(DANTE_SDP), got.append)
    rx.handle(packet(100, frames(48, 8388607)), "192.168.1.20")
    rx.handle(packet(101, frames(48)), "192.168.1.20")
    eq("two in-order packets arrive whole", (rx.packets, rx.lost, len(got), got[0].shape), (2, 0, 2, (48, 2)))
    check("the audio is decoded", abs(float(got[0][0, 0]) - 1.0) < 1e-6)
    rx.handle(packet(104, frames(48)), "192.168.1.20")          # 102 and 103 never came
    eq("a hole counts as lost", rx.lost, 2)
    eq("and is filled with silence of the same length", (len(got), got[2].shape, float(np.abs(got[2]).max())), (4, (96, 2), 0.0))
    rx.handle(packet(102, frames(48)), "192.168.1.20")
    eq("a late packet is dropped", (rx.late, len(got)), (1, 4))
    rx.handle(packet(105, frames(48), ssrc=0x9999), "192.168.1.20")
    eq("another sender's stream is ignored", (rx.ignored, rx.packets), (1, 3))
    rx.handle(packet(105, frames(48)), "10.0.0.9")
    eq("a sender the SDP does not name is ignored", rx.ignored, 2)
    rx.handle(packet(105, frames(48), pt=96), "192.168.1.20")
    rx.handle(b"junk", "192.168.1.20")
    eq("another payload type and junk are bad packets", rx.bad, 2)
    rx.handle(packet(105, frames(48)), "192.168.1.20")
    rx.handle(packet(65535 - 5, frames(48)), "192.168.1.20")
    eq("a restart far behind is late, not a hole", (rx.late, rx.lost), (2, 2))
    rx2, big = Receiver(sdp.parse(DANTE_SDP), lambda b: None), []
    rx2._deliver = big.append
    rx2.handle(packet(1, frames(48)), "192.168.1.20")
    rx2.handle(packet(1 + 5000, frames(48)), "192.168.1.20")
    eq("a very long hole is counted but not filled", (rx2.lost > 100, len(big)), (True, 2))
    eq("drift is not reported before it can mean anything", rx.drift_ppm(), None)
    rx._first, rx._last = 100.0, 160.0                            # 60 s of audio at exactly 48 kHz minus 100 ppm
    rx.frames, rx.lost, rx.packets = int(60 * 48000 * (1 - 100e-6)), 0, 1000
    eq("drift is the arrival rate against the stream's", rx.drift_ppm(), -100.0)
    st = rx.stats()
    eq("stats name the counters", sorted(st), ["bad", "drift_ppm", "ignored", "late", "lost", "packets", "receiving"])

with section("the stream table"):
    svc = Service()
    svc.observe(sap(DANTE_SDP), now=1000.0)
    svc.observe(sap(L16_MONO, msg_id=8), now=1000.0)
    svc.observe(b"junk", now=1000.0)
    snap = svc.snapshot(now=1010.0)
    eq("each stream appears once", [x["id"] for x in snap["streams"]], ["239.69.1.2:5004", "239.192.5.6:5004"])
    eq("a Dante stream sorts first and shows its age", (snap["streams"][0]["dante"], snap["streams"][0]["age"]), (True, 10))
    svc.observe(sap(DANTE_SDP), now=1020.0)
    eq("a repeat announcement is the same stream", len(svc.snapshot(now=1020.0)["streams"]), 2)
    svc.observe(sap(DANTE_SDP, withdraw=True), now=1021.0)
    eq("a deletion removes it", [x["id"] for x in svc.snapshot()["streams"]], ["239.192.5.6:5004"])
    svc.expire(now=1000.0 + STALE_S + 1)
    eq("a stream that went quiet is dropped after five minutes", svc.snapshot()["streams"], [])
    full = Service()
    for i in range(MAX_STREAMS + 5):
        full.observe(sap(DANTE_SDP.replace("239.69.1.2", f"239.1.{i // 250}.{i % 250 + 1}")), now=1.0)
    eq("the table is bounded", len(full.snapshot(now=1.0)["streams"]), MAX_STREAMS)

with section("a chosen stream is remembered with its SDP"):
    eq("settings keep only an SDP that matches its id",
       clean_settings({"remembered": {"239.69.1.2:5004": DANTE_SDP, "1.2.3.4:5": DANTE_SDP, "x": 5}})["remembered"],
       {"239.69.1.2:5004": DANTE_SDP})
    eq("junk is no settings", clean_settings("x"), {"remembered": {}, "output": output_clean({})})
    cold = Service()
    cold.configure({"remembered": {"239.69.1.2:5004": DANTE_SDP}})
    check("the saved input is found before any announcement", cold.find("239.69.1.2:5004") is not None)
    row = cold.snapshot()["streams"][0]
    eq("and listed as not announced now", row["live"], False)
    eq("an unknown id is not found", cold.find("239.0.0.1:1"), None)
    cold.observe(sap(DANTE_SDP.replace("AVIO-Out-ab12cd : 2", "Renamed")), now=1.0)
    eq("a live announcement wins over the remembered one", cold.find("239.69.1.2:5004").name, "Renamed")

with section("the source reaches capture like a sound device"):
    svc = Service()
    svc.observe(sap(DANTE_SDP))
    src = AES67Source(svc.find)
    eq("it names itself", (src.id, src.label), ("aes67", "AES67 network stream"))
    check("a stream that is not announced is noticed", src.notice("239.0.0.1:9") is not None)
    eq("a usable stream has no notice", src.notice("239.69.1.2:5004"), None)
    try:
        src.open("239.0.0.1:9", 0, lambda b: None)
        refused = ""
    except audio.SourceError as exc:
        refused = str(exc)
    check("opening an unknown stream is refused with a sentence", "not announced" in refused, refused)
    svc.observe(sap(DANTE_SDP.replace("239.69.1.2", "10.1.1.1"), msg_id=2))
    try:
        src.open("10.1.1.1:5004", 0, lambda b: None)
        refused = ""
    except audio.SourceError as exc:
        refused = str(exc)
    check("so is a stream that cannot be received", "multicast" in refused, refused)
    check("the lane bus gets a multichannel input of its own", src.lane_input().__class__.__name__ == "AES67LaneInput")

with section("through a real multicast socket"):
    svc, heard = Service(), []
    src = AES67Source(svc.find)
    svc.observe(sap(DANTE_SDP.replace("a=source-filter: incl IN IP4 239.69.1.2 192.168.1.20\n", "")))
    try:
        info = src.open("239.69.1.2:5004", "mix", heard.append)
    except audio.SourceError as exc:
        print(f"  skipped: this machine cannot listen for multicast ({exc})")
        info = None
    if info is not None:
        eq("the source reports the stream's rate and mix", (info.rate, info.channel, info.device["channels"]), (48000, "mix", 2))
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        sent = 0
        try:
            def send_some():
                global sent
                tx.sendto(packet(sent, frames(48, 4000)), ("239.69.1.2", 5004))
                sent += 1
                return len(heard) >= 3
            ok = wait_for(send_some, 4.0, 0.02)
        except OSError as exc:
            print(f"  skipped: nothing can be sent to the group here ({exc})")
            ok = None
        if ok is not None:
            check("mono audio arrives from a socket", ok, f"{len(heard)} blocks")
            if ok:
                check("it is the mix of the two channels", abs(float(heard[0][0]) - 4000 / 8388608) < 1e-6)
                check("and the receiver says it is receiving", src.alive() and src.stats()["packets"] >= 3)
        tx.close()
        src.close()
        check("close stops it", not src.alive())
        src.close()

with section("the module"):
    eq("it uses the Dante module when that is on", MODULE.manifest.uses, ("dante",))
    eq("it asks for a port of its own for its media", [(t.id, t.media) for t in MODULE.manifest.traffic], [("aes67", True)])
    eq("it starts off", MODULE.configured({}), False)


async def scenarios():
    config.save({"modules": {"aes67": {"enabled": True}}})
    await registry.start_enabled()
    check("switching it on registers the source", audio.get_source("aes67") is not None)
    from app.alerts import alerts as core_alerts  # noqa: E402
    from app.modules import using as provided_by  # noqa: E402
    from app.modules.aes67.output import output as live_output  # noqa: E402
    check("and the output", audio.get_output("aes67_out") is not None)
    check("and listens for alerts", live_output.on_alert in core_alerts.listeners)
    check("and offers speech to other modules", provided_by("aes67.speak") is not None)
    from app.modules.aes67.service import service as live

    check("and starts listening for announcements (or says why not)", live.running or live.snapshot()["error"])
    open_sdp = DANTE_SDP.replace("a=source-filter: incl IN IP4 239.69.1.2 192.168.1.20\n", "")   # this machine sends
    live.observe(sap(open_sdp))
    h = MODULE.health()
    eq("health counts the announced streams", (h.state, "1 AES67" in h.detail), ("ok", True))
    t = await MODULE.test()
    eq("the test passes with a stream announced", t["ok"], True)
    # -- the stream feeds capture through core's pump, with a stream the way a device announces it ---------------
    cap = audio.AudioCapture(asyncio.get_running_loop())
    q = cap.add_consumer()
    cap.gate_silence = False
    try:
        dev = cap.start("239.69.1.2:5004", "mix", "aes67")
    except RuntimeError:
        print(f"  skipped: capture could not listen for multicast here ({cap.error})")
        dev = None
    if dev is not None:
        eq("capture reports the stream as its device", (dev["label"], cap.status()["native_rate"]),
           ("AES67: AVIO-Out-ab12cd : 2", 48000))
        tx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        tx.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_LOOP, 1)
        n = [0]

        def feed():
            tx.sendto(packet(n[0], frames(48, 4000)), ("239.69.1.2", 5004))
            n[0] += 1
            return not q.empty()

        try:
            check("audio from the stream reaches the capture's consumers", await await_for(feed, 4.0, 0.02))
        except OSError as exc:
            print(f"  skipped: nothing can be sent to the group here ({exc})")
        finally:
            tx.close()
            cap.stop()
    live.expire(now=1e12)
    eq("with none, health says it is looking", MODULE.health().state, "warn")
    await registry.set_enabled("aes67", False)
    check("switching it off removes the source", audio.get_source("aes67") is None)
    check("and the output, the listener and the offer", audio.get_output("aes67_out") is None
          and live_output.on_alert not in core_alerts.listeners and provided_by("aes67.speak") is None)


with section("sending: encoding and packets"):
    block = np.array([[0.5, -0.5], [1.0, -1.0], [2.0, -2.0]], dtype=np.float32)
    back = rtp.decode(sender.encode(block, "L24"), "L24", 2)
    check("L24 decodes back to what was sent (clipped at full scale)",
          back is not None and np.allclose(back, np.clip(block, -1, 1), atol=2e-7))
    back16 = rtp.decode(sender.encode(block, "L16"), "L16", 2)
    check("L16 does too", back16 is not None and np.allclose(back16, np.clip(block, -1, 1), atol=5e-5))
    pkt = rtp.parse(sender.rtp_packet(65535, 7, 99, b"\0" * 6))
    eq("an RTP packet parses with its header", (pkt.payload_type, pkt.seq, pkt.timestamp, pkt.ssrc), (97, 65535, 7, 99))
    check("the sequence wraps", rtp.parse(sender.rtp_packet(65536, 0, 0, b"\0" * 6)).seq == 0)
    eq("a 1 ms packet of 8 channels fits one frame", len(sender.encode(np.zeros((48, 8), np.float32), "L24")), 1152)

with section("sending: the announcement is one Relay can read itself"):
    text = sender.build_sdp("Relay", "239.69.67.1", 5004, 2, "L24", "192.168.1.5", ["Alert", ""], 42)
    st = sdp.parse(text)
    eq("the stream the SDP names", (st.id, st.channels, st.rate, st.encoding, st.problem),
       ("239.69.67.1:5004", 2, 48000, "L24", ""))
    eq("with its channel names", st.labels, ("Alert", "Ch 2"))
    check("the packet time and a source filter are stated", "a=ptime:1" in text and "incl IN IP4 239.69.67.1 192.168.1.5" in text)
    ann = sdp.parse_sap(sender.build_sap(text, "192.168.1.5", 7))
    eq("SAP carries it", (ann.withdraw, ann.msg_id, ann.origin, ann.sdp == text), (False, 7, "192.168.1.5", True))
    check("and can withdraw it", sdp.parse_sap(sender.build_sap(text, "192.168.1.5", 7, True)).withdraw)

with section("sending: the mixer"):
    m = sender.Mixer(2, [-6.0, 0.0])
    m.overlay(0, np.ones(100, np.float32))
    first = m.pull(48)
    check("gain applies per channel", abs(float(first[0, 0]) - 10 ** (-6 / 20)) < 1e-6 and float(first[0, 1]) == 0.0)
    m.overlay(0, np.ones(10, np.float32))
    check("a clip mixes into what waits, starting at once", abs(float(m.pull(48)[0, 0]) - 2 * 10 ** (-6 / 20)) < 1e-6)
    m.pull(48)
    check("the queue empties and then sends silence", float(m.pull(48)[:, 0].sum()) == 0.0)
    eq("append takes what fits", m.append(1, np.ones(sender.RATE * 5, np.float32)), m.cap)
    check("and counts the rest as dropped", m.dropped == sender.RATE * 5 - m.cap)
    check("peaks are reported once", m.peaks_db()[0] is not None and m.peaks_db()[0] is None)
    check("a channel outside the stream is ignored", m.append(5, np.ones(4, np.float32)) == 0)
    check("resampling 24 kHz doubles the frames", len(sender.resample(np.zeros(240, np.float32), 24000)) == 480)
    check("the alert tone grows with the level",
          len(sender.alert_tone("info")) < len(sender.alert_tone("attention")) < len(sender.alert_tone("urgent")))

with section("sending: settings"):
    d = output_clean({})
    eq("defaults: off, two channels, L24", (d["enabled"], d["channels"], d["encoding"], d["alert_channel"]), (False, 2, "L24", 1))
    j = output_clean({"channels": 99, "group": "10.0.0.1", "port": 5, "encoding": "mp3", "gain_db": ["x", 5],
                      "languages": {"es": 9, "fr": 2, "": 1}})
    eq("junk falls back to defaults", (j["channels"], j["group"], j["port"], j["encoding"]), (2, "239.69.67.1", 5004, "L24"))
    eq("gains are numbers, one per channel", j["gain_db"], [0.0, 5.0])
    eq("a language keeps only a channel inside the stream", j["languages"], {"fr": 2})
    for label, bad in (("not an object", 5), ("unicast address", {"group": "10.0.0.1"}), ("port", {"port": 80}),
                       ("channels", {"channels": 9}), ("format", {"encoding": "L32"}), ("gain", {"gain_db": [99]}),
                       ("language channel", {"channels": 2, "languages": {"es": 3}}),
                       ("alert channel", {"channels": 2, "alert_channel": 3})):
        try:
            validate(bad)
            check("refused: " + label, False)
        except Invalid as exc:
            check("refused: " + label, bool(exc.message))
    eq("good settings are accepted", validate({"enabled": True, "channels": 3, "languages": {"es": 3}})["languages"], {"es": 3})

with section("sending: through a real multicast socket"):
    out = Output()
    off = out.play(b"\0\0" * 10, 48000, 1)
    check("nothing plays while it is off", off is False and out.play_alert("urgent") is False)
    out.configure({"enabled": True, "channels": 2, "group": "239.69.67.9", "port": 5006, "encoding": "L16",
                   "alert_channel": 2, "gain_db": [0, 0], "languages": {"es": 1}})
    if not out.running:
        print(f"  skipped: this machine cannot send multicast ({out.error})")
    else:
        got: list = []
        rx = Receiver(sdp.parse(out.sender.sdp), got.append)
        try:
            rx.start()
        except audio.SourceError as exc:
            print(f"  skipped: this machine cannot listen for multicast ({exc})")
            rx = None
        if rx is not None:
            check("silence flows before anything plays", wait_for(lambda: len(got) >= 5, 4.0), f"{len(got)} blocks")
            t0 = len(got)
            out.on_alert({"event": "alert.sent", "level": "urgent"})
            loud = wait_for(lambda: any(float(np.max(np.abs(b[:, 1]))) > 0.05 for b in got[t0:]), 0.5)
            check("a stage alert is on its channel within 500 ms", loud)
            check("and not on the other", all(float(np.max(np.abs(b[:, 0]))) < 1e-3 for b in got[t0:]))
            t1 = len(got)
            pcm = (np.sin(np.arange(2400) / 8.0) * 12000).astype("<i2").tobytes()
            check("a spoken language plays on its own channel", out.speak("es", pcm, 24000))
            check("a language with no channel is not played", out.speak("fr", pcm, 24000) is False)
            check("spoken audio arrives on channel 1",
                  wait_for(lambda: any(float(np.max(np.abs(b[:, 0]))) > 0.1 for b in got[t1:]), 2.0))
            check("the test tone is queued on its channel", out.test_tone(1, 0.1))
            check("and refused on a channel outside the stream", out.test_tone(3, 0.1) is False)
            snap = out.snapshot()
            check("the snapshot reports packets and the address", snap["packets"] > 0 and snap["address"] != "")
            rx.stop()
        out.stop()
        check("stopping ends the stream", not out.running)

with section("sending: the audio output and the alert listener"):
    ao = AES67Output()
    ao.play(b"\0\0" * 4, 48000, 1)              # off: logged and dropped, raises nothing
    check("a lane sink is refused while the stream is off", _raises(lambda: ao.lane_sink(1, 2)))


with section("on and off"):
    asyncio.run(scenarios())

done()
