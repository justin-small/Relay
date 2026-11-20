"""ShowXpress / TheLightingController backend (#378) against a fake Live API server on localhost: login and a wrong
password, the list (XML parsed safely), toggling and flash buttons, release, faders, pushed changes on the held
connection, and failures. No real controller. Run: python tests/test_lighting_tlc.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting import thelightingcontroller as tlc  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

PASSWORD = "sesame"
state = {"Walk-in": False, "Worship Wash": True, "Strobe": False}
FLASH = {"Strobe"}
FADERS = {"3": ["Master", -50]}
received: list[str] = []
writers: list = []


def xml() -> str:
    btns = "".join(f'<button column="{i}" line="0" type="0" flash="{int(n in FLASH)}" color="#ff0000" '
                   f'pressed="{int(on)}">{n}</button>' for i, (n, on) in enumerate(state.items()))
    bad = '<button column="9" line="9" type="0" flash="0" pressed="0">Pipe|Name</button>'
    f = "".join(f'<fader index="{i}" value="{v[1]}">{v[0]}</fader>' for i, v in FADERS.items())
    return f'<buttons><page name="Main &amp; Co">{btns}{bad}</page>{f}</buttons>'


async def handler(reader, writer):
    logged = False
    try:
        while True:
            line = (await reader.readuntil(b"\r\n"))[:-2].decode()
            parts = line.split("|")
            if parts[0] == "HELLO":
                if len(parts) == 3 and parts[2] == PASSWORD:
                    logged = True
                    writers.append(writer)
                    writer.write(b"HELLO\r\n")
                else:
                    writer.write(b"ERROR|BAD PASSWORD\r\n")
            elif not logged:
                writer.write(b"ERROR|NOT LOGGED\r\n")
            elif parts[0] == "BUTTON_LIST":
                writer.write(("BUTTON_LIST|" + xml() + "\r\n").encode())
            else:
                received.append(line)
                if parts[0] in ("BUTTON_PRESS", "BUTTON_RELEASE"):
                    state[parts[1]] = parts[0] == "BUTTON_PRESS"
                elif parts[0] == "FADER_CHANGE":
                    FADERS[parts[1]][1] = int(parts[2])
            await writer.drain()
    except (asyncio.IncompleteReadError, ConnectionError):
        pass
    finally:
        writer.close()


async def raises(coro) -> str:
    try:
        await coro
    except be.BackendError as e:
        return str(e)
    return ""


async def until(pred, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.02)


async def main():
    server = await asyncio.start_server(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    b = tlc.TheLightingController({"host": "127.0.0.1", "port": port, "password": PASSWORD})
    ids = {n: tlc.button_id(n) for n in state}

    with section("registered"):
        eq("known by id", be.BACKENDS.get("thelightingcontroller"), tlc.TheLightingController)

    with section("the list"):
        cues = await b.list_cues()
        eq("buttons then faders; an unaddressable name is left out", [(c.name, c.kind) for c in cues],
           [("Walk-in", "button"), ("Worship Wash", "button"), ("Strobe", "button"), ("Master", "slider")])
        check("ids are short plain text", all(len(c.id) <= 64 and c.id.replace("s", "").isalnum() for c in cues))
        eq("a button's id comes from its name", cues[0].id, ids["Walk-in"])
        eq("state", await b.state(), {ids["Walk-in"]: False, ids["Worship Wash"]: True, ids["Strobe"]: False})
        eq("a fader's -100..0 is 0..100 percent", await b.levels(), {"s3": 50})

    with section("parser is safe"):
        btns, faders = tlc.parse_list('<!DOCTYPE x [<!ENTITY e "boom">]><buttons><button pressed="1">&e;&amp;A</button>'
                                      '<button>\n</button><fader index="x" value="1">bad</fader></buttons>')
        eq("an entity is not followed, an empty name or bad fader is dropped",
           ([x["name"] for x in btns], faders), (["&e;&A"], []))

    with section("press"):
        received.clear()
        await b.press(ids["Walk-in"])
        await b.press(ids["Worship Wash"])
        await b.press(ids["Strobe"])
        await asyncio.sleep(0.1)
        eq("an off button is pressed, an on one released, a flash one pressed and let go", received,
           ["BUTTON_PRESS|Walk-in", "BUTTON_RELEASE|Worship Wash", "BUTTON_PRESS|Strobe", "BUTTON_RELEASE|Strobe"])
        received.clear()
        await b.release(ids["Walk-in"])
        await asyncio.sleep(0.05)
        eq("release", received, ["BUTTON_RELEASE|Walk-in"])
        check("a fader is not pressed", "fader" in await raises(b.press("s3")))
        check("a button that is gone is refused", "no longer" in await raises(b.press("bdeadbeef00")))

    with section("faders"):
        received.clear()
        await b.set_level("s3", 100)
        await b.set_level("s3", 0)
        await asyncio.sleep(0.05)
        eq("percent becomes -100..0", received, ["FADER_CHANGE|3|0", "FADER_CHANGE|3|-100"])
        check("a button is not a fader", "fader" in await raises(b.set_level(ids["Walk-in"], 5)))

    with section("held connection follows pushes"):
        state.update({"Walk-in": False, "Worship Wash": True})
        await b.state()
        await until(lambda: b._live)
        check("live", b._live)
        for w in writers[:]:
            if not w.is_closing():
                w.write(b"BUTTON_PRESS|Walk-in\r\nFADER_CHANGE|3|-20\r\n")
        await until(lambda: b._on.get(ids["Walk-in"]) is True and b._pct.get("s3") == 80)
        check("a pushed press and fader move reach the copy", b._on[ids["Walk-in"]] is True and b._pct["s3"] == 80)
        state["Strobe"] = True
        for w in writers[:]:
            if not w.is_closing():
                w.write(b"INTERFACE_CHANGE\r\n")
        await until(lambda: b._on.get(ids["Strobe"]) is True)
        check("an interface change re-reads the list", b._on[ids["Strobe"]] is True)
        await b.close()
        await b.close()
        check("close stops it", b._task is None and not b._live)

    with section("test"):
        r = await b.test()
        check("ok, counts", r["ok"] and "3 buttons and 1 fader" in r["detail"], r)

    with section("failures"):
        bad = tlc.TheLightingController({"host": "127.0.0.1", "port": port, "password": "wrong"})
        r = await bad.test()
        check("a wrong password says so", not r["ok"] and "password" in r["detail"], r)
        check("so does a press", "password" in await raises(bad.press(ids["Walk-in"])))
        r = await tlc.TheLightingController({"host": "", "port": 0}).test()
        check("no host", not r["ok"] and "address" in r["detail"])
        server.close()
        await server.wait_closed()
        r = await b.test()
        check("not reachable is a sentence", not r["ok"] and "Could not reach" in r["detail"], r)
        check("a press fails with one", bool(await raises(b.press(ids["Walk-in"]))))


asyncio.run(main())
done()
