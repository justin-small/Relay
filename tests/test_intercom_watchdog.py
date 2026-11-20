"""The intercom watchdog (#276): each check forced into its warn and error states, the pass that builds the cards,
the module's Health and top bar pill, the bridge check, and the connection log with its CSV export.

Offline: a throwaway database, fake phones put straight into the live state, fake clocks; no LiveKit, no Cloudflare.
Run: python tests/test_intercom_watchdog.py
"""
import asyncio
import csv
import io
import os
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"
os.environ["RELAY_DB_BACKUPS"] = os.path.join(TMP, "backups")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from app import config, db, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules.intercom import MODULE as intercom, arbiter as arb, devices, store, watchdog as wd  # noqa: E402
from app.modules.intercom.livekit import supervisor  # noqa: E402
from app.modules.intercom.presence import Conn, presence  # noqa: E402
from app.modules.intercom_remote import watch as bwatch  # noqa: E402
from app.modules.intercom_remote.bridge import bridge  # noqa: E402
from _harness import await_for, check, done, eq, section  # noqa: E402

users.SCRYPT_N = 2 ** 10
store.forget()
store.ready()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"intercom": {"enabled": True}}})
T = dict(wd.DEFAULTS)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


with section("thresholds"):
    eq("nothing saved: the issue's defaults", wd.clean_thresholds(None), wd.DEFAULTS)
    got = wd.clean_thresholds({"lag_warn_ms": 80, "loss_warn_pct": 1.5, "rss_warn_mb": "lots", "wal_warn_mb": 10 ** 9,
                               "grant_warn_ms": True, "nonsense": 4})
    eq("a good value is kept", (got["lag_warn_ms"], got["loss_warn_pct"]), (80, 1.5))
    eq("a bad, huge or boolean value goes back to its default",
       (got["rss_warn_mb"], got["wal_warn_mb"], got["grant_warn_ms"]), (1024, 64, 100))
    check("an unknown key is dropped", "nonsense" not in got)
    eq("the module keeps them in its settings", config.get()["intercom"]["thresholds"], wd.DEFAULTS)
    eq("percentile, nearest rank", (wd.percentile([10, 20, 30, 40], 50), wd.percentile(list(range(1, 101)), 95),
                                    wd.percentile([], 95)), (20, 95, None))

with section("LiveKit"):
    eq("running and answering", wd.eval_livekit("running", "", 0, 2, 5).state, "ok")
    check("with room and participant counts", "2 rooms, 5 on" in wd.eval_livekit("running", "", 0, 2, 5).detail)
    eq("one failed check is a warning", wd.eval_livekit("running", "", 1).state, "warn")
    eq("restarting is a warning", wd.eval_livekit("restarting", "LiveKit stopped answering.", 3).state, "warn")
    c = wd.eval_livekit("error", "LiveKit stopped five times in ten minutes, so Relay gave up.", 0)
    eq("given up is an error, with the supervisor's sentence", (c.state, c.detail[:22]), ("error", "LiveKit stopped five t"))
    eq("missing binary is an error", wd.eval_livekit("missing", "Install livekit-server", 0).state, "error")
    eq("switched off is idle", wd.eval_livekit("off", "", 0).state, "idle")

with section("Relay app"):
    eq("quiet loop", wd.eval_app(T, 3, 0, 200).state, "ok")
    eq("lag over 50 ms warns", wd.eval_app(T, 80, 0, 200).state, "warn")
    eq("memory over 1 GB warns", wd.eval_app(T, 3, 0, 2048).state, "warn")
    eq("lag over 250 ms for under 15 s is still a warning", wd.eval_app(T, 400, 10, 200).state, "warn")
    eq("over 250 ms for 15 s is an error", wd.eval_app(T, 400, 15, 200).state, "error")
    eq("before the first sample: idle", wd.eval_app(T, None, 0, None).state, "idle")
    clock = Clock()
    m = wd.LagMeter(clock)
    for i in range(10):
        clock.t += 1
        m.add(clock.t, 300)
    eq("ten seconds of 300 ms samples (plus the first stall)", round(m.over_error_s(clock.t), 1), 9.3)
    clock.t += 1
    m.add(clock.t, 5)
    eq("one quick sample ends the run", m.over_error_s(clock.t), 0.0)
    eq("the worst of the last 5 s is shown", m.lag_ms(clock.t), 300)
    check("memory and open files are measured here",
          wd.rss_mb() is None or wd.rss_mb() > 1, (wd.rss_mb(), wd.open_files()))

with section("database"):
    eq("a quick write with a fresh backup", wd.eval_db(T, True, 3, 1, 2).state, "ok")
    eq("a slow write warns", wd.eval_db(T, True, 80, 1, 2).state, "warn")
    eq("a big WAL warns", wd.eval_db(T, True, 3, 100, 2).state, "warn")
    c = wd.eval_db(T, True, 3, 1, 30)
    eq("an old backup warns", (c.state, c.detail), ("warn", "The last backup is 30 h old"))
    eq("no backup at all warns", wd.eval_db(T, True, 3, 1, None).state, "warn")
    c = wd.eval_db(T, False, None, None, None)
    eq("a failed write is an error", c.state, "error")
    check("and says local audio carries on", "Local audio carries on" in c.detail)
    ok, write_ms, wal, backup = wd._db_probe()
    check("the real probe writes the scratch row", ok and write_ms is not None and write_ms >= 0, (ok, write_ms))
    eq("one scratch row however often", db.one("select count(*) as n from intercom_watchdog")["n"], 1)
    eq("no backup in this throwaway folder", backup, None)
    db.backup()
    ok, _, _, backup = wd._db_probe()
    check("after a backup its age is under an hour", backup is not None and backup < 1, backup)

with section("Cloudflare bridge"):
    eq("connected", wd.eval_bridge(T, "connected", "", 0, True).state, "ok")
    eq("connected but the cloud did not answer warns", wd.eval_bridge(T, "connected", "", 0, False).state, "warn")
    c = wd.eval_bridge(T, "reconnecting", "Reconnecting to Cloudflare", 30, False)
    eq("reconnecting warns", c.state, "warn")
    check("and says local audio is fine", "Local audio is not affected" in c.detail)
    eq("down two minutes is an error", wd.eval_bridge(T, "reconnecting", "", 120, False).state, "error")
    eq("missing libraries are an error", wd.eval_bridge(T, "missing", "aiortc is not installed", 0, None).state, "error")
    clock = Clock()
    probe_calls = []
    bc = bwatch.BridgeCheck(clock)
    bc.probe = lambda: probe_calls.append(1) or True
    bridge.state = "connected"
    card = asyncio.run(bc(T))
    eq("the check reads the bridge", (card.id, card.state), ("bridge", "ok"))
    bc.state_changed("reconnecting")
    bridge.state = "reconnecting"
    clock.t += 60
    eq("down a minute: warn", asyncio.run(bc(T)).state, "warn")
    clock.t += 61
    eq("down over two minutes: error", asyncio.run(bc(T)).state, "error")
    eq("the cloud was asked at most every 30 s", len(probe_calls), 3)
    for _ in range(5):
        asyncio.run(bc(T))
    eq("and not again within the 30 s", len(probe_calls), 3)
    bc.state_changed("connected")
    bridge.state = "connected"
    eq("back up: ok again", asyncio.run(bc(T)).state, "ok")
    bwatch.register()
    check("registering adds the card source and the state hook",
          wd.sources.get("bridge") is bwatch.check and bridge.on_state == bwatch.check.state_changed)
    bwatch.unregister()
    check("unregistering removes both", "bridge" not in wd.sources and bridge.on_state is None)
    bridge.state = "off"

with section("audio quality"):
    lt = wd.LossTracker()
    lt.add(1, {"packets_lost": 0, "packets_received": 1000, "jitter_ms": 10}, 0)
    lt.add(1, {"packets_lost": 20, "packets_received": 1480, "jitter_ms": 20}, 5)
    lt.add(1, {"packets_lost": 40, "packets_received": 1960, "jitter_ms": 30}, 10)
    loss, jitter = lt.view(1, 10)
    eq("loss is a percentage over 10 s, from the counts", (loss, jitter), (4.0, 20.0))
    lt.add(1, {"packets_lost": 0, "packets_received": 50, "jitter_ms": 5}, 15)
    eq("counts that drop (the phone rejoined) start again", lt.view(1, 15), (None, 5.0))
    eq("one sample says nothing about loss", lt.view(2, 15), (None, None))
    eq("phone levels", [wd.phone_level(T, l, j) for l, j in ((1, 10), (3, 10), (1, 40), (6, 10), (1, 70), (None, None))],
       ["ok", "warn", "warn", "error", "error", ""])
    eq("all fine", wd.eval_audio(T, [{"name": "A", "loss_pct": 0.5, "jitter_ms": 10}]).state, "ok")
    c = wd.eval_audio(T, [{"name": "A", "loss_pct": 0.5, "jitter_ms": 10}, {"name": "Bob", "loss_pct": 3, "jitter_ms": 10}])
    eq("over 2 % loss warns and names the phone", (c.state, c.detail[:4]), ("warn", "Bob:"))
    c = wd.eval_audio(T, [{"name": "A", "loss_pct": 3, "jitter_ms": 10}, {"name": "Cy", "loss_pct": 1, "jitter_ms": 70}])
    eq("over 60 ms jitter is an error and beats a warning", (c.state, c.detail[:3]), ("error", "Cy:"))
    eq("no phone reporting is idle", wd.eval_audio(T, []).state, "idle")

with section("latency and grant time"):
    eq("round trips under 150 ms", wd.eval_latency(T, [20, 30, 40]).state, "ok")
    eq("p95 over 150 ms warns", wd.eval_latency(T, [20, 30, 400]).state, "warn")
    eq("no phone: idle", wd.eval_latency(T, []).state, "idle")
    eq("grants under 100 ms", wd.eval_grant(T, [5, 8, 12]).state, "ok")
    eq("p95 over 100 ms warns", wd.eval_grant(T, [5, 8, 300]).state, "warn")
    a = arb.Arbiter()

    async def lookup(user_id, channel_id):
        return {"can_talk": 1, "can_listen": 1, "priority": 5}, {"mode": "party", "max_talkers": 8, "latch_seconds": 0}

    async def slow_send(device_id, msg):
        await asyncio.sleep(0.12)

    a.lookup, a.send = lookup, slow_send
    asyncio.run(a.request(1, 1, "A", [7]))
    got = a.grant_times(0)
    check("the arbiter times press to grant, including sending it", len(got) == 1 and got[0] >= 110, got)
    eq("and that shows as a slow grant", wd.eval_grant(T, got).state, "warn")
    a.reset()
    eq("reset forgets them", a.grant_times(0), [])

alice = users.create("alice", "Alice", "member", "correct horse", temporary=False)
cred, a_dev = devices.create(alice.id, name="Alice phone")


def events(kind):
    return [e for e in store.events(500) if e["kind"] == kind]


with section("a whole pass"):
    clock = Clock()
    w = wd.Watchdog(clock)
    supervisor.state, supervisor.detail, supervisor.fails = "error", "LiveKit stopped five times in ten minutes.", 0
    presence.conns.clear()
    c = Conn(None, a_dev, SimpleNamespace(id=alice.id, name="Alice"), 1)
    presence.conns[a_dev["id"]] = c
    w.probe = lambda: (True, 2.0, 1.0, 3.0)
    for i, (lost, recv) in enumerate(((0, 1000), (50, 1450), (100, 1900))):
        c.stats = {"packets_lost": lost, "packets_received": recv, "jitter_ms": 12, "rtt_ms": 40 + i}
        w.lag.add(clock.t, 2)
        asyncio.run(w.tick())
        clock.t += 5
    ids = list(w.cards)
    eq("cards in the page's order; no bridge without intercom_remote", ids,
       ["livekit", "app", "database", "audio", "latency", "grant"])
    eq("the worst decides", w.state(), "error")
    eq("Alice's loss over the window (10 %) is an error", (w.cards["audio"].state, w.phone_levels[a_dev["id"]][2]),
       ("error", "error"))
    eq("latency from her round trips", w.cards["latency"].facts["p95_ms"], 42.0)
    eq("one audio_warning row for repeated bad passes", len(events("audio_warning")), 1)
    clock.t += wd.AUDIO_WARN_EVERY_S
    for lost, recv in ((200, 2000), (300, 2100)):     # still losing packets five minutes on
        c.stats = {"packets_lost": lost, "packets_received": recv, "jitter_ms": 12, "rtt_ms": 40}
        asyncio.run(w.tick())
        clock.t += 5
    eq("another after five minutes", len(events("audio_warning")), 2)
    row = events("audio_warning")[0]
    eq("it names the phone, and holds numbers only", (row["user_id"], row["device_id"]), (alice.id, a_dev["id"]))
    check("no credential in the log", cred not in row["detail"])

    w.probe = lambda: (False, None, None, None)
    asyncio.run(w.tick())
    eq("database down: its card is an error", w.cards["database"].state, "error")

    async def blocked_loop():
        """Fill the event loop for real: the lag meter sees it and, held past lag_error_s, the card goes red."""
        meter = wd.LagMeter()
        task = asyncio.create_task(meter.run())
        await asyncio.sleep(0.3)
        time.sleep(1.3)                     # nothing else on this loop runs meanwhile
        await asyncio.sleep(0.3)
        task.cancel()
        now = time.monotonic()
        return meter.lag_ms(now), meter.over_error_s(now)

    lag, over = asyncio.run(blocked_loop())
    check("a blocked loop shows as one long late wake-up", lag is not None and lag > 1000, lag)
    eq("one stall that has ended is a warning, not an error", wd.eval_app(T, lag, over, 100).state, "warn")
    eq("held over 1 s with that threshold it would be an error",
       wd.eval_app({**T, "lag_error_s": 1}, lag, 1.3, 100).state, "error")

    clock.t += 1
    supervisor.state, supervisor.fails = "running", 1
    sources_before = dict(wd.sources)
    wd.sources["bridge"] = lambda t: asyncio.sleep(0, result=wd.eval_bridge(t, "reconnecting", "", 200, False))
    asyncio.run(w.tick())
    eq("a registered source adds its card after the database", list(w.cards)[:4],
       ["livekit", "app", "database", "bridge"])
    eq("LiveKit with one failed check is a warning", w.cards["livekit"].state, "warn")
    wd.sources.clear()
    wd.sources.update(sources_before)

    old = wd.watchdog
    wd.watchdog.cards = dict(w.cards)
    h = intercom.health()
    eq("the module's Health is the worst card", h.state, "error")
    check("and names it", "Cloudflare bridge" in h.detail and "Audio quality" in h.detail, h.detail)
    pills = intercom.pills()
    red = [p for p in pills if p.get("level") == "error"]
    eq("an error shows in the top bar", (len(red), red[0]["text"] if red else ""), (1, "INTERCOM 3 PROBLEMS"))
    wd.watchdog.cards = {"livekit": wd.eval_livekit("running", "", 0)}
    eq("all well: LiveKit's own state", intercom.health().state, "ok")
    eq("and no red pill", [p for p in intercom.pills() if p.get("level")], [])
    wd.watchdog.cards = {}
    supervisor.state, supervisor.fails = "off", 0
    presence.conns.clear()

with section("the module runs it"):
    async def cycle():
        await intercom.start()
        try:
            got = await await_for(lambda: "database" in wd.watchdog.cards and supervisor.state == "missing", 8)
            return bool(got), dict(wd.watchdog.cards), intercom.health()
        finally:
            await intercom.stop()

    ran, cards, health = asyncio.run(cycle())
    check("switching on starts the watchdog; its first pass lands within one interval", ran, list(cards))
    eq("no LiveKit binary here: the LiveKit card says so", cards["livekit"].state, "error")
    eq("and the module's Health follows", health.state, "error")
    eq("the start is logged", len(events("server_restart")), 1)
    eq("switching off clears the cards", wd.watchdog.cards, {})

viewer = _db.client(app, role="viewer")
admin = _db.client(app, role="admin")
A = "/api/admin/intercom"

with section("health and thresholds routes"):
    v = viewer.get(A + "/health").json()
    eq("a viewer reads the cards and thresholds", (sorted(v), v["thresholds"]["loss_warn_pct"]),
       (["cards", "defaults", "labels", "state", "thresholds"], 2))
    eq("a viewer may not change them", viewer.put(A + "/health/thresholds", json={"thresholds": {}}).status_code, 403)
    r = admin.put(A + "/health/thresholds", json={"thresholds": {"loss_warn_pct": 1, "jitter_warn_ms": 9999}})
    eq("an admin may", r.status_code, 200)
    eq("good values saved, bad ones back to default", (r.json()["thresholds"]["loss_warn_pct"],
                                                       r.json()["thresholds"]["jitter_warn_ms"]), (1, 30))
    eq("kept in config.json", config.get()["intercom"]["thresholds"]["loss_warn_pct"], 1)
    eq("the change is logged", len(events("thresholds_changed")), 1)
    admin.put(A + "/health/thresholds", json={"thresholds": {}})
    eq("an empty save is back to defaults", config.get()["intercom"]["thresholds"], wd.DEFAULTS)

with section("connection log and export"):
    with db.transaction() as tx:
        store.log_event(tx, "device_connected", user_id=alice.id, device_id=a_dev["id"])
        store.log_event(tx, "channel_created", detail={"name": "=HYPERLINK(\"http://x\")"})
        store.log_event(tx, "livekit_restarted")
    got = viewer.get(A + "/events?hours=24").json()
    kinds = [e["kind"] for e in got["events"]]
    check("newest first, with names", kinds[0] == "livekit_restarted" and "device_connected" in kinds, kinds[:4])
    con = next(e for e in got["events"] if e["kind"] == "device_connected")
    eq("names, not ids", (con["user"], con["device"]), ("Alice", "Alice phone"))
    eq("a kind filter", {e["kind"] for e in viewer.get(A + "/events?kind=device_").json()["events"]},
       {"device_connected", "device_signed_in"})
    with db.transaction() as tx:
        tx.execute("insert into intercom_events (at, kind) values ('2020-01-01T00:00:00Z', 'ancient')")
    check("the time filter leaves out older rows", "ancient" not in [e["kind"] for e in
                                                                    viewer.get(A + "/events?hours=720").json()["events"]])
    r = viewer.get(A + "/events.csv?hours=24")
    eq("CSV for a viewer", (r.status_code, r.headers["content-type"].split(";")[0]), (200, "text/csv"))
    check("downloaded as a file", "attachment" in r.headers.get("content-disposition", ""))
    text = r.content.decode("utf-8-sig")
    rows = list(csv.reader(io.StringIO(text)))
    eq("a header row a spreadsheet can use", rows[0], ["time (UTC)", "event", "person", "phone", "channel", "by",
                                                       "detail"])
    check("every row has the same columns", all(len(x) == 7 for x in rows), {len(x) for x in rows})
    detail = next(x[6] for x in rows if x[1] == "channel_created")
    check("a cell that looks like a formula is not run as one", not detail.startswith("=") and "HYPERLINK" in detail,
          detail)
    hash_ = db.one("select credential_hash from intercom_devices where id = ?", (a_dev["id"],))
    secrets = [cred] + ([hash_["credential_hash"]] if hash_ and hash_.get("credential_hash") else [])
    check("no credential, hash or token anywhere in it", not any(s and s in text for s in secrets) and
          "token" not in text.lower())

done()
