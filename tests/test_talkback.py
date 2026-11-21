"""Talkback (#93): the engine that transcribes a bus lane on a session of its own. Settings, refusals (no lane, no key,
rehearsal), text arriving while the main feed is stopped and the other way round, a dropped talkback session
reconnecting without touching the main one, silence not being sent, the cost beside the main feed's, and no talkback
text in the caption feeds or the recording. A backend registered the way a module registers one stands in for the
transcription service; the lane is fed by a fake device. Offline.
Run: .venv/bin/python tests/test_talkback.py
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import await_for, check, done, eq, section, tmpdir  # noqa: E402

os.environ["RELAY_STATE_DIR"] = tmpdir()
os.environ["RELAY_CONFIG"] = os.path.join(tmpdir(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DEMO", None)

import numpy as np  # noqa: E402

from app import audio, audiobus, config, engine as engine_mod, modules as modmod, realtime, transcribe  # noqa: E402
from app.hub import hub  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.talkback import MODULE  # noqa: E402
from app.modules.talkback.service import Refused, TalkbackEngine, clean_settings  # noqa: E402

RATE = audio.LANE_RATE


# -- a transcription backend that never leaves the machine -----------------------------------------------------------
class FakeSession(realtime.RealtimeSession):
    """What a backend's session is: it takes the capture's PCM, reports state and cost, and says what it is told to."""

    kind, backend, price_per_hour = "transcribe", "tbfake", 3600.0   # $1 a second, so cost is easy to read

    def __init__(self, *, on_source_delta, on_source_final, **kw):
        kw.pop("source_language", None)
        super().__init__(model="fake", api_key="", **kw)
        self.delta, self.final = on_source_delta, on_source_final

    async def _run(self):
        self._set_state("connected")
        while True:
            self._count(await self._audio_q.get())

    def say(self, text):
        self.delta(text)
        self.final(text)

    def drop(self):
        """A dropped connection the session recovers from on its own."""
        self.reconnects += 1
        self._set_state("reconnecting")
        self._set_state("connected")


SESSIONS: list[FakeSession] = []


def make_session(cfg, key, **kw):
    s = FakeSession(**kw)
    SESSIONS.append(s)
    return s


transcribe.register_backend(transcribe.Backend(id="tbfake", label="Fake", session=make_session))
transcribe.register_backend(transcribe.Backend(id="tbkey", label="Keyed", session=make_session,
                                               key_setting="vercel_api_key", key_label="a Vercel AI Gateway key"))


# -- a device on the bus ---------------------------------------------------------------------------------------------
class FakeInput(audio.LaneInput):
    def __init__(self, owner):
        self.owner, self.deliver = owner, None

    def open(self, selector, deliver):
        self.deliver = deliver
        self.owner.live.append(self)
        return audio.SourceInfo(rate=RATE, device={"label": "Fake mic", "channels": 1}, channel="all")

    def close(self):
        self.deliver = None
        if self in self.owner.live:
            self.owner.live.remove(self)

    def alive(self):
        return self.deliver is not None


class FakeSource(audio.AudioSource):
    id, label = "tbmic", "Fake mic"

    def __init__(self):
        self.live = []

    def lane_input(self):
        return FakeInput(self)

    def open(self, selector, channel, deliver):
        raise audio.SourceError("mono not used")

    def push(self, block):
        for li in list(self.live):
            if li.deliver:
                li.deliver(block.reshape(-1, 1))


MIC = FakeSource()
audio.register_source(MIC)
TONE = (0.5 * np.sin(2 * np.pi * 440 * np.arange(RATE) / RATE)).astype(np.float32)      # one second of speech
QUIET = np.zeros(RATE, dtype=np.float32)


async def speak(seconds=1, quiet=False):
    """Put `seconds` of audio on the device in 50 ms blocks, giving the pumps time to carry it."""
    block = QUIET if quiet else TONE
    for _ in range(seconds * 20):
        MIC.push(block[: RATE // 20])
        await asyncio.sleep(0.05)       # allow-sleep: paces the feeder at real time; the speech gate and loss watchdog use the wall clock


def settings(**over):
    return config.save({"talkback": {"lane": 3, "backend": "tbfake", **over}})


with section("settings"):
    eq("the defaults mean no lane, the main feed's backend and language",
       clean_settings(None), {"lane": 0, "backend": "", "source_language": ""})
    eq("a lane outside 1 to 32 is no lane",
       [clean_settings({"lane": n})["lane"] for n in (0, 33, -1, True, "4", 7)], [0, 0, 0, 0, 0, 7])
    eq("a backend is kept lower case and a language only when it exists",
       clean_settings({"backend": " TbFake ", "source_language": "NOWHERE"}),
       {"lane": 0, "backend": "tbfake", "source_language": ""})
    saved = config.save({"talkback": {"lane": 9, "junk": 1}})
    eq("config keeps only what the module's clean() allows", saved["talkback"],
       {"lane": 9, "backend": "", "source_language": ""})
    config.save({"talkback": {"lane": 0}})


async def scenarios():
    loop = asyncio.get_running_loop()
    config.save({"modules": {"talkback": {"enabled": True}}})
    await registry.start_enabled()
    from app.modules.talkback.service import service

    check("switching the module on registers its own lane source", audio.get_source("talkback_lane") is not None)
    check("and offers its lines to other modules", modmod.using("talkback.lines") is not None)

    # -- the main engine and the bus ------------------------------------------------------------------------------
    main = engine_mod.engine
    main.bind(loop)

    def bus_on():
        config.save({"audio_bus": {"enabled": True, "lanes": [
            {"lane": 3, "name": "Talkback", "source": "tbmic", "device": "mic", "channel": 0},
            {"lane": 4, "name": "Room", "source": "tbmic", "device": "mic", "channel": 0}]}})
        audiobus.bus.apply(config.get()["audio_bus"])
        return bool(MIC.live)

    with section("a start that cannot go ahead says why"):
        try:
            await service.start()
            key = None
        except Refused as exc:
            key = exc.key
        eq("no lane chosen: refused", key, "no_lane")
        check("the sentence is shown in status", "lane" in (service.status()["error"] or ""), service.status()["error"])
        settings(backend="tbkey")
        try:
            await service.start()
            key = None
        except Refused as exc:
            key = exc.key
        eq("a backend whose key is missing names it", key, "no_vercel_key")
        config.save({"vercel_api_key": "vk_1"})
        os.environ["RELAY_DEMO"] = "1"
        try:
            await service.start()
            key = None
        except Refused as exc:
            key = exc.key
        os.environ.pop("RELAY_DEMO")
        eq("rehearsal mode refuses, like the main engine", key, "rehearsal")
        check("and opened no session", not SESSIONS, SESSIONS)
        settings()
        try:
            await service.start()
            key = None
        except Refused as exc:
            key = exc.key
        eq("with the audio bus off it is the capture's own refusal", key, "capture")
        eq("a refused start leaves it stopped", service.running, False)

    check("the fake mic feeds both lanes", bus_on())

    # -- talkback alone ------------------------------------------------------------------------------------------------
    with section("talkback text arrives while the main feed is stopped"):
        settings()
        heard = []
        service.subscribe(heard.append)
        st = await service.start()
        eq("running on its lane and backend", (st["running"], st["lane"], st["backend"]["id"]), (True, 3, "tbfake"))
        eq("the main feed is not running", main.running, False)
        session = service.session
        await speak(1)
        check("speech is sent to the session", await await_for(lambda: session.health()["audio_s"] > 0.3),
              session.health())
        session.say("Camera two, tight on the pastor.")
        eq("the line is kept", [x["text"] for x in service.recent()], ["Camera two, tight on the pastor."])
        check("and told to listeners as a delta, then a line",
              [e["type"] for e in heard] == ["delta", "line"], heard)
        check("the lines other modules ask for are the same ones",
              modmod.using("talkback.lines")["recent"]()[-1]["text"].startswith("Camera two"))

    with section("silence is not sent"):
        await speak(1, quiet=True)         # the gate holds for 0.6 s after the last speech
        before = session.health()["audio_s"]
        await speak(2, quiet=True)
        await asyncio.sleep(0.2)   # allow-sleep: shows that no audio arrives
        eq("a quiet channel adds no billed audio", session.health()["audio_s"], before)

    # -- both at once, then one at a time ------------------------------------------------------------------------
    with section("the main feed and talkback run side by side"):
        lines = []
        hub.add_line_sink(lambda *args: lines.append(args))     # every committed line goes to each sink, as to a recording
        config.save({"audio_lane": 4,
                     "realtime": {"backend": "tbfake"}})
        await main.start()
        check("the main feed runs on its own lane", main.running)
        main_session = main.transcriber
        check("with a session of its own", main_session is not None and main_session is not service.session)
        await speak(1)
        check("both are fed", await await_for(
            lambda: main_session.health()["audio_s"] > 0.3 and service.session.health()["audio_s"] > 0.3))
        service.session.say("Standby lights.")
        main_session.say("Welcome, everyone.")
        eq("talkback text is only in talkback", [x["text"] for x in service.recent()][-1], "Standby lights.")
        main_feed = hub.channel(engine_mod.TRANSCRIPTION_STREAM, config.get()["source_language"])
        eq("the caption feed has the main line and no talkback line",
           [h["text"] for h in main_feed.history], ["Welcome, everyone."])
        eq("a sink on every committed line saw only the main line", [a[3] for a in lines], ["Welcome, everyone."])
        check("no talkback stream exists in the hub", all("talkback" not in k for k in hub.channels), list(hub.channels))
        sb = service.status()
        check("status shows talkback's time beside the main feed's",
              sb["billed"]["audio_s"] > 0 and sb["main_billed"]["audio_s"] > 0 and sb["main_billed"]["running"], sb)

    with section("a dropped talkback session reconnects without touching the main sessions"):
        before = [(main.transcriber, main.transcriber.state, main.transcriber.reconnects)]
        service.session.drop()
        eq("talkback counts the reconnect", service.status()["session"]["reconnects"], 1)
        eq("and is connected again", service.session.state, "connected")
        eq("the main session is the same object, in the same state",
           [(main.transcriber, main.transcriber.state, main.transcriber.reconnects)], before)
        eq("and the main feed never stopped", main.running, True)

    with section("stopping one leaves the other"):
        await service.stop()
        eq("talkback is stopped", (service.running, service.status()["session"]), (False, None))
        check("the main feed carries on", main.running and main.transcriber is main_session)
        main_session.say("Still here.")
        eq("and still publishes", [h["text"] for h in main_feed.history][-1], "Still here.")
        await service.start()
        await main.stop()
        check("stopping the main feed leaves talkback running", service.running and not main.running)
        service.session.say("Talkback alone again.")
        eq("and talkback text still arrives", service.recent()[-1]["text"], "Talkback alone again.")
        hub.line_sinks.clear()

    with section("restart"):
        await speak(1)
        await await_for(lambda: service.session.health()["audio_s"] > 0.3)
        billed = service.status()["billed"]["audio_s"]
        st = await service.restart()
        check("a restart opens a new session", st["running"] and service.session is SESSIONS[-1])
        check("and keeps what was billed so far", st["billed"]["audio_s"] >= billed, (billed, st["billed"]))
        check("the cost is shown", st["billed"]["cost_usd"] is not None and st["billed"]["cost_usd"] >= 1.0, st["billed"])

    with section("it follows the main feed's backend and language until told otherwise"):
        await service.stop()
        config.save({"talkback": {"lane": 3}, "realtime": {"backend": "tbfake"}})
        st = await service.start()
        eq("the backend is the main feed's", st["backend"]["id"], "tbfake")
        eq("so is the language", st["language"], config.get()["source_language"])
        await service.stop()
        config.save({"talkback": {"lane": 3, "backend": "tbkey", "source_language": "ENGLISH"}})
        st = await service.start()
        eq("a backend of its own wins over the main feed's", st["backend"]["id"], "tbkey")
        eq("and a language of its own is kept", st["language"], "ENGLISH")
        await service.stop()

    with section("switching the module off stops it"):
        await service.shutdown()
        eq("nothing is left running", service.running, False)
        MODULE.detach()
        check("the audio source and the lines go with the module",
              audio.get_source("talkback_lane") is None and modmod.using("talkback.lines") is None)
    audiobus.bus.stop()


asyncio.run(scenarios())
done()
