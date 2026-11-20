"""Transcription backends (app/transcribe.py, issue 320): which backend a feed runs on and the warning when it is
not the one chosen, the Scribe v2 Realtime session against a real local WebSocket server that speaks the Vercel
AI Gateway's streaming-transcription frames, a backend registered the way a module registers one driving a feed
end to end through the engine, and the cost every session reports. Offline.
Run: python tests/test_transcribe.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import await_for, check, done, eq, section, tmpdir  # noqa: E402

os.environ["RELAY_STATE_DIR"] = tmpdir()

import websockets  # noqa: E402

from app import config, engine as engine_mod, realtime, transcribe  # noqa: E402
from app.hub import hub  # noqa: E402


def cfg_with(**over) -> dict:
    base = {"realtime": {"backend": "scribe"}, "openai_api_key": "", "vercel_api_key": ""}
    rt = over.pop("realtime", {})
    base.update(over)
    base["realtime"] = {**base["realtime"], **rt}
    return base


with section("which backend runs"):
    eq("Scribe is the default", config.DEFAULTS["realtime"]["backend"], "scribe")
    b, warn = transcribe.resolve(cfg_with(vercel_api_key="vk_1"))
    check("Scribe runs with the Vercel key and no warning", b.id == "scribe" and warn is None, (b.id, warn))
    b, warn = transcribe.resolve(cfg_with(openai_api_key="sk-1"))
    check("without the Vercel key an OpenAI install keeps running on OpenAI, with a warning",
          b.id == "openai" and warn and "Vercel" in warn, (b.id, warn))
    b, warn = transcribe.resolve(cfg_with(openai_api_key="sk-1", realtime={"backend": "nonesuch"}))
    check("an unknown backend falls back to OpenAI with a warning",
          b.id == "openai" and warn and "nonesuch" in warn, (b.id, warn))
    b, warn = transcribe.resolve(cfg_with(realtime={"backend": "openai"}, openai_api_key="sk-1"))
    check("OpenAI chosen runs with no warning", b.id == "openai" and warn is None, (b.id, warn))
    b, warn = transcribe.resolve(cfg_with())
    check("with no key at all the chosen backend is returned, so the start names its key",
          b.id == "scribe" and b.key_setting == "vercel_api_key", (b.id, warn))
    check("no backend translates on its own any more (#496)", not any(hasattr(x, "translates") for x in transcribe.backends()))
    saved = config.save({"realtime": {"backend": ""}})
    eq("an empty saved backend means the default", saved["realtime"]["backend"], "scribe")
    try:
        transcribe.unregister_backend("openai")
        removed = True
    except ValueError:
        removed = False
    check("a built-in backend cannot be removed", not removed)


class Gateway:
    """The AI Gateway's streaming transcription socket: records what the client sends, plays a script back."""

    def __init__(self, script):
        self.script = script
        self.urls, self.protocols, self.frames = [], [], []
        self.server = None

    async def handler(self, ws):
        self.urls.append(ws.path)
        self.protocols.append(ws.subprotocol)
        start = json.loads(await ws.recv())
        self.frames.append(start)
        got_audio = asyncio.create_task(self._audio(ws))
        for part in self.script:
            await ws.send(json.dumps(part))
            await asyncio.sleep(0)
        await got_audio
        await ws.wait_closed()

    async def _audio(self, ws):
        try:
            self.frames.append(await ws.recv())
        except websockets.ConnectionClosed:
            pass

    async def open(self):
        self.server = await websockets.serve(self.handler, "127.0.0.1", 0, subprotocols=[
            "ai-gateway-transcription.v1", "ai-gateway-auth.vk_secret"])
        return f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}/v4/ai/transcription-model"

    async def close(self):
        self.server.close()
        await self.server.wait_closed()


def scribe(base_url, **kw):
    got = {"d": [], "f": []}
    s = transcribe.ScribeSession(name="transcribe", api_key="vk_secret", source_language="ENGLISH",
                                 on_source_delta=got["d"].append, on_source_final=got["f"].append,
                                 base_url=base_url, **kw)
    return s, got


async def scenario_scribe_frames():
    gw = Gateway([
        {"type": "stream-start", "warnings": []},
        {"type": "transcript-delta", "delta": "Hello "},
        {"type": "transcript-delta", "delta": "there."},
        {"type": "transcript-final", "text": "Hello there."},
        {"type": "transcript-delta", "delta": "Next"},
    ])
    s, got = scribe(await gw.open())
    q = asyncio.Queue()
    s.start(q)
    await q.put(b"\x01\x02" * 24000)  # one second of 24 kHz PCM
    check("it connects", await await_for(lambda: s.state == "connected"))
    check("and delivers the deltas and one final", await await_for(lambda: got["d"] == ["Hello ", "there.", "Next"]))
    query = parse_qs(urlsplit(gw.urls[0]).query)
    eq("the model rides the URL", query, {"ai-model-id": ["elevenlabs/scribe-v2-realtime"]})
    eq("the gateway protocol is negotiated", gw.protocols[0], "ai-gateway-transcription.v1")
    start = gw.frames[0]
    eq("the first frame starts the stream at 24 kHz PCM", start["type"], "transcription-stream.start")
    eq("...with the audio format", start["inputAudioFormat"], {"type": "audio/pcm", "rate": 24000})
    eq("...the language and voice-activity commits and word timestamps",
       start["providerOptions"]["elevenlabs"],
       {"languageCode": "en", "streaming": {"commitStrategy": "vad", "includeTimestamps": True,
                                            "vadSilenceThresholdSecs": 0.8}})
    check("audio goes as binary frames", await await_for(lambda: len(gw.frames) > 1) and
          isinstance(gw.frames[1], bytes) and gw.frames[1] == b"\x01\x02" * 24000, gw.frames[1:])
    eq("the final commits the line the deltas showed, once", got["f"], ["Hello there."])
    health = s.health()
    eq("a second of audio is counted", health["audio_s"], 1.0)
    eq("and priced at Vercel's $0.39 an hour", health["cost_usd"], round(0.39 / 3600, 4))
    eq("the backend is named in health", (health["backend"], health["price_per_hour"]), ("scribe", 0.39))
    await s.stop()
    await gw.close()


async def scenario_final_without_deltas():
    gw = Gateway([
        {"type": "transcript-partial", "text": "Good"},
        {"type": "transcript-partial", "text": "Good morning, Evie."},
        {"type": "transcript-partial", "text": "Good morning, everyone. Welcome"},
        {"type": "transcript-final", "text": "Good morning, everyone. Welcome to church today."},
    ])
    s, got = scribe(await gw.open())
    s.start(asyncio.Queue())
    check("partials with no deltas still reach the screen as growing text",
          await await_for(lambda: got["d"] == ["Good", " morning, Evie."]), got)
    check("a revised partial is not shown twice, and the final is the settled line",
          await await_for(lambda: got["f"] == ["Good morning, everyone. Welcome to church today."]), got)
    await s.stop()
    await gw.close()


async def scenario_final_only():
    gw = Gateway([{"type": "transcript-final", "text": "Amen."}])
    s, got = scribe(await gw.open())
    s.start(asyncio.Queue())
    check("a turn that arrives as one final is committed whole", await await_for(lambda: got["f"] == ["Amen."]), got)
    await s.stop()
    await gw.close()


async def scenario_auth_error_is_fatal():
    gw = Gateway([{"type": "error", "error": {"name": "GatewayAuthenticationError",
                                              "message": "Unauthenticated request to AI Gateway: invalid api key"}}])
    s, got = scribe(await gw.open())
    s.start(asyncio.Queue())
    check("a rejected key stops the retries and says why",
          await await_for(lambda: s.fatal and s.state == "error" and "invalid api key" in (s.error or "").lower()),
          (s.state, s.error))
    await s.stop()
    await gw.close()


for name, scenario in [("scribe frames", scenario_scribe_frames), ("partials without deltas", scenario_final_without_deltas),
                       ("a turn as one final", scenario_final_only), ("a rejected key", scenario_auth_error_is_fatal)]:
    with section(f"Scribe session: {name}"):
        asyncio.run(scenario())


# ------------------------------------------------------------------ a backend registered the way a module does
class ScriptedSession(realtime.RealtimeSession):
    """A module's backend: no network, it speaks a fixed script as soon as audio is attached."""

    kind = "transcribe"
    backend = "scripted"
    price_per_hour = 1.0

    def __init__(self, *, source_language, on_source_delta, on_source_final, **kw):
        kw.setdefault("model", "scripted")
        super().__init__(**kw)
        self._d, self._f = on_source_delta, on_source_final

    def start(self, audio_q):
        self._audio_q = audio_q
        self._d("Welcome ")
        self._d("home.")
        self._f("Welcome home.")
        self._set_state("connected")

    async def stop(self):
        self._set_state("idle")


class FakeCapture:
    """What the engine asks of the audio capture, without a sound card."""

    error = None

    def __init__(self):
        self.consumers = []

    def start(self, *a, **kw):
        return {}

    def stop(self):
        pass

    def status(self):
        return {}

    def add_consumer(self):
        q = asyncio.Queue()
        self.consumers.append(q)
        return q

    def remove_consumer(self, q):
        self.consumers.remove(q)


async def scenario_engine_runs_a_registered_backend():
    transcribe.register_backend(transcribe.Backend(
        id="scripted", label="Scripted", session=lambda cfg, key, **kw: ScriptedSession(api_key=key, **kw),
        key_setting=None, price_per_hour=1.0))
    config.save({"realtime": {"backend": "scripted"}, "targets": {}})
    eng = engine_mod.Engine()
    eng.bind(asyncio.get_running_loop())
    eng.capture = FakeCapture()
    status = await eng.start()
    check("the engine ran the module's backend", eng.backend and eng.backend.id == "scripted" and
          status["backend"]["id"] == "scripted" and status["backend"]["warning"] is None, status["backend"])
    check("every backend gets the text pipeline", eng.translator is not None and
          isinstance(eng.transcriber, ScriptedSession))
    lines = hub.channel(engine_mod.TRANSCRIPTION_STREAM, config.get()["source_language"]).snapshot()
    check("its lines reached the viewers' transcription feed", "Welcome home." in json.dumps(lines), lines)
    check("and the audio queue was attached to it", len(eng.capture.consumers) == 1)
    check("the status lists every registered backend", {"scribe", "openai", "scripted"} <=
          {b["id"] for b in status["backends"]}, status["backends"])
    check("the session reports its cost", status["sessions"][0]["backend"] == "scripted" and
          status["sessions"][0]["cost_usd"] == 0.0, status["sessions"][0])
    await eng.stop()
    transcribe.unregister_backend("scripted")


async def scenario_fallback_warning_in_status():
    config.save({"realtime": {"backend": "scribe"}, "openai_api_key": "sk-test",
                 "vercel_api_key": "", "targets": {}})
    eng = engine_mod.Engine()
    eng.bind(asyncio.get_running_loop())
    eng.capture = FakeCapture()
    status = await eng.start()
    check("OpenAI ran, and the status says why", status["backend"]["id"] == "openai" and
          "Vercel" in (status["backend"]["warning"] or ""), status["backend"])
    await eng.stop()


async def scenario_start_refused_names_the_key():
    config.save({"realtime": {"backend": "scribe"}, "openai_api_key": "", "vercel_api_key": ""})
    eng = engine_mod.Engine()
    eng.bind(asyncio.get_running_loop())
    eng.capture = FakeCapture()
    try:
        await eng.start()
        key = None
    except engine_mod.StartRefused as exc:
        key = exc.key
    eq("a start with no usable key is refused naming the Vercel key", key, "no_vercel_key")


for name, scenario in [("a module's backend through the engine", scenario_engine_runs_a_registered_backend),
                       ("fallback warning in the status", scenario_fallback_warning_in_status),
                       ("a refused start", scenario_start_refused_names_the_key)]:
    with section(name):
        asyncio.run(scenario())

done()
