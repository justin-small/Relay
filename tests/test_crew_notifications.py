"""Crew pages that buzz until read (#228): web push built on cryptography, the re-fire schedule, each person's
confirm link, read receipts, and the push-service allowlist. Run: python tests/test_crew_notifications.py"""
import base64
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
from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules  # noqa: E402
from app.modules.crew import MODULE, notifications, people, webpush  # noqa: E402
from app.modules.planning_center.pclive import Track, follower  # noqa: E402
from cryptography.hazmat.primitives import hashes  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import eq, fails  # noqa: E402


print("encryption (RFC 8291 appendix A)")
key = ec.derive_private_key(int.from_bytes(webpush.b64d("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big"),
                            ec.SECP256R1())
body = webpush.encrypt(b"When I grow up, I want to be a watermelon",
                       webpush.b64d("BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4"),
                       webpush.b64d("BTBZMqHH6r4Tts7J_aSIgg"), as_key=key, salt=webpush.b64d("DGv6ra1nlYgDCS1FRnbzlw"))
eq("the body is the one the RFC gives", webpush.b64e(body),
   "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A"
   "_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN")

print("VAPID")
vk = webpush.new_key()
h = webpush.vapid_header(vk, "https://fcm.googleapis.com/fcm/send/abc", now=1000)
t, k = h[len("vapid t="):].split(", k=")
head, claims, sig = t.split(".")
raw = webpush.b64d(sig)
vk.public_key().verify(__import__("cryptography.hazmat.primitives.asymmetric.utils", fromlist=["x"]).encode_dss_signature(
    int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")), f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
eq("the signature verifies with the public key it names", k, webpush.b64e(webpush.public_bytes(vk)))
eq("it names the push service and lasts under a day",
   __import__("json").loads(webpush.b64d(claims)), {"aud": "https://fcm.googleapis.com", "exp": 1000 + 43200,
                                                    "sub": webpush.SUBJECT})

print("only browsers' push services")
for url, want in (("https://fcm.googleapis.com/fcm/send/x", True), ("https://updates.push.services.mozilla.com/wpush/v2/x", True),
                  ("https://web.push.apple.com/x", True), ("https://wns2-par02p.notify.windows.com/x", True),
                  ("http://fcm.googleapis.com/x", False), ("https://evil.example/x", False),
                  ("https://fcm.googleapis.com.evil.example/x", False), ("https://127.0.0.1/x", False),
                  ("https://user@fcm.googleapis.com/x", False), ("https://fcm.googleapis.com:8443/x", False), (5, False)):
    eq(f"{url}", webpush.allowed(url), want)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"crew": {"enabled": True}, "planning_center": {"enabled": True}}})
modules.attach("planning_center")        # what the page asks Planning Center for, it asks by name (#523)
admin = _db.client(app, base_url="http://relay.local:8001")
operator = _db.client(app, role="operator", base_url="http://relay.local:8001", username="booth")
viewer = _db.client(app, role="viewer", base_url="http://relay.local:8001", username="watcher")
mike_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
sara_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="sara")
anon = TestClient(app, base_url="http://relay.local:8001")
viewer_port = TestClient(app, base_url="http://relay.local:8000")
mike, sara = users.get_by_username("mike"), users.get_by_username("sara")
users.update(mike.id, name="Mike Smith", actor=None)
users.update(sara.id, name="Sara Lee", actor=None)


def phone(prefix="x"):
    k = webpush.new_key()
    return {"endpoint": f"https://fcm.googleapis.com/fcm/send/{prefix}",
            "keys": {"p256dh": webpush.b64e(webpush.public_bytes(k)), "auth": webpush.b64e(b"0123456789abcdef")}}, k


print("phones")
eq("a member reads the public key", len(webpush.b64d(mike_c.get("/crew/api/push/key").json()["key"])), 65)
eq("the key is the same each time", mike_c.get("/crew/api/push/key").json() == mike_c.get("/crew/api/push/key").json(), True)
eq("nothing on at first", mike_c.get("/crew/api/push").json(), {"enabled": False})
sub_m, _ = phone("mike1")
eq("subscribe", mike_c.post("/crew/api/push", json=sub_m).json(), {"enabled": True})
eq("a foreign address is refused", mike_c.post("/crew/api/push", json={**sub_m, "endpoint": "https://evil.example/x"}).status_code, 400)
eq("bad keys are refused", mike_c.post("/crew/api/push", json={"endpoint": sub_m["endpoint"], "keys": {"p256dh": "AA", "auth": "AA"}}).status_code, 400)
eq("signed out cannot", anon.get("/crew/api/push/key").status_code, 401)
eq("the viewer port has none of it", viewer_port.get("/crew/api/push/key").status_code, 404)
sub_s, _ = phone("sara1")
sara_c.post("/crew/api/push", json=sub_s)

sent = []                                          # (endpoint, status to answer)
answers = {}


def fake_post(endpoint, headers, data):
    sent.append((endpoint, headers, data))
    return answers.get(endpoint, 201)


webpush.post = fake_post

print("sending")
eq("a member may not page", mike_c.post("/api/admin/crew/notifications", json={"message": "hi", "users": [sara.id]}).status_code, 403)
eq("a viewer may not", viewer.post("/api/admin/crew/notifications", json={"message": "hi", "users": [sara.id]}).status_code, 403)
eq("empty words refused", operator.post("/api/admin/crew/notifications", json={"message": "  ", "users": [sara.id]}).status_code, 400)
eq("too long refused", operator.post("/api/admin/crew/notifications", json={"message": "x" * 141, "users": [sara.id]}).status_code, 400)
eq("nobody chosen refused", operator.post("/api/admin/crew/notifications", json={"message": "hi", "users": []}).status_code, 400)

print("the schedule")
T0 = 1_000_000.0
pid = notifications.create("Camera 1 to the stage", None, [mike.id, sara.id], now=T0)
fired = []
for at in (0, 0.5, 4.9, 5, 9.9, 10, 19, 20, 39, 40, 69, 70, 119, 120, 121, 500):
    n = notifications.tick(T0 + at)
    fired.append((at, n))
eq("front-loaded: pushes at 0, 5, 10, 20, 40, 70 and 120 seconds, then never", [a for a, n in fired if n],
   [0, 5, 10, 20, 40, 70, 120])
eq("each push reached both phones", len(sent), 14)
eq("every push is encrypted for that phone, with the VAPID header",
   all(h["Content-Encoding"] == "aes128gcm" and h["Authorization"].startswith("vapid t=") and h["TTL"] == "120"
       for _, h, _ in sent), True)
notifications.pending = True
eq("a page two minutes old is finished", notifications.tick(T0 + 130), 0)

print("confirming")
sent.clear()
T1 = 2_000_000.0
pid2 = notifications.create("Mic check", None, [mike.id, sara.id], now=T1)
notifications.tick(T1)
tokens = {r["user_id"]: r["token"] for r in db.query("select user_id, token from crew_notification_recipients where notification_id = ?", (pid2,))}
r = anon.get(f"/crew/notification/{tokens[mike.id]}")
eq("the link opens with no sign-in and shows the words", (r.status_code, "Mic check" in r.text), (200, True))
eq("the viewer port refuses the link", viewer_port.get(f"/crew/notification/{tokens[mike.id]}").status_code, 404)
eq("a made-up link is not found", anon.get("/crew/notification/" + "a" * 32).status_code, 404)
eq("a short one too", anon.get("/crew/notification/abc").status_code, 404)
r = anon.post(f"/crew/notification/{tokens[mike.id]}")
eq("confirming says so", (r.status_code, "Confirmed" in r.text), (200, True))
sent.clear()
notifications.tick(T1 + 5)
eq("only the one who has not confirmed is buzzed again", len(sent), 1)
eq("confirming twice is harmless", anon.post(f"/crew/notification/{tokens[mike.id]}").status_code, 200)
got = operator.get("/api/admin/crew/notifications").json()
page = [p for p in got["notifications"] if p["id"] == pid2][0]
eq("receipts by name", sorted((w["name"], w["state"]) for w in page["people"]), [("Mike Smith", "confirmed"), ("Sara Lee", "waiting")])
eq("no token ever reaches the admin page", any(t in str(got) for t in tokens.values()), False)

print("a message is shown as text")
pid3 = notifications.create("<script>alert(1)</script>", None, [sara.id], now=T1)
tok = db.one("select token from crew_notification_recipients where notification_id = ?", (pid3,))["token"]
txt = anon.get(f"/crew/notification/{tok}").text
eq("escaped on the confirm page", ("<script>alert(1)</script>" in txt, "&lt;script&gt;" in txt), (False, True))

print("a person with no phone, and a dead one")
sent.clear()
users_no = _db.client(app, role="member", base_url="http://relay.local:8001", username="nophone")
nop = users.get_by_username("nophone")
pid4 = notifications.create("Anyone?", None, [nop.id], now=T1 + 1000)
notifications.tick(T1 + 1000)
st = [w["state"] for p in operator.get("/api/admin/crew/notifications").json()["notifications"] if p["id"] == pid4 for w in p["people"]]
eq("shown as having no phone", st, ["no_phone"])
eq("and nothing was sent", sent, [])
answers[sub_s["endpoint"]] = 410
pid5 = notifications.create("Gone?", None, [sara.id], now=T1 + 2000)
notifications.tick(T1 + 2000)
eq("a 410 removes that subscription", sara_c.get("/crew/api/push").json(), {"enabled": False})

print("teams from Planning Center")
tr = follower.tracks.setdefault("7", Track("7"))
tr.team = [{"person": "501", "name": "Michael Smith", "team": "Camera", "status": "confirmed"},
           {"person": "502", "name": "Sara Lee", "team": "Camera", "status": "declined"},
           {"person": "900", "name": "No Account", "team": "Camera", "status": "confirmed"},
           {"person": "502", "name": "Sara Lee", "team": "Audio", "status": "confirmed"}]
MODULE._seen = ""
MODULE._scan()                                      # matches Mike and Sara to 501 and 502 (#227)
tg = operator.get("/api/admin/crew/notifications").json()["targets"]["teams"]
eq("the teams serving today, with how many have an account", tg, [{"name": "Audio", "people": 1, "reachable": 1},
                                                                  {"name": "Camera", "people": 2, "reachable": 1}])
r = operator.post("/api/admin/crew/notifications", json={"message": "Camera team to the booth", "teams": ["Camera"]})
eq("a team page reaches the matched people serving, and names who has no account",
   (r.status_code, r.json()["people"], r.json()["unmatched"]), (200, 1, ["No Account"]))
r = operator.post("/api/admin/crew/notifications", json={"message": "Anyone", "teams": ["Nope"]})
eq("an unknown team reaches nobody, so it is refused", r.status_code, 400)

print("a restart picks a page up where it was")
notifications.pending = False
notifications.create("After restart", None, [mike.id], now=T1 + 5000)
eq("sending wakes the pager", notifications.pending, True)

print("account removal")
users.delete(nop.id, actor=None)
eq("phones and pages go with the account", db.one("select count(*) as n from crew_notification_recipients where user_id = ?", (nop.id,))["n"], 0)

print("FAILED" if fails else "all passed", fails or "")
sys.exit(1 if fails else 0)
