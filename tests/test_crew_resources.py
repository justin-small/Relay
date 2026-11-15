"""Crew resources per position (#233): files, links and embedded pages for a team or for everyone. Run:
python tests/test_crew_resources.py"""
import io
import os
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

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
from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.crew import calltimes, people, resources  # noqa: E402
from app.modules.planning_center.pclive import Track  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def office(folder):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr("[Content_Types].xml", "<x/>")
        z.writestr(folder + "document.xml", "<x/>")
    return b.getvalue()


PDF = b"%PDF-1.4\n" + b"x" * 100
PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 100

print("what a file is")
p = Path(TMP) / "t"
for label, data, want in (("pdf", PDF, "pdf"), ("png", PNG, "png"), ("jpeg", b"\xff\xd8\xff\xe0" + b"x" * 20, "jpg"),
                          ("text", "héllo\nworld".encode(), "txt"), ("word", office("word/"), "docx"),
                          ("excel", office("xl/"), "xlsx"), ("slides", office("ppt/"), "pptx"),
                          ("a zip that is no office file", office("other/"), None), ("an exe", b"MZ\x90\x00" + b"\x00" * 50, None),
                          ("html is just text", b"<html>", "txt"), ("random bytes", b"\xff\xfe\x00\x01", None)):
    p.write_bytes(data)
    eq(label, resources.classify(p, data[:8192]), want)
eq("an svg is text, served only as plain text", resources.TYPES["txt"][0], "text/plain; charset=utf-8")

print("addresses")
H = resources.DEFAULT_EMBED_HOSTS
for label, kind, url, ok in (("https link", "link", "https://example.com/guide", True), ("http link", "link", "http://example.com", True),
                             ("javascript", "link", "javascript:alert(1)", False), ("data", "link", "data:text/html,x", False),
                             ("credentials", "link", "https://a:b@example.com", False), ("no host", "link", "https:///x", False),
                             ("space", "link", "https://example.com/a b", False), ("an ip", "link", "https://10.0.0.1/x", False),
                             ("allowed embed", "embed", "https://docs.google.com/document/d/1/pub", True),
                             ("subdomain of an allowed host", "embed", "https://www.youtube.com/embed/abcdefghijk", True),
                             ("a host not allowed", "embed", "https://example.com/x", False),
                             ("a lookalike host", "embed", "https://notyoutube.com/x", False),
                             ("http embed", "embed", "http://docs.google.com/x", False)):
    try:
        resources.clean_url(url, kind, H)
        got = True
    except resources.Invalid:
        got = False
    eq(f"{label} is {'kept' if ok else 'refused'}", got, ok)
eq("a watch address becomes the embed", resources.clean_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=5", "embed", H),
   "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ")
eq("a short address too", resources.clean_url("https://youtu.be/dQw4w9WgXcQ", "embed", H), "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ")
eq("hosts are cleaned", resources.clean_hosts(["YouTube.com ", "bad host", 5, "youtube.com", "a.example.org"]), ["youtube.com", "a.example.org"])

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
B = "http://relay.local:8001"
admin = _db.client(app, base_url=B)
booth = _db.client(app, role="operator", base_url=B, username="booth")
mike_c = _db.client(app, role="member", base_url=B, username="mike")
sara_c = _db.client(app, role="member", base_url=B, username="sara")
lead_c = _db.client(app, role="member", base_url=B, username="lena")
mike, sara, lena = (users.get_by_username(n) for n in ("mike", "sara", "lena"))
ZONE = ZoneInfo("America/New_York")
SUN = datetime(2026, 11, 15, 8, 0, tzinfo=ZONE).timestamp()
tr = Track("7")
tr.plan = {"id": "700", "times": [{"id": "t1", "type": "service", "starts_at": SUN + 3600}]}
tr.team = [{"person": "501", "name": "Mike", "team": "Camera", "status": "confirmed", "times": None},
           {"person": "502", "name": "Sara", "team": "Audio", "status": "confirmed", "times": None}]
calltimes._state = lambda: ([tr], ZONE)
people.ready()
for uid, pid in ((mike.id, "501"), (sara.id, "502")):
    db.execute("insert into crew_profiles (user_id, pc_person, pc_name, updated_at) values (?, ?, ?, ?)"
               " on conflict (user_id) do update set pc_person = excluded.pc_person", (uid, pid, "x", db.now()))

print("the setting")
eq("defaults to the common sites", config.get()["crew"]["embed_hosts"], H)
r = admin.post("/api/admin/crew", json={"name": "Relay crew", "show_plan": True, "geofence": None, "embed_hosts": ["Vimeo.com", "x y"]})
eq("an admin saves the allow-list (cleaned)", (r.status_code, r.json()["embed_hosts"]), (200, ["vimeo.com"]))
eq("an old client that omits it keeps it", admin.post("/api/admin/crew", json={"name": "Relay crew", "show_plan": True}).json()["embed_hosts"], ["vimeo.com"])
eq("not a list is refused", admin.post("/api/admin/crew", json={"name": "A", "show_plan": True, "embed_hosts": "x"}).status_code, 400)
admin.post("/api/admin/crew", json={"name": "Relay crew", "show_plan": True, "embed_hosts": H + ["example.org"]})

print("adding")
eq("a plain member may not", mike_c.post("/crew/api/resources/link", json={"kind": "link", "title": "x", "position": "Camera", "url": "https://example.com"}).status_code, 403)
eq("nor upload", mike_c.post("/crew/api/resources/file?title=x&position=Camera", content=PDF).status_code, 403)
eq("nor list the editor", mike_c.get("/crew/api/resources/manage").status_code, 403)
r = booth.post("/crew/api/resources/link", json={"kind": "link", "title": "Camera guide", "position": "camera", "url": "https://example.com/cam"})
eq("an operator adds a link", r.status_code, 200)
r = booth.post("/crew/api/resources/link", json={"kind": "embed", "title": "Run of show", "position": "", "url": "https://docs.google.com/document/d/1/pub"})
eq("and an embed for everyone", r.status_code, 200)
eq("an embed from another host is refused", booth.post("/crew/api/resources/link", json={"kind": "embed", "title": "x", "position": "", "url": "https://evil.test/x"}).status_code, 400)
eq("a bad kind is refused", booth.post("/crew/api/resources/link", json={"kind": "file", "title": "x", "url": "https://example.com"}).status_code, 400)
eq("no title is refused", booth.post("/crew/api/resources/link", json={"kind": "link", "title": " ", "url": "https://example.com"}).status_code, 400)
r = booth.post("/crew/api/resources/file?title=Mic%20chart&position=Audio", content=PDF)
eq("an operator uploads a file", r.status_code, 200)
audio_file = [x for x in r.json()["resources"] if x["kind"] == "file"][0]["id"]
eq("a disallowed type is refused", booth.post("/crew/api/resources/file?title=x&position=Audio", content=b"MZ\x90\x00" + b"\x00" * 50).status_code, 415)
eq("an empty upload is refused", booth.post("/crew/api/resources/file?title=x&position=Audio", content=b"").status_code, 415)
resources.FILE_LIMIT = 1000
eq("a file over the cap is refused", booth.post("/crew/api/resources/file?title=x&position=Audio", content=PDF + b"x" * 2000).status_code, 413)
resources.FILE_LIMIT = 25 * 1024 * 1024
eq("nothing stray is left behind", sorted(n for n in os.listdir(resources.folder()) if n.startswith(".")), [])
stored = db.one("select stored from crew_resources where id = ?", (audio_file,))["stored"]
eq("stored under a random name, not the title", (len(stored), "Mic" in stored, stored.endswith(".pdf")), (36, False, True))

print("team leads")
booth.post("/api/admin/crew/checklist-leads", json={"user_id": lena.id, "position": "Audio"})
eq("a lead sees only their position's", [r["title"] for r in lead_c.get("/crew/api/resources/manage").json()["resources"]], ["Mic chart"])
eq("adds for their position", lead_c.post("/crew/api/resources/link", json={"kind": "link", "title": "Audio wiki", "position": "AUDIO", "url": "https://example.com/a"}).status_code, 200)
eq("not for another", lead_c.post("/crew/api/resources/link", json={"kind": "link", "title": "x", "position": "Camera", "url": "https://example.com"}).status_code, 403)
eq("not for everyone", lead_c.post("/crew/api/resources/link", json={"kind": "link", "title": "x", "position": "", "url": "https://example.com"}).status_code, 403)
eq("no upload to another position, nothing read", lead_c.post("/crew/api/resources/file?title=x&position=Camera", content=PDF).status_code, 403)
cam = db.one("select id from crew_resources where title = 'Camera guide'")["id"]
eq("not to change another's", lead_c.put(f"/crew/api/resources/{cam}", json={"title": "x", "position": "Camera", "url": "https://example.com"}).status_code, 403)
eq("not to delete another's", lead_c.delete(f"/crew/api/resources/{cam}").status_code, 403)
wiki = db.one("select id from crew_resources where title = 'Audio wiki'")["id"]
eq("not to move their own onto another", lead_c.put(f"/crew/api/resources/{wiki}", json={"title": "Audio wiki", "position": "Camera", "url": "https://example.com/a"}).status_code, 403)
eq("to change their own", lead_c.put(f"/crew/api/resources/{wiki}", json={"title": "Audio notes", "position": "Audio", "url": "https://example.com/b"}).status_code, 200)

print("what a person sees")
r = mike_c.get("/crew/api/resources").json()
eq("Mike gets the camera link and the everyone embed", sorted(x["title"] for x in r["resources"]), ["Camera guide", "Run of show"])
eq("not an editor", r["editor"], False)
eq("Sara gets audio and everyone", sorted(x["title"] for x in sara_c.get("/crew/api/resources").json()["resources"]), ["Audio notes", "Mic chart", "Run of show"])
eq("an unmatched lead gets only everyone's", [x["title"] for x in lead_c.get("/crew/api/resources").json()["resources"]], ["Run of show"])

print("files")
r = sara_c.get(f"/crew/api/resources/{audio_file}/file")
eq("the right team gets the file", (r.status_code, r.content[:5], r.headers["content-type"], r.headers["x-content-type-options"]), (200, b"%PDF-", "application/pdf", "nosniff"))
eq("shown in the browser", r.headers["content-disposition"].startswith("inline"), True)
eq("another team does not (it is not there for them)", mike_c.get(f"/crew/api/resources/{audio_file}/file").status_code, 404)
eq("a link has no file", mike_c.get(f"/crew/api/resources/{cam}/file").status_code, 404)
eq("a lead of the position may", lead_c.get(f"/crew/api/resources/{audio_file}/file").status_code, 200)
eq("a missing id", mike_c.get("/crew/api/resources/9999/file").status_code, 404)
png = booth.post("/crew/api/resources/file?title=Map&position=", content=PNG).json()["resources"]
pid = [x for x in png if x["title"] == "Map"][0]["id"]
r = mike_c.get(f"/crew/api/resources/{pid}/file")
eq("a picture is sandboxed", (r.status_code, "sandbox" in r.headers["content-security-policy"]), (200, True))
docx = booth.post("/crew/api/resources/file?title=Plan&position=", content=office("word/")).json()["resources"]
did = [x for x in docx if x["title"] == "Plan"][0]["id"]
eq("an Office file downloads", mike_c.get(f"/crew/api/resources/{did}/file").headers["content-disposition"].startswith("attachment"), True)
eq("a hand-edited stored name never leaves the folder", resources.file_path({"stored": "../../etc/passwd"}), None)

print("removing")
n = len(os.listdir(resources.folder()))
eq("an operator deletes a file and it leaves the disk", (booth.delete(f"/crew/api/resources/{audio_file}").status_code, len(os.listdir(resources.folder()))), (200, n - 1))
eq("a missing resource is 404", booth.delete("/crew/api/resources/9999").status_code, 404)

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
