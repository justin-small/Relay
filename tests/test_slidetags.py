"""Slide-note tags (#138, stage 5): the parser, the deck-wide fallback, the settle (debounce) and no-re-fire rules, the
actions main.py wires to Relay, and that nothing fires while tags are off.

Offline, no ProPresenter: the watcher is driven directly with `observe`, on a real event loop with short settle times.
The follower reading notes from a (fake) ProPresenter is in tests/test_propresenter.py.

Run: python tests/test_slidetags.py
"""
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pg  # noqa: E402  (a throwaway Postgres for the user system; must come before app.main)

_pg.start()
_pg.reset()

from app import config
from app.modules.propresenter import slidetags as st
from app.modules.propresenter.slidetags import Tag

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def check(label, cond, extra=""):
    eq(label + (f" ({extra})" if extra and not cond else ""), bool(cond), True)


def slide_of(notes):
    return st.parse(notes).slide


# ------------------------------------------------------------------------------------------------- parsing
print("parsing")
eq("a plain tag", slide_of("[relay:lyrics]"), [Tag("lyrics")])
eq("a tag with a value", slide_of("[relay:alert=Sound desk needed]"), [Tag("alert", "Sound desk needed")])
eq("action names are not case-sensitive", slide_of("[RELAY:Capture-Start] [Relay:LYRICS]"),
   [Tag("capture-start"), Tag("lyrics")])
eq("several to a slide, in the order written", slide_of("[relay:capture-start][relay:lyrics][relay:lang=es]"),
   [Tag("capture-start"), Tag("lyrics"), Tag("lang", "SPANISH")])
eq("anything else in the notes is ignored", slide_of("Pause here.\n[relay:lyrics]\nthen sing [x] [relay] relay:lyrics"),
   [Tag("lyrics")])
eq("a tag mid-sentence", slide_of("Start the band [relay:captions] now"), [Tag("captions")])
eq("spaces around the name and value are trimmed", slide_of("[relay: lyrics ] [relay:alert =  Sound   desk ]"),
   [Tag("lyrics"), Tag("alert", "Sound desk")])
eq("a value may hold spaces and punctuation", slide_of("[relay:alert=Keys: mic 2, please!]"),
   [Tag("alert", "Keys: mic 2, please!")])
eq("a value may hold a balanced [pair]", slide_of("[relay:alert=Check [mic] now]"), [Tag("alert", "Check [mic] now")])
eq("a value may hold an escaped ]", slide_of("[relay:alert=Level 3\\] reached]"), [Tag("alert", "Level 3] reached")])
eq("a value may hold = signs", slide_of("[relay:alert=a=b]"), [Tag("alert", "a=b")])
eq("a repeated tag is kept in the parse (resolve de-duplicates)", slide_of("[relay:lyrics][relay:lyrics]"),
   [Tag("lyrics"), Tag("lyrics")])
eq("empty notes, None and non-text", (slide_of(""), slide_of(None), slide_of(42)), ([], [], []))
eq("a relay tag is not read from the middle of another word", slide_of("x[relayx:lyrics] [notrelay:lyrics]"), [])

p = st.parse("[relay:lyrics")
eq("a tag with no closing ] is skipped and said", (p.slide, len(p.warnings)), ([], 1))
p = st.parse("[relay:alert=never closed\n[relay:lyrics]")
eq("a value that runs to the end of its line is skipped; the next line is still read",
   (p.slide, len(p.warnings)), ([Tag("lyrics")], 1))
p = st.parse("[relay:] [relay:=x] [relay:two words]")
eq("tags with no usable action name are skipped with a warning each", (p.slide, len(p.warnings)), ([], 3))
p = st.parse("[relay:alert=[unbalanced]")
eq("an unbalanced [ inside a value never closes", (p.slide, len(p.warnings)), ([], 1))
p = st.parse("[relay:dance]")
eq("an unknown action is ignored and listed", (p.slide, p.warnings), ([], ['Unknown action "dance" ignored.']))
p = st.parse("[relay:lang]")
check("a missing value is a warning", not p.slide and "needs a value" in p.warnings[0], p.warnings)
p = st.parse("[relay:alert=]")
check("an empty value is a warning", not p.slide and "needs a value" in p.warnings[0], p.warnings)
p = st.parse("[relay:capture-start=now]")
check("a value on an action that takes none is a warning and the tag does not run",
      not p.slide and "takes no value" in p.warnings[0], p.warnings)
eq("lang accepts a code", slide_of("[relay:lang=es]"), [Tag("lang", "SPANISH")])
eq("lang accepts a name, any capitals", slide_of("[relay:lang=Portuguese] [relay:lang=FRENCH]"),
   [Tag("lang", "PORTUGUESE"), Tag("lang", "FRENCH")])
eq("lang accepts a code that is not two letters (hmn)", slide_of("[relay:lang=hmn]"), [Tag("lang", "HMONG")])
p = st.parse("[relay:lang=klingon]")
check("lang with a language Relay does not have is a warning", not p.slide and "not a language" in p.warnings[0], p.warnings)
p = st.parse("[relay-deck:lyrics] [relay:captions]")
eq("deck tags and slide tags are told apart", (p.deck, p.slide), ([Tag("lyrics")], [Tag("captions")]))
eq("the long value is cut", len(slide_of("[relay:alert=" + "x" * 500 + "]")[0].value), st.VALUE_MAX)
big = "a" * 30000 + "[relay:lyrics]"
eq("a huge notes box is only read so far", st.parse(big).slide, [])

# ------------------------------------------------------------------------------------------------ deck-wide
print("deck-wide defaults")
notes = ["Intro [relay-deck:lyrics]", "", "Last verse [relay-deck:lyrics][relay-deck:lang=es] [relay-deck:oops]"]
deck = st.parse_deck(notes)
eq("deck tags are collected from every slide, each once", deck.tags, (Tag("lyrics"), Tag("lang", "SPANISH")))
check("a deck warning names the slide", any(w.startswith("Slide 3:") for w in deck.warnings), deck.warnings)
eq("a slide with no tag of its own gets the deck's", st.resolve([], deck.tags), (Tag("lyrics"), Tag("lang", "SPANISH")))
eq("a slide with its own tag does not", st.resolve([Tag("captions")], deck.tags), (Tag("captions"),))
eq("resolve drops repeats", st.resolve([Tag("lyrics"), Tag("lyrics")], []), (Tag("lyrics"),))
eq("no tags anywhere resolves to nothing", st.resolve([], ()), ())

t = st.Tags()
d1 = t.deck_for("U1", notes)
d2 = t.deck_for("U1", list(notes))
check("a presentation's notes are parsed once, then served from the cache", d1 is d2 and t.deck_reads == 1)
t.deck_for("U1", notes + ["[relay-deck:captions]"])
eq("changed notes are parsed again", t.deck_reads, 2)
t.deck_for("U2", notes)
eq("another presentation with the same notes is its own entry", t.deck_reads, 3)
for i in range(st.DECKS_KEEP + 5):
    t.deck_for(f"X{i}", [f"[relay-deck:lyrics] {i}"])
check("the cache is bounded", len(t.decks) <= st.DECKS_KEEP, len(t.decks))


# --------------------------------------------------------------------------------------------------- watcher
def harness(settle=0.12):
    """A Tags with every action recording a call, and observe() with the arguments a follower would give."""
    t = st.Tags()
    calls = []

    def make(name):
        async def fn(value):
            calls.append((name, value))
            return f"{name} done"
        return fn
    t.actions = {n: make(n) for n in st.ACTIONS}

    def see(uuid="U", index=0, notes="", deck=None, enabled=True, settle_s=settle, title="Song"):
        t.observe(enabled=enabled, settle_s=settle_s, uuid=uuid, title=title, index=index, notes=notes, deck=deck)
    return t, calls, see


async def settle_wait(t, extra=0.0):
    await asyncio.sleep(0.12 + 0.15 + extra)


async def scenarios():
    print("settle (debounce)")
    t, calls, see = harness()
    see(index=0, notes="[relay:capture-start]")
    await asyncio.sleep(0.04)
    eq("nothing runs before the slide has settled", calls, [])
    check("the slide and its tag are shown straight away, marked as settling",
          t.seen and t.seen["settling"] and t.seen["resolved"] == ["capture-start"], t.seen)
    await settle_wait(t)
    eq("it runs once the slide has stayed", calls, [("capture-start", None)])
    check("…and is no longer settling", t.seen["settling"] is False)

    t, calls, see = harness()
    for i in range(8):                       # a fast run, each slide gone before the settle time
        see(index=i, notes=f"[relay:alert=Slide {i}]")
        await asyncio.sleep(0.03)
    await settle_wait(t)
    eq("a fast run of slides runs only the one it stopped on", calls, [("alert", "Slide 7")])

    t, calls, see = harness()
    see(index=0, notes="[relay:capture-start]")
    await asyncio.sleep(0.05)
    see(index=1, notes="")
    await settle_wait(t)
    eq("a slide that was gone before the settle time runs nothing", calls, [])

    print("same set, once")
    deck = st.parse_deck(["[relay-deck:lyrics]", "", ""])
    t, calls, see = harness()
    see(index=0, notes="", deck=deck)
    await settle_wait(t)
    for i in (1, 2, 1, 0):
        see(index=i, notes="", deck=deck)
        await settle_wait(t)
    eq("a deck-wide tag runs once on entry, not at every slide", calls, [("lyrics", None)])

    t, calls, see = harness()
    see(index=0, notes="[relay:lyrics]")
    await settle_wait(t)
    see(index=1, notes="[relay:lyrics]")
    await settle_wait(t)
    eq("two slides that each carry the same tag run it once", calls, [("lyrics", None)])
    see(index=2, notes="[relay:captions]")
    await settle_wait(t)
    eq("a different set runs", calls, [("lyrics", None), ("captions", None)])
    see(index=3, notes="")
    await settle_wait(t)
    see(index=4, notes="[relay:captions]")
    await settle_wait(t)
    eq("the same tag after a slide with none runs again", calls[2:], [("captions", None)])

    t, calls, see = harness()
    see(index=0, notes="[relay:lyrics]")
    await asyncio.sleep(0.04)
    see(index=1, notes="")
    await asyncio.sleep(0.04)
    see(index=2, notes="[relay:lyrics]")
    await settle_wait(t)
    eq("a slide that never settled does not count as the set that last ran", calls, [("lyrics", None)])

    t, calls, see = harness()
    see(index=0, notes="[relay:lyrics][relay:lang=fr]")
    await settle_wait(t)
    see(index=1, notes="[relay:lang=fr][relay:lyrics]")
    await settle_wait(t)
    eq("the same actions in another order are the same set only if written in the same order (order is the sequence)",
       [c[0] for c in calls], ["lyrics", "lang", "lang", "lyrics"])

    print("deck fallback while moving")
    deck = st.parse_deck(["[relay-deck:lyrics]", "[relay-deck:lang=es]", ""])
    t, calls, see = harness()
    see(index=0, notes="", deck=deck)
    await settle_wait(t)
    see(index=1, notes="[relay:captions]", deck=deck)
    await settle_wait(t)
    see(index=2, notes="", deck=deck)
    await settle_wait(t)
    eq("the deck's set, the slide's own, then the deck's again",
       calls, [("lyrics", None), ("lang", "SPANISH"), ("captions", None), ("lyrics", None), ("lang", "SPANISH")])
    check("the admin state shows deck tags and warnings", t.seen["deck_tags"] == ["lyrics", "lang=SPANISH"], t.seen)

    print("a deck tag on the live slide counts before the deck has been read")
    t, calls, see = harness()
    see(index=0, notes="Go [relay-deck:lyrics]", deck=None)
    await settle_wait(t)
    eq("…it runs", calls, [("lyrics", None)])

    print("off, and nothing live")
    t, calls, see = harness()
    see(index=0, notes="[relay:capture-start]", enabled=False)
    await settle_wait(t)
    eq("tags off: nothing fires", calls, [])
    eq("tags off: nothing is shown as seen", t.seen, None)
    see(index=0, notes="[relay:capture-start]")
    await asyncio.sleep(0.04)
    see(index=0, notes="[relay:capture-start]", enabled=False)
    await settle_wait(t)
    eq("switched off while a slide is settling: it does not fire", calls, [])
    see(index=0, notes="[relay:capture-start]")
    await settle_wait(t)
    eq("switched back on: the slide is read afresh", calls, [("capture-start", None)])

    t, calls, see = harness()
    see(index=0, notes="[relay:lyrics]")
    await settle_wait(t)
    see(uuid=None, index=None, notes="")
    await settle_wait(t)
    see(index=0, notes="[relay:lyrics]")
    await settle_wait(t)
    eq("nothing live (ProPresenter cleared) is a set of its own, so the tag runs again after it",
       calls, [("lyrics", None), ("lyrics", None)])

    print("results and failures")
    t, calls, see = harness()

    async def boom(value):
        raise RuntimeError("No OpenAI API key set.")
    t.actions["capture-start"] = boom
    see(index=0, notes="[relay:capture-start][relay:lyrics]")
    await settle_wait(t)
    eq("a failing action is recorded and the next one still runs",
       [(r["action"], r["ok"]) for r in reversed(t.history)], [("capture-start", False), ("lyrics", True)])
    eq("the reason is kept for admin", t.history[1]["result"], "No OpenAI API key set.")
    t.actions.pop("lyrics")
    see(index=1, notes="[relay:lyrics]")
    await settle_wait(t)
    eq("an action with nothing wired is recorded, not raised", (t.history[0]["ok"], t.history[0]["action"]), (False, "lyrics"))
    check("history is bounded", t.history.maxlen == st.HISTORY_KEEP)


asyncio.run(scenarios())

# -------------------------------------------------------------------------- the actions main.py wires
print("actions wired to Relay")
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"propresenter": {"enabled": True}, "planning_center": {"enabled": True}}})
# Both modules are switched on (#157); their hooks into core are set without starting their background work.
from app.modules import modules as _modules  # noqa: E402
_modules.attach("planning_center")
_modules.attach("propresenter")
from app import main as app_main, panels as panels_mod, alerts as alerts_mod  # noqa: E402
from app.modules.propresenter import propresenter as pp  # noqa: E402
from app.engine import engine  # noqa: E402
from app.scheduler import scheduler  # noqa: E402

eq("every tag action has a function", sorted(st.tags.actions), sorted(st.ACTIONS))


async def wiring():
    log = []
    real = (engine.start, engine.stop, engine.running, scheduler.manual_start, scheduler.manual_stop,
            app_main.panel_registry.show_lyrics, app_main.panel_registry.show_captions,
            app_main.panel_registry.show_language, app_main.alerts.raise_named)

    async def fake_start():
        log.append("engine.start")
        engine.running = True
        return {}

    async def fake_stop():
        log.append("engine.stop")
        engine.running = False
        return {}
    engine.start, engine.stop, engine.running = fake_start, fake_stop, False
    scheduler.manual_start = lambda: log.append("scheduler.manual_start")
    scheduler.manual_stop = lambda: log.append("scheduler.manual_stop")
    app_main.panel_registry.show_lyrics = lambda: log.append("lyrics") or "ok"
    app_main.panel_registry.show_captions = lambda: log.append("captions") or "ok"
    app_main.panel_registry.show_language = lambda v: log.append(("lang", v)) or "ok"
    app_main.alerts.raise_named = lambda text, sender: log.append(("alert", text, sender)) or "ok"
    try:
        acts = st.tags.actions
        await acts["capture-start"](None)
        eq("capture-start starts Relay's own captioning the way the Start button does",
           log, ["scheduler.manual_start", "engine.start"])
        log.clear()
        await acts["capture-start"](None)
        eq("…and does nothing when it is already running", log, [])
        await acts["capture-stop"](None)
        eq("capture-stop stops it the way the Stop button does", log, ["scheduler.manual_stop", "engine.stop"])
        log.clear()
        await acts["capture-stop"](None)
        eq("…and does nothing when it is already stopped", log, [])
        await acts["lyrics"](None)
        await acts["captions"](None)
        await acts["lang"]("SPANISH")
        await acts["alert"]("Sound desk")
        eq("lyrics, captions, lang and alert reach the panels and Alerts", log,
           ["lyrics", "captions", ("lang", "SPANISH"), ("alert", "Sound desk", "ProPresenter")])

        async def refuse():
            raise RuntimeError("No OpenAI API key set. Add one in the admin panel.")
        engine.start = refuse
        try:
            await acts["capture-start"](None)
            check("a start that Relay refuses is an error the history can show", False)
        except RuntimeError as exc:
            check("a start that Relay refuses is an error the history can show", "No OpenAI API key" in str(exc), str(exc))
        app_main.alerts.raise_named = real[8]
        try:
            await acts["alert"]("Nobody has this name")
            check("an alert with no button of that name is an error the history can show", False)
        except RuntimeError as exc:
            check("an alert with no button of that name is an error the history can show", "No alert button" in str(exc), str(exc))
    finally:
        (engine.start, engine.stop, engine.running, scheduler.manual_start, scheduler.manual_stop,
         app_main.panel_registry.show_lyrics, app_main.panel_registry.show_captions,
         app_main.panel_registry.show_language, app_main.alerts.raise_named) = real


asyncio.run(wiring())
check("ProPresenter's own capture is never in the action list", not any("propresenter" in n or n == "capture" for n in st.ACTIONS))

# ---------------------------------------------------------------------------------------------------- alerts
print("alert=<text> through Alerts")
button = {"id": "ab12cd34", "name": "Sound desk", "icon": "bell", "panels": [], "screens": [], "stage": False}
config.save({"alerts": {"auto_stop_s": 120, "buttons": [button]}}, replace=("alerts",))
al = alerts_mod.alerts
eq("the button is found by its name, any capitals, and the alert starts",
   al.raise_named("sound DESK", "ProPresenter"), "Alert Sound desk raised.")
eq("it is on, and says who raised it", (al.latest["ab12cd34"].state, al.latest["ab12cd34"].sender), ("on", "ProPresenter"))
eq("a second tag does not cancel it the way a second press would", al.raise_named("Sound desk", "ProPresenter"),
   "The Sound desk alert is already on.")
eq("…it is still on", al.latest["ab12cd34"].state, "on")
try:
    al.raise_named("Drums", "ProPresenter")
    check("no button with that name is refused", False)
except alerts_mod.Refused as exc:
    eq("no button with that name is refused", exc.status, 404)
al.cancel("ab12cd34")

# -------------------------------------------------------------------------------------------------- panels
print("panels: lyrics, captions, lang")
reg = panels_mod.Registry()
# A registry made here: the ProPresenter module's attach wires only the shared one.
reg.lyrics_offered = lambda: pp.panel_offers()["lyrics"]
config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": 50402, "songs_library": "Songs",
                              "lyrics": {"viewer": False, "screen": True, "overlay": False, "panels": True}},
             "targets": {"SPANISH": {"enabled": True}, "FRENCH": {"enabled": False}}, "source_language": "ENGLISH"},
            replace=("propresenter",))


async def panels_scenario():
    def checkin(pid, feeds, scripts=("latin",)):
        reg.checkin({"id": pid, "name": "P", "feeds": feeds, "scripts": list(scripts), "feed_rev": 0}, "10.0.0.9")

    a, b, c, d = "a" * 32, "b" * 32, "c" * 32, "d" * 32
    checkin(a, [{"stream": "transcription"}])
    checkin(b, [{"stream": "translation", "lang": "SPANISH"}, {"stream": "alert"}])
    checkin(c, [{"stream": "service", "service_type": "99"}])
    checkin(d, [{"stream": "transcription"}])
    qs = {pid: reg.subscribe(pid) for pid in (a, b, c)}      # d has no event stream: unreachable
    for q in qs.values():
        while not q.empty():
            q.get_nowait()

    def drain(pid):
        import json as _json
        out = []
        while not qs[pid].empty():
            out.append(_json.loads(qs[pid].get_nowait().decode()[len("data: "):]))
        return out

    msg = reg.show_lyrics()
    eq("lyrics: panels showing captions are switched, the others are left", msg, "Lyrics shown on 2 panels.")
    eq("a caption feed becomes the lyrics", [m["feeds"] for m in drain(a)], [[{"stream": "lyrics"}]])
    eq("a panel's other feed (alert) stays", [m["feeds"] for m in drain(b)], [[{"stream": "lyrics"}, {"stream": "alert"}]])
    eq("a panel on the service is not touched", drain(c), [])
    check("nothing was stored: the operator's choice is not overwritten", reg.stored() == {} or all(
        not v.get("feeds") for v in reg.stored().values()))
    reg.show_lyrics()
    eq("a second lyrics changes nothing", (drain(a), drain(b)), ([], []))
    eq("a panel the tag changed has its own revision, above what it applied",
       reg._rev_floor[a] > 0, True)

    msg = reg.show_language("SPANISH")
    eq("lang while on the lyrics changes what captions will go back to, and sends nothing",
       (msg, drain(a), drain(b)), ("Spanish set on 2 panels.", [], []))
    msg = reg.show_captions()
    eq("captions puts back what they showed (in the new language)", msg, "Captions back on 2 panels.")
    eq("…panel a", [m["feeds"] for m in drain(a)], [[{"stream": "translation", "lang": "SPANISH"}]])
    eq("…panel b, its alert feed kept", [m["feeds"] for m in drain(b)],
       [[{"stream": "translation", "lang": "SPANISH"}, {"stream": "alert"}]])
    eq("captions again has nothing to put back", reg.show_captions(), "No panel was showing lyrics from a tag.")
    revs = []
    reg.show_lyrics()
    revs += [m["rev"] for m in drain(a)]
    reg.show_captions()
    revs += [m["rev"] for m in drain(a)]
    check("every push carries a new revision (a panel ignores one it already applied)",
          len(set(revs)) == 2 and all(r > 0 for r in revs), revs)
    drain(b)

    print("  a choice made at a panel's screen is not undone")
    reg.show_lyrics()
    drain(a), drain(b)
    checkin(a, [{"stream": "translation", "lang": "SPANISH"}])   # someone picked Spanish at panel a
    eq("captions leaves a panel that was changed at its screen", reg.show_captions(), "Captions back on 1 panel.")
    eq("…panel a got nothing", drain(a), [])

    print("  the operator's own choice wins over a tag")
    reg.show_lyrics()
    drain(a), drain(b)
    row = reg.update(b, feeds=[{"stream": "transcription"}])
    got = drain(b)
    eq("an operator's set_feed is sent", [m["feeds"] for m in got], [[{"stream": "transcription"}]])
    check("…with a revision no tag has used", got[0]["rev"] > reg._rev_floor[b], (got[0]["rev"], reg._rev_floor[b]))
    eq("…and the tag's memory of panel b is dropped, so captions leaves it", b in reg._swapped, False)
    reg.show_captions()
    drain(a)
    reg.update(b, feeds=None)

    print("  lang")
    drain(a), drain(b)
    checkin(a, [{"stream": "transcription"}])
    checkin(b, [{"stream": "translation", "lang": "SPANISH"}, {"stream": "alert"}])
    try:
        reg.show_language("FRENCH")
        check("lang for a language that is not enabled is refused", False)
    except ValueError as exc:
        check("lang for a language that is not enabled is refused", "not switched on" in str(exc), str(exc))
    eq("lang sets the translation feed on panels showing captions (one is on Spanish already)",
       reg.show_language("SPANISH"), "Spanish set on 1 panel.")
    eq("…a (transcription panel becomes Spanish)", [m["feeds"] for m in drain(a)],
       [[{"stream": "translation", "lang": "SPANISH"}]])
    eq("the source language is the transcription feed", reg.show_language("ENGLISH"), "English set on 2 panels.")
    eq("…panel a", [m["feeds"] for m in drain(a)], [[{"stream": "transcription"}]])
    reg.panels[a].scripts = ["latin"]
    config.save({"targets": {"SPANISH": {"enabled": True}, "RUSSIAN": {"enabled": True}}}, replace=("targets",))
    check("a panel that cannot draw the script is skipped and counted",
          reg.show_language("RUSSIAN").endswith("cannot draw it.") or "0 panels" in reg.show_language("RUSSIAN"))

    print("  lyrics switched off for panels")
    config.save({"propresenter": {"enabled": True, "host": "127.0.0.1", "port": 50402,
                                  "lyrics": {"viewer": False, "screen": True, "overlay": False, "panels": False}}},
                replace=("propresenter",))
    try:
        reg.show_lyrics()
        check("lyrics refuses while panels may not show the lyrics", False)
    except ValueError as exc:
        check("lyrics refuses while panels may not show the lyrics", "switched off" in str(exc), str(exc))


asyncio.run(panels_scenario())

# -------------------------------------------------------------------------------------------------- settings
print("settings")
from app.modules.propresenter import propresenter as pp2  # noqa: E402
eq("tags are off by default, settle 1.5 s", pp2.default_settings()["tags"], {"enabled": False, "settle_s": 1.5})
eq("a hand-edited config with bad values falls back", pp2.clean_stored({"tags": {"enabled": "yes", "settle_s": "soon"}})["tags"],
   {"enabled": False, "settle_s": 1.5})
eq("only a real true switches them on", pp2.clean_stored({"tags": {"enabled": True}})["tags"]["enabled"], True)
eq("settle outside 0.2 to 30 s is refused with a sentence", pp2.tag_fields({"settle_s": 0.01})[1],
   "The settle time is a number of seconds from 0.2 to 30.")
eq("settle in range is kept", pp2.tag_fields({"settle_s": 2})[0], {"settle_s": 2.0})

print()
print("FAILED: " + ", ".join(fails) if fails else "all passed")
sys.exit(1 if fails else 0)
