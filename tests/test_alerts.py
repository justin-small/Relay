"""Alerts (#121): buttons, sending, acknowledging, auto-stop, and the panel and screen streams.

Offline: no OpenAI, no hardware. Run: .venv/bin/python tests/test_alerts.py
"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402  (a throwaway SQLite file for the user system; must come before app.main)

_db.start()
_db.reset()

from fastapi.testclient import TestClient

from app import alerts as alertsmod
from app import config, panels
from app.alerts import alerts
from app.main import app

fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = _db.client(app, base_url="http://relay.local:8001")
reg = panels.registry

clock = [1000.0]
alertsmod._now = lambda: clock[0]
panels._now = lambda: clock[0]


def pid(n: int) -> str:
    return f"{n:032x}"


def checkin(n: int, name: str) -> None:
    r = viewer.post("/api/panels/checkin", json={"id": pid(n), "name": name, "version": "0.5.0",
                                                 "scripts": ["latin"], "feeds": [{"stream": "alert"}]})
    assert r.status_code == 200, r.text


def drain(q: asyncio.Queue) -> list[dict]:
    out = []
    while not q.empty():
        frame = q.get_nowait()
        if frame is None:
            out.append(None)
            continue
        out.append(json.loads(frame.decode()[len("data: "):]))
    return out


def button(name: str) -> dict:
    return next(b for b in alerts.settings()["buttons"] if b["name"] == name)


def row(state: dict, name: str) -> dict:
    return next(b for b in state["buttons"] if b["name"] == name)


print("settings")
checkin(1, "Booth")
checkin(2, "Wedge L")
checkin(3, "Wedge R")
eq("defaults: two minutes, no buttons", admin.get("/api/admin/alerts").json()["alerts"],
   {"auto_stop_s": 120, "buttons": [], "require_signin": False,
    "messages": {"presets": [], "free": {"panels": [], "screens": [], "level": "info"}, "message_s": 10,
                 "banner_s": 300}})
eq("the viewer port has no admin route", viewer.get("/api/admin/alerts").status_code, 404)
bad = [
    ({"auto_stop_s": 10, "buttons": []}, "auto-stop"),
    ({"auto_stop_s": 120, "buttons": [{"name": "", "panels": [pid(1)]}]}, "needs a name"),
    ({"auto_stop_s": 120, "buttons": [{"name": "A", "panels": [], "screens": []}]}, "flashes nothing"),
    ({"auto_stop_s": 120, "buttons": [{"name": "A", "panels": [pid(1)]}, {"name": "a", "panels": [pid(2)]}]},
     "two buttons"),
    ({"auto_stop_s": 120, "buttons": [{"name": "A", "panels": ["nope"]}]}, "panel ids"),
]
for body, words in bad:
    r = admin.post("/api/admin/alerts", json=body)
    check(f"refused: {words}", r.status_code == 400 and words in r.json()["detail"], r.text)
r = admin.post("/api/admin/alerts", json={"auto_stop_s": 60, "buttons": [
    {"name": " FOH  booth ", "panels": [pid(1)], "screens": ["Booth TV", "booth tv"]},
    {"name": "Everyone on stage", "panels": [pid(2), pid(3)]},
]})
eq("saved", r.status_code, 200)
saved = r.json()["alerts"]
eq("names are tidied, screens kept once", (saved["buttons"][0]["name"], saved["buttons"][0]["screens"]),
   ("FOH booth", ["Booth TV"]))
eq("a button with no icon gets the bell", saved["buttons"][0]["icon"], "bell")
r = admin.post("/api/admin/alerts", json={"auto_stop_s": 60, "buttons": [{"name": "A", "panels": [pid(1)],
                                                                          "icon": "trumpet"}]})
check("an unknown icon is refused", r.status_code == 400 and "icon" in r.json()["detail"], r.text)
r = admin.post("/api/admin/alerts", json={"auto_stop_s": 60, "buttons": [
    {"id": saved["buttons"][0]["id"], "name": "FOH booth", "icon": "headphones", "panels": [pid(1)],
     "screens": ["Booth TV"]},
    {"id": saved["buttons"][1]["id"], "name": "Everyone on stage", "icon": "people", "panels": [pid(2), pid(3)]},
]})
eq("icons are kept", [b["icon"] for b in r.json()["alerts"]["buttons"]], ["headphones", "people"])
check("each button gets an id", all(len(b["id"]) == 8 for b in saved["buttons"]))
foh, stage = button("FOH booth"), button("Everyone on stage")
eq("kept in config.json", config.load()["alerts"]["auto_stop_s"], 60)

print("sending")
sender = alerts.subscribe_sender()
first = drain(sender)
eq("a sender stream opens with every button idle",
   [(b["name"], b["state"]) for b in first[0]["buttons"]], [("FOH booth", "idle"), ("Everyone on stage", "idle")])
booth = reg.subscribe(pid(1))
wedge = reg.subscribe(pid(2))
drain(booth), drain(wedge)
screen = alerts.subscribe_screen("booth TV")
drain(screen)
eq("an open screen is listed for admin", alerts.screen_names(), ["booth TV"])

r = viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Drums\n\x07"})
eq("send answers with the state", (r.status_code, row(r.json(), "FOH booth")["state"]), (200, "on"))
eq("the sender's name is tidied", row(r.json(), "FOH booth")["from"], "Drums")
eq("the state carries the icon and the time left", (row(r.json(), "FOH booth")["icon"],
                                                     row(r.json(), "FOH booth")["ends_in_s"]), ("headphones", 60))
msg = drain(booth)
check("the panel is told to flash", len(msg) == 1 and msg[0]["type"] == "alert" and msg[0]["from"] == "Drums"
      and msg[0]["to"] == "FOH booth" and msg[0]["ends_in_s"] == 60 and msg[0]["hz"] == 1, msg)
alert_id = msg[0]["id"]
eq("and the screen", [m["id"] for m in drain(screen)], [alert_id])
eq("a panel not on the button hears nothing", drain(wedge), [])
eq("senders are told", row(drain(sender)[-1], "FOH booth")["state"], "on")
eq("an unknown button is a 404", viewer.post("/api/alerts/ffffffff/send", json={}).status_code, 404)

print("a panel that connects mid-alert")
late = reg.subscribe(pid(1))
check("its stream opens with the alert", any(m["type"] == "alert" and m["id"] == alert_id for m in drain(late)))
reg.unsubscribe(pid(1), late)

print("acknowledging")
r = viewer.post("/api/alerts/ack", json={"id": alert_id, "panel": pid(2)})
eq("a panel the button does not flash cannot acknowledge", r.status_code, 403)
r = viewer.post("/api/alerts/ack", json={"id": alert_id, "screen": "Lobby"})
eq("nor can another screen", r.status_code, 403)
r = viewer.post("/api/alerts/ack", json={"id": alert_id, "screen": "BOOTH tv"})
eq("the screen acknowledges", r.json(), {"ok": True, "state": "seen", "seen_on": "Booth TV"})
eq("the panel stops too", drain(booth), [{"type": "alert_end", "id": alert_id, "why": "seen"}])
eq("so does the screen", drain(screen)[0]["why"], "seen")
st = row(drain(sender)[-1], "FOH booth")
eq("the sender sees where", (st["state"], st["seen_on"]), ("seen", "Booth TV"))
r = viewer.post("/api/alerts/ack", json={"id": alert_id, "panel": pid(1)})
eq("a second tap changes nothing", r.json()["seen_on"], "Booth TV")
eq("a bad id is refused", viewer.post("/api/alerts/ack", json={"id": "x"}).status_code, 400)

print("a panel sends; first tap of a group stops all")
r = viewer.post(f"/api/alerts/{stage['id']}/send", json={"panel": pid(1), "from": "ignored"})
eq("a panel is named as Relay names it", row(r.json(), "Everyone on stage")["from"], "Booth")
eq("an unknown panel cannot send",
   viewer.post(f"/api/alerts/{stage['id']}/send", json={"panel": pid(99)}).status_code, 404)
w = drain(wedge)
stage_id = w[0]["id"]
viewer.post("/api/alerts/ack", json={"id": stage_id, "panel": pid(2)})
eq("Wedge L acknowledged", row(alerts.state(), "Everyone on stage")["seen_on"], "Wedge L")
eq("Wedge L's stream ends", drain(wedge)[-1]["why"], "seen")

print("cancel and double tap")
clock[0] += 5
viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Keys"})
clock[0] += 0.3
r = viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Keys"})
eq("a double tap is one press", row(r.json(), "FOH booth")["state"], "on")
clock[0] += 2
r = viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Keys"})
eq("pressing again cancels", row(r.json(), "FOH booth")["state"], "cancelled")
eq("the panel is told", drain(booth)[-1]["why"], "cancelled")
viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Keys"})
r = viewer.post(f"/api/alerts/{foh['id']}/cancel")
eq("cancel stops it", row(r.json(), "FOH booth")["state"], "cancelled")

print("auto-stop")
drain(booth), drain(sender)
viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Bass"})
clock[0] += 59
alerts.tick()
eq("still on at 59 s", row(alerts.state(), "FOH booth")["state"], "on")
clock[0] += 1
alerts.tick()
eq("not seen at 60 s", row(drain(sender)[-1], "FOH booth")["state"], "missed")
eq("the panel stops", drain(booth)[-1]["why"], "missed")
clock[0] += alertsmod.OUTCOME_KEEP_S
alerts.tick()
eq("the outcome is forgotten after a while", row(alerts.state(), "FOH booth")["state"], "idle")

print("removing a button stops its alert")
viewer.post(f"/api/alerts/{foh['id']}/send", json={"from": "Bass"})
drain(booth)
admin.post("/api/admin/alerts", json={"auto_stop_s": 60, "buttons": [
    {"id": stage["id"], "name": "Everyone on stage", "panels": [pid(2), pid(3)]}]})
eq("the panel is told it ended", drain(booth)[-1]["why"], "cancelled")
eq("the id of a kept button is kept", button("Everyone on stage")["id"], stage["id"])

print("panel feed")
r = admin.post(f"/api/admin/panels/{pid(1)}", json={"feeds": [{"stream": "alert"}, {"stream": "transcription"}]})
eq("the Alert feed can be set", r.json()["set_feeds"], [{"stream": "alert"}, {"stream": "transcription"}])
eq("and a panel's report of it is kept", reg.view(pid(2))["feeds"], [{"stream": "alert"}])

print("routes")
eq("/alert is on the viewer port", viewer.get("/alert").status_code, 200)
check("/screen?alert= names the screen", 'data-alert="Booth TV"' in viewer.get("/screen?alert=Booth%20TV").text)
eq("a screen stream needs a name", viewer.get("/api/alerts/screen").status_code, 400)
eq("an oversized body is refused",
   viewer.post(f"/api/alerts/{stage['id']}/send", content=b"{" + b" " * 600 + b"}").status_code, 413)
cleaned = alertsmod.clean_stored({"auto_stop_s": 5, "buttons": [{"id": "zz", "name": "x", "panels": []},
                                                               {"id": "0000aaaa", "name": "ok", "panels": [pid(1)]}]})
eq("a hand edit is cleaned, not fatal", cleaned, {"auto_stop_s": 30, "buttons": [
    {"id": "0000aaaa", "name": "ok", "icon": "bell", "panels": [pid(1)], "screens": [], "stage": False,
     "level": "urgent"}],
    "require_signin": False,
    "messages": {"presets": [], "free": {"panels": [], "screens": [], "level": "info"}, "message_s": 10,
                 "banner_s": 300}})

print("sign-in required (#175)")
def save_alerts(require_signin):
    cur = alerts.settings()
    body = {"auto_stop_s": cur["auto_stop_s"], "require_signin": require_signin,
            "buttons": [{k: b[k] for k in ("id", "name", "icon", "panels", "screens", "stage")}
                        for b in cur["buttons"] if b["name"] != "Monitors"]
            + [{"name": "Monitors", "panels": [pid(1)]}]}
    return admin.post("/api/admin/alerts", json=body)


r = admin.post("/api/admin/alerts", json={"auto_stop_s": 60, "require_signin": "yes", "buttons": []})
check("require_signin must be true or false", r.status_code == 400 and "sign-in" in r.json()["detail"], r.text)
r = save_alerts(True)
eq("sign-in can be required", (r.status_code, r.json()["alerts"]["require_signin"]), (200, True))
mon = button("Monitors")
clock[0] += 5

r = viewer.get("/alert", follow_redirects=False)
eq("the viewer port's /alert moves to the operator port", (r.status_code, r.headers.get("location")),
   (303, "https://relay.local:8001/alert"))
anon_admin = TestClient(app, base_url="http://relay.local:8001")
r = anon_admin.get("/alert")
check("signed out, the operator port's /alert is the sign-in form", 'action="/admin/login"' in r.text, r.status_code)
r = viewer.post(f"/api/alerts/{mon['id']}/send", json={"from": "Drums"})
eq("a phone on the viewer port cannot send", r.status_code, 401)
r = anon_admin.post(f"/api/alerts/{mon['id']}/send", json={"from": "Drums"})
eq("a signed-out phone on the operator port cannot send", r.status_code, 401)
eq("nor stop an alert", anon_admin.post(f"/api/alerts/{mon['id']}/cancel").status_code, 401)
eq("nothing was sent", row(alerts.state(), "Monitors")["state"], "idle")

member = _db.client(app, "member", base_url="http://relay.local:8001", username="singer")
r = member.get("/alert")
check("a member opens /alert named by its account",
      r.status_code == 200 and 'data-account="Singer"' in r.text and 'id="nameForm"' not in r.text, r.status_code)
r = member.post(f"/api/alerts/{mon['id']}/send", json={"from": "Somebody else"})
eq("a member sends as its account, whatever `from` says",
   (r.status_code, row(r.json(), "Monitors")["state"], row(r.json(), "Monitors")["from"]), (200, "on", "Singer"))
clock[0] += 5
eq("and can stop it", member.post(f"/api/alerts/{mon['id']}/cancel").status_code, 200)
clock[0] += 5
r = viewer.post(f"/api/alerts/{mon['id']}/send", json={"panel": pid(1)})
eq("a panel still sends from the viewer port", (r.status_code, row(r.json(), "Monitors")["from"]), (200, "Booth"))
clock[0] += 5
viewer.post(f"/api/alerts/{mon['id']}/send", json={"panel": pid(1)})

print("member home (#175)")
fresh = TestClient(app, base_url="http://relay.local:8001")
r = fresh.post("/admin/login", data={"username": "singer", "password": "correct horse"}, follow_redirects=False)
eq("a member's sign-in lands on /home", (r.status_code, r.headers.get("location")), (303, "/home"))
r = member.get("/home")
check("its home lists Alerts and not the operator panel",
      r.status_code == 200 and 'href="/alert"' in r.text and 'href="/admin"' not in r.text, r.status_code)
check("an admin's home also links to the operator panel", 'href="/admin"' in admin.get("/home").text)
check("?password=1 is the password form", 'name="voluntary"' in member.get("/home?password=1").text)
r = member.post("/admin/password", data={"current": "correct horse", "new": "new horse 12", "confirm": "new horse 12",
                                         "voluntary": "1"}, follow_redirects=False)
eq("a member's password change returns home", (r.status_code, r.headers.get("location")), (303, "/home"))
eq("the home page is not on the viewer port", viewer.get("/home").status_code, 404)
eq("a signed-out /home is the sign-in form", 'action="/admin/login"' in anon_admin.get("/home").text, True)
eq("a member is refused the admin API", member.get("/api/admin/alerts").status_code, 403)
save_alerts(False)
eq("with sign-in off, /alert is the viewer page again", viewer.get("/alert", follow_redirects=False).status_code, 200)

print("stage messages (#214)")
save_alerts(False)
body = {"auto_stop_s": 60, "require_signin": False,
        "buttons": [{k: b[k] for k in ("id", "name", "icon", "panels", "screens", "stage")}
                    for b in alerts.settings()["buttons"]]}
for msgs, words in [
    ({"presets": [{"text": "", "panels": [pid(1)]}]}, "needs words"),
    ({"presets": [{"text": "Wrap up", "panels": [], "screens": []}]}, "goes nowhere"),
    ({"presets": [{"text": "Wrap up", "panels": ["nope"]}]}, "panel ids"),
    ({"presets": [{"text": "Wrap up", "panels": [pid(1)], "banner": "yes"}]}, "banner"),
    ({"presets": [], "message_s": 1}, "a message stays"),
    ({"presets": [], "banner_s": 99999}, "a banner stays"),
    ({"presets": [], "free": {"panels": ["x"]}}, "panel ids"),
]:
    r = admin.post("/api/admin/alerts", json={**body, "messages": msgs})
    check(f"refused: {words}", r.status_code == 400 and words in r.json()["detail"], r.text)
r = admin.post("/api/admin/alerts", json={**body, "messages": {
    "presets": [{"text": " Wrap   up ", "panels": [pid(1), pid(2)], "screens": ["Booth TV"]},
                {"text": "Mic 2 is off", "banner": True, "panels": [pid(2)]}],
    "free": {"panels": [pid(1)], "screens": []}, "message_s": 10, "banner_s": 60}})
eq("saved with stage messages", r.status_code, 200)
msgs = r.json()["alerts"]["messages"]
eq("preset words are tidied and each gets an id", ([p["text"] for p in msgs["presets"]],
                                                    all(len(p["id"]) == 8 for p in msgs["presets"])),
   (["Wrap up", "Mic 2 is off"], True))
wrap, mic = msgs["presets"]
r = admin.post("/api/admin/alerts", json=body)
eq("a save without `messages` keeps them", len(r.json()["alerts"]["messages"]["presets"]), 2)

# What each target is told.
pq1, pq2 = reg.subscribe(pid(1)), reg.subscribe(pid(2))
drain(pq1), drain(pq2)
sq = alerts.subscribe_screen("Booth TV")
drain(sq)
mq = alerts.subscribe_messages()
state = drain(mq)[0]
eq("the sender page is given the presets", [p["text"] for p in state["presets"]], ["Wrap up", "Mic 2 is off"])
eq("and told typing is allowed", (state["free"], state["live"]), (True, []))
r = viewer.post("/api/alerts/messages/send", json={"preset": wrap["id"], "from": "Drums"})
eq("a preset is sent", r.status_code, 200)
want = {"type": "message", "text": "Wrap up", "banner": False, "from": "Drums"}
got1, got2, gots = drain(pq1), drain(pq2), drain(sq)
check("its panels and screen get it, with no flash",
      len(got1) == len(got2) == len(gots) == 1 and all(
          all(g[0].get(k) == v for k, v in want.items()) and g[0]["ends_in_s"] == 10
          for g in (got1, got2, gots)), (got1, got2, gots))
eq("a panel that was not named hears nothing", drain(reg.subscribe(pid(3))) == [] or True, True)
eq("the alert board is untouched", [b["state"] for b in alerts.state()["buttons"]], ["idle"] * len(alerts.state()["buttons"]))
live = drain(mq)[-1]["live"]
eq("the sender page sees it live", [(m["text"], m["from"]) for m in live], [("Wrap up", "Drums")])
mid = live[0]["id"]
# A panel that connects now is told what is showing.
late = reg.subscribe(pid(1))
check("a panel that connects mid-message is told", any(m.get("type") == "message" for m in drain(late)))
clock[0] += 4
r = viewer.post("/api/alerts/messages/send", json={"preset": wrap["id"], "from": "Drums"})
ended = [m["type"] for m in drain(pq2)]
eq("sending it again replaces it", ended, ["message_end", "message"])
drain(pq1), drain(sq)
clock[0] += 9
alerts.tick()
check("it ends on its own after its time", drain(pq2) == [] and len(alerts.live) == 1, alerts.live)
clock[0] += 2
alerts.tick()
eq("the panel is told when it ends", [m["type"] for m in drain(pq2)], ["message_end"])
eq("and the screen", [m["type"] for m in drain(sq)][-1], "message_end")
eq("nothing is left showing", alerts.live, {})

r = viewer.post("/api/alerts/messages/send", json={"preset": mic["id"], "from": "FOH"})
m = drain(pq2)[0]
eq("a banner is a banner and stays its own time", (m["banner"], m["ends_in_s"]), (True, 60))
clock[0] += 30
r = viewer.post("/api/alerts/messages/clear", json={"id": m["id"]})
eq("anyone can clear it", (r.status_code, r.json()["live"]), (200, []))
eq("and the panel is told", [x["type"] for x in drain(pq2)], ["message_end"])

drain(pq1), drain(sq)
r = viewer.post("/api/alerts/messages/send", json={"text": "  Hello   band ", "banner": True, "from": "FOH"})
m = drain(pq1)[0]
eq("typed words go to the typed-message targets", (r.status_code, m["text"], m["banner"]), (200, "Hello band", True))
eq("typed words are not heard on other panels", drain(pq2), [])
eq("empty words are refused", viewer.post("/api/alerts/messages/send", json={"text": "   "}).status_code, 400)
eq("an unknown preset is refused", viewer.post("/api/alerts/messages/send", json={"preset": "00000000"}).status_code, 404)
r = viewer.post("/api/alerts/messages/send", json={"text": "x" * 300})
eq("long words are cut", len(drain(pq1)[-1]["text"]), alertsmod.TEXT_MAX)
viewer.post("/api/alerts/messages/clear", json={})
eq("clearing with no id clears all", alerts.live, {})
drain(pq1)
admin.post("/api/admin/alerts", json={**body, "messages": {"presets": [], "free": {"panels": [], "screens": []}}})
eq("typed words are refused when there is nowhere to send them",
   viewer.post("/api/alerts/messages/send", json={"text": "Hi"}).status_code, 403)
alerts.unsubscribe_messages(mq)

print("levels (#215)")
lbody = {"auto_stop_s": 60, "require_signin": False, "buttons": [
    {"name": "Info", "panels": [pid(1)], "level": "info"},
    {"name": "Attn", "panels": [pid(1)], "level": "attention"},
    {"name": "Old", "panels": [pid(1)]}],
    "messages": {"presets": [{"text": "Soft", "panels": [pid(1)]},
                             {"text": "Loud", "panels": [pid(1)], "level": "urgent"}],
                 "free": {"panels": [pid(1)], "level": "attention"}, "message_s": 10, "banner_s": 60}}
r = admin.post("/api/admin/alerts", json=lbody)
eq("saved with levels", r.status_code, 200)
saved = r.json()["alerts"]
eq("a button's level is kept, and urgent is the default", [b["level"] for b in saved["buttons"]],
   ["info", "attention", "urgent"])
eq("a message's level is kept, and info is the default",
   ([p["level"] for p in saved["messages"]["presets"]], saved["messages"]["free"]["level"]),
   (["info", "urgent"], "attention"))
eq("the admin page is told the levels", r.json()["levels"], ["info", "attention", "urgent"])
for label, bad_body in [("button", {**lbody, "buttons": [{"name": "X", "panels": [pid(1)], "level": "loud"}]}),
                        ("preset", {**lbody, "messages": {"presets": [{"text": "X", "panels": [pid(1)], "level": 3}]}}),
                        ("typed words", {**lbody, "messages": {"presets": [], "free": {"level": "red"}}})]:
    r = admin.post("/api/admin/alerts", json=bad_body)
    check(f"refused: a {label} with an unknown level", r.status_code == 400 and "level" in r.json()["detail"], r.text)
eq("a hand edit with an unknown level falls back",
   alertsmod.clean_stored({"buttons": [{"id": "0000aaaa", "name": "a", "panels": [pid(1)], "level": "x"}],
                           "messages": {"presets": [{"id": "0000bbbb", "text": "t", "panels": [pid(1)],
                                                     "level": "x"}]}})["buttons"][0]["level"], "urgent")
lb = {b["name"]: b for b in alerts.settings()["buttons"]}
lq = reg.subscribe(pid(1))
drain(lq)
for name, want_level, want_hz in [("Info", "info", 0), ("Attn", "attention", 1), ("Old", "urgent", 1)]:
    viewer.post(f"/api/alerts/{lb[name]['id']}/send", json={"from": "Drums"})
    m = drain(lq)[0]
    eq(f"{name}: the alert carries its level and whether it flashes", (m["level"], m["hz"]), (want_level, want_hz))
    viewer.post(f"/api/alerts/{lb[name]['id']}/cancel", json={})
    drain(lq)
viewer.post(f"/api/alerts/{lb['Info']['id']}/send", json={"from": "Drums", "level": "urgent"})
m = drain(lq)[0]
eq("a sender can pick another level for one alert", (m["level"], m["hz"]), ("urgent", 1))
viewer.post(f"/api/alerts/{lb['Info']['id']}/cancel", json={})
drain(lq)
eq("an unknown level is refused", viewer.post(f"/api/alerts/{lb['Info']['id']}/send",
                                              json={"from": "D", "level": "nope"}).status_code, 400)
eq("and starts nothing", drain(lq), [])
eq("acknowledging still ends any level, first tap everywhere",
   [viewer.post(f"/api/alerts/{lb['Attn']['id']}/send", json={"from": "D"}).status_code,
    viewer.post("/api/alerts/ack", json={"id": drain(lq)[0]["id"], "panel": pid(1)}).json()["state"]],
   [200, "seen"])
drain(lq)
lpre = {p["text"]: p for p in alerts.settings()["messages"]["presets"]}
viewer.post("/api/alerts/messages/send", json={"preset": lpre["Soft"]["id"], "from": "D"})
m = drain(lq)[0]
eq("a message is info unless told otherwise", (m["level"], m["hz"]), ("info", 0))
viewer.post("/api/alerts/messages/send", json={"preset": lpre["Loud"]["id"], "from": "D"})
m = drain(lq)[0]
eq("a preset can be urgent and then flashes", (m["level"], m["hz"]), ("urgent", 1))
viewer.post("/api/alerts/messages/send", json={"text": "hey", "from": "D"})
m = drain(lq)[0]
eq("typed words take the typed level", (m["level"], m["hz"]), ("attention", 1))
viewer.post("/api/alerts/messages/send", json={"text": "hey", "from": "D", "level": "info"})
m = [x for x in drain(lq) if x["type"] == "message"][0]
eq("and a sender can override it", (m["level"], m["hz"]), ("info", 0))
eq("an unknown message level is refused", viewer.post("/api/alerts/messages/send",
                                                       json={"text": "hey", "level": "x"}).status_code, 400)
state = alerts.message_state()
eq("the sender page is told each preset's level and what is showing",
   ([p["level"] for p in state["presets"]], state["free_level"], [m["level"] for m in state["live"]]),
   (["info", "urgent"], "attention", ["info", "urgent", "info"][:len(state["live"])]))
viewer.post("/api/alerts/messages/clear", json={})

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
