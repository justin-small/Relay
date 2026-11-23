"""The app behind the Rust server (app/front.py, #653): its two calls with the server, and the port the server forwards.

In this process, with no Rust and no sockets: tests/test_rust_listeners.py runs the real programs together. Offline.
Run: .venv/bin/python tests/test_front.py
"""
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import front, serving, tls  # noqa: E402
from app.main import app  # noqa: E402

from _harness import check, done, eq, section  # noqa: E402

TOKEN = "front-token"
HEAD = {"X-Relay-Front": TOKEN}
REPORT = {
    "viewer_port": 8081, "admin_port": 8444, "notices": ["The viewer pages could not use port 80."],
    "groups": [
        {"key": "viewers", "label": "viewer pages", "traffic": "viewers", "configured": "0.0.0.0", "port": 8081, "secure": False,
         "hosts": ["0.0.0.0"], "notes": [], "prefixes": []},
        {"key": "panel", "label": "operator panel", "traffic": "crew", "configured": "0.0.0.0", "port": 8444, "secure": True,
         "hosts": ["10.0.0.5", "127.0.0.1"], "notes": ["Only on 10.0.0.5."], "prefixes": []},
        {"key": "signage.screens", "label": "Signage (screens)", "traffic": "viewers", "configured": "0.0.0.0", "port": 8091,
         "secure": False, "hosts": ["0.0.0.0"], "notes": [], "prefixes": ["/signage"]},
    ],
    "certificate": {"fingerprint": "AA:BB", "names": ["localhost", "127.0.0.1"], "expires": "2027-12-25T00:00:00Z",
                    "changed": {"from": "11:22", "to": "AA:BB", "at": "2026-11-23T00:00:00Z"}},
}


def scope(client="127.0.0.1", port=b"8081", typ="http"):
    headers = [(b"host", b"relay")] + ([(front.PORT_HEADER, port)] if port is not None else [])
    return {"type": typ, "client": (client, 5000), "server": ("127.0.0.1", 18080), "headers": headers}


with section("without a front token there is no front door"):
    os.environ.pop("RELAY_FRONT_TOKEN", None)
    eq("not enabled", front.enabled(), False)
    s = scope()
    front.adopt_port(s)
    eq("the port header is not believed", s["server"], ("127.0.0.1", 18080))
    client = TestClient(app, client=("127.0.0.1", 5000))
    eq("the plan is a 404", client.get("/api/internal/front", headers=HEAD).status_code, 404)
    eq("so is a report", client.post("/api/internal/front", headers=HEAD, json=REPORT).status_code, 404)

os.environ["RELAY_FRONT_TOKEN"] = TOKEN

with section("the port the client reached is put back in the scope"):
    eq("enabled", front.enabled(), True)
    s = scope()
    front.adopt_port(s)
    eq("from a loopback peer", s["server"], ("127.0.0.1", 8081))
    s = scope(typ="websocket", port=b"443")
    front.adopt_port(s)
    eq("a websocket too", s["server"], ("127.0.0.1", 443))
    for label, s in (("a peer on the LAN", scope(client="192.168.1.9")), ("a header that is not a number", scope(port=b"abc")),
                     ("a port out of range", scope(port=b"70000")), ("no header", scope(port=None)),
                     ("a lifespan scope", scope(typ="lifespan"))):
        front.adopt_port(s)
        eq(f"{label} changes nothing", s["server"], ("127.0.0.1", 18080))

with section("the server's calls need the token"):
    client = TestClient(app, client=("127.0.0.1", 5000))
    for label, headers in (("none", {}), ("a wrong one", {"X-Relay-Front": "guess"}), ("one of another length", {"X-Relay-Front": "x"})):
        eq(f"{label}: the plan", client.get("/api/internal/front", headers=headers).status_code, 404)
        eq(f"{label}: a report", client.post("/api/internal/front", headers=headers, json=REPORT).status_code, 404)
    plan = client.get("/api/internal/front", headers=HEAD)
    eq("the right one gets the plan", plan.status_code, 200)
    body = plan.json()
    check("with the module listeners and the reserved prefixes", {"listeners", "listener_prefixes", "panel_prefixes", "published"} <= set(body), body)
    eq("nothing is published until the server reports", body["published"]["viewer_port"], None)

with section("the module listeners are what the registry says"):
    from app.modules import modules

    on = [(m.manifest.id, lst.name, port) for m, lst, port in modules.listeners({})]
    eq("one entry for each listener of a module that is on", [(x["key"], x["port"]) for x in front.plan()["listeners"]],
       [(f"{m}.{n}", p) for m, n, p in on])
    for x in front.plan()["listeners"]:
        check(f"{x['key']} carries its class and a policy", x["traffic"] and x["policy"] in ("strict", "fallback"), x)
    eq("every prefix a module reserves", front.plan()["listener_prefixes"], list(modules.listener_prefixes()))

with section("what the server bound is published as run.py publishes it"):
    ok = client.post("/api/internal/front", headers=HEAD, json=REPORT)
    eq("accepted", (ok.status_code, ok.json()), (200, {"ok": True}))
    eq("the ports", (serving.viewer_port(0), serving.admin_port(0)), (8081, 8444))
    eq("the module's port answers only its prefix", serving.listener_prefixes(8091), ("/signage",))
    eq("and the viewer port is not a module's", serving.listener_prefixes(8081), None)
    eq("a module's bound port", serving.listener_bound(("/signage",)), 8091)
    notices = serving.notices()
    check("the notices: the server's and each listener's", "The viewer pages could not use port 80." in notices and "Only on 10.0.0.5." in notices, notices)
    eq("the Network page can say where the panel listens", [g.hosts for g in serving.groups() if g.key == "panel"], [["10.0.0.5", "127.0.0.1"]])
    d = tls.panel.describe()
    eq("the fingerprint", d["fingerprint"], "AA:BB")
    eq("the names", d["names"], ["localhost", "127.0.0.1"])
    eq("a replaced certificate is shown as changed", (d["changed"] or {}).get("to"), "AA:BB")
    eq("the plan now says what was published", client.get("/api/internal/front", headers=HEAD).json()["published"],
       {"viewer_port": 8081, "admin_port": 8444, "fingerprint": "AA:BB"})
    eq("a report that is not understood is refused", client.post("/api/internal/front", headers=HEAD, json={"nope": 1}).status_code, 400)
    eq("and one that is not JSON", client.post("/api/internal/front", headers=HEAD, content=b"{").status_code, 400)

with section("the app's own port rules follow the port the server forwarded"):
    local = TestClient(app, client=("127.0.0.1", 5000))
    eq("an admin path forwarded from the viewer port is refused", local.get("/admin", headers={"X-Relay-Port": "8081"}).status_code, 404)
    check("from the panel port it is not", local.get("/admin", headers={"X-Relay-Port": "8444"}, follow_redirects=False).status_code != 404)
    eq("a module's prefix is refused on the viewer port", local.get("/signage/x", headers={"X-Relay-Port": "8081"}).status_code, 404)
    lan = TestClient(app, client=("192.168.1.9", 5000))
    check("a client that is not the server cannot make itself the viewer port",
          lan.get("/admin", headers={"X-Relay-Port": "8081"}, follow_redirects=False).status_code != 404)

serving.reset()
done()
