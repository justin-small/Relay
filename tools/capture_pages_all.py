"""Visit every page of the Python server with every module on, so `capture_pages.py` records its renders (#654).

Run by `capture_pages.py` under the render hook (it is not a test). A throwaway config and database, an Owner signed in on the
panel port, every module switched on; then each GET route that needs no id is requested on the panel and on the viewer port.
Pages that need an id (a room, a screen) are visited with ids this script creates where a module has a seed helper below,
and are otherwise left to the tests that already render them.
"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config, db, users  # noqa: E402
from app.main import app, module_registry  # noqa: E402

users.SCRYPT_N = 2 ** 10
ADMIN = "http://relay.local:8001"
VIEWER = "http://relay.local:8000"

config.ensure_file()
config.save({"port": 8000, "admin_port": 8001,
             "blocklist_file": str(Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt"),
             "modules": {m.manifest.id: {"enabled": True} for m in module_registry.all()}})
db.init()

owner = _db.client(app, "owner", base_url=ADMIN)
from fastapi.testclient import TestClient  # noqa: E402

viewer = TestClient(app, base_url=VIEWER)


def flat(routes):
    """Every route, with the ones a module added (FastAPI keeps them inside an included-router object) unpacked."""
    for r in routes:
        if type(r).__name__ == "_IncludedRouter":
            yield from flat(r.original_router.routes)
        elif hasattr(r, "path"):
            yield r


paths = sorted({r.path for r in flat(app.routes) if "GET" in getattr(r, "methods", ()) and "{" not in r.path
                and not r.path.startswith(("/static", "/api", "/ws", "/events"))})
seen = 0
for client in (owner, viewer):
    for path in paths:
        try:
            r = client.get(path, follow_redirects=False)
        except Exception as e:                     # a page that needs hardware or a service
            sys.stderr.write(f"visit: {path}: {type(e).__name__}\n")
            continue
        seen += r.status_code == 200

# Pages no route of a test server reaches (they need a live board, a failed database, a running NDI source ...): rendered by the
# app's own template environment from contexts shaped like the ones their handlers build.
from app.main import templates  # noqa: E402

VIEWER_CTX = {"font_px": 28, "source_language": "en", "source_label": "English", "source_iso": "en-US", "present": False}
BOARD = {"id": "b1", "name": "Front desk", "ratio": "16:9", "cols": 12, "rows": 6, "public": False, "rev": "r1",
         "pages": [{"id": "p1", "name": "Main", "items": []}]}
for name, ctx in (
    ("db_down.html", {"message": "Relay's database is not answering."}),
    ("password_change.html", {"error": None, "voluntary": False}),
    ("password_change.html", {"error": "That is not the same twice.", "voluntary": True}),
    ("board.html", {"data": {"board": BOARD, "press": ["link"], "state": {}}, "title": "Front desk"}),
    ("rundown.html", VIEWER_CTX | {"rundown_view": "full", "rundown_layout": "fit", "rundown_cards": "person",
                                   "rundown_team": "Band & choir", "rundown_time": "0930", "rundown_type": "2",
                                   "rundown_captions": ""}),
    ("rundown.html", VIEWER_CTX | {"rundown_view": "side", "rundown_layout": "compact", "rundown_cards": "position",
                                   "rundown_team": "", "rundown_time": "", "rundown_type": "",
                                   "rundown_captions": "/screen?lang=en"}),
    ("crew/notification.html", {"app_name": "Relay", "found": True, "message": "Doors open <now> & 'soon'", "sender": "Sam",
                                "sent_ts": 1700000000, "confirmed": False}),
    ("crew/notification.html", {"app_name": "Relay", "found": False, "message": "", "sender": "", "sent_ts": 0,
                                "confirmed": False}),
    ("ndi/view.html", {"data": {"source": "CAM1 (Mac)", "title": "Camera 1", "machine": "Mac", "camera": 2}}),
    ("propresenter/stage.html", {}),
):
    templates.env.get_template(name).render(ctx)

print(f"visited {len(paths)} paths on two ports, {seen} answered 200")
