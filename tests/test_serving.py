"""HTTPS panel and plain-HTTP viewer ports served by Relay itself, no proxy (app/tls.py, app/serving.py, run.py).

Mints real certificates, opens real sockets on 127.0.0.1 and talks to a real server in this process.
Offline. Run: .venv/bin/python tests/test_serving.py
"""
import hashlib
import http.client
import io
import logging
import os
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["RELAY_ANNOUNCE"] = "0"
_tmp = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(_tmp, "config.json")
os.environ["RELAY_CERT_DIR"] = os.path.join(_tmp, "certs")
os.environ.pop("RELAY_ADMIN_IPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()

from app import config, netinfo, serving, tls  # noqa: E402

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    check(label, got == want, f"got {got!r}, want {want!r}")


IPS = ["192.0.2.10"]
netinfo.lan_ips = lambda: list(IPS)          # a fixed LAN, so names and fingerprints are predictable

# ------------------------------------------------------------------ the certificate
print("certificate")
log_stream = io.StringIO()
handler = logging.StreamHandler(log_stream)
logging.getLogger("relay").addHandler(handler)
logging.getLogger("relay").setLevel(logging.DEBUG)

p1 = tls.Panel()
info = p1.start("")
check("a certificate and key are written to certs/", tls.cert_path().exists() and tls.key_path().exists())
if sys.platform != "win32":
    eq("the key is 0600", stat.S_IMODE(tls.key_path().stat().st_mode), 0o600)
check("it names the LAN address, loopback, localhost and the host name",
      {"192.0.2.10", "127.0.0.1", "localhost"} <= set(info.ips + info.dns)
      and socket.gethostname().split(".")[0] in info.dns, info)
check("the fingerprint is colon-separated SHA-256", len(info.fingerprint.split(":")) == 32, info.fingerprint)
check("a first certificate is not a change", p1.changed is None)

p2 = tls.Panel()
again = p2.start("")
eq("a restart reuses the certificate: same fingerprint", again.fingerprint, info.fingerprint)
check("...and says it was reused", again.reused is True)

p2.fqdn = "relay.example.org"
check("an operator's admin_fqdn the certificate lacks gets a new one", p2.refresh() is True)
check("...with a different fingerprint, reported as changed",
      p2.changed and p2.changed["from"] == info.fingerprint and p2.changed["to"] == p2.info.fingerprint, p2.changed)
eq("...that names the fqdn", p2.info.dns[0], "relay.example.org")
fp_fqdn = p2.info.fingerprint

IPS[:] = ["198.51.100.7"]
check("a new LAN address the certificate does not cover mints a new one", p2.refresh() is True)
check("...which names the new address", "198.51.100.7" in p2.info.ips, p2.info.ips)
check("...and the fingerprint changed again", p2.info.fingerprint != fp_fqdn)
check("an unchanged LAN does not", p2.refresh() is False)

real = tls.RENEW_WITHIN_DAYS
tls.RENEW_WITHIN_DAYS = 400                    # every certificate is now "within 400 days of expiry"
before = p2.info.fingerprint
check("a certificate close to expiry is replaced", p2.refresh() is True and p2.info.fingerprint != before)
tls.RENEW_WITHIN_DAYS = real

tls.key_path().write_text("not a key")
check("a corrupt key is replaced, not fatal", tls.Panel().start("").fingerprint)
check("the private key is never logged", "PRIVATE KEY" not in log_stream.getvalue())

# ------------------------------------------------------------------ ports
print("ports and fallback")
eq("80 over http has no port in the link", serving.link("http", "10.0.0.5", 80), "http://10.0.0.5/")
eq("443 over https has no port in the link", serving.link("https", "10.0.0.5", 443, "/admin"), "https://10.0.0.5/admin")
eq("8080 is in the link", serving.link("http", "10.0.0.5", 8080), "http://10.0.0.5:8080/")
eq("8443 is in the link", serving.link("https", "10.0.0.5", 8443, "/admin"), "https://10.0.0.5:8443/admin")

busy = socket.socket()
busy.bind(("127.0.0.1", 0))
busy.listen(1)
taken = busy.getsockname()[1]
spare = socket.socket()
spare.bind(("127.0.0.1", 0))
free = spare.getsockname()[1]
spare.close()
with mock.patch.dict(serving.FALLBACK, {taken: free}):
    s, got, note = serving.listen("127.0.0.1", taken, "viewer pages")
    eq("a port in use falls back", got, free)
    check("...and says so", note and str(taken) in note and str(free) in note and "in use" in note, note)
    s.close()
try:
    serving.listen("127.0.0.1", taken, "viewer pages")
    check("a port in use with no fallback is an error", False)
except SystemExit as exc:
    check("a port in use with no fallback is an error", "Cannot listen" in str(exc), exc)
busy.close()

serving.reset()
eq("unpublished: the configured port is the viewer's", serving.viewer_port(8000), 8000)
serving.publish(8080, 8443, ["note"])
eq("published: the bound port wins", (serving.viewer_port(8000), serving.admin_port(8001)), (8080, 8443))
eq("...and the notices are kept", serving.notices(), ["note"])
serving.reset()

# ------------------------------------------------------------------ a real server
print("a real server, no proxy")
config.ensure_file()
IPS[:] = ["192.0.2.10"]
tls.panel = tls.Panel()
tls.panel.start("")
viewer_sock, vport, _ = serving.listen("127.0.0.1", 0, "viewer pages")
admin_sock, aport, _ = serving.listen("127.0.0.1", 0, "operator panel")
serving.publish(vport, aport)
from app import main as M  # noqa: E402  (after the config and ports above exist)

M.tls.panel = tls.panel
server = serving.make_server("app.main:app", [viewer_sock], [admin_sock], tls.panel.context)
thread = threading.Thread(target=server.run, kwargs={"sockets": [viewer_sock, admin_sock]}, daemon=True)
thread.start()
for _ in range(100):
    if server.started:
        break
    time.sleep(0.1)
check("the server starts", server.started)


def peer_fingerprint(port):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw, ctx.wrap_socket(raw) as tlsock:
        der = tlsock.getpeercert(binary_form=True)
    h = hashlib.sha256(der).hexdigest().upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def https_get(path):
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    c = http.client.HTTPSConnection("127.0.0.1", aport, context=ctx, timeout=5)
    c.request("GET", path)
    return c.getresponse()


def http_get(port, path):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request("GET", path)
    return c.getresponse()


eq("the panel presents the fingerprint Relay printed", peer_fingerprint(aport), tls.panel.info.fingerprint)
r = https_get("/admin")
check("https://<lan-ip>/admin works", r.status == 200, r.status)
for h, v in (("x-content-type-options", "nosniff"), ("x-frame-options", "DENY"), ("referrer-policy", "no-referrer"),
             ("strict-transport-security", "max-age=34300800")):
    eq(f"the panel sends {h}", r.getheader(h), v)

try:
    resp = http_get(aport, "/admin")
    check("http://<lan-ip>/admin on the panel port is refused", resp.status != 200, resp.status)
except (http.client.HTTPException, OSError):
    check("http://<lan-ip>/admin on the panel port is refused", True)

r = http_get(vport, "/admin")
eq("the viewer port refuses /admin", r.status, 404)
eq("...and /api/admin/state", http_get(vport, "/api/admin/state").status, 404)
r = http_get(vport, "/")
eq("viewer pages are plain HTTP", r.status, 200)
check("the viewer port sends no panel headers",
      r.getheader("strict-transport-security") is None and r.getheader("x-frame-options") is None)
try:
    peer_fingerprint(vport)
    check("the viewer port does not speak TLS", False)
except (ssl.SSLError, OSError):
    check("the viewer port does not speak TLS", True)

# A LAN change while running: the next handshake presents the new certificate, no restart.
old_fp = peer_fingerprint(aport)
IPS[:] = ["203.0.113.44"]
check("a new LAN address mints a certificate while running", tls.panel.refresh() is True)
new_fp = peer_fingerprint(aport)
check("the running socket presents it", new_fp == tls.panel.info.fingerprint and new_fp != old_fp)
check("...and the app reports that the fingerprint changed",
      M._serving_info()["certificate"]["changed"]["to"] == new_fp)
check("the panel links name the new address", any("203.0.113.44" in u for u in M._serving_info()["panel_urls"]))

server.should_exit = True
thread.join(timeout=10)
check("the server stops", not thread.is_alive())

print()
if fails:
    print(f"{len(fails)} FAILED")
    for f in fails:
        print("  -", f)
    sys.exit(1)
print("all serving checks passed")
