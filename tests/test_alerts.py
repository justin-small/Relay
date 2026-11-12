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
   {"auto_stop_s": 120, "buttons": []})
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
    {"id": "0000aaaa", "name": "ok", "icon": "bell", "panels": [pid(1)], "screens": [], "stage": False}]})

print()
if fails:
    print(f"{len(fails)} FAILED")
    sys.exit(1)
print("all passed")
