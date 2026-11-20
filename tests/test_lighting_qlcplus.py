"""QLC+ lighting backend (#381) against a fake QLC+ web API server on localhost: listing buttons only, state, a
press, the connection test, and failures. No real QLC+. Run: python tests/test_lighting_qlcplus.py
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

import websockets  # noqa: E402

from app.modules.lighting import backend as be  # noqa: E402
from app.modules.lighting.qlcplus import QLCPlus  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

WIDGETS = {"1": ["Walk-in", "Button", "0"], "2": ["Worship", "Button", "255"], "3": ["Title", "Label", "0"],
           "4": ["House", "Slider", "128"]}
FUNCTIONS = {"7": ["Chase", "Stopped"], "8": ["Wash", "Running"]}
received: list[str] = []
clients: list = []


async def handler(ws):
    clients.append(ws)
    async for msg in ws:
        parts = msg.split("|")
        if parts[0] != "QLC+API":
            received.append(msg)
            continue
        if parts[1] == "getFunctionsList":
            await ws.send("QLC+API|getFunctionsList|" + "|".join(f"{i}|{f[0]}" for i, f in FUNCTIONS.items()))
        elif parts[1] == "getFunctionStatus":
            await ws.send(f"QLC+API|getFunctionStatus|{FUNCTIONS[parts[2]][1]}")
        elif parts[1] == "setFunctionStatus":
            FUNCTIONS[parts[2]][1] = "Running" if parts[3] == "1" else "Stopped"
            received.append(msg)
            for c in [c for c in clients if c.open]:
                await c.send(f"FUNCTION|{parts[2]}|{FUNCTIONS[parts[2]][1]}")
            continue
        if parts[1] == "getWidgetsList":
            await ws.send("QLC+API|getWidgetsList|" + "|".join(f"{i}|{w[0]}" for i, w in WIDGETS.items()))
        elif parts[1] == "getWidgetType":
            await ws.send(f"QLC+API|getWidgetType|{parts[2]}|{WIDGETS[parts[2]][1]}")
        elif parts[1] == "getWidgetStatus":
            await ws.send(f"QLC+API|getWidgetStatus|{parts[2]}|{WIDGETS[parts[2]][2]}")


async def wait_for_async(pred, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.02)


async def raises(coro) -> bool:
    try:
        await coro
    except be.BackendError:
        return True
    return False


async def main():
    server = await websockets.serve(handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    b = QLCPlus({"host": "127.0.0.1", "port": port})

    with section("registered"):
        eq("known by id", be.BACKENDS.get("qlcplus"), QLCPlus)

    with section("buttons only"):
        eq("labels are left out, a slider is marked", [(c.id, c.name, c.kind) for c in await b.list_cues()],
           [("1", "Walk-in", "button"), ("2", "Worship", "button"), ("s4", "House", "slider")])

    with section("state"):
        eq("255 is on", await b.state(), {"1": False, "2": True})
        eq("a slider reads as a percent", await b.levels(), {"s4": 50})

    with section("press"):
        received.clear()
        await b.press("1")
        await asyncio.sleep(0.1)
        eq("one id|255 message", received, ["1|255"])
        check("a bad id is refused", await raises(b.press("1|0;x")))
        eq("still one message", len(received), 1)
        check("release is not supported", await raises(b.release("1")))

    with section("levels"):
        received.clear()
        await b.set_level("s4", 100)
        await b.set_level("s4", 0)
        await asyncio.sleep(0.1)
        eq("percent becomes 0 to 255", received, ["4|255", "4|0"])
        check("a button is not a slider", await raises(b.set_level("1", 50)))
        check("a slider is not pressed", await raises(b.press("s4")))

    with section("functions"):
        eq("off by default", [c.id for c in await b.list_cues() if c.kind == "function"], [])
        f = QLCPlus({"host": "127.0.0.1", "port": port, "functions": True})
        eq("listed when the setting is on", [(c.id, c.name) for c in await f.list_cues() if c.kind == "function"],
           [("f7", "Chase"), ("f8", "Wash")])
        eq("their state is read", {k: v for k, v in (await f.state()).items() if k.startswith("f")},
           {"f7": False, "f8": True})
        received.clear()
        await f.press("f7")
        await f.press("f8")
        await asyncio.sleep(0.1)
        eq("a function toggles", received, ["QLC+API|setFunctionStatus|7|1", "QLC+API|setFunctionStatus|8|0"])

        with section("live state follows a push"):
            await asyncio.sleep(0.3)
            check("held connection is current", f._live)
            FUNCTIONS["7"][1] = "Stopped"
            for c in [c for c in clients if c.open]:
                await c.send("FUNCTION|7|Stopped")
            await wait_for_async(lambda: f._on.get("f7") is False)
            check("the copy changed without a new connection", f._on["f7"] is False and f._live)
        await f.close()
        await f.close()
        check("close stops the held connection", f._task is None and not f._live)

    with section("test"):
        r = await b.test()
        check("ok, counts buttons and sliders", r["ok"] and "2 buttons, 1 slider" in r["detail"])

    with section("failures"):
        server.close()
        await server.wait_closed()
        r = await b.test()
        check("not reachable is a sentence", not r["ok"] and "Could not reach QLC+" in r["detail"])
        check("press fails with a sentence", await raises(b.press("1")))
        r = await QLCPlus({"host": "", "port": 0}).test()
        check("no host", not r["ok"] and "address" in r["detail"])

asyncio.run(main())
done()
