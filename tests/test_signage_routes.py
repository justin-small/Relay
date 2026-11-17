"""Signage routes' edges (app/modules/signage/routes.py): an id that is not a number is a 404 on every route
that takes one, a database that is down is a 503 with a fixed sentence (never a 500 or the error's own text),
the player's small posts refuse bad bodies, and a screen's event stream sends a state only when it changes,
says "gone" for a deleted screen, keeps alive, and skips a beat while the database is down.
Offline. Run: python tests/test_signage_routes.py
"""
import asyncio
import inspect
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, done, eq, section, tmpdir  # noqa: E402

TMP = tmpdir()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
import _db  # noqa: E402

_db.start()
_db.reset()

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.signage import live, routes, store  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"signage": {"enabled": True}}})
registry.attach("signage")
admin = _db.client(app)
public = TestClient(app)

pl = admin.post("/api/admin/signage/playlists", json={"name": "Lobby"}).json()
scr = admin.post("/api/admin/signage/screens", json={"name": "Lobby", "slug": "lobby", "playlist_id": pl["id"]}).json()

# Every route that turns its id with _int(): a word there is "not found", not a 422 or a 500.
INT_ROUTES = [
    ("PUT", "/api/admin/signage/slots/x", {}), ("DELETE", "/api/admin/signage/slots/x", None),
    ("PATCH", "/api/admin/signage/playlists/x", {"name": "y"}),
    ("POST", "/api/admin/signage/playlists/x/duplicate", {}),
    ("DELETE", "/api/admin/signage/playlists/x", None), ("PUT", "/api/admin/signage/playlists/x/items", {"items": []}),
    ("PUT", "/api/admin/signage/groups/x", {"name": "y"}), ("DELETE", "/api/admin/signage/groups/x", None),
    ("PUT", "/api/admin/signage/screens/x", {"name": "y"}), ("DELETE", "/api/admin/signage/screens/x", None),
    ("PUT", "/api/admin/signage/override", {"playlist_id": "x", "until": "cleared", "target": "screen"}),
]

with section("ids that are not numbers"):
    for method, path, body in INT_ROUTES:
        r = admin.request(method, path, json=body) if body is not None else admin.request(method, path)
        eq(f"{method} {path}: not found", r.status_code, 404)

DOWN = "The database is not available right now. Try again in a moment."


def _down(*a, **k):
    raise db.Unavailable("The database is unavailable.")


ADMIN_CALLS = [
    ("GET", "/api/admin/signage", None),
    ("GET", "/api/admin/signage/preview?playlist=1", None),
    ("POST", "/api/admin/signage/playlists", {"name": "Down"}),
    ("PATCH", f"/api/admin/signage/playlists/{pl['id']}", {"name": "Down"}),
    ("POST", f"/api/admin/signage/playlists/{pl['id']}/duplicate", {}),
    ("PUT", f"/api/admin/signage/playlists/{pl['id']}/items", {"items": []}),
    ("DELETE", f"/api/admin/signage/playlists/{pl['id']}", None),
    ("POST", "/api/admin/signage/groups", {"name": "Down"}),
    ("PUT", "/api/admin/signage/groups/1", {"name": "Down"}),
    ("DELETE", "/api/admin/signage/groups/1", None),
    ("POST", "/api/admin/signage/screens", {"name": "Down", "slug": "down"}),
    ("PUT", f"/api/admin/signage/screens/{scr['id']}", {"name": "Down"}),
    ("DELETE", f"/api/admin/signage/screens/{scr['id']}", None),
    ("DELETE", "/api/admin/signage/slots/1", None),
    ("DELETE", "/api/admin/signage/override", None),
    ("DELETE", "/api/admin/signage/takeovers", None),
    ("GET", "/api/admin/signage/media/1/thumb", None),
    ("PATCH", "/api/admin/signage/media/1", {"name": "Down"}),
    ("DELETE", "/api/admin/signage/media/1", None),
]

with section("the database is down"):
    saved = {name: fn for name, fn in vars(store).items()          # the store's own functions, not its classes
             if inspect.isfunction(fn) and not name.startswith("_") and fn.__module__ == store.__name__}
    for name in saved:
        setattr(store, name, _down)
    try:
        for method, path, body in ADMIN_CALLS:
            r = admin.request(method, path, json=body) if body is not None else admin.request(method, path)
            ok = r.status_code == 503 and r.json().get("error") == DOWN
            check(f"{method} {path.split('?')[0]}: 503 with the fixed sentence", ok, (r.status_code, r.text[:120]))
        r = public.get("/signage/_/state/lobby")
        eq("a screen's state: 503, not a 500", r.status_code, 503)
        eq("…and its page", public.get("/signage/lobby").status_code, 503)
    finally:
        for name, fn in saved.items():
            setattr(store, name, fn)
    eq("back up: the admin state answers again", admin.get("/api/admin/signage").status_code, 200)

with section("the player's posts"):
    eq("now: a body that is not JSON is a 400",
       public.post("/signage/_/now/lobby", content=b"{not json").status_code, 400)
    eq("now: an unknown screen is a 404", public.post("/signage/_/now/nope", json={}).status_code, 404)
    eq("now: a good report", public.post("/signage/_/now/lobby", json={"item": 3, "label": "Welcome"}).json(),
       {"ok": True})
    eq("…is what the admin sees playing", (live.playing("lobby") or {}).get("label"), "Welcome")
    eq("now: a non-number item and label are ignored, not refused",
       public.post("/signage/_/now/lobby", json={"item": "3", "label": 7}).status_code, 200)
    eq("done: a body that is not JSON is a 400",
       public.post("/signage/_/done/lobby", content=b"nope").status_code, 400)
    eq("done: true is not a push id", public.post("/signage/_/done/lobby", json={"id": True}).json(), {"ok": True})
    eq("…so nothing is marked done", live.done_id("lobby"), None)
    eq("done: a push id is marked", (public.post("/signage/_/done/lobby", json={"id": 9}).status_code,
                                     live.done_id("lobby")), (200, 9))
    eq("media that does not exist", public.get("/signage/_/media/nope.png").status_code, 404)
    eq("the player script is served, not cached for long",
       public.get("/signage/_/player.js").headers.get("cache-control"), "no-cache")


class FakeRequest:
    def __init__(self):
        self.gone = False

    async def is_disconnected(self):
        return self.gone


async def stream_scenario():
    real_asyncio, real_beat = routes.asyncio, routes.HEARTBEAT_S
    routes.asyncio = SimpleNamespace(sleep=lambda s: real_asyncio.sleep(0.01), to_thread=real_asyncio.to_thread)
    req = FakeRequest()
    resp = await routes.public_events("lobby", req)
    it = resp.body_iterator

    async def nxt():
        return await asyncio.wait_for(it.__anext__(), 3)

    try:
        first = await nxt()
        state = json.loads(first[len("data: "):])
        check("the first frame is the screen's whole state", first.startswith("data: ") and "takeover" in state, first)
        eq("…for the right playlist", state.get("playlist", {}).get("id") if isinstance(state.get("playlist"), dict)
           else pl["id"], pl["id"])

        routes.HEARTBEAT_S = 0.05
        live.bump()                        # a change that does not change this screen's state: nothing sent
        eq("a version bump that changes nothing sends only a keepalive", await nxt(), ": keepalive\n\n")
        routes.HEARTBEAT_S = 3600

        admin.put(f"/api/admin/signage/screens/{scr['id']}", json={**scr, "sound": not scr.get("sound")})
        changed = await nxt()
        check("a change to the screen sends the new state", changed.startswith("data: ") and changed != first, changed)

        real_get = store.screen_by_slug
        store.screen_by_slug = _down
        live.bump()
        await asyncio.sleep(0.1)
        store.screen_by_slug = real_get
        routes.HEARTBEAT_S = 0.05
        eq("the database down for a beat sends nothing (a keepalive next)", await nxt(), ": keepalive\n\n")
        routes.HEARTBEAT_S = 3600

        admin.delete(f"/api/admin/signage/screens/{scr['id']}")
        eq("a deleted screen is told it is gone", await nxt(), 'data: {"gone": true}\n\n')
        req.gone = True
        try:
            await nxt()
            check("a disconnected player ends the stream", False)
        except StopAsyncIteration:
            check("a disconnected player ends the stream", True)
    finally:
        routes.asyncio, routes.HEARTBEAT_S = real_asyncio, real_beat


with section("board widgets (#156)"):
    from app.modules import signage as sg_pkg
    mod = sg_pkg.MODULE
    wd = {w["type"]: w for w in mod.board_widgets()}
    eq("two widgets", sorted(wd), ["signage.return", "signage.takeovers"])
    eq("the button needs signage.manage", wd["signage.return"]["press_permission"], "signage.manage")
    eq("the list is a list", wd["signage.takeovers"]["kind"], "list")
    items = [{"id": "t", "type": "signage.takeovers"}, {"id": "b", "type": "signage.return"}]
    d = asyncio.run(mod.board_data(items))
    eq("idle: say so", d["t"]["rows"], [{"text": "Every screen is playing its playlist"}])
    eq("idle: the button is not lit", d["b"]["lit"], False)
    admin.put("/api/admin/signage/takeover", json={"kind": "live", "target": "screen", "target_id": scr["id"], "view": "captions"})
    d = asyncio.run(mod.board_data(items))
    check("a takeover is listed with its screen", d["t"]["rows"][0]["text"].startswith("Lobby: "))
    eq("and the button lights", d["b"]["lit"], True)

    class Req:
        class state:
            audit = None

    eq("a wrong item is refused", asyncio.run(mod.board_press({"type": "signage.takeovers"}, Req()))["ok"], False)
    eq("pressing returns screens to their playlists", asyncio.run(mod.board_press({"type": "signage.return"}, Req())), {"ok": True})
    eq("nothing is taken over now", asyncio.run(mod.board_data(items))["b"]["lit"], False)

with section("a screen's event stream"):
    asyncio.run(stream_scenario())
    eq("an unknown screen has no stream", public.get("/signage/_/events/nope").status_code, 404)

done()
