"""Relay Helpers (#497), Relay's end: one-use pairing codes, tokens kept as hashes, the websocket a helper dials, calls
answered, timeouts and disconnects, removal, and the admin routes with their permissions. The helper itself is
native/relay-helper (its own tests); here a socket that speaks the same frames stands in for it.
Run: python tests/test_helperhub.py
"""
import asyncio
import json
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

import app.main as main  # noqa: E402,F401
from app import config, helperhub as hh  # noqa: E402
from app.main import app  # noqa: E402
from _harness import await_for, check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})


class FakeSocket:
    """What a helper looks like from Relay: frames in, frames out."""

    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []
        self.closed = False

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def close(self, code=1000):
        self.closed = True

    def feed(self, frame):
        self.inbox.put_nowait(json.dumps(frame) if not isinstance(frame, str) else frame)


def run(coro):
    return asyncio.run(coro)


async def connect(h, helper_id, token, caps=None):
    ws = FakeSocket()
    ws.feed({"type": "hello", "helper_id": helper_id, "token": token, "name": "FOH Mac", "os": "macos", "version": "0.1.0",
             "capabilities": caps or [{"id": "propresenter", "version": 1, "ops": ["status", "open_files", "api"]}]})
    task = asyncio.create_task(h.serve(ws))
    await await_for(lambda: helper_id in h.connections or ws.closed, 2)
    return ws, task


with section("pairing codes"):
    h = hh.Hub()
    made = h.make_code()
    eq("a code is XXXX-XXXX from the clear alphabet", bool(__import__("re").fullmatch(r"[A-HJKMNP-Z2-9]{4}-[A-HJKMNP-Z2-9]{4}", made["code"])), True)
    eq("it runs out in ten minutes", made["expires_in"], 600)
    paired = h.pair(made["code"].lower().replace("-", " - ").replace(" ", ""), "  FOH   Mac ", "macos", "0.1.0")
    check("a code works typed in lower case", len(paired["token"]) >= 40 and len(paired["helper_id"]) == 16)
    stored = h.stored()[paired["helper_id"]]
    eq("the name is tidied and kept", stored["name"], "FOH Mac")
    check("the token itself is not stored, its hash is", paired["token"] not in json.dumps(stored) and len(stored["token_hash"]) == 64)
    try:
        h.pair(made["code"], "x", "", "")
        check("a code works once", False)
    except hh.HelperError as exc:
        check("a code works once", "not right" in exc.message)
    stale = h.make_code()["code"]
    h.codes[stale] = 1.0
    try:
        h.pair(stale, "x", "", "")
        check("an expired code is refused", False)
    except hh.HelperError:
        check("an expired code is refused", True)
    h2 = hh.Hub()
    for _ in range(hh.PAIR_FAILS_MAX):
        try:
            h2.pair("WRONG-CODE", "x", "", "")
        except hh.HelperError:
            pass
    good = h2.make_code()["code"]
    try:
        h2.pair(good, "x", "", "")
        check("too many wrong codes stop pairing, even for a right one", False)
    except hh.HelperError as exc:
        check("too many wrong codes stop pairing, even for a right one", "Too many" in exc.message)
    h3 = hh.Hub()
    for _ in range(hh.MAX_CODES):
        h3.make_code()
    try:
        h3.make_code()
        check("only a few codes wait at once", False)
    except hh.HelperError:
        check("only a few codes wait at once", True)

with section("a helper connects and is called"):
    config.save({"helpers": {}}, replace=("helpers",))
    async def scenario():
        h = hh.Hub()
        h.codes["AAAA-BBBB"] = 1e12
        ws = FakeSocket()
        ws.feed({"type": "pair", "code": "AAAA-BBBB", "name": "FOH", "os": "macos", "version": "0.1.0"})
        await h.serve(ws)
        paired = ws.sent[0]
        eq("pairing answers with an id and a token", (paired["type"], len(paired["helper_id"]), bool(paired["token"])), ("paired", 16, True))
        eq("and closes", ws.closed, True)

        bad, task = await connect(h, paired["helper_id"], "wrong-token")
        await task
        eq("a wrong token is refused", bad.sent[0]["type"], "refused")
        unknown, task = await connect(h, "0123456789abcdef", paired["token"])
        await task
        eq("an unknown helper is refused", unknown.sent[0]["type"], "refused")

        ws, task = await connect(h, paired["helper_id"], paired["token"])
        eq("a good hello is welcomed", ws.sent[0]["type"], "welcome")
        eq("and listed online with its capabilities", [(v["online"], v["capabilities"][0]["id"]) for v in h.view()], [(True, "propresenter")])

        async def answer(frame_check, reply):
            await await_for(lambda: len(ws.sent) > answer.n, 2)
            frame = ws.sent[-1]
            frame_check(frame)
            ws.feed({"type": "result", "id": frame["id"], **reply})
            answer.n = len(ws.sent)
        answer.n = len(ws.sent)

        call = asyncio.create_task(h.call("propresenter", "status", {}))
        await answer(lambda f: eq("the call frame", (f["type"], f["cap"], f["op"], f["args"]), ("call", "propresenter", "status", {})),
                     {"ok": True, "data": {"api_reachable": True}})
        eq("a good answer returns its data", await call, {"api_reachable": True})

        call = asyncio.create_task(h.call("propresenter", "api", {"path": "/x"}))
        await answer(lambda f: None, {"ok": False, "error": "A ProPresenter API path must start with /v1/ and be a plain path."})
        try:
            await call
            check("a helper's error comes back as a sentence", False)
        except hh.HelperError as exc:
            eq("a helper's error comes back as a sentence", exc.message, "A ProPresenter API path must start with /v1/ and be a plain path.")

        for cap, op, why in (("obs", "x", "a capability no helper has"), ("propresenter", "nope", "an operation it did not list")):
            try:
                await h.call(cap, op, {})
                check(f"{why} is refused before anything is sent", False)
            except hh.HelperError as exc:
                check(f"{why} is refused before anything is sent", "No connected helper" in exc.message)

        try:
            await h.call("propresenter", "status", {}, timeout=0.05)
            check("a helper that goes quiet times out", False)
        except hh.HelperError as exc:
            eq("a helper that goes quiet times out", exc.message, "The helper did not answer in time.")
        eq("and the call is forgotten", h.connections[paired["helper_id"]].pending, {})

        answer.n = len(ws.sent)
        ws.feed("not json")
        ws.feed(json.dumps([1, 2]))
        ws.feed({"type": "result", "id": 999, "ok": True})
        ws.feed({"type": "pong"})
        call = asyncio.create_task(h.call("propresenter", "status", {}))
        await answer(lambda f: None, {"ok": True, "data": 1})
        eq("junk frames change nothing", await call, 1)

        call = asyncio.create_task(h.call("propresenter", "status", {}))
        await await_for(lambda: bool(h.connections[paired["helper_id"]].pending), 2)
        ws.inbox.put_nowait(None)
        await task
        try:
            await call
            check("a call waiting when the helper drops fails at once", False)
        except hh.HelperError as exc:
            eq("a call waiting when the helper drops fails at once", exc.message, "The helper disconnected before it answered.")
        eq("and it is shown offline", [v["online"] for v in h.view()], [False])

        # the same helper dialling again replaces its old connection
        a, ta = await connect(h, paired["helper_id"], paired["token"])
        b, tb = await connect(h, paired["helper_id"], paired["token"])
        await await_for(lambda: a.closed, 2)
        eq("the old connection is closed", a.closed, True)
        eq("one connection is kept", len(h.connections), 1)

        # removing a helper stops its token for good
        removed = await h.remove(paired["helper_id"])
        eq("remove returns what it forgot", removed["name"], "FOH")
        check("a connected helper is told", any(f.get("type") == "refused" for f in b.sent))
        eq("it is gone", (h.stored(), h.view()), ({}, []))
        c, tc = await connect(h, paired["helper_id"], paired["token"])
        await tc
        eq("its token no longer works", c.sent[0]["type"], "refused")
        eq("removing again finds nothing", await h.remove(paired["helper_id"]), None)

        # junk first frames
        for first in ("not json", json.dumps({"type": "whatever"}), json.dumps([1])):
            s = FakeSocket()
            s.feed(first)
            await h.serve(s)
            check(f"a first frame of {first[:12]!r} closes the socket", s.closed)

    run(scenario())

with section("what a helper says about itself is cut down"):
    caps = hh._clean_caps([{"id": "propresenter", "version": 1, "ops": ["status", "BAD NAME", 5, "open_files"]},
                           {"id": "Bad Id", "ops": []}, "junk", {"id": "x" * 40}])
    eq("only well-formed capabilities and operations stay", caps, [{"id": "propresenter", "version": 1, "ops": ["status", "open_files"]}])
    eq("a list too long is cut", len(hh._clean_caps([{"id": f"c{i}"} for i in range(100)])), 32)
    eq("no capabilities for a non-list", hh._clean_caps("x"), [])

with section("the websocket and the admin routes"):
    config.save({"helpers": {}}, replace=("helpers",))
    admin = _db.client(app, base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    operator = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")
    code = admin.post("/api/admin/helpers/code").json()
    eq("an admin makes a code", sorted(code), ["code", "expires_in"])
    eq("an operator cannot", operator.post("/api/admin/helpers/code").status_code, 403)
    eq("a viewer cannot", viewer.post("/api/admin/helpers/code").status_code, 403)
    eq("a viewer cannot list helpers", viewer.get("/api/admin/helpers").status_code, 403)
    with admin.websocket_connect("/ws/helper") as ws:
        ws.send_text(json.dumps({"type": "pair", "code": code["code"], "name": "Stage Mac", "os": "macos", "version": "0.1.0"}))
        paired = json.loads(ws.receive_text())
    eq("pairing over the real route", paired["type"], "paired")
    listed = admin.get("/api/admin/helpers").json()
    eq("the list shows it offline", [(h["name"], h["online"]) for h in listed["helpers"]], [("Stage Mac", False)])
    eq("and no secret", "token" in json.dumps(listed), False)
    with admin.websocket_connect("/ws/helper") as ws:
        ws.send_text(json.dumps({"type": "hello", "helper_id": paired["helper_id"], "token": paired["token"], "name": "Stage Mac",
                                 "os": "macos", "version": "0.1.0", "capabilities": [{"id": "propresenter", "version": 1, "ops": ["status"]}]}))
        eq("a hello over the real route is welcomed", json.loads(ws.receive_text())["type"], "welcome")
        online = admin.get("/api/admin/helpers").json()["helpers"][0]
        eq("it shows online with its capabilities", (online["online"], online["capabilities"][0]["id"]), (True, "propresenter"))
    with admin.websocket_connect("/ws/helper") as ws:
        ws.send_text(json.dumps({"type": "hello", "helper_id": paired["helper_id"], "token": "nope"}))
        eq("a bad token over the real route is refused", json.loads(ws.receive_text())["type"], "refused")
    eq("rename", admin.post(f"/api/admin/helpers/{paired['helper_id']}", json={"name": "Balcony"}).status_code, 200)
    eq("renamed", admin.get("/api/admin/helpers").json()["helpers"][0]["name"], "Balcony")
    eq("an operator cannot rename", operator.post(f"/api/admin/helpers/{paired['helper_id']}", json={"name": "x"}).status_code, 403)
    eq("an operator cannot remove", operator.delete(f"/api/admin/helpers/{paired['helper_id']}").status_code, 403)
    eq("remove", admin.delete(f"/api/admin/helpers/{paired['helper_id']}").status_code, 200)
    eq("removing again is a 404", admin.delete(f"/api/admin/helpers/{paired['helper_id']}").status_code, 404)
    eq("renaming nothing is a 404", admin.post("/api/admin/helpers/0123456789abcdef", json={"name": "x"}).status_code, 404)
    eq("the list is empty again", admin.get("/api/admin/helpers").json()["helpers"], [])

done()
