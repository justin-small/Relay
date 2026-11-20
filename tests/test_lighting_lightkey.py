"""Lightkey backend (#383): the OSC address built from a cue id, the packets (an optional float fade time), the
refusal of OSC pattern characters, the fade setting, and the test message, against a fake Lightkey (a UDP socket on
localhost). No real Lightkey. Run: python tests/test_lighting_lightkey.py
"""
import asyncio
import os
import socket
import struct
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting import lightkey, osc  # noqa: E402
from app.modules.lighting import service as svc  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402


def decode(data: bytes):
    """(address, [args]) of an OSC message with string and float arguments."""
    def take(b):
        end = b.index(b"\0")
        return b[:end].decode(), b[(end // 4 + 1) * 4:]
    addr, rest = take(data)
    tags, rest = take(rest)
    out = []
    for t in tags[1:]:
        if t == "s":
            v, rest = take(rest)
        else:
            v, rest = round(struct.unpack(">f", rest[:4])[0], 4), rest[4:]
        out.append(v)
    return addr, out


async def raises(coro) -> str:
    try:
        await coro
    except be.BackendError as e:
        return str(e)
    return ""


async def main():
    rx = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    rx.bind(("127.0.0.1", 0))
    rx.setblocking(False)
    port = rx.getsockname()[1]

    async def got():
        for _ in range(300):
            try:
                return decode(rx.recvfrom(2048)[0])
            except BlockingIOError:
                await asyncio.sleep(0.01)
        raise AssertionError("nothing arrived")

    async def nothing():
        await asyncio.sleep(0.1)
        try:
            rx.recvfrom(2048)
            return False
        except BlockingIOError:
            return True

    b = lightkey.Lightkey({"host": "127.0.0.1", "port": port, "fade": ""})

    with section("registered"):
        eq("known by id", be.BACKENDS.get("lightkey"), lightkey.Lightkey)
        check("no list, no state", not b.can_list and not b.can_report_state)

    with section("packets"):
        eq("a float and a string", osc.message("/a", 1.5, "x"), b"/a\0\0,fs\0\x3f\xc0\0\0x\0\0\0")
        eq("no arguments", osc.message("/a"), b"/a\0\0,\0\0\0")

    with section("addresses"):
        eq("page and cue", lightkey.address_for("My_Control_Panel:Walk-in"),
           "/live/My_Control_Panel/cue/Walk-in/activate")
        eq("the selected page", lightkey.address_for("selected:Verse", "deactivate"),
           "/live/selected/cue/Verse/deactivate")
        for bad, why in (("Walk-in", "no page"), ("P:", "no cue"), (":C", "no page name"), ("P:C:D", "two colons"),
                         ("*:C", "wildcard page"), ("P:*", "wildcard cue"), ("P:C?", "pattern char"),
                         ("P:{a,b}", "braces"), ("P/Q:C", "slash"), ("P:Walk in", "a space"), ("P:" + "x" * 65, "long")):
            try:
                lightkey.address_for(bad)
                check(f"refused: {why}", False)
            except be.BackendError:
                check(f"refused: {why}", True)

    with section("press and release"):
        await b.press("Main:Walk-in")
        eq("activate, no argument when no fade is set", await got(), ("/live/Main/cue/Walk-in/activate", []))
        await b.release("Main:Walk-in")
        eq("release deactivates", await got(), ("/live/Main/cue/Walk-in/deactivate", []))
        timed = lightkey.Lightkey({"host": "127.0.0.1", "port": port, "fade": 2.5})
        await timed.press("Main:Walk-in")
        eq("a fade time is sent as a float", await got(), ("/live/Main/cue/Walk-in/activate", [2.5]))
        await timed.release("Main:Walk-in")
        eq("…on release too", await got(), ("/live/Main/cue/Walk-in/deactivate", [2.5]))
        zero = lightkey.Lightkey({"host": "127.0.0.1", "port": port, "fade": 0})
        await zero.press("Main:Walk-in")
        eq("a zero fade is sent, not dropped", await got(), ("/live/Main/cue/Walk-in/activate", [0.0]))
        check("a bad id says what is wanted", "Page:Cue" in await raises(b.press("nope")))
        check("and sends nothing", await nothing())

    with section("test"):
        r = await b.test()
        check("sent, and says it cannot confirm", r["ok"] and "cannot tell" in r["detail"], r)
        eq("a harmless unmatched address", await got(), ("/relay/test", []))
        r = await lightkey.Lightkey({"host": ""}).test()
        check("no host", not r["ok"] and "address" in r["detail"])
        check("no host, a press", "address" in await raises(lightkey.Lightkey({"host": ""}).press("A:B")))

    with section("settings"):
        c = svc.clean_settings
        eq("fade is kept, rounded, or blank", (c({"fade": 1.5})["fade"], c({"fade": 0})["fade"], c({})["fade"],
                                               c({"fade": -1})["fade"], c({"fade": 99999})["fade"],
                                               c({"fade": True})["fade"], c({"fade": "2"})["fade"]),
           (1.5, 0.0, "", "", "", "", ""))
    rx.close()


asyncio.run(main())
done()
