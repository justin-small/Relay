"""The audio bus, routes and meters on the Rust server (#657), with this app running behind it.

Starts the real programs the way they run together (`python run.py --upstream-port N` and `relay-server --rust-audio`, both with
RELAY_RUST_AUDIO=1) and checks over real HTTP what the Python audio tests check in process:

* the admin routes for the bus, routes and channels are answered by the server, behind the panel's sign-in, with the saved setting
  in config.json and the crosspoints in the routes table, where the app reads them;
* `/api/admin/audiobus` with a full bus (32 lanes, three 8-channel outputs, 768 crosspoints) answers in under 50 ms at the median (#648);
* the app's way in (`app/audiobus_remote.py`): a lane reader, a network source pushed to the engine, a network sender drained from it,
  and a destination registered by a module, through the loopback door that answers to nobody else;
* what only the app knows (the audio sources modules registered) is asked of it by the server.

The Rust side of the same rules, with the engine's fake sound card, is native/relay-audio/tests/audio.rs and
native/relay-server/tests/audio.rs. Needs cargo (the server is built here if it is not already); skipped without it.

Run: .venv/bin/python tests/test_rust_audio.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import ssl
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from _harness import check, done, eq, section, skip, tmpdir, wait_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"
TOKEN = "front-token-for-tests"

if shutil.which("cargo") is None:
    skip("cargo is not installed")

build = subprocess.run(["cargo", "build", "--locked", "-p", "relay-server"], cwd=NATIVE, capture_output=True, text=True,
                       timeout=900)  # allow-timeout: a cold build compiles every dependency
if build.returncode != 0:
    print(build.stdout + build.stderr)
    check("relay-server builds", False)
    done()
BINARY = NATIVE / "target" / "debug" / ("relay-server.exe" if os.name == "nt" else "relay-server")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


VIEWER, ADMIN, UPSTREAM = free_port(), free_port(), free_port()
HOME = tmpdir("relay-rust-audio-")
Path(HOME, "config.json").write_text(json.dumps({
    "host": "127.0.0.1", "admin_host": "127.0.0.1", "port": VIEWER, "admin_port": ADMIN,
}), encoding="utf-8")
ENV = {**os.environ, "RELAY_HOME": HOME, "RELAY_CONFIG": str(Path(HOME, "config.json")), "RELAY_FRONT_TOKEN": TOKEN,
       "RELAY_ANNOUNCE": "0", "RELAY_KEY_STORE": "file", "RELAY_RUST_AUDIO": "1", "PYTHONUNBUFFERED": "1"}
ENV.pop("RELAY_DB_BACKUPS", None)


def context() -> ssl.SSLContext:
    c = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    c.minimum_version = ssl.TLSVersion.TLSv1_2
    c.load_verify_locations(cafile=str(Path(HOME, "certs", "admin.crt")))
    return c


def ask(port: int, path: str, secure: bool = False, headers: dict | None = None, method: str = "GET", body=None):
    """(status, headers lower-cased, body bytes) or None when nothing is listening."""
    try:
        conn = (http.client.HTTPSConnection("127.0.0.1", port, context=context(), timeout=5) if secure
                else http.client.HTTPConnection("127.0.0.1", port, timeout=5))
        data = body if isinstance(body, (bytes, type(None))) else json.dumps(body).encode()
        conn.request(method, path, body=data, headers=headers or {})
        resp = conn.getresponse()
        out = resp.status, {k.lower(): v for k, v in resp.getheaders()}, resp.read()
        conn.close()
        return out
    except (OSError, http.client.HTTPException):
        return None


COOKIE = ""


def sign_in() -> str:
    """A session for a new Owner, made by the app's own code on the database both programs share."""
    code = ("from app import db, users; db.init(); u = users.create('boss', 'Boss', 'owner', os.environ['PW'], temporary=False);"
            " print(users.start_session(u, via='password')[0])")
    got = subprocess.run([sys.executable, "-c", "import os;" + code], cwd=ROOT, env={**ENV, "PW": os.urandom(12).hex()}, capture_output=True,
                         text=True, timeout=60)  # allow-timeout: opens the database
    return "relay_admin=" + got.stdout.strip().splitlines()[-1] if got.returncode == 0 else ""


def panel(path: str, method: str = "GET", body=None):
    """An admin route over TLS, signed in with a session the app made (read by the server from the same database)."""
    headers = {"cookie": COOKIE, "content-type": "application/json"}
    return ask(ADMIN, path, secure=True, headers=headers, method=method, body=body if body is not None else ({} if method == "POST" else None))


def js(reply):
    try:
        return json.loads((reply or (0, {}, b""))[2])
    except ValueError:
        return None


app = subprocess.Popen([sys.executable, "run.py", "--upstream-port", str(UPSTREAM)], cwd=ROOT, env=ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
server = None
try:
    with section("the app and the server start together, with the server owning the bus"):
        up = wait_for(lambda: ask(UPSTREAM, "/healthz"), timeout=60)  # allow-timeout: the app builds its database on first start
        check("the app answers on its loopback port", bool(up) and up[0] == 200)
        server = subprocess.Popen([str(BINARY), "--rust-audio"], cwd=ROOT, env={**ENV, "RELAY_UPSTREAM": f"http://127.0.0.1:{UPSTREAM}"},
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready = wait_for(lambda: ask(VIEWER, "/healthz"), timeout=60)  # allow-timeout: it waits for the app's plan, then mints the certificate
        check("the viewer port answers through the server", bool(ready) and ready[0] == 200)
        eq("the bus routes are the server's: they ask for a sign-in", (ask(ADMIN, "/api/admin/audiobus", secure=True) or (0,))[0], 401)
        COOKIE = sign_in()
        check("an Owner signs in", COOKIE.startswith("relay_admin="), COOKIE)

    with section("the bus is saved and read by the server"):
        raw = panel("/api/admin/audiobus")
        first = js(raw)
        eq("a fresh bus says what first setup still asks for", (first or {}).get("setup"), {"input": True, "output": True})
        eq("32 lanes", (first or {}).get("lanes"), 32)
        check("the app's registered sources are listed (asked of it by the server)",
              "portaudio" in [s["id"] for s in (first or {}).get("sources", [])], str(first and first.get("sources")))
        lanes = [{"lane": 1, "name": "Cam", "source": "ndi", "device": "cam", "channel": 0}]
        outputs = [{"id": "aes", "name": "AES", "output": "aes67", "device": None, "channels": 2}]
        saved = panel("/api/admin/audiobus", "POST", {"lanes": lanes, "outputs": outputs,
                                                         "matrix": [{"in": 1, "out": "aes", "ch": 1}, {"in": 1, "out": "aes", "ch": 2}]})
        eq("a save is accepted", (saved or (0,))[0], 200)
        body = js(saved) or {}
        eq("the matrix is the routes' answer", len(body.get("config", {}).get("matrix", [])), 2)
        cfg = json.loads(Path(HOME, "config.json").read_text(encoding="utf-8"))
        eq("config.json keeps the lanes and outputs, where the app reads them", (len(cfg["audio_bus"]["lanes"]), cfg["audio_bus"]["matrix"]), (1, []))
        routes = js(panel("/api/admin/routes?provider=audiobus")) or {}
        eq("the crosspoints are routes, listed with what the engine mixes", [(r["name"], r["state"]) for r in routes.get("routes", [])],
           [("AES 1", "same"), ("AES 2", "same")])
        wait_for(lambda: (js(ask(UPSTREAM, "/api/internal/front", headers={"X-Relay-Front": TOKEN})) or {}).get("published"), timeout=20)  # allow-timeout: the server reports its ports on its first look

    with section("the app's way into the bus"):
        os.environ.update({"RELAY_FRONT_TOKEN": TOKEN, "RELAY_RUST_AUDIO": "1", "RELAY_RUST_URL": f"http://127.0.0.1:{VIEWER}"})
        import numpy as np
        from app import audiobus, audiobus_remote

        bus = audiobus.bus
        check("this process's bus is the remote one", audiobus.REMOTE and isinstance(bus, audiobus_remote.RemoteBus))
        bus.start_remote()
        following = wait_for(lambda: bus.running and [x["lane"] for x in bus.config["lanes"]] == [1], timeout=20)  # allow-timeout: long-poll then adopt
        check("it follows the server's setting", bool(following))
        status = audiobus_remote.get_json("/internal/audio/status")
        eq("the server's status says the bus is running", (status["running"], len(status["lanes"])), (True, 32))
        wait_for(lambda: audiobus_remote.get_json("/internal/audio/status")["lanes"][0]["state"] == "waiting", timeout=10)  # allow-timeout: the app reports its source
        lane = audiobus_remote.get_json("/internal/audio/status")["lanes"][0]
        eq("a source no module registered is waiting, and says why", lane["error"], "That audio source is not available. Check its module is switched on.")

        heard: list[float] = []
        token = bus.lane(1).subscribe(lambda block: heard.append(float(np.abs(block).max())))
        tone = (0.5 * np.sin(np.arange(9600) * 0.1)).astype(np.float32).reshape(-1, 1)
        got = []

        def feed() -> bool:
            bus.engine.push("ndi:cam", tone, 48000)
            block = bus.engine.pull("aes", 960)
            got.append(float(np.abs(block).max()))
            return max(got) > 0.1 and max(heard or [0.0]) > 0.1
        check("a network source pushed to the engine comes out of the network sender drained from it, and a lane reader hears it",
              bool(wait_for(feed, timeout=10)), f"out {max(got or [0])}, lane {max(heard or [0])}")  # allow-timeout: the long-lived reader connects first
        eq("the sender's block is whole frames of its channels", bus.engine.pull("aes", 480).shape, (480, 2))
        bus.lane(1).unsubscribe(token)
        eq("an alert tone has nowhere to go until it is routed", bus.play_to("alerts", np.zeros(480, dtype=np.float32)), 0)
        audiobus.register_destination("talkback", "Talkback")
        names = [c["shown"] for c in (js(panel("/api/admin/network/channels")) or {}).get("channels", [])]
        check("a module's destination is a source channel on the server", "Talkback" in names, str(names))
        for path, peer_headers in (("/internal/audio/status", {}), ("/internal/audio/status", {"X-Relay-Front": "guess"})):
            eq(f"the door is closed to a client without the secret ({peer_headers or 'no header'})", (ask(VIEWER, path, headers=peer_headers) or (0,))[0], 404)
        bus.stop()

    with section("a full bus answers the admin page in under 50 ms (#648)"):
        many = [{"lane": n, "name": f"L{n}", "source": "ndi", "device": f"d{n}", "channel": 0} for n in range(1, 33)]
        outs = [{"id": f"o{o}", "output": "aes67", "device": None, "channels": 8} for o in (1, 2, 3)]
        cells = [{"in": n, "out": f"o{o}", "ch": ch, "gain_db": -6} for o in (1, 2, 3) for ch in range(1, 9) for n in range(1, 33)]
        started = time.perf_counter()
        full = panel("/api/admin/audiobus", "POST", {"lanes": many, "outputs": outs, "matrix": cells})
        eq("768 crosspoints are saved", len((js(full) or {}).get("config", {}).get("matrix", [])), 768)
        print(f"  saving 768 crosspoints took {1000 * (time.perf_counter() - started):.0f} ms")
        times = []
        for _ in range(40):
            t = time.perf_counter()
            reply = panel("/api/admin/audiobus")
            times.append(1000 * (time.perf_counter() - t))
            if (reply or (0,))[0] != 200:
                break
        median = statistics.median(times)
        print(f"  GET /api/admin/audiobus: p50 {median:.1f} ms, slowest {max(times):.1f} ms over {len(times)}")
        check(f"p50 is under 50 ms (took {median:.1f} ms)", median < 50)
finally:
    for proc in (server, app):
        if proc is not None and proc.poll() is None:
            proc.terminate()
    for proc in (server, app):
        if proc is not None:
            try:
                proc.wait(timeout=15)  # allow-timeout: a graceful stop of the app flushes its database
            except subprocess.TimeoutExpired:
                proc.kill()

done()
