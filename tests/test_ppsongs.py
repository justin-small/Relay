"""Songs into ProPresenter (#497): a request becomes `.pro` files, the helper is asked to open them, a missing helper
or a bad request is answered with a fixed sentence, and a `.pro` file can be read back. The helper is a real websocket
client speaking the helper protocol (the helper program itself has its own tests).
Run: python tests/test_ppsongs.py
"""
import base64
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, str(HERE))
import _db  # noqa: E402

_db.start()
_db.reset()

import app.main as main  # noqa: E402,F401
from app import config, helperhub  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.propresenter import ppfile, ppsongs  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"propresenter": {"enabled": True}},
             "helpers": {}}, replace=("helpers",))
_modules.attach("propresenter")

SONG = {"name": "Amazing Grace", "ccli_number": 22025, "author": "John Newton", "music_key": "G",
        "sections": [{"name": "Verse 1", "slides": [{"text": "Amazing grace, how sweet the sound", "translation": "Gracia sublime",
                                                      "chords": [[0, "G"]], "notes": "[relay: look Worship]", "label": "Open"},
                                                     "That saved a wretch like me"]},
                     {"name": "Chorus", "slides": ["Sing it out"]}],
        "arrangements": [{"name": "Default", "sections": ["Verse 1", "Chorus", "Verse 1"]}]}

with section("a request becomes files"):
    files = ppsongs.build_files([SONG, dict(SONG), {**SONG, "name": "a/b:c*?"}])
    eq("one file per song, named for the song", [f["name"] for f in files], ["Amazing Grace.pro", "Amazing Grace 2.pro", "abc.pro"])
    back = ppfile.parse_song(base64.b64decode(files[0]["data_b64"]))
    eq("the song survives", (back.name, back.ccli_number, back.author, back.music_key), ("Amazing Grace", 22025, "John Newton", "G"))
    first = back.sections[0].slides[0]
    eq("with its chords, notes, label and translation", (first.chords, first.notes, first.label, first.translation),
       ([(0, "G")], "[relay: look Worship]", "Open", "Gracia sublime"))
    eq("and its arrangement", back.arrangements, [("Default", ["Verse 1", "Chorus", "Verse 1"])])
    eq("a file name is never a path", ppsongs.ppfile_name("../../etc/passwd", set()), "etcpasswd.pro")
    eq("an empty title still gets a name", ppsongs.ppfile_name("***", set()), "Song.pro")

with section("a bad request is refused with a sentence"):
    def refused(label, songs, text=None):
        try:
            ppsongs.build_files(songs)
            check(label, False)
        except ppsongs.SongError as exc:
            check(label + ": " + exc.message, text is None or text in exc.message)

    refused("nothing to send", [], "at least one")
    refused("not a list", "x", "at least one")
    refused("too many", [SONG] * 51, "At most")
    refused("a song that is not an object", ["x"], "object")
    refused("no name", [{"sections": [{"name": "V", "slides": ["x"]}]}], "name")
    refused("no slides", [{"name": "X", "sections": []}], "slide")
    refused("too many slides", [{"name": "X", "sections": [{"name": "V", "slides": ["x"] * 301}]}], "at most")
    refused("a bad colour", [{"name": "X", "background": [2, 0, 0], "sections": [{"name": "V", "slides": ["x"]}]}], "colour")
    refused("a bad key", [{"name": "X", "music_key": "H", "sections": [{"name": "V", "slides": ["x"]}]}], "musical key")
    refused("a bad slide action", [{"name": "X", "sections": [{"name": "V", "slides": [{"text": "x", "actions": [{"kind": "nope"}]}]}]}], "Unknown")
    refused("a bad number", [{"name": "X", "ccli_number": "abc", "sections": [{"name": "V", "slides": ["x"]}]}], "number")

with section("reading a file back"):
    data_b64 = files[0]["data_b64"]
    got = ppsongs.read_song(data_b64)
    eq("name and CCLI", (got["name"], got["ccli_number"]), ("Amazing Grace", 22025))
    eq("sections with their text", [(s["name"], [x["text"] for x in s["slides"]]) for s in got["sections"]],
       [("Verse 1", ["Amazing grace, how sweet the sound", "That saved a wretch like me"]), ("Chorus", ["Sing it out"])])
    eq("arrangements as objects", got["arrangements"], [{"name": "Default", "sections": ["Verse 1", "Chorus", "Verse 1"]}])
    for bad in ("!!!", "", base64.b64encode(b"not a song \xff").decode()):
        try:
            ppsongs.read_song(bad)
            check(f"{bad[:8]!r} refused", False)
        except ppsongs.SongError:
            check(f"{bad[:8]!r} refused", True)

with section("the routes"):
    admin = _db.client(app, base_url="http://relay.local:8001")
    operator = _db.client(app, role="operator", username="operator1", base_url="http://relay.local:8001")
    viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
    state = admin.get("/api/admin/propresenter/songs/helper").json()
    eq("no helper yet", (state["connected"], state["paired"]), (False, 0))
    r = admin.post("/api/admin/propresenter/songs/send", json={"songs": [SONG]})
    eq("sending with no helper says so", (r.status_code, "helper" in r.json()["detail"].lower()), (409, True))
    eq("a bad request is a 400", admin.post("/api/admin/propresenter/songs/send", json={"songs": []}).status_code, 400)
    eq("an operator cannot send", operator.post("/api/admin/propresenter/songs/send", json={"songs": [SONG]}).status_code, 403)
    eq("a viewer can read a file", viewer.post("/api/admin/propresenter/songs/read", json={"data_b64": data_b64}).status_code, 200)
    eq("a bad file is a 400", viewer.post("/api/admin/propresenter/songs/read", json={"data_b64": "!!"}).status_code, 400)

    code = helperhub.hub.make_code()["code"]
    admin.__enter__()           # one event loop for every request, so the websocket and the route share it
    with admin.websocket_connect("/ws/helper") as ws:
        ws.send_text(json.dumps({"type": "pair", "code": code, "name": "FOH Mac", "os": "macos", "version": "0.1.0"}))
        paired = json.loads(ws.receive_text())
    with admin.websocket_connect("/ws/helper") as ws:
        ws.send_text(json.dumps({"type": "hello", "helper_id": paired["helper_id"], "token": paired["token"], "name": "FOH Mac",
                                 "os": "macos", "version": "0.1.0",
                                 "capabilities": [{"id": "propresenter", "version": 1, "ops": ["status", "open_files", "api"]}]}))
        eq("welcomed", json.loads(ws.receive_text())["type"], "welcome")
        state = admin.get("/api/admin/propresenter/songs/helper").json()
        eq("the helper is seen", (state["connected"], state["helper"]), (True, "FOH Mac"))

        answers = {}

        def post():
            answers["r"] = admin.post("/api/admin/propresenter/songs/send", json={"songs": [SONG, {**SONG, "name": "Second"}]})
        t = threading.Thread(target=post)
        t.start()
        call = json.loads(ws.receive_text())
        eq("Relay asks the helper to open the files", (call["type"], call["cap"], call["op"]), ("call", "propresenter", "open_files"))
        names = [f["name"] for f in call["args"]["files"]]
        eq("with a file per song", names, ["Amazing Grace.pro", "Second.pro"])
        check("each a real song file", all(ppfile.parse_song(base64.b64decode(f["data_b64"])).name for f in call["args"]["files"]))
        ws.send_text(json.dumps({"type": "result", "id": call["id"], "ok": True, "data": {"opened": 2}}))
        t.join(10)
        eq("the route answers", (answers["r"].status_code, answers["r"].json()["sent"], answers["r"].json()["opened"]), (200, 2, 2))

        t = threading.Thread(target=post)
        t.start()
        call = json.loads(ws.receive_text())
        ws.send_text(json.dumps({"type": "result", "id": call["id"], "ok": False, "error": "ProPresenter would not open the files (is it installed?)."}))
        t.join(10)
        eq("a helper's failure comes back as its sentence", (answers["r"].status_code, answers["r"].json()["detail"]),
           (409, "ProPresenter would not open the files (is it installed?)."))
    admin.__exit__(None, None, None)
    eq("the helper going away is seen", admin.get("/api/admin/propresenter/songs/helper").json()["connected"], False)

done()
