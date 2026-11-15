"""app/realtime.py against a fake WebSocket: the connection loop (session.update first, audio pumped, events
dispatched), reconnects with backoff, fatal errors that stop retrying, the idle segmenter that stands in for
the done event the translations endpoint never sends, and the transcription session's turn commits.
Offline. Run: python tests/test_realtime.py
"""
import asyncio
import base64
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import await_for, check, done, eq, section  # noqa: E402

from app import realtime  # noqa: E402


class FakeWS:
    """Scripted server: hands out `events`, then waits until closed (or ends if `hang_up`)."""

    def __init__(self, events=(), hang_up=False):
        self.events = list(events)
        self.hang_up = hang_up
        self.sent = []
        self.closed = asyncio.Event()

    async def send(self, raw):
        self.sent.append(json.loads(raw))

    async def close(self):
        self.closed.set()

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for evt in self.events:
            yield json.dumps(evt)
            await asyncio.sleep(0)
        if not self.hang_up:
            await self.closed.wait()


class Server:
    """Stands in for realtime._connect: each connect takes the next FakeWS, or raises the next exception."""

    def __init__(self, *script):
        self.script = list(script)
        self.urls, self.headers, self.made = [], [], []

    async def connect(self, url, headers):
        self.urls.append(url)
        self.headers.append(headers)
        nxt = self.script.pop(0) if self.script else FakeWS()
        if isinstance(nxt, Exception):
            raise nxt
        self.made.append(nxt)
        return nxt


def translate_session(**kw):
    got = {"sd": [], "sf": [], "td": [], "tf": []}
    statuses = []
    s = realtime.TranslationRelaySession(
        name="FRENCH", api_key="sk-test", model="gpt-realtime-translate", source_language="ENGLISH",
        target="FRENCH", on_source_delta=got["sd"].append, on_source_final=got["sf"].append,
        on_target_delta=got["td"].append, on_target_final=got["tf"].append,
        on_status=lambda: statuses.append(s.state), **kw)
    return s, got, statuses


async def scenario_connect_and_dispatch():
    server = Server(FakeWS([
        {"type": "session.created"},
        {"type": "session.input_transcript.delta", "delta": "Hello "},
        {"type": "session.output_transcript.delta", "delta": {"text": "Bonjour "}},
        {"type": "session.input_transcript.delta", "delta": "world."},
        {"type": "session.input_transcript.done"},
        {"type": "session.output_transcript.done", "transcript": "Bonjour le monde."},
        {"type": "session.output_transcript.delta", "delta": "Encore"},
    ]))
    realtime._connect = server.connect
    s, got, statuses = translate_session(transcribe_model="gpt-live-transcribe", noise_reduction="near_field")
    q = asyncio.Queue()
    s.start(q)
    await q.put(b"\x01\x02" * 4)
    check("it connects", await await_for(lambda: s.state == "connected"))
    ws = server.made[0]
    eq("to the translations endpoint for the model", server.urls[0],
       "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate")
    eq("with the key as a bearer token", server.headers[0], {"Authorization": "Bearer sk-test"})
    eq("session.update is the first thing sent, with the output language and the opt-in input settings",
       ws.sent[0], {"type": "session.update", "session": {"audio": {
           "output": {"language": "fr"},
           "input": {"transcription": {"model": "gpt-live-transcribe"}, "noise_reduction": {"type": "near_field"}}}}})
    check("audio is pumped as base64 under the session.-prefixed append",
          await await_for(lambda: len(ws.sent) > 1))
    eq("…the frame itself", ws.sent[1], {"type": "session.input_audio_buffer.append",
                                       "audio": base64.b64encode(b"\x01\x02" * 4).decode()})
    await await_for(lambda: got["td"] == ["Bonjour ", "Encore"])
    eq("source deltas reach the source sink", got["sd"], ["Hello ", "world."])
    eq("target deltas (dict form too) reach the target sink", got["td"], ["Bonjour ", "Encore"])
    eq("a done with no text commits what was buffered", got["sf"], ["Hello world."])
    eq("a done with a transcript commits that", got["tf"], ["Bonjour le monde."])
    check("health reports the connection", s.health()["state"] == "connected" and s.health()["kind"] == "translate"
          and s.health()["connected_at"] and s.health()["last_delta_ts"], s.health())

    await s.stop()
    eq("stopping flushes what was still in flight", got["tf"], ["Bonjour le monde.", "Encore"])
    eq("stopped: idle", s.state, "idle")
    check("…and the socket was closed", ws.closed.is_set())
    eq("the states it went through", [x for i, x in enumerate(statuses) if i == 0 or statuses[i - 1] != x][:2],
       ["connecting", "connected"])


async def scenario_reconnect():
    server = Server(OSError("connection refused"), FakeWS([{"type": "session.output_transcript.delta",
                                                            "delta": "Salut"}], hang_up=True), FakeWS())
    realtime._connect = server.connect
    s, got, _ = translate_session()
    s.start(asyncio.Queue())
    check("a refused connection is an error, not fatal",
          await await_for(lambda: s.reconnects >= 1) and not s.fatal and "connection refused" in (s.error or ""))
    check("it reconnects after the backoff", await await_for(lambda: len(server.made) >= 1, 3))
    check("a socket that hangs up mid-line flushes the line",
          await await_for(lambda: got["tf"] == ["Salut"], 3), got)
    check("…and it reconnects again (backoff reset after a good connect)",
          await await_for(lambda: len(server.made) >= 2, 3) and s.reconnects == 2, s.reconnects)
    eq("each reconnect counted in health", s.health()["reconnects"], 2)
    await s.stop()


async def scenario_fatal():
    server = Server(FakeWS([{"type": "error", "error": {"message": "Incorrect API key provided: sk-***"}}], hang_up=True))
    realtime._connect = server.connect
    s, _, _ = translate_session()
    s.start(asyncio.Queue())
    check("an error event naming a bad key is fatal", await await_for(lambda: s.fatal))
    check("…the session ends in error with the API's own words",
          await await_for(lambda: s._task.done()) and s.state == "error" and "Incorrect API key" in s.error, s.error)
    eq("…and does not retry", (len(server.urls), s.reconnects), (1, 0))

    server = Server(Exception("server rejected WebSocket connection: HTTP 401"))
    realtime._connect = server.connect
    s, _, _ = translate_session()
    s.start(asyncio.Queue())
    check("a 401 on connect is fatal too", await await_for(lambda: s._task.done()) and s.fatal and s.state == "error")
    eq("…tried once", len(server.urls), 1)

    server = Server(FakeWS([{"type": "response.failed", "error": "rate limited"}], hang_up=True), FakeWS())
    realtime._connect = server.connect
    s, _, _ = translate_session()
    s.start(asyncio.Queue())
    check("a *.failed event that is not fatal keeps retrying",
          await await_for(lambda: len(server.made) == 2, 3) and not s.fatal, (len(server.made), s.error))
    await s.stop()
    for msg in ("Incorrect API key", "insufficient_quota", "HTTP 403", "model_not_found"):
        check(f"fatal: {msg}", realtime._is_fatal(msg))
    check("not fatal: a timeout", not realtime._is_fatal("TimeoutError: timed out"))
    check("not fatal: nothing", not realtime._is_fatal(None))


async def scenario_segmenter():
    s, got, _ = translate_session(segment_idle_s=0.05, segment_max_idle_s=0.4)
    eq("segment times can be set per session", (s.SEGMENT_IDLE_S, s.SEGMENT_MAX_IDLE_S), (0.05, 0.4))
    seg = asyncio.create_task(s._commit_idle_segments())
    s._handle({"type": "session.output_transcript.delta", "delta": "The moon."})
    check("a line ending a sentence commits once its deltas go quiet",
          await await_for(lambda: got["tf"] == ["The moon."], 2), got["tf"])
    s._handle({"type": "session.output_transcript.delta", "delta": "Los cerez"})
    await asyncio.sleep(0.3)
    eq("a pause mid-sentence waits for more", got["tf"], ["The moon."])
    check("past the longest wait it commits up to the last space, keeping the half word",
          await await_for(lambda: got["tf"] == ["The moon.", "Los"], 2) and s._partials["target"] == "cerez",
          (got["tf"], s._partials))
    s._handle({"type": "session.output_transcript.delta", "delta": "os."})
    check("the word is whole when the rest arrives", await await_for(lambda: got["tf"][-1:] == ["cerezos."], 2), got)
    s._handle({"type": "session.output_transcript.delta", "delta": "Supercalifragilistic"})
    await asyncio.sleep(0.6)
    eq("a single unfinished word is never split", got["tf"][-1], "cerezos.")
    s._partials["target"] = ""
    s._handle({"type": "session.output_transcript.delta", "delta": " ."})
    await asyncio.sleep(0.6)
    eq("punctuation with no word is held for the next delta", (got["tf"][-1], s._partials["target"]),
       ("cerezos.", " ."))
    seg.cancel()

    s, got, _ = translate_session()
    realtime.UTTERANCE_GAP_S, gap = 0.0, realtime.UTTERANCE_GAP_S
    try:
        s._handle({"type": "session.output_transcript.delta", "delta": "So,"})
        s._handle({"type": "session.output_transcript.delta", "delta": "Why"})
        s._handle({"type": "session.output_transcript.delta", "delta": "cerez"})
        s._handle({"type": "session.output_transcript.delta", "delta": "os"})
    finally:
        realtime.UTTERANCE_GAP_S = gap
    eq("after a pause, a clause end gets a space before the next utterance; a word does not",
       s._partials["target"], "So, Whycerezos")
    s._handle({"type": "session.unknown_event"})
    s._handle({"type": "session.updated"})
    eq("unknown and informational events change nothing", s._partials["target"], "So, Whycerezos")


async def scenario_transcription():
    sd, sf = [], []
    s = realtime.TranscriptionSession(name="src", api_key="k", model="gpt-live-transcribe",
                                      source_language="ENGLISH", on_source_delta=sd.append,
                                      on_source_final=sf.append, noise_reduction="far_field",
                                      segment_idle_s=0.5, segment_max_idle_s=5)
    eq("no model in the transcription URL", s._url(), "wss://api.openai.com/v1/realtime?intent=transcription")
    eq("the transcription session update",
       s._session_update(), {"type": "session.update", "session": {"type": "transcription", "audio": {"input": {
           "format": {"type": "audio/pcm", "rate": 24000},
           "transcription": {"model": "gpt-live-transcribe", "languages": ["en"], "delay": "low"},
           "turn_detection": None, "noise_reduction": {"type": "far_field"}}}}})
    s.COMMIT_GAP_S, s.MAX_TURN_S = 0.05, 0.2
    q = asyncio.Queue()
    s._audio_q = q
    ws = FakeWS()
    pump = asyncio.create_task(s._pump_audio(ws))
    await q.put(b"\0\0")
    check("a pause after audio commits the turn",
          await await_for(lambda: [m["type"] for m in ws.sent] == ["input_audio_buffer.append",
                                                                   "input_audio_buffer.commit"]), ws.sent)
    ws.sent.clear()
    for _ in range(12):                    # a long unbroken turn: frames every 30ms for ~0.36s
        await q.put(b"\0\0")
        await asyncio.sleep(0.03)
    commits = [m for m in ws.sent if m["type"] == "input_audio_buffer.commit"]
    check("a turn longer than MAX_TURN_S is committed without a pause", len(commits) >= 1, len(commits))
    check("…the appends use the un-prefixed type", ws.sent[0]["type"] == "input_audio_buffer.append")
    pump.cancel()

    s._handle({"type": "conversation.item.input_audio_transcription.delta", "delta": "Good morning"})
    s._handle({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Good morning"})
    eq("a completed event commits what is in flight, not its own transcript again", sf, ["Good morning"])
    s._handle({"type": "conversation.item.input_audio_transcription.completed", "transcript": "Good morning"})
    eq("a completed event with nothing in flight adds nothing", sf, ["Good morning"])
    eq("a target event means nothing to a transcription session",
       (s._delta_channel("session.output_transcript.delta"), s._final_channel("response.text.done")), (None, None))


async def scenario_base():
    base = realtime.RealtimeSession(name="b", api_key="k", model="m")
    for fn in (base._url, base._session_update):
        try:
            fn()
            check(f"{fn.__name__} must be overridden", False)
        except NotImplementedError:
            check(f"{fn.__name__} must be overridden", True)
    eq("the base session maps no events", (base._delta_channel("x"), base._final_channel("x")), (None, None))
    base._handle({"type": "session.input_transcript.delta", "delta": "x"})
    eq("…and buffers nothing", base._partials, {})
    eq("health with no queue reports nothing dropped", base.health()["dropped_audio"], 0)


async def main():
    real_connect = realtime._connect
    try:
        for name, scenario in (("connect and dispatch", scenario_connect_and_dispatch),
                               ("reconnect", scenario_reconnect), ("fatal", scenario_fatal),
                               ("segmenter", scenario_segmenter), ("transcription", scenario_transcription),
                               ("base", scenario_base)):
            with section(name):
                await asyncio.wait_for(scenario(), 20)
    finally:
        realtime._connect = real_connect


asyncio.run(main())
done()
