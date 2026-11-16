"""Signing in to another service (#304, app/oauth.py): the device code, waiting for the operator, keeping the
tokens sealed in config.json, refreshing them, noticing a revoked sign-in, disconnecting, the audit entries and the
routes a module adds. Against a fake service: no network.

Run: .venv/bin/python tests/test_oauth.py
"""
import asyncio
import json
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi import APIRouter, FastAPI, HTTPException, Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main  # noqa: E402,F401  (loads the module registry, as Relay does)
from app import audit, config, netsock, oauth, secretbox  # noqa: E402
from app.modules import modules  # noqa: E402
from _harness import capture_log, check, done, eq, section  # noqa: E402

oauth.MIN_INTERVAL = 0.01
oauth.SLOW_DOWN = 0.01

# Seal the fake module's account like a real module's secret_settings would.
_paths = modules.secret_paths
modules.secret_paths = lambda: _paths() + [("fakesvc", "account")]


SHOWN = ("client_id", "scope", "grant_type", "device_code", "refresh_token", "token")


class Fake(oauth.Provider):
    """A service that answers from a script: each URL has a list of (status, body) replies, used in turn."""
    id, name = "fake", "FakeTube"
    device_url = "https://fake.example/device"
    token_url = "https://fake.example/token"
    revoke_url = "https://fake.example/revoke"

    def __init__(self):
        super().__init__("fakesvc")
        self.replies: dict[str, list] = {}
        self.sent: list[tuple[str, dict | None, str]] = []
        self.client_key_sent: list[bool] = []
        self.who = {"id": "UC123", "name": "Grace Church"}

    def script(self, url, *replies):
        self.replies.setdefault(url, []).extend(replies)

    async def call(self, url, form=None, *, token=""):
        if not url.startswith("https://"):
            raise oauth.Problem("not set up")
        # Only the fields a check prints: CodeQL flags a client secret reaching the harness's printed output.
        self.client_key_sent.append(bool(form) and form.get("client_secret") == CLIENT["secret"])
        self.sent.append((url, {k: form[k] for k in SHOWN if k in form} if form is not None else None, token))
        queue = self.replies.get(url) or []
        if not queue:
            raise oauth.Problem("Could not reach FakeTube.")
        return queue.pop(0)

    async def identity(self, tokens):
        self.sent.append(("identity", None, tokens.access_token))
        return self.who


FAKE = Fake()
CLIENT = {"id": "client-id", "secret": "client-not-secret"}
ACC = oauth.Account("fakesvc", FAKE, settings_key="fakesvc",
                    client=lambda: oauth.Client(CLIENT["id"], CLIENT["secret"]), scopes=("read", "more"))


class Person:
    id, name, username = 1, "Pat Admin", "pat"


def code_reply(**kw):
    return 200, {"device_code": "dev-1", "user_code": "ABCD-EFGH", "verification_url": "https://fake.example/device",
                 "expires_in": 600, "interval": 0.01} | kw


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


asyncio.set_event_loop(asyncio.new_event_loop())


async def wait_state(state, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if ACC.status()["state"] == state:
            return True
        await asyncio.sleep(0.01)
    return False


with section("not connected"):
    s = ACC.status()
    eq("state", s["state"], "disconnected")
    check("no account fields", "account" not in s)
    eq("no health while never connected", ACC.health(), None)
    try:
        run(ACC.token())
        check("token() refuses", False)
    except oauth.NeedsSignIn as e:
        eq("token() says not connected", str(e), "FakeTube is not connected.")

with section("connect: code, pending, slow down, yes"):
    FAKE.script(FAKE.device_url, code_reply())
    FAKE.script(FAKE.token_url, (428, {"error": "authorization_pending"}), (400, {"error": "slow_down"}),
                (200, {"access_token": "access-one", "refresh_token": "refresh-one", "expires_in": 3600,
                       "scope": "read more"}))

    async def connect():
        s = await ACC.connect(Person())
        eq("waiting", s["state"], "waiting")
        eq("code shown", s["code"], "ABCD-EFGH")
        eq("address shown", s["url"], "https://fake.example/device")
        check("no device code in status", "dev-1" not in json.dumps(s))
        check("connected in the end", await wait_state("connected"))

    run(connect())
    eq("asked with the client id and scopes", FAKE.sent[0], (FAKE.device_url, {"client_id": "client-id", "scope": "read more"}, ""))
    eq("polled with the device grant", FAKE.sent[1][1]["grant_type"], oauth.DEVICE_GRANT)
    check("polled with the client's secret", FAKE.client_key_sent[1])
    eq("three polls", sum(1 for u, *_ in FAKE.sent if u == FAKE.token_url), 3)
    s = ACC.status()
    eq("account name", s["account"], "Grace Church")
    eq("scopes", s["scopes"], ["more", "read"])
    eq("connected by", s["connected_by"], "Pat Admin")
    check("no token in status", "access-one" not in json.dumps(s) and "refresh-one" not in json.dumps(s))

with section("kept sealed in config.json, never in what the browser reads"):
    disk = Path(os.environ["RELAY_CONFIG"]).read_text()
    check("not in plain text on disk", "refresh-one" not in disk and "access-one" not in disk)
    stored = json.loads(disk)["fakesvc"]["account"]
    check("sealed", secretbox.is_sealed(stored))
    rec = json.loads(config.get()["fakesvc"]["account"])
    eq("opened in memory", rec["tokens"]["refresh_token"], "refresh-one")
    eq("audit: connected, by whom", audit.search(kind="fakesvc")["entries"][0]["summary"], "Connected FakeTube")
    eq("audit actor", audit.search(kind="fakesvc")["entries"][0]["actor_name"], "Pat Admin")
    check("no token in the audit log", "refresh-one" not in json.dumps(audit.search(kind="fakesvc")))

with section("token: current, then refreshed when it is about to run out"):
    eq("current token", run(ACC.token()), "access-one")
    ACC._tokens.expires_at = time.time() + 30          # inside the early window
    before = Path(os.environ["RELAY_CONFIG"]).stat().st_mtime_ns
    FAKE.script(FAKE.token_url, (200, {"access_token": "access-two", "expires_in": 3600}))
    eq("refreshed", run(ACC.token()), "access-two")
    sent = FAKE.sent[-1][1]
    eq("refresh grant", (sent["grant_type"], sent["refresh_token"]), ("refresh_token", "refresh-one"))
    eq("refresh token kept when none comes back", ACC._tokens.refresh_token, "refresh-one")
    eq("config.json not rewritten for an access token", Path(os.environ["RELAY_CONFIG"]).stat().st_mtime_ns, before)
    ACC._tokens.expires_at = 1
    FAKE.script(FAKE.token_url, (200, {"access_token": "access-three", "refresh_token": "refresh-two", "expires_in": 3600}))
    eq("refreshed again", run(ACC.token()), "access-three")
    eq("a rotated refresh token is kept", json.loads(config.get()["fakesvc"]["account"])["tokens"]["refresh_token"], "refresh-two")

with section("a refused token is refreshed before the next call"):
    ACC.refused()
    FAKE.script(FAKE.token_url, (200, {"access_token": "access-four", "expires_in": 3600}))
    eq("refreshed after 401", run(ACC.token()), "access-four")

with section("refresh fails without saying no: try again later, still connected"):
    ACC._tokens.expires_at = 1
    FAKE.script(FAKE.token_url, (503, {"error": "temporarily_unavailable", "error_description": "<b>busy</b>"}))
    with capture_log("relay.oauth") as logged:
        try:
            run(ACC.token())
            check("raised", False)
        except oauth.NeedsSignIn:
            check("not a sign-in problem", False)
        except oauth.Problem as e:
            eq("fixed sentence", str(e), "FakeTube did not answer as expected. Relay will try again.")
    check("the service's description is never logged", not any("busy" in m for m in logged), logged)
    eq("still connected", ACC.status()["state"], "connected")

with section("revoked: needs signing in again, health warns"):
    FAKE.script(FAKE.token_url, (400, {"error": "invalid_grant", "error_description": "Token has been expired or revoked."}))
    try:
        run(ACC.token())
        check("raised", False)
    except oauth.NeedsSignIn as e:
        eq("sentence", str(e), "FakeTube needs signing in again.")
    eq("state", ACC.status()["state"], "sign_in")
    eq("account still named", ACC.status()["account"], "Grace Church")
    eq("health", ACC.health(), ("warn", "FakeTube needs signing in again."))
    n = len(FAKE.sent)
    try:
        run(ACC.token())
    except oauth.NeedsSignIn:
        pass
    eq("asks the service no more", len(FAKE.sent), n)
    check("audit: stopped accepting", any(e["action"] == "fakesvc.signed_out"
                                          for e in audit.search(kind="fakesvc")["entries"]))

with section("declined, expired, and the service refusing a code"):
    FAKE.script(FAKE.device_url, code_reply())
    FAKE.script(FAKE.token_url, (400, {"error": "access_denied"}))

    async def declined():
        await ACC.connect(Person())
        check("back to sign in", await wait_state("sign_in"))   # the old account is still there, revoked
        eq("message", ACC.status()["message"], "Sign-in was declined.")

    run(declined())
    FAKE.script(FAKE.device_url, code_reply(expires_in=0.05))   # 0 or less would mean "use the default"
    FAKE.script(FAKE.token_url, *[(428, {"error": "authorization_pending"})] * 50)

    async def expired():
        await ACC.connect(Person())
        check("stops waiting", await wait_state("sign_in"))
        eq("message", ACC.status()["message"], "The code ran out before it was entered. Press Connect for a new one.")

    run(expired())
    FAKE.replies[FAKE.token_url] = []
    FAKE.script(FAKE.device_url, (400, {"error": "invalid_scope", "error_description": "<script>"}))
    try:
        run(ACC.connect(Person()))
        check("raised", False)
    except oauth.Problem as e:
        eq("fixed sentence", str(e), "FakeTube did not give a sign-in code. Try again in a minute.")
    CLIENT["id"] = ""
    try:
        run(ACC.connect(Person()))
    except oauth.Problem as e:
        eq("no client id: not set up", str(e), "FakeTube sign-in is not set up in this copy of Relay.")
    CLIENT["id"] = "client-id"

with section("connect again replaces the revoked account"):
    FAKE.who = {"id": "UC999", "name": "Grace Church Live"}
    FAKE.script(FAKE.device_url, code_reply())
    FAKE.script(FAKE.token_url, (200, {"access_token": "access-five", "refresh_token": "refresh-five", "expires_in": 3600, "scope": "read"}))

    async def again():
        await ACC.connect(Person())
        check("connected", await wait_state("connected"))

    run(again())
    eq("new account", ACC.status()["account"], "Grace Church Live")
    eq("no health warning", ACC.health(), None)
    eq("token", run(ACC.token()), "access-five")

with section("cancel and close stop the wait"):
    FAKE.script(FAKE.device_url, code_reply(), code_reply())
    FAKE.script(FAKE.token_url, *[(428, {"error": "authorization_pending"})] * 1000)

    async def cancel():
        await ACC.connect(Person())
        task = ACC._task
        await ACC.cancel()
        check("task ended", task.done())
        eq("connected account kept", ACC.status()["state"], "connected")
        await ACC.connect(Person())
        task = ACC._task
        await ACC.close()
        check("close ends it", task.done())

    try:
        run(cancel())
    finally:
        FAKE.replies[FAKE.token_url] = []

with section("the routes a module adds"):
    def allow(request: Request):
        request.state.user = Person()

    def deny():
        raise HTTPException(403, "no")

    api = FastAPI()
    seen = []

    @api.middleware("http")
    async def keep_notes(request, call_next):
        resp = await call_next(request)
        seen.extend(audit.pending(request))
        return resp

    r = APIRouter()
    oauth.add_routes(r, "/api/admin/fakesvc/account", ACC, view=allow, manage=allow)
    r2 = APIRouter()
    oauth.add_routes(r2, "/api/admin/locked/account", ACC, view=allow, manage=deny)
    api.include_router(r)
    api.include_router(r2)
    c = TestClient(api)
    s = c.get("/api/admin/fakesvc/account").json()
    eq("status route", s["state"], "connected")
    eq("manage permission guards connect", c.post("/api/admin/locked/account/connect").status_code, 403)
    eq("manage permission guards disconnect", c.post("/api/admin/locked/account/disconnect").status_code, 403)
    FAKE.script(FAKE.device_url, (500, {}))
    s = c.post("/api/admin/fakesvc/account/connect").json()
    eq("a refused code is ok:false with a sentence", (s["ok"], s["error"]), (False, "FakeTube did not give a sign-in code. Try again in a minute."))
    FAKE.script(FAKE.revoke_url, (200, {}))
    s = c.post("/api/admin/fakesvc/account/disconnect").json()
    eq("disconnected", s["state"], "disconnected")
    eq("revoked with the refresh token", FAKE.sent[-1][:2], (FAKE.revoke_url, {"token": "refresh-five"}))
    eq("forgotten in config", config.get()["fakesvc"]["account"], "")
    eq("gone from disk", json.loads(Path(os.environ["RELAY_CONFIG"]).read_text())["fakesvc"]["account"], "")
    note = [n for n in seen if n["action"] == "fakesvc.disconnect"]
    check("disconnect noted for the audit log", note and note[0]["target"] == "Grace Church Live", seen)
    eq("noted whether it was revoked", note[0]["detail"], {"revoked": True})

with section("disconnect when the service does not confirm"):
    FAKE.script(FAKE.device_url, code_reply())
    FAKE.script(FAKE.token_url, (200, {"access_token": "access-six", "expires_in": 60}))

    async def no_refresh():
        await ACC.connect(Person())
        check("connected", await wait_state("connected"))

    run(no_refresh())
    s = run(ACC.disconnect())
    eq("forgotten anyway", s["state"], "disconnected")
    check("says to remove it at the service", "did not confirm" in s["message"], s["message"])

with section("a token with no refresh token: sign in again when it runs out"):
    FAKE.script(FAKE.device_url, code_reply())
    FAKE.script(FAKE.token_url, (200, {"access_token": "access-seven", "expires_in": 60}))
    run(no_refresh())
    try:
        run(ACC.token())
        check("raised", False)
    except oauth.NeedsSignIn:
        eq("sign in", ACC.status()["state"], "sign_in")
    FAKE.script(FAKE.device_url, code_reply())
    FAKE.script(FAKE.token_url, (200, {"access_token": "page-token"}))      # no expiry: a Facebook Page token
    run(no_refresh())
    eq("a token with no expiry stays current", run(ACC.token()), "page-token")
    ACC.refused()
    eq("refused with nothing to refresh: sign in", ACC.status()["state"], "sign_in")

with section("a stored value that is not JSON"):
    config.save({"fakesvc": {"account": "not json"}})
    with capture_log("relay.oauth") as logged:
        eq("disconnected", ACC.status()["state"], "disconnected")
    check("logged", logged)

with section("Provider.call: only https, and through the module's traffic class"):
    p = oauth.Provider("fakesvc")
    p.name = "Thing"
    try:
        run(p.call("http://plain.example/token", {}))
        check("refused", False)
    except oauth.Problem as e:
        eq("not set up", str(e), "Thing sign-in is not set up in this copy of Relay.")
    got = []
    real = netsock.fetch
    netsock.fetch = lambda route, url, **kw: got.append((route, url, kw)) or (400, b'{"error":"invalid_grant"}')
    try:
        eq("status and JSON", run(p.call("https://x.example/t", {"a": "1"}, token="tok")), (400, {"error": "invalid_grant"}))
        eq("routed by class", got[0][0], "fakesvc")
        eq("bearer header", got[0][2]["headers"], {"Authorization": "Bearer tok"})
        netsock.fetch = lambda route, url, **kw: (200, b"<html>")
        eq("not JSON is an empty object", run(p.call("https://x.example/t")), (200, {}))

        def down(route, url, **kw):
            raise OSError("connection refused by 10.0.0.1")
        netsock.fetch = down
        try:
            run(p.call("https://x.example/t"))
        except oauth.Problem as e:
            eq("a fixed sentence, not the OS's text", str(e), "Could not reach Thing.")
    finally:
        netsock.fetch = real

with section("error codes are short fixed tokens, never free text"):
    eq("plain", oauth._err({"error": "invalid_grant"}), "invalid_grant")
    eq("free text dropped", oauth._err({"error": "you <b>lost</b>"}), "")
    eq("graph object", oauth._err({"error": {"code": 190, "message": "Error validating"}}), "190")

with section("netsock.fetch over a real socket: POST a form, GET, and an error status with its body"):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, status, body):
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(body).encode())

        def do_POST(self):
            n = int(self.headers.get("Content-Length") or 0)
            form = self.rfile.read(n).decode()
            self._send(400 if "bad" in form else 200, {"form": form, "type": self.headers.get("Content-Type")})

        def do_GET(self):
            self._send(200, {"auth": self.headers.get("Authorization"), "accept": self.headers.get("Accept")})

    srv = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        status, body = netsock.fetch("translation", base + "/t", form={"a": "1 2", "b": "x"})
        eq("POST status", status, 200)
        eq("form encoded", json.loads(body), {"form": "a=1+2&b=x", "type": "application/x-www-form-urlencoded"})
        status, body = netsock.fetch("translation", base + "/me", headers={"Authorization": "Bearer t"})
        eq("GET with headers", (status, json.loads(body)), (200, {"auth": "Bearer t", "accept": "application/json"}))
        status, body = netsock.fetch("translation", base + "/t", form={"bad": "1"})
        eq("an error status keeps its body", (status, json.loads(body)["form"]), (400, "bad=1"))
        eq("the body is limited", len(netsock.fetch("translation", base + "/me", limit=5)[1]), 5)
    finally:
        srv.shutdown()
        srv.server_close()

done()
