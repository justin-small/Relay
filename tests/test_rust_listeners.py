"""The Rust server's listeners, TLS and port rules (#653), with this app running behind it.

Starts the real programs: `python run.py --upstream-port N` (the app on loopback) and `relay-server` (the sockets), the way
they run together, then checks over real HTTP and TLS what the Python port and auth tests check against the Python listeners:

* the viewer port never reaches the panel (`/admin`, `/api/admin`, `/home`), and the panel does, behind its sign-in;
* the panel speaks only TLS, with the security headers, HSTS and the fingerprint `relay-server --fingerprint` prints;
* a module's own port (signage screens) answers only that module's prefix, and that prefix answers nowhere else;
* the app is told the ports really bound, and the app's calls with the server are never answered to a client.

The Rust side of the same rules (fallback ports, routes, certificate renewal, forwarding) is native/relay-server/tests/listeners.rs.
Needs cargo (the server is built here if it is not already); skipped without it.

Run: .venv/bin/python tests/test_rust_listeners.py
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import socket
import ssl
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _harness import check, done, eq, section, skip, tmpdir, wait_for  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
NATIVE = ROOT / "native"
TOKEN = "front-token-for-tests"
PANEL_HEADERS = {"x-content-type-options": "nosniff", "x-frame-options": "SAMEORIGIN", "referrer-policy": "no-referrer",
                 "strict-transport-security": "max-age=34300800"}

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


VIEWER, ADMIN, SIGNAGE, UPSTREAM = free_port(), free_port(), free_port(), free_port()
HOME = tmpdir("relay-rust-listeners-")
Path(HOME, "config.json").write_text(json.dumps({
    "host": "127.0.0.1", "admin_host": "127.0.0.1", "port": VIEWER, "admin_port": ADMIN,
    "modules": {"signage": {"enabled": True}}, "signage": {"port": SIGNAGE},
}), encoding="utf-8")
ENV = {**os.environ, "RELAY_HOME": HOME, "RELAY_CONFIG": str(Path(HOME, "config.json")), "RELAY_FRONT_TOKEN": TOKEN,
       "RELAY_ANNOUNCE": "0", "RELAY_KEY_STORE": "file", "PYTHONUNBUFFERED": "1"}
ENV.pop("RELAY_DB_BACKUPS", None)

def trusting_context() -> ssl.SSLContext:
    """TLS that trusts the panel's own certificate and nothing else, as an operator does after checking its fingerprint."""
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_verify_locations(cafile=str(Path(HOME, "certs", "admin.crt")))
    return context


def ask(port: int, path: str, secure: bool = False, headers: dict | None = None, method: str = "GET"):
    """(status, headers lower-cased, body text) or None when nothing is listening."""
    try:
        conn = (http.client.HTTPSConnection("127.0.0.1", port, context=trusting_context(), timeout=5) if secure
                else http.client.HTTPConnection("127.0.0.1", port, timeout=5))
        conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        body = resp.read().decode("utf-8", "replace")
        out = resp.status, {k.lower(): v for k, v in resp.getheaders()}, body
        conn.close()
        return out
    except (OSError, http.client.HTTPException):
        return None


def detail(reply) -> object:
    try:
        return json.loads(reply[2])
    except (TypeError, ValueError):
        return None


app = subprocess.Popen([sys.executable, "run.py", "--upstream-port", str(UPSTREAM)], cwd=ROOT, env=ENV,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
server = None
try:
    with section("the app and the server start together"):
        up = wait_for(lambda: ask(UPSTREAM, "/healthz"), timeout=60)  # allow-timeout: the app builds its database on first start
        check("the app answers on its loopback port", bool(up) and up[0] == 200)
        server = subprocess.Popen([str(BINARY)], cwd=ROOT, env={**ENV, "RELAY_UPSTREAM": f"http://127.0.0.1:{UPSTREAM}"},
                                  stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ready = wait_for(lambda: ask(VIEWER, "/healthz"), timeout=60)  # allow-timeout: it waits for the app's plan, then mints the certificate
        check("the viewer port answers through the server", bool(ready) and ready[0] == 200)
        eq("and says what the app says", detail(ready), {"ok": True, "running": False})

    with section("the viewer port never reaches the panel"):
        for path in ("/admin", "/admin/login", "/api/admin/users", "/home"):
            reply = ask(VIEWER, path)
            eq(f"{path} is refused", (reply or (0,))[0], 404)
            eq(f"{path}: by the port rule, not by a missing page", detail(reply), {"error": "Not found"})
        check("the headers of the panel are not on viewer answers", "strict-transport-security" not in (ask(VIEWER, "/healthz") or (0, {}))[1])

    with section("the panel is HTTPS only, behind its sign-in, with its headers"):
        reply = ask(ADMIN, "/healthz", secure=True)
        eq("a viewer page answers on the panel port too", (reply or (0,))[0], 200)
        for name, value in PANEL_HEADERS.items():
            eq(f"{name} on the panel", (reply or (0, {}))[1].get(name), value)
        check("the Permissions-Policy is there", "microphone=" in ((reply or (0, {}))[1].get("permissions-policy") or ""))
        unsigned = ask(ADMIN, "/api/admin/users", secure=True)
        eq("an admin route asks for a sign-in", (unsigned or (0,))[0], 401)
        check("the answer is the app's, through the server", "Sign in required" in (unsigned or (0, {}, ""))[2])
        eq("plain HTTP on the panel port is not served", ask(ADMIN, "/healthz"), None)

    with section("the certificate is the one relay-server prints"):
        shown = subprocess.run([str(BINARY), "--fingerprint"], cwd=ROOT, env=ENV, capture_output=True, text=True, timeout=30)  # allow-timeout: reads the interfaces
        printed = shown.stdout.strip()
        with socket.create_connection(("127.0.0.1", ADMIN), timeout=5) as raw, trusting_context().wrap_socket(raw, server_hostname="127.0.0.1") as tls:
            der = tls.getpeercert(binary_form=True) or b""
        import hashlib
        seen = ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())
        eq("the fingerprint a browser sees is the one printed", seen, printed)
        eq("and a second run keeps it", subprocess.run([str(BINARY), "--fingerprint"], cwd=ROOT, env=ENV, capture_output=True,
                                                       text=True, timeout=30).stdout.strip(), printed)  # allow-timeout: reads the interfaces

    with section("a module's own port answers only that module"):
        own = ask(SIGNAGE, "/signage/nothing")
        check("its own prefix reaches the module", bool(own) and detail(own) != {"error": "Not found"}, str(own))
        for path in ("/healthz", "/admin", "/api/admin/users", "/"):
            eq(f"{path} is refused on the module's port", detail(ask(SIGNAGE, path)), {"error": "Not found"})
        eq("its prefix is refused on the viewer port", detail(ask(VIEWER, "/signage/nothing")), {"error": "Not found"})
        eq("and on the panel port", detail(ask(ADMIN, "/signage/nothing", secure=True)), {"error": "Not found"})

    with section("the app is told what is really bound"):
        plan = ask(UPSTREAM, "/api/internal/front", headers={"X-Relay-Front": TOKEN})
        eq("the app gives its plan to the server", (plan or (0,))[0], 200)
        body = detail(plan) or {}
        eq("the module listener, with the port from its settings", [(x["key"], x["port"], x["prefixes"]) for x in body.get("listeners", [])],
           [("signage.screens", SIGNAGE, ["/signage"])])
        published = wait_for(lambda: (detail(ask(UPSTREAM, "/api/internal/front", headers={"X-Relay-Front": TOKEN})) or {}).get("published", {}).get("admin_port"))
        eq("it took the panel port the server reported", published, ADMIN)
        pub = (detail(ask(UPSTREAM, "/api/internal/front", headers={"X-Relay-Front": TOKEN})) or {}).get("published", {})
        eq("and the viewer port", pub.get("viewer_port"), VIEWER)
        eq("and the certificate's fingerprint", pub.get("fingerprint"), printed)

    with section("the two programs' own calls are never answered to a client"):
        for port, secure in ((VIEWER, False), (ADMIN, True), (SIGNAGE, False)):
            eq(f"the server refuses them on {port}", (ask(port, "/api/internal/front", secure=secure, headers={"X-Relay-Front": TOKEN}) or (0,))[0], 404)
        eq("the app refuses them without the token", (ask(UPSTREAM, "/api/internal/front") or (0,))[0], 404)
        eq("or with a wrong one", (ask(UPSTREAM, "/api/internal/front", headers={"X-Relay-Front": "guess"}) or (0,))[0], 404)
        eq("a report from a stranger is refused", (ask(UPSTREAM, "/api/internal/front", method="POST", headers={"X-Relay-Front": "guess"}) or (0,))[0], 404)
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
