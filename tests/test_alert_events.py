"""Alert events out (#219): the OSC packet, the webhook body, the settings, the listener the module adds to core, and
the Companion state read, with the module on and off. Nothing touches a network: the senders are replaced.
Run: python tests/test_alert_events.py
"""
import asyncio
import json
import os
import struct
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
from app import config  # noqa: E402
from app.alerts import alerts  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.alert_events import MODULE, events  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"alert_events": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = TestClient(app, base_url="http://relay.local:8000")

print("OSC")
msg = events.osc_message("/a", "hi", 3, 1.5)
eq("an OSC message pads to four bytes and tags its arguments",
   msg, b"/a\0\0" + b",sif\0\0\0\0" + b"hi\0\0" + struct.pack(">i", 3) + struct.pack(">f", 1.5))
eq("each event has its own address", [events.osc_for({"event": e}).split(b"\0")[0] for e in events.EVENTS],
   [b"/relay/alert/sent", b"/relay/alert/acknowledged", b"/relay/alert/ended", b"/relay/message/sent"])
eq("an acknowledge carries button, where and reply",
   events.osc_for({"event": "alert.acknowledged", "button": "FOH", "seen_on": "Booth", "reply": "On my way"}),
   events.osc_message("/relay/alert/acknowledged", "FOH", "Booth", "On my way"))

print("settings")
eq("defaults are off", config.get()["alert_events"], {"webhook": {"enabled": False, "url": ""},
                                                       "osc": {"enabled": False, "host": "", "port": 8000}})
eq("a hand edit is cleaned", events.clean_stored({"webhook": {"enabled": True, "url": "ftp://x"},
                                                    "osc": {"enabled": True, "host": " ", "port": 99999}}),
   {"webhook": {"enabled": False, "url": "ftp://x"}, "osc": {"enabled": False, "host": "", "port": 8000}})
for bad, words in (({"webhook": {"enabled": True, "url": ""}}, "webhook address"),
                   ({"webhook": {"url": "javascript:x"}}, "http://"),
                   ({"osc": {"enabled": True, "host": ""}}, "OSC address"),
                   ({"osc": {"host": "a b"}}, "OSC address"),
                   ({"osc": {"host": "h", "port": 0}}, "port")):
    r = admin.post("/api/admin/alert-events", json=bad)
    eq("refused: " + words, (r.status_code, words in r.json()["detail"]), (400, True))
r = admin.post("/api/admin/alert-events", json={"webhook": {"enabled": True, "url": "https://hooks.example/x"},
                                                "osc": {"enabled": True, "host": "10.0.0.9", "port": 9000}})
eq("saved", (r.status_code, r.json()["webhook"]["enabled"], r.json()["osc"]["port"]), (200, True, 9000))
eq("the viewer port has no routes", viewer.get("/api/admin/alert-events").status_code, 404)

print("delivery")
web, udp = [], []
events.send_webhook = lambda url, ev: web.append((url, ev)) or ""
events.send_osc = lambda host, port, ev: udp.append((host, port, ev)) or ""
eq("an event goes to both", (events.deliver(config.get()["alert_events"], {"event": "alert.sent"}), len(web), len(udp)),
   ([], 1, 1))
eq("to the saved addresses", (web[0][0], udp[0][:2]), ("https://hooks.example/x", ("10.0.0.9", 9000)))
events.send_webhook = lambda url, ev: "The webhook could not be reached."
eq("a failure is a fixed sentence", events.deliver(config.get()["alert_events"], {"event": "alert.sent"}),
   ["The webhook could not be reached."])
body = json.loads(events.body({"event": "alert.sent", "from": "Drums"}))
eq("the webhook body is the event and when", (body["event"], body["from"], body["at"].endswith("Z")),
   ("alert.sent", "Drums", True))

print("the listener")
MODULE.attach()   # the registry does this at startup; the test suites never run startup
eq("it is in core's list while the module is on", MODULE._listen in alerts.listeners, True)


async def run():
    await MODULE.start()
    events.send_webhook = lambda url, ev: web.append((url, ev)) or ""
    web.clear(); udp.clear()
    alerts.save({"auto_stop_s": 60, "buttons": [{"name": "FOH", "panels": [], "screens": ["Booth TV"]}]})
    bid = alerts.settings()["buttons"][0]["id"]
    alerts.press(bid, "Drums")
    sent = alerts.latest[bid]
    alerts.ack(sent.id, screen="Booth TV")
    for _ in range(50):
        if len(web) >= 2:
            break
        await asyncio.sleep(0.05)
    await MODULE.stop()
    return [w[1]["event"] for w in web]


eq("an alert sent and acknowledged is delivered in order", asyncio.run(run()), ["alert.sent", "alert.acknowledged"])
eq("and what it says", (web[1][1]["button"], web[1][1]["seen_on"], web[1][1]["from"]), ("FOH", "Booth TV", "Drums"))
eq("OSC went too", [u[2]["event"] for u in udp], ["alert.sent", "alert.acknowledged"])

print("Companion state")
r = admin.get("/api/admin/alert-events/state")
st = r.json()
eq("variables per button, and none on", (r.status_code, st["variables"]["alerts_on"], st["variables"]["foh_state"],
                                         st["variables"]["foh_seen_on"], st["feedbacks"]["alert_seen"]),
   (200, "0", "seen", "Booth TV", [alerts.settings()["buttons"][0]["id"]]))
eq("the last event is there for a variable", (st["variables"]["last_event"], st["variables"]["last_from"]),
   ("alert.acknowledged", "Drums"))
alerts.press(alerts.settings()["buttons"][0]["id"], "Bass")
clock_state = admin.get("/api/admin/alert-events/state").json()
eq("a flashing button lights the feedback", (clock_state["variables"]["alerts_on"], len(clock_state["feedbacks"]["alert_on"])),
   ("1", 1))

print("switched off")
config.save({"modules": {"alert_events": {"enabled": False}}})
MODULE.detach()
eq("the listener is gone", MODULE._listen in alerts.listeners, False)
eq("the routes answer 404", admin.get("/api/admin/alert-events/state").status_code, 404)

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
