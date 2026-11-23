"""Start opens the stream, and captions are one part of it (#592): with captions off Start needs no key or audio,
a module hears stream.started and stream.stopped, and a refused caption start leaves nothing half started.
Run: .venv/bin/python tests/test_stream.py
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from _harness import check, done, eq, section  # noqa: E402
from app import config, modules  # noqa: E402
from app.engine import StartRefused, engine  # noqa: E402
from app.stream import STARTED, STOPPED, stream  # noqa: E402

config.ensure_file()
config.save({"port": 8000, "admin_port": 8000, "admin_token": "t0ken"})
heard: list[str] = []
on_started, on_stopped = (lambda: heard.append("started")), (lambda: heard.append("stopped"))
modules.listen(STARTED, on_started)
modules.listen(STOPPED, on_stopped)


with section("captions are on by default"):
    eq("the saved setting", config.get()["stream_captions"], True)
    eq("a fresh stream is stopped", (stream.running, stream.started_at), (False, None))


with section("a caption start Relay refuses leaves the stream stopped"):
    # No key is saved, so the engine refuses before it touches the audio device.
    try:
        asyncio.run(stream.start())
        check("start with captions on and no key is refused", False)
    except StartRefused as exc:
        check("start with captions on and no key is refused", exc.key in ("no_vercel_key", "no_key", "no_backend_key"), exc.key)
    eq("the stream did not start", stream.running, False)
    eq("no module heard a start", heard, [])


with section("captions off: Start needs no key and no audio"):
    config.save({"stream_captions": False})
    st = asyncio.run(stream.start())
    eq("the stream is running", (st["running"], st["captions"]), (True, False))
    check("with a start time", isinstance(st["started_at"], float))
    eq("captions did not start", engine.running, False)
    eq("a module heard stream.started once", heard, ["started"])
    status = engine.status()
    eq("the admin status shows the stream running", (status["running"], status["captions_running"]), (True, False))
    eq("and carries the stream", status["stream"]["running"], True)
    asyncio.run(stream.start())
    eq("a second Start changes nothing and announces nothing", heard, ["started"])
    asyncio.run(stream.apply_captions())
    eq("turning captions off while they are off does nothing", engine.running, False)
    asyncio.run(stream.stop())
    eq("Stop ends the stream", (stream.running, stream.started_at), (False, None))
    eq("and a module heard stream.stopped", heard, ["started", "stopped"])
    asyncio.run(stream.stop())
    eq("a second Stop announces nothing", heard, ["started", "stopped"])


with section("the admin routes"):
    from app.main import app

    heard.clear()
    cl = _db.client(app, base_url="http://testserver")
    with cl:
        got = cl.post("/api/admin/start")
        eq("Start answers with the stream running", (got.status_code, got.json()["running"]), (200, True))
        got = cl.post("/api/admin/stop")
        eq("Stop answers with it stopped", (got.status_code, got.json()["running"]), (200, False))
        eq("the routes announced both", heard, ["started", "stopped"])
        config.save({"stream_captions": True})
        got = cl.post("/api/admin/start")
        eq("with captions on and no key Start is refused with the engine's sentence",
           (got.status_code, "key" in got.json()["error"].lower()), (400, True))
        eq("and the stream stays stopped", got.json()["running"], False)
        got = cl.post("/api/admin/config", json={"stream_captions": False})
        eq("the setting saves through the config route", (got.status_code, got.json()["config"]["stream_captions"]),
           (200, False))

modules.unlisten(STARTED, on_started)
modules.unlisten(STOPPED, on_stopped)
done()
