"""Vista backend (#382): the OSC address built from a cue id, the packets (an OSC string "true" then "false"), the range
checks that keep a bad number from reaching the console, and the test message, against a fake Vista (a UDP socket on
localhost). No real console. Run: python tests/test_lighting_vista.py
"""
import asyncio
import os
import socket
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting import service as svc  # noqa: E402
from app.modules.lighting import vista  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402


def decode(data: bytes) -> tuple[str, list[str]]:
    """(address, string arguments) of an OSC message made only of strings."""
    def take(b: bytes) -> tuple[str, bytes]:
        end = b.index(b"\0")
        return b[:end].decode(), b[(end // 4 + 1) * 4:]
    addr, rest = take(data)
    tags, rest = take(rest)
    out = []
    for _ in tags[1:]:
        s, rest = take(rest)
        out.append(s)
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
    loop = asyncio.get_running_loop()

    async def got(n):
        out = []
        for _ in range(n):
            data, _ = await asyncio.wait_for(loop.sock_recvfrom(rx, 2048) if hasattr(loop, "sock_recvfrom")
                                             else _recv(rx), 3)
            out.append(decode(data))
        return out

    async def _recv(sock):
        while True:
            try:
                return sock.recvfrom(2048)
            except BlockingIOError:
                await asyncio.sleep(0.01)

    b = vista.Vista({"host": "127.0.0.1", "port": port, "console": "EX"})
    A = "/V3/console/EX/1/playbackButton/panel/0/bank/1/row/0/col/0"

    with section("registered, and it has nothing to list"):
        eq("known by id", be.BACKENDS.get("vista"), vista.Vista)
        check("no list, no state", not b.can_list and not b.can_report_state)

    with section("addresses"):
        eq("a full id", vista.address_for("EX.1.0.1.0.0"), A)
        eq("an id without the type uses the setting", vista.address_for("1.0.1.0.0", "EX"), A)
        eq("another console type", vista.address_for("SL.2.3.4.5.6"),
           "/V3/console/SL/2/playbackButton/panel/3/bank/4/row/5/col/6")
        for bad, why in (("EX.1.0.1.0", "wrong number of parts"), ("EX.1.11.1.0.0", "panel over 10"),
                         ("EX.1.0.101.0.0", "bank over 100"), ("EX.1.0.1.12.0", "row over 11"),
                         ("EX.1.0.1.0.60", "column over 59"), ("EX.0.0.1.0.0", "console 0"),
                         ("EX.1.0.1.0.-1", "negative"), ("E X.1.0.1.0.0", "bad type"), ("EX.1.0.1.0.x", "not a number"),
                         ("EX/../1.0.1.0.0", "slashes")):
            try:
                vista.address_for(bad)
                check(f"refused: {why}", False)
            except be.BackendError:
                check(f"refused: {why}", True)

    with section("the packet"):
        eq("an OSC string message, padded to 4 bytes", vista.osc_message("/a", "true"),
           b"/a\0\0,s\0\0true\0\0\0\0")
        eq("no arguments", vista.osc_message("/a"), b"/a\0\0,\0\0\0")

    with section("press and release"):
        await b.press("EX.1.0.1.0.0")
        eq("a tap: true then false", await got(2), [(A, ["true"]), (A, ["false"])])
        await b.release("1.0.1.0.0")
        eq("release is false", await got(1), [(A, ["false"])])
        check("a bad id sends nothing", "panel" in await raises(b.press("EX.1.99.1.0.0")))
        await asyncio.sleep(0.1)
        try:
            rx.recvfrom(2048)
            check("nothing arrived", False)
        except BlockingIOError:
            check("nothing arrived", True)

    with section("test"):
        r = await b.test()
        check("sent, and says it cannot confirm", r["ok"] and "cannot tell" in r["detail"], r)
        eq("a harmless unmatched address", await got(1), [("/relay/test", [])])
        r = await vista.Vista({"host": "", "port": 0}).test()
        check("no host", not r["ok"] and "address" in r["detail"])
        check("no host, a press", "address" in await raises(vista.Vista({"host": ""}).press("EX.1.0.1.0.0")))

    with section("settings"):
        eq("console type is cleaned", (svc.clean_program({"console": " sl "})["console"],
                                       svc.clean_program({"console": "bad type!"})["console"],
                                       svc.clean_program({})["console"]), ("sl", "EX", "EX"))
        eq("a good one is kept", svc.clean_program({"console": "SL2"})["console"], "SL2")
    rx.close()


asyncio.run(main())
done()
