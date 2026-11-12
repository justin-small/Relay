"""The Planning Center link (#138 stage 6): matching, the setlist swap, follow the plan, and ProPresenter driving
Services LIVE.

Everything is a fake: ProPresenter is a Client whose `_send` answers from tables (so view-only and every write rule
in the real Client still apply), and Planning Center is a Client with the LIVE behaviour tried on a real account
(Next and Previous refused without control, toggle_control taking it). Nothing touches a network.

Offline. Run: python tests/test_pclink.py
"""
import asyncio
import json
import logging
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")

from fastapi.testclient import TestClient

import app.main as main
from app import config, plmatch, pclive, planningcenter as pc, propresenter as pp
from app.main import app, panel_control, pc_link, service_feed
from app.pclink import link

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label + (f" ({extra})" if extra and not cond else ""), bool(cond), True)


SECRET = "S3cr3t-TOKEN-xyz"
ME, OTHER = "9116316", "777"


# ---------------------------------------------------------------------------------------- 1. the matching rules
print("normalising titles")
for raw, want in [
    ("Goodness of God (Live)", "goodness of god"), ("Goodness Of God - G", "goodness of god"),
    ("Don’t Stop Believin'", "dont stop believin"), ("Amazing Grace (My Chains Are Gone) - Key of D", "amazing grace"),
    ("How Great Thou Art in Bb", "how great thou art"), ("Béni soit", "beni soit"),
    ("10,000 Reasons - Default Arrangement", "10 000 reasons"), ("Jesus - Lord of All", "jesus lord of all"),
    ("Holy Spirit & Fire", "holy spirit and fire"), ("Great Is Thy Faithfulness - Live", "great is thy faithfulness"),
    ("Way Maker – A", "way maker"), ("  SPACES   and   CASE ", "spaces and case"), ("", ""),
]:
    eq(f"normalise {raw!r}", plmatch.normalise(raw), want)

print("similarity")
check("a typo is close", plmatch.similarity("goodnes of god", "goodness of god") > 0.9)
check("different songs are not close", plmatch.similarity("holy spirit", "holy forever") < 0.8)
check("Message is not Messiah", plmatch.similarity("message", "messiah") < 0.8)

CANDS = [{"uuid": "U-GOG", "name": "Goodness Of God"}, {"uuid": "U-WM", "name": "Way Maker"},
         {"uuid": "U-AG", "name": "Amazing Grace "}, {"uuid": "U-SERM", "name": "Sermon: Grace"},
         {"uuid": "U-HS", "name": "Holy Spirit"}, {"uuid": "U-HF", "name": "Holy Forever"}]


def one(title, typ="song", cands=CANDS, **kw):
    return plmatch.match_items([{"id": "1", "title": title, "type": typ}], cands, **kw)[0]


r = one("Goodness of God (Live)")
eq("exact after normalising", (r["status"], r["method"], r["presentation"]["uuid"]), ("matched", "exact", "U-GOG"))
r = one("Goodnes of God")
eq("fuzzy typo", (r["status"], r["method"], r["presentation"]["uuid"]), ("matched", "fuzzy", "U-GOG"))
r = one("Way Maker - A")
eq("key suffix", (r["status"], r["presentation"]["uuid"]), ("matched", "U-WM"))
r = one("Message", "item")
eq("a non-song with a different presentation title does not match by guess", r["status"], "unmatched")
r = one("Message", "item", mapping=[{"item": "message", "uuid": "U-SERM", "name": "Sermon: Grace"}])
eq("an operator mapping matches it", (r["status"], r["method"], r["presentation"]["uuid"]), ("matched", "mapping", "U-SERM"))
r = one("Totally Unknown Song")
eq("no match", (r["status"], r["presentation"]), ("unmatched", None))
r = one("Holy Spirit Forever", cands=[{"uuid": "A", "name": "Holy Spirit Forever Live Now"}, {"uuid": "B", "name": "Holy Spirit Forever Live Nowe"}],
        threshold=0.7)
eq("two equally close presentations are ambiguous, not picked", r["status"], "ambiguous")
check("the ambiguous row lists the options", len(r["options"]) == 2)
r = one("Worship", "header")
eq("a header is skipped", r["status"], "skipped")
r = one("Holy Spirit")
eq("a same-prefix song is not confused", r["presentation"]["uuid"], "U-HS")
rows = plmatch.match_items([{"id": "1", "title": "Way Maker", "type": "song"}, {"id": "2", "title": "Way Maker", "type": "song"}],
                           CANDS)
eq("a song used twice matches twice", [x["presentation"]["uuid"] for x in rows], ["U-WM", "U-WM"])
eq("items_for by uuid", plmatch.items_for(rows, "U-WM"), ["1", "2"])
eq("items_for unknown", plmatch.items_for(rows, "NOPE"), [])

print("a synced playlist beats title matching, in its own order")
synced = [{"name": "Welcome", "type": "placeholder", "uuid": ""}, {"name": "Goodness of God", "type": "presentation", "uuid": "S-GOG"},
          {"name": "Way Maker", "type": "presentation", "uuid": "S-WM"}]
items = [{"id": "1", "title": "Welcome", "type": "item"}, {"id": "2", "title": "Goodness of God (Live)", "type": "song"},
         {"id": "3", "title": "Way Maker - A", "type": "song"}]
rows = plmatch.match_items(items, CANDS, synced=synced)
eq("synced uuids win over the library's", [(x["status"], x["method"], (x["presentation"] or {}).get("uuid")) for x in rows],
   [("placeholder", "synced", None), ("matched", "synced", "S-GOG"), ("matched", "synced", "S-WM")])
rows = plmatch.match_items(items, CANDS, synced=synced, mapping=[{"item": "Way Maker", "uuid": "U-OP", "name": "x"}])
eq("an operator pairing still beats the synced playlist", rows[2]["presentation"]["uuid"], "U-OP")
rows = plmatch.match_items(items[1:], CANDS, synced=[{"name": "Something Else", "type": "presentation", "uuid": "S-X"}])
eq("a synced playlist that does not fit falls back to titles", rows[0]["method"], "exact")

print("settings")
d = pp.default_link()
eq("both switches start off", (d["follow"], d["drive"], d["dwell_s"], d["allow_back"]), (False, False, 10.0, False))
eq("a bad dwell is refused with a fixed sentence", pp.link_fields({"dwell_s": 0})[1],
   "The dwell time is a number of seconds from 1 to 300.")
eq("dwell is clamped by refusing", pp.link_fields({"dwell_s": 3000})[0], {})
eq("a string for a switch is not on", pp.link_fields({"drive": "yes"})[0], {"drive": False})
check("owned is not settable by a save", "owned" not in pp.link_fields({"owned": {"uuid": "X", "name": "Relay – x"}})[0])
cl = pp.clean_link_settings({"drive": True}, {**d, "owned": {"uuid": "PL-1", "name": "Relay – 2026-11-15"}})
eq("an owned playlist survives a save", cl["owned"]["uuid"], "PL-1")
eq("an owned playlist not named Relay is dropped", pp.clean_link_settings({}, {**d, "owned": {"uuid": "PL-1", "name": "Sunday"}})["owned"], {})
eq("stored garbage is dropped, not fatal", pp.clean_stored({"link": "nonsense"})["link"], pp.default_link())

# ---------------------------------------------------------------------------------------------------- the fakes
PLAN_ITEMS = [("1", "Welcome", "item"), ("2", "Goodness of God (Live)", "song"), ("3", "Way Maker - A", "song"),
              ("4", "Message", "item"), ("5", "Amazing Grace", "song"), ("6", "Closing Prayer", "item")]
ORDER = [i[0] for i in PLAN_ITEMS]


class FakePC(pc.Client):
    """Planning Center's LIVE for one plan."""

    def __init__(self):
        super().__init__("id", SECRET)
        self.controller = None
        self.cur = None
        self.calls = []
        self.skip = set()       # item ids Planning Center steps over (excluded from this service)
        self.fail = None

    def _doc(self):
        inc, rel = [], {"controller": {"data": None}, "current_item_time": {"data": None},
                        "next_item_time": {"data": None}}
        if self.controller:
            rel["controller"]["data"] = {"type": "Person", "id": self.controller}
            inc.append({"type": "Person", "id": self.controller,
                        "attributes": {"full_name": "Relay Owner" if self.controller == ME else "Dana Ortiz"}})
        if self.cur is not None:
            row = {"type": "ItemTime", "id": "t" + ORDER[self.cur],
                   "attributes": {"live_start_at": "2026-11-15T19:00:00Z", "live_end_at": None, "exclude": False},
                   "relationships": {"item": {"data": {"type": "Item", "id": ORDER[self.cur]}},
                                     "plan_time": {"data": {"type": "PlanTime", "id": "pt"}}}}
            rel["current_item_time"]["data"] = {"type": "ItemTime", "id": row["id"]}
            inc.append(row)
        return {"data": {"type": "Live", "id": "700", "attributes": {}, "relationships": rel}, "included": inc}

    def get(self, path, params=None):
        self.calls.append(("GET", path))
        if self.fail:
            raise self.fail
        if path == "/services/v2/me":
            return {"data": {"type": "Person", "id": ME, "attributes": {"full_name": "Relay Owner"}}}
        return self._doc()

    def post(self, path, params=None):
        self.calls.append(("POST", path))
        if self.fail:
            raise self.fail
        action = path.rsplit("/", 1)[1]
        if action == "toggle_control":
            self.controller = None if self.controller == ME else ME
        elif self.controller != ME:
            raise pc.PlanningCenterError("auth", "no", status=403)
        elif action == "go_to_next_item":
            i = -1 if self.cur is None else self.cur
            i += 1
            while i < len(ORDER) - 1 and ORDER[i] in self.skip:
                i += 1
            self.cur = min(i, len(ORDER) - 1)
        elif action == "go_to_previous_item":
            i = (self.cur or 0) - 1
            while i > 0 and ORDER[i] in self.skip:
                i -= 1
            self.cur = max(0, i)
        return self._doc()

    def posts(self):
        return [c for c in self.calls if c[0] == "POST"]


def person(n):
    return {"uuid": n, "name": n}


class FakePP(pp.Client):
    """ProPresenter, answering from tables. Every write is recorded; the real Client still applies view-only."""

    def __init__(self):
        self.vo = False
        super().__init__("127.0.0.1", 1, lambda: self.vo)
        self.sent = []
        self.created = 0
        self.stall = False
        self.lists = {
            "PL-SUN": ("Sunday Service", [
                self.item(0, "Welcome", "placeholder", "", pco=True),
                self.item(1, "Goodness of God", "presentation", "SYN-GOG", pco=True),
                self.item(2, "Way Maker", "presentation", "SYN-WM", pco=True)]),
            "PL-OLD": ("Old Songs", [self.item(0, "Holy Spirit", "presentation", "OLD-HS")])}
        self.libs = {"LIB1": ("Default", [("LIB-GOG", "Goodness Of God"), ("LIB-WM", "Way Maker"),
                                          ("LIB-AG", "Amazing Grace "), ("LIB-SERM", "Sermon: Grace"),
                                          ("LIB-WEL", "Welcome Slides")])}

    @staticmethod
    def item(i, name, kind, uuid, pco=False):
        o = {"id": {"uuid": f"I{i}", "name": name, "index": i}, "type": kind, "is_hidden": False, "is_pco": pco}
        if kind == "presentation":
            o["presentation_info"] = {"presentation_uuid": uuid}
        return o

    def _send(self, method, path, body=None, *, timeout=3, raw_body=False):
        self.sent.append((method, path, body))
        if self.stall:
            raise pp.ProError("timeout", "slow")
        if method == "GET" and path == "/v1/playlists":
            return [{"id": {"uuid": u, "name": n, "index": i}, "field_type": "playlist", "children": []}
                    for i, (u, (n, _)) in enumerate(self.lists.items())]
        if method == "GET" and path.startswith("/v1/playlist/"):
            u = path.rsplit("/", 1)[1]
            if u not in self.lists:
                raise pp.ProError("http", "nf", 404)
            n, items = self.lists[u]
            return {"id": {"uuid": u, "name": n, "index": 0}, "items": items}
        if method == "GET" and path == "/v1/libraries":
            return [{"uuid": u, "name": n, "index": i} for i, (u, (n, _)) in enumerate(self.libs.items())]
        if method == "GET" and path.startswith("/v1/library/"):
            return {"update_type": "all", "items": [{"uuid": u, "name": n, "index": i}
                                                   for i, (u, n) in enumerate(self.libs[path.rsplit("/", 1)[1]][1])]}
        if method == "POST" and path == "/v1/playlists":
            self.created += 1
            u = f"PL-NEW{self.created}"
            self.lists[u] = (body["name"], [])
            return {"id": {"uuid": u, "name": body["name"], "index": len(self.lists)}, "field_type": "playlist"}
        if method == "PUT" and path.startswith("/v1/playlist/"):
            u = path.rsplit("/", 1)[1]
            items = []
            for n, o in enumerate(body):
                kind = o["type"]
                items.append(self.item(n, o["id"]["name"], kind, (o.get("presentation_info") or {}).get("presentation_uuid", "")))
            self.lists[u] = (self.lists[u][0], items)
            return None
        raise AssertionError(f"unexpected {method} {path}")

    def writes(self):
        return [s for s in self.sent if s[0] != "GET"]


class Quiet(logging.Handler):
    def __init__(self):
        super().__init__()
        self.lines = []

    def emit(self, record):
        self.lines.append(record.getMessage())


logs = Quiet()
logging.getLogger().addHandler(logs)
logging.getLogger().setLevel(logging.INFO)

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
config.save({"planning_center": {"app_id": "id", "secret": SECRET, "service_types": ["7"]},
             "propresenter": {**config.get()["propresenter"], "enabled": True, "host": "127.0.0.1", "port": 1}},
            replace=("propresenter", "planning_center"))
viewer = TestClient(app, base_url="http://relay.local:8000")
admin = TestClient(app, base_url="http://relay.local:8001", headers={"x-admin-token": "t0ken"})
anon = TestClient(app, base_url="http://relay.local:8001")

fpc, fpp = FakePC(), FakePP()
f = pclive.follower
f._client = fpc
f.me_id = ME
f.names["7"] = "Sunday AM"
tr = f.tracks.setdefault("7", pclive.Track("7"))
import datetime as _dt
tr.day = _dt.date(2026, 11, 15)
tr.plan = {"id": "700", "title": "Sunday", "series_title": "", "dates": "November 15", "sort_date": 0, "updated_at": "", "times": []}
tr.items = [{"id": i, "title": t, "type": k, "length": 300, "position": "during", "sequence": n, "key": "",
             "arrangement": "", "arrangement_sequence": [], "notes": [], "times": []}
            for n, (i, t, k) in enumerate(PLAN_ITEMS)]


async def _no_poke():
    pass


f.poke = _no_poke
pp.follower.client = fpp
pp.follower.up = True
pp.follower.poke = _no_poke


def present(uuid, name="", song=True):
    pp.follower.presentation = ({"uuid": uuid, "name": name or uuid, "path": "", "library": "Default", "groups": [], "cues": []}
                                if uuid else None)


def link_cfg(**kw):
    saved = config.get()["propresenter"]
    link_save(kw)


def link_save(fields):
    pc_link.save(fields)


T = [1000.0]
link.clock = lambda: T[0]


def reset(**kw):
    """Fresh LIVE and drive state, with the link settings given on top of everything-off."""
    fpc.calls.clear()
    fpc.controller, fpc.cur, fpc.skip, fpc.fail = None, None, set(), None
    fpp.sent.clear()
    fpp.vo = False
    link_save({**pp.default_link(), "mapping": [{"item": "Message", "uuid": "LIB-SERM", "name": "Sermon: Grace"}], **kw})
    link.act_uuid, link.act_n, link.fired, link._firing, link._drive_on = None, 0, 0, False, False
    link.log.clear()
    link.last = None
    present(None)
    panel_control.actions.clear()


async def settle(seconds, step=0.5):
    end = T[0] + seconds
    while T[0] < end:
        T[0] += step
        await link.tick()


async def main_async():
    # ------------------------------------------------------------------------------------------- matching state
    print("the link reads ProPresenter and matches the plan")
    reset()
    await link.refresh()
    st = link.state()
    by = {r["title"]: r for r in st["rows"]}
    eq("synced playlist found", st["synced_playlist"], "Sunday Service")
    eq("a synced placeholder is a placeholder", by["Welcome"]["status"], "placeholder")
    eq("synced beats the library for a song", by["Goodness of God (Live)"]["presentation"]["uuid"], "SYN-GOG")
    eq("a song the playlist lacks falls back to the library", (by["Amazing Grace"]["method"], by["Amazing Grace"]["presentation"]["uuid"]),
       ("exact", "LIB-AG"))
    eq("Message is matched by the operator's pairing", (by["Message"]["method"], by["Message"]["presentation"]["uuid"]),
       ("mapping", "LIB-SERM"))
    eq("an item with no presentation is unmatched", by["Closing Prayer"]["status"], "unmatched")
    eq("counts", st["counts"], {"placeholder": 1, "matched": 4, "unmatched": 1})
    eq("it only read", [s for s in fpp.sent if s[0] != "GET"], [])
    check("no request acts", not any(pp.is_action_path(s[1]) for s in fpp.sent))
    eq("PC's own playlists are not touched by a read", fpp.created, 0)

    # --------------------------------------------------------------------------------------------- follow state
    print("follow the plan: both sides, and when they disagree")
    reset(follow=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 1
    f.tracks["7"].live = {"id": "700", "controller": {"id": ME, "name": "Relay Owner"},
                          "current": {"item": "2", "plan_time": "pt", "live_start_at": 1.0, "live_end_at": None, "id": "t2", "exclude": False},
                          "next": None}
    present("SYN-GOG", "Goodness of God")
    fo = link.follow()
    eq("they agree", (fo["agree"], fo["pp_item"]["id"], fo["pc_item"]["id"]), (True, "2", "2"))
    present("SYN-WM", "Way Maker")
    fo = link.follow()
    eq("they disagree: both shown", (fo["agree"], fo["pp_item"]["title"], fo["pc_item"]["title"]),
       (False, "Way Maker - A", "Goodness of God (Live)"))
    eq("the Service feed carries the item ProPresenter has up", link.feed_extra("7"), {"pp_item": "3", "pp_agree": False})
    msg = service_feed.payload("7")
    eq("and the message has it", (msg["pp_item"], msg["pp_agree"]), ("3", False))
    check("no lyrics in the Service message", "lyrics" not in msg and "text" not in msg)
    present("NOT-IN-PLAN", "Some Other Song")
    fo = link.follow()
    eq("a presentation not in the plan shows no item", (fo["pp_item"], fo["agree"]), (None, False))
    reset()
    present("SYN-WM")
    eq("with follow and drive off the feed gets nothing", link.feed_extra("7"), {})
    eq("and the other service type gets nothing", (link_save({"follow": True}), link.feed_extra("other"))[1], {})

    # -------------------------------------------------------------------------------------------------- driving
    print("drive is off by default and does nothing when off")
    reset()
    present("SYN-WM", "Way Maker")
    fpc.controller, fpc.cur = ME, 0
    await settle(120)
    eq("no Planning Center call at all", fpc.calls, [])
    eq("no ProPresenter call either", fpp.sent, [])
    eq("status says it is off", link.drive_status()["code"], "off")

    print("the dwell: nothing before 10 s, one move after, never twice")
    reset(drive=True)
    fpc.controller, fpc.cur = ME, 0
    await link.refresh()
    present("SYN-WM", "Way Maker")
    T[0] += 1
    await link.tick()               # the activation starts here
    t0 = T[0]
    await settle(9)
    eq("9 s: nothing yet", fpc.posts(), [])
    eq("status counts down", link.drive_status()["code"], "waiting")
    await settle(1.5)
    eq("past 10 s: Planning Center is on Way Maker", fpc.cur, 2)
    eq("two Next presses took it from Welcome to Way Maker", len(fpc.posts()), 2)
    check("no toggle_control, ever", not any(p[1].endswith("toggle_control") for p in fpc.posts()))
    n = len(fpc.posts())
    await settle(60)
    eq("no second move for the same activation", len(fpc.posts()), n)
    fpc.cur = 0                      # someone steps PC back by hand; ProPresenter still shows the same song
    await settle(30)
    eq("and not even when Planning Center is moved away", len(fpc.posts()), n)
    eq("the result is in the log", link.log[0]["result"], "advanced")
    eq("and in the panel actions list as ProPresenter", (panel_control.recent()[0]["panel_name"], panel_control.recent()[0]["action"]),
       ("ProPresenter", "drive"))

    print("a quick skip through songs restarts the wait")
    reset(drive=True)
    fpc.controller, fpc.cur = ME, 0
    await link.refresh()
    for uuid in ("SYN-GOG", "SYN-WM", "LIB-AG"):
        present(uuid)
        T[0] += 1
        await link.tick()
        await settle(4)
    eq("three songs in 15 s: nothing moved", fpc.posts(), [])
    await settle(7)
    eq("only the last one, once it has stayed 10 s", fpc.cur, 4)
    eq("it was moved to once", link.log[0]["result"], "advanced")
    eq("only one decision", len(link.log), 1)

    print("a presentation that comes back after being cleared is a new activation")
    present(None)
    await settle(1)
    present("LIB-AG")
    await settle(11)
    eq("already there: no press", link.log[0]["result"], "already")
    eq("still on Amazing Grace", fpc.cur, 4)

    print("it never takes control")
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = None, 0
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("nobody in control: no press", fpc.posts(), [])
    eq("and it says so", (link.drive_status()["code"], "Nobody holds LIVE control" in link.drive_status()["text"]), ("nobody", True))
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = OTHER, 0
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("someone else in control: no press", fpc.posts(), [])
    check("it names who", "Dana Ortiz holds LIVE control" in link.drive_status()["text"], link.drive_status()["text"])
    eq("Relay still does not hold control", fpc.controller, OTHER)
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    fpc.controller = OTHER               # control changes hands during the wait
    await settle(11)
    eq("control lost during the wait: no press", fpc.posts(), [])

    print("going back")
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 4
    present("SYN-GOG")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("behind the current item and going back is off: no press", fpc.posts(), [])
    eq("it says why", link.drive_status()["code"], "behind")
    reset(drive=True, allow_back=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 4
    present("SYN-GOG")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("allowed: it stops exactly at the matched item", fpc.cur, 1)
    eq("three Previous presses", [p[1].rsplit("/", 1)[1] for p in fpc.posts()], ["go_to_previous_item"] * 3)
    eq("result", link.log[0]["result"], "went_back")

    print("skipped items: stop where Planning Center lands")
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    fpc.skip = {"3"}                     # Way Maker is excluded from this service
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("no runaway: it stopped after landing past it", len(fpc.posts()) <= 3, True)

    print("a presentation with no matching item")
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    present("OTHER-SONG", "Some Other Song")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("nothing is pressed", fpc.posts(), [])
    eq("it says no match", link.drive_status()["code"], "no_match")

    print("turning drive on with something already up starts the wait then")
    reset()
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    present("SYN-WM")
    await settle(60)
    link_save({"drive": True})
    T[0] += 0.5
    await link.tick()
    eq("not at once", fpc.posts(), [])
    await settle(11)
    eq("after the dwell", fpc.cur, 2)

    print("the dwell is configurable")
    reset(drive=True, dwell_s=30.0)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(25)
    eq("25 s of 30: nothing", fpc.posts(), [])
    await settle(6)
    eq("then it moves", fpc.cur, 2)

    print("Planning Center failing says nothing secret")
    reset(drive=True)
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    fpc.fail = pc.PlanningCenterError("network", f"cannot reach host with {SECRET}")
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(11)
    st = link.state()
    eq("result is a fixed code", link.drive_status()["code"], "unreachable")
    check("no secret in the admin state", SECRET not in json.dumps(st))
    check("no secret in the log", not any(SECRET in line for line in logs.lines))
    fpc.fail = None

    print("ProPresenter unreadable: it tries again and does not spend the activation")
    reset(drive=True)
    fpc.controller, fpc.cur = ME, 0
    link.loaded_at = 0.0
    link.pool, link.playlists, link._rows_key = [], [], None
    fpp.stall = True
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(12)
    eq("no press while ProPresenter cannot be read", fpc.posts(), [])
    fpp.stall = False
    await settle(25)
    eq("it moved once ProPresenter answered", fpc.cur, 2)

    # ----------------------------------------------------------------------------------------- setlist swap
    print("the setlist swap")
    reset()
    await link.refresh()
    fpp.sent.clear()
    res = await link.build_playlist()
    eq("one playlist was created", fpp.created, 1)
    eq("named for the plan's date", res["playlist"]["name"], "Relay – 2026-11-15")
    posts = [s for s in fpp.writes() if s[0] == "POST"]
    eq("the create body", posts[0][2], {"name": "Relay – 2026-11-15", "type": "playlist"})
    put = [s for s in fpp.writes() if s[0] == "PUT"]
    eq("one PUT, to the new playlist only", [p[1] for p in put], ["/v1/playlist/PL-NEW1"])
    eq("the order is the plan's", [(o["type"], o["target_uuid"] if o["type"] == "presentation" else None) for o in put[0][2]],
       [("presentation", "SYN-GOG"), ("presentation", "SYN-WM"), ("presentation", "LIB-SERM"), ("presentation", "LIB-AG")])
    eq("read back and verified", res["verified"], True)
    eq("what was left out is named", res["left_out"], ["Welcome", "Closing Prayer"])
    eq("the owned playlist is remembered", config.get()["propresenter"]["link"]["owned"],
       {"uuid": "PL-NEW1", "name": "Relay – 2026-11-15"})
    check("the church's playlists were never written", all(not s[1].endswith(("PL-SUN", "PL-OLD")) for s in fpp.writes()))
    eq("church playlists unchanged", (len(fpp.lists["PL-SUN"][1]), len(fpp.lists["PL-OLD"][1])), (3, 1))
    fpp.sent.clear()
    res = await link.build_playlist()
    eq("a second click reuses it", fpp.created, 1)
    eq("and writes only to it", {s[1] for s in fpp.writes()}, {"/v1/playlist/PL-NEW1"})
    check("the Relay playlist is not a source for matching", all(p["uuid"] != "PL-NEW1" for p in link.playlists))

    print("a same-named playlist Relay did not make is left alone")
    reset()
    fpp.lists["PL-THEIRS"] = ("Relay – 2026-11-15", [FakePP.item(0, "Theirs", "presentation", "T-1")])
    await link.refresh()
    fpp.sent.clear()
    try:
        await link.build_playlist()
        eq("refused", "built", "refused")
    except Exception as exc:
        eq("refused with a sentence", type(exc).__name__, "LinkError")
        check("that names the problem", "did not create" in str(exc))
    eq("nothing was written", fpp.writes(), [])
    eq("theirs is intact", len(fpp.lists["PL-THEIRS"][1]), 1)
    del fpp.lists["PL-THEIRS"]

    print("an owned playlist the church renamed is not written to")
    reset()
    for k in [k for k in fpp.lists if k.startswith("PL-NEW")]:
        del fpp.lists[k]               # the church deleted what Relay made; the config still remembers it
    link_save({"owned": {"uuid": "PL-GONE", "name": "Relay – 2026-11-15"}})
    await link.refresh()
    fpp.sent.clear()
    created = fpp.created
    await link.build_playlist()
    check("a new one was made, the missing one not written", fpp.created == created + 1 and
          all(s[1] != "/v1/playlist/PL-GONE" for s in fpp.writes()))

    print("view-only")
    reset()
    await link.refresh()
    fpp.vo = True
    fpp.sent.clear()
    try:
        await link.build_playlist()
        eq("refused", "built", "refused")
    except pp.ViewOnly:
        eq("view-only refuses the swap", True, True)
    eq("no write was sent", fpp.writes(), [])
    reset(drive=True)
    fpp.vo = True
    await link.refresh()
    fpc.controller, fpc.cur = ME, 0
    present("SYN-WM")
    T[0] += 1
    await link.tick()
    await settle(11)
    eq("view-only ProPresenter still lets it move Planning Center (it only reads ProPresenter)", fpc.cur, 2)
    eq("and writes nothing to ProPresenter", fpp.writes(), [])
    fpp.vo = False


asyncio.run(main_async())

# ---------------------------------------------------------------------------------------------------- the routes
print("routes")
reset()
for method, path, body in [("get", "/api/admin/propresenter/link", None),
                           ("get", "/api/admin/propresenter/link/presentations", None),
                           ("post", "/api/admin/propresenter/link/refresh", {}),
                           ("post", "/api/admin/propresenter/link/mapping", {"item": "x", "uuid": "U1"}),
                           ("post", "/api/admin/propresenter/link/playlist", {})]:
    before = (len(fpp.sent), config.get()["propresenter"]["link"])
    r = getattr(anon, method)(path, **({"json": body} if body is not None else {}))
    eq(f"{method.upper()} {path} needs the admin token", r.status_code, 401)
    r = getattr(viewer, method)(path, **({"json": body} if body is not None else {}))
    eq(f"{path} is not on the viewer port", r.status_code, 404)
    eq("and nothing happened", (len(fpp.sent), config.get()["propresenter"]["link"]), before)

r = admin.get("/api/admin/propresenter/link")
eq("state", r.status_code, 200)
body = r.json()
check("state has rows, drive and follow", all(k in body for k in ("rows", "drive", "follow", "settings", "plan", "loaded")))
check("no secret in the route's answer", SECRET not in r.text)
r = admin.get("/api/admin/propresenter/link/presentations")
eq("the pairing picker's list", (r.status_code, {"uuid": "LIB-SERM", "name": "Sermon: Grace"} in r.json()["presentations"]), (200, True))
eq("refresh reads", admin.post("/api/admin/propresenter/link/refresh").status_code, 200)
eq("a save of the link settings", admin.post("/api/admin/propresenter", json={"link": {"drive": True, "dwell_s": 12}}).status_code, 200)
eq("it is saved", (config.get()["propresenter"]["link"]["drive"], config.get()["propresenter"]["link"]["dwell_s"]), (True, 12.0))
r = admin.post("/api/admin/propresenter", json={"link": {"dwell_s": 0}})
eq("a bad dwell is a 400 with a fixed sentence", (r.status_code, r.json()["error"]),
   (400, "The dwell time is a number of seconds from 1 to 300."))
r = admin.post("/api/admin/propresenter", json={"link": {"owned": {"uuid": "PL-SUN", "name": "Relay – hacked"}}})
eq("a save cannot set the owned playlist", config.get()["propresenter"]["link"]["owned"].get("uuid"), None)
r = admin.post("/api/admin/propresenter/link/mapping", json={"item": "Closing Prayer", "uuid": "LIB-WEL", "name": "Welcome Slides"})
eq("a pairing is saved", (r.status_code, any(m["item"] == "Closing Prayer" for m in config.get()["propresenter"]["link"]["mapping"])),
   (200, True))
r = admin.post("/api/admin/propresenter/link/mapping", json={"item": "Closing Prayer", "uuid": "../x"})
eq("a path-shaped id is refused", r.status_code, 400)
r = admin.post("/api/admin/propresenter/link/mapping", json={"item": "Closing Prayer", "uuid": ""})
eq("an empty uuid clears the pairing", (r.status_code, any(m["item"] == "Closing Prayer" for m in config.get()["propresenter"]["link"]["mapping"])),
   (200, False))
for k in [k for k in fpp.lists if k.startswith("PL-NEW")]:
    del fpp.lists[k]
fpp.sent.clear()
r = admin.post("/api/admin/propresenter/link/playlist")
eq("the playlist route builds", (r.status_code, r.json().get("verified")), (200, True))
fpp.vo = True
fpp.sent.clear()
r = admin.post("/api/admin/propresenter/link/playlist")
eq("view-only answers 409 with a fixed sentence", (r.status_code, "View-only mode" in r.json()["error"]), (409, True))
eq("and writes nothing", fpp.writes(), [])
fpp.vo = False
fpp.stall = True
r = admin.post("/api/admin/propresenter/link/refresh")
eq("a stalled ProPresenter is a 504 with a fixed sentence", (r.status_code, r.json()["error"]),
   (504, "ProPresenter did not answer in time."))
fpp.stall = False

print()
print("FAILED:" if fails else "all passed", *fails, sep="\n  " if fails else " ")
sys.exit(1 if fails else 0)
