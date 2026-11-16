"""YouTube live status module (#252) against a fake Google: connecting the channel through device sign-in (#304),
reading live broadcasts and viewers into the shared stream status, quota and refusals, how often it checks, the
admin routes and what the browser may see. No network.

Run: .venv/bin/python tests/test_youtube.py
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
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import config, oauth  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.youtube import service as svc  # noqa: E402
from app.streamstatus import streams  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

oauth.MIN_INTERVAL = 0.01
API = svc.API
LIST = f"{API}/liveBroadcasts?part=id,snippet,status&broadcastStatus=active&broadcastType=all&maxResults=5"


class FakeGoogle:
    """Answers PROVIDER.call by URL prefix from a script; records what was asked."""

    def __init__(self):
        self.replies: dict[str, list] = {}
        self.sent: list[tuple[str, dict | None, str]] = []

    def script(self, url, *replies):
        self.replies.setdefault(url, []).extend(replies)

    async def call(self, url, form=None, *, token=""):
        self.sent.append((url, form, token))
        for prefix, queue in self.replies.items():
            if url.startswith(prefix) and queue:
                return queue.pop(0)
        raise oauth.Problem("Could not reach YouTube.")


FAKE = FakeGoogle()
svc.PROVIDER.call = FAKE.call
P = svc.PROVIDER
CHANNEL = (200, {"items": [{"id": "UCgrace", "snippet": {"title": "Grace Church"}}]})


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


asyncio.set_event_loop(asyncio.new_event_loop())


def entry():
    return next((e for e in streams.entries(admin=True) if e["source"] == "youtube"), None)


def broadcast(vid="vid1", state="live", title="Sunday Worship", start="2026-11-15T15:00:00Z"):
    return {"id": vid, "snippet": {"title": title, "actualStartTime": start},
            "status": {"lifeCycleStatus": state, "privacyStatus": "public"}}


def connect():
    """Connect through the device flow against the fake."""
    FAKE.script(P.device_url, (200, {"device_code": "d", "user_code": "WXYZ-1234", "verification_url": "https://www.google.com/device",
                                     "expires_in": 600, "interval": 0.01}))
    FAKE.script(P.token_url, (200, {"access_token": "yt-access", "refresh_token": "yt-refresh", "expires_in": 3600,
                                    "scope": svc.SCOPE}))
    FAKE.script(f"{API}/channels", CHANNEL)

    async def go():
        await svc.ACCOUNT.connect()
        end = time.time() + 3
        while time.time() < end and svc.ACCOUNT.status()["state"] != "connected":
            await asyncio.sleep(0.01)

    run(go())


with section("settings: cleaned, and the browser never sees a secret"):
    s = svc.clean_settings({"live_s": 5, "idle_s": "99999", "client_id": " x.apps.googleusercontent.com ", "account": 3})
    eq("bounds", (s["live_s"], s["idle_s"]), (30, 3600))
    eq("trimmed", s["client_id"], "x.apps.googleusercontent.com")
    eq("a non-string account is dropped", s["account"], "")
    eq("defaults", svc.clean_settings(None), svc.DEFAULTS)
    pub = svc.public({**svc.DEFAULTS, "account": "{tokens}", "client_secret": "shh"})
    check("no account, no secret", "account" not in pub and "client_secret" not in pub)
    eq("only whether a secret is set", pub["client_secret_set"], True)
    eq("module defaults match the service's", registry.get("youtube").defaults, svc.DEFAULTS)

with section("not connected"):
    run(svc.service.check())
    eq("no stream status entry", entry(), None)
    eq("health", svc.service.health(), ("idle", "Not connected. Press Connect on the YouTube page."))
    try:
        run(svc.ACCOUNT.connect())
        check("refused", False)
    except oauth.Problem as e:
        eq("no Google app in this copy", str(e), "YouTube sign-in is not set up in this copy of Relay.")
    eq("nothing was sent", FAKE.sent, [])

config.save({"youtube": {"client_id": "church.apps.googleusercontent.com", "client_secret": "church-app-secret"}})

with section("connect: the church's own app, the channel's name"):
    poked = []
    real_poke = svc.service.poke
    svc.service.poke = lambda: poked.append(1)
    connect()
    svc.service.poke = real_poke
    st = svc.ACCOUNT.status()
    eq("connected", st["state"], "connected")
    eq("channel", st["account"], "Grace Church")
    eq("scope", st["scopes"], [svc.SCOPE])
    eq("asked with the church's client id", FAKE.sent[0][1]["client_id"], "church.apps.googleusercontent.com")
    eq("asked for read-only", FAKE.sent[0][1]["scope"], svc.SCOPE)
    eq("the token exchange used the secret", FAKE.sent[1][1]["client_secret"], "church-app-secret")
    eq("channel read with the new token", FAKE.sent[2][2], "yt-access")
    check("connecting asks the service to check now", poked)
    disk = Path(os.environ["RELAY_CONFIG"]).read_text()
    check("tokens and secret sealed on disk", "yt-refresh" not in disk and "church-app-secret" not in disk)
    red = json.dumps(config.redacted())
    check("never in the config the admin page reads", "yt-refresh" not in red and "church-app-secret" not in red)

with section("an account with no channel"):
    FAKE.script(f"{API}/channels", (200, {"items": []}))
    try:
        run(P.identity(oauth.Tokens("t")))
        check("refused", False)
    except oauth.Problem as e:
        eq("says so", str(e), "That Google account has no YouTube channel. Sign in with the church's channel.")
    FAKE.script(f"{API}/channels", (403, {}))
    try:
        run(P.identity(oauth.Tokens("t")))
    except oauth.Problem as e:
        eq("an error reading it", str(e), "YouTube did not say which channel this is. Try again.")

with section("connected, not live"):
    FAKE.sent.clear()
    FAKE.script(LIST, (200, {"items": []}))
    run(svc.service.check())
    eq("one call", [u for u, *_ in FAKE.sent], [LIST])
    eq("bearer token", FAKE.sent[0][2], "yt-access")
    e = entry()
    eq("published off air", (e["label"], e["on_air"], e["health"], e["viewers"]), ("YouTube", False, "idle", None))
    eq("health", svc.service.health(), ("idle", "Connected; not live."))
    eq("waits the idle time", svc.service.next_wait(), 600.0)

with section("live: viewers, since when, on the status view"):
    FAKE.sent.clear()
    FAKE.script(LIST, (200, {"items": [broadcast(), broadcast("vid2", "testing", "Backup feed", "")]}))
    FAKE.script(f"{API}/videos", (200, {"items": [{"id": "vid1", "liveStreamingDetails": {"concurrentViewers": "142"}}]}))
    run(svc.service.check())
    eq("asked for viewers of the live one only", FAKE.sent[1][0], f"{API}/videos?part=liveStreamingDetails&id=vid1")
    e = entry()
    eq("on air", (e["on_air"], e["health"], e["viewers"]), (True, "ok", 142))
    eq("live since the broadcast started", e["live_since"], 1794754800.0)   # 2026-11-15T15:00:00Z
    check("titles only in the admin detail", "Sunday Worship" in e["detail"])
    pub = next(x for x in streams.entries() if x["source"] == "youtube")
    check("the public entry has no titles", "detail" not in pub and "Sunday" not in json.dumps(pub))
    eq("health", svc.service.health(), ("ok", "Live: Sunday Worship"))
    snap = svc.service.snapshot()
    eq("two broadcasts", [b["state"] for b in snap["broadcasts"]], ["live", "testing"])
    eq("links", snap["broadcasts"][0]["studio"], "https://studio.youtube.com/video/vid1/livestreaming")
    eq("waits the live time", svc.service.next_wait(), 60.0)

with section("live with no viewer count"):
    FAKE.script(LIST, (200, {"items": [broadcast()]}))
    FAKE.script(f"{API}/videos", (200, {"items": [{"id": "vid1", "liveStreamingDetails": {}}]}))
    run(svc.service.check())
    eq("viewers unknown", entry()["viewers"], None)
    eq("still on air", entry()["on_air"], True)

with section("another stream on air: check as often as when live"):
    FAKE.script(LIST, (200, {"items": []}))
    run(svc.service.check())
    eq("idle", svc.service.next_wait(), 600.0)
    streams.publish("obs_a", label="OBS Studio", on_air=True, health="ok")
    eq("OBS is streaming: live interval", svc.service.next_wait(), 60.0)
    streams.clear("obs_a")

with section("refusals: 401, quota, permission, not enabled, other"):
    FAKE.script(LIST, (401, {"error": {"code": 401}}))
    run(svc.service.check())
    eq("401 message", svc.service.health(), ("warn", "YouTube refused Relay's sign-in. Relay will try again."))
    check("the token is refreshed next time", svc.ACCOUNT._tokens.expires_at == 1.0)
    FAKE.script(P.token_url, (200, {"access_token": "yt-access-2", "expires_in": 3600}))
    FAKE.script(LIST, (200, {"items": []}))
    run(svc.service.check())
    eq("refreshed and fine", svc.service.health(), ("idle", "Connected; not live."))
    eq("used the new token", FAKE.sent[-1][2], "yt-access-2")

    quota = (403, {"error": {"code": 403, "errors": [{"reason": "quotaExceeded", "message": "<b>quota</b>"}]}})
    FAKE.script(LIST, quota)
    run(svc.service.check())
    level, text = svc.service.health()
    eq("quota warns", level, "warn")
    check("quota sentence", "daily allowance" in text and "<b>" not in text, text)
    check("waits until after midnight Pacific", 0 < svc.service.next_wait() <= 25 * 3600)
    n = len(FAKE.sent)
    run(svc.service.check())
    eq("asks nothing while the quota is used up", len(FAKE.sent), n)
    eq("the reset is just after midnight Pacific", time.gmtime(svc._quota_reset(1763218800.0))[3:5], (8, 5))
    svc.service.quota_until = 0

    FAKE.script(LIST, (403, {"error": {"errors": [{"reason": "insufficientPermissions"}]}}))
    run(svc.service.check())
    eq("permission", svc.service.health()[1], "YouTube did not give Relay permission to read the channel. Press Connect again.")
    FAKE.script(LIST, (403, {"error": {"errors": [{"reason": "liveStreamingNotEnabled"}]}}))
    run(svc.service.check())
    check("live streaming off", "cannot go live yet" in svc.service.health()[1])
    FAKE.script(LIST, (500, {"error": {"message": "backend <error>"}}))
    run(svc.service.check())
    eq("other", svc.service.health(), ("warn", "YouTube did not answer as expected. Relay will try again."))
    run(svc.service.check())       # nothing scripted: cannot reach
    eq("unreachable", svc.service.health(), ("warn", "Could not reach YouTube."))
    eq("the status view shows the warning", entry()["health"], "warn")

with section("revoked: sign in again"):
    svc.ACCOUNT._tokens.expires_at = 1
    FAKE.script(P.token_url, (400, {"error": "invalid_grant"}))
    run(svc.service.check())
    eq("health", svc.service.health(), ("warn", "YouTube needs signing in again."))
    eq("module health", registry.get("youtube").health().state, "warn")
    eq("status view warns", entry()["health"], "warn")

# ------------------------------------------------------------------------------------------------ routes
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"youtube": {"enabled": True}}})
registry.attach("youtube")
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
A = "/api/admin/youtube"

with section("routes: read, settings, the account"):
    r = viewer.get(A)
    eq("a viewer reads", r.status_code, 200)
    body = r.json()
    eq("health in the read", body["health"]["level"], "warn")
    check("no secret in the read", "church-app-secret" not in r.text and "yt-refresh" not in r.text)
    eq("account state in the read", body["account"]["state"], "sign_in")
    eq("a viewer cannot change settings", viewer.put(A + "/settings", json={"live_s": 45}).status_code, 403)
    eq("a viewer cannot connect", viewer.post(A + "/account/connect").status_code, 403)
    eq("a viewer cannot check", viewer.post(A + "/check").status_code, 403)
    eq("a viewer reads the account", viewer.get(A + "/account").json()["account"], "Grace Church")

    r = admin.put(A + "/settings", json={"live_s": 45, "idle_s": 900, "client_id": "church.apps.googleusercontent.com",
                                         "client_secret": ""})
    eq("saved", (r.status_code, r.json()["settings"]["live_s"], r.json()["settings"]["idle_s"]), (200, 45, 900))
    eq("a blank secret keeps the saved one", svc.settings()["client_secret"], "church-app-secret")
    r = admin.put(A + "/settings", json={"client_id": "not-a-google-id"})
    eq("not a Google client id", (r.status_code, r.json()["error"]),
       (400, "That is not a Google client ID: it ends in .apps.googleusercontent.com."))
    r = admin.put(A + "/settings", json={"clear_client": True})
    eq("back to Relay's app", (svc.settings()["client_id"], svc.settings()["client_secret"]), ("", ""))
    eq("Relay's app is not there yet", r.json()["builtin"], False)
    r = admin.post(A + "/account/connect")
    eq("connect without any app: a sentence", (r.json()["ok"], r.json()["error"]),
       (False, "YouTube sign-in is not set up in this copy of Relay."))

    FAKE.script(P.revoke_url, (200, {}))
    r = admin.post(A + "/account/disconnect")
    eq("disconnected", r.json()["state"], "disconnected")
    eq("revoked the refresh token", FAKE.sent[-1][:2], (P.revoke_url, {"token": "yt-refresh"}))
    run(svc.service.check())
    eq("disconnected: off the status view", entry(), None)

with section("the module's connection test and stop"):
    res = run(registry.get("youtube").test())
    eq("not connected", res, {"ok": False, "detail": "Not connected. Press Connect on the YouTube page."})
    config.save({"youtube": {"client_id": "church.apps.googleusercontent.com", "client_secret": "church-app-secret"}})
    connect()
    FAKE.script(LIST, (200, {"items": [broadcast()]}))
    FAKE.script(f"{API}/videos", (200, {"items": []}))
    res = run(registry.get("youtube").test())
    eq("test checks now", res, {"ok": True, "detail": "Live: Sunday Worship"})
    check("published", entry() is not None)

    async def lifecycle():
        FAKE.script(LIST, *[(200, {"items": []})] * 5)
        await svc.service.start()
        await asyncio.sleep(0.05)
        await svc.service.stop()

    run(lifecycle())
    eq("stop clears the status view", entry(), None)
    check("stop ends the task", svc.service.task is None)

done()
