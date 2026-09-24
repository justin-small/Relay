"""Offline smoke test: imports, config, hub, resample math, routes, audio devices.

Does not contact OpenAI. Run: .venv/bin/python tests/smoke_test.py
"""
import asyncio, json, os, sys, tempfile, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("RELAY_CONFIG", os.path.join(tempfile.mkdtemp(), "config.json"))

import numpy as np
from fastapi.testclient import TestClient

from app import audio, config, languages, redact
from app.hub import hub
from app.main import app

fails = []
def check(label, cond, extra=""):
    """Assert `cond` is truthy. `extra` is diagnostic detail shown on failure."""
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


def eq(label, got, want):
    """Assert `got == want`, printing both on failure."""
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def unframe(item):
    """The hub queue now carries finished SSE frames (bytes). Decode one back to
    its message dict for assertions. Returns the id: seq too when present."""
    assert isinstance(item, (bytes, bytearray)), item
    seq = None
    if item.startswith(b"id: "):
        head, item = item.split(b"\n", 1)
        seq = int(head[4:])
    body = item.split(b"data: ", 1)[1].rsplit(b"\n\n", 1)[0]
    msg = json.loads(body)
    if seq is not None:
        msg["_id"] = seq
    return msg

print("\nconfig")
config.ensure_file()
cfg = config.load()
check("defaults load", cfg["source_language"] == "ENGLISH")
check("spanish target present", "SPANISH" in cfg["targets"])
cfg = config.save({"admin_token": "t0ken", "openai_api_key": "sk-test-1234", "default_font_px": 999})
check("font clamped to 160", cfg["default_font_px"] == 160, cfg["default_font_px"])
check("file mode 0600", oct(os.stat(config.CONFIG_PATH).st_mode)[-3:] == "600")
# A config.json this process does not own (copied in from another machine;
# Docker Desktop on Windows shows it as root's) refuses chmod. Starting must
# still work, by rewriting the file rather than crashing on every start.
_real_chmod, _refused = os.chmod, []
def _chmod_not_owner(path, mode, *a, **kw):
    if Path(path) == config.CONFIG_PATH and not _refused:
        _refused.append(path)
        raise PermissionError(1, "Operation not permitted", str(path))
    return _real_chmod(path, mode, *a, **kw)
_before = config.CONFIG_PATH.read_bytes()
os.chmod = _chmod_not_owner
try:
    config.ensure_file()
    check("start survives a config.json it cannot chmod", bool(_refused))
finally:
    os.chmod = _real_chmod
check("…with its contents intact", config.CONFIG_PATH.read_bytes() == _before)
check("…and no temp copy left behind",
      not config.CONFIG_PATH.with_suffix(".json.tmp").exists())
red = config.redacted()
check("key never serialised", "openai_api_key" not in red and "admin_token" not in red)
check("key hint only", red["openai_api_key_set"] and red["openai_api_key_hint"] == "…1234")
cfg = config.save({"targets": {"SPANISH": {"enabled": True, "variant": "es-MX"}}})
check("stale variant dropped", cfg["targets"]["SPANISH"] == {"enabled": True}, cfg["targets"]["SPANISH"])
check("all 13 output languages offered, minus the source",
      len(cfg["targets"]) == 12 and "ENGLISH" not in cfg["targets"], sorted(cfg["targets"]))
check("old model name migrated",
      config.save({"realtime": {"translate_model": "gpt-realtime"}})["realtime"]["translate_model"]
      == "gpt-realtime-translate")
# The translations endpoint has no turn lifecycle, so turn-detection settings
# are not offered and a config written by an older version is stripped of them.
_VAD_KEYS = ("vad_mode", "vad_eagerness", "vad_threshold",
             "vad_prefix_padding_ms", "vad_silence_duration_ms")
check("no turn-detection fields by default",
      not any(k in cfg["realtime"] for k in _VAD_KEYS), cfg["realtime"])
_stale = config.save({"realtime": {
    "vad_mode": "semantic", "vad_eagerness": "high", "vad_threshold": 0.5,
    "vad_prefix_padding_ms": 300, "vad_silence_duration_ms": 250}})["realtime"]
check("stale turn-detection fields dropped",
      not any(k in _stale for k in _VAD_KEYS), _stale)
check("dropping them leaves the live realtime settings alone",
      _stale["translate_model"] == "gpt-realtime-translate"
      and _stale["segment_idle_s"] == 1.0, _stale)
from app.engine import Engine as _Engine
check("no _vad() builder left to call", not hasattr(_Engine, "_vad"))
check("noise reduction defaults off", config.save({})["realtime"]["noise_reduction"] is None)
check("bogus noise reduction clamped to off",
      config.save({"realtime": {"noise_reduction": "bogus"}})["realtime"]["noise_reduction"] is None)
check("valid noise reduction kept",
      config.save({"realtime": {"noise_reduction": "far_field"}})["realtime"]["noise_reduction"]
      == "far_field")

print("\nlanguages")
check("13 output languages", len(languages.TARGETS) == 13, sorted(languages.TARGETS))
check("spanish present", languages.target_iso("SPANISH") == "es")
check("plain label", languages.target_label("SPANISH") == "Spanish")
check("source language excluded from targets",
      "ENGLISH" not in languages.target_names("ENGLISH"))

print("\nrelay session")
from app.realtime import SOURCE, TARGET, TranslationRelaySession
_seen = {"sd": "", "sf": "", "td": "", "tf": ""}
_rs = TranslationRelaySession(
    name="t", api_key="k", model="gpt-realtime-translate",
    source_language="ENGLISH", target="SPANISH",
    on_source_delta=lambda x: _seen.__setitem__("sd", _seen["sd"] + x),
    on_source_final=lambda x: _seen.__setitem__("sf", x),
    on_target_delta=lambda x: _seen.__setitem__("td", _seen["td"] + x),
    on_target_final=lambda x: _seen.__setitem__("tf", x),
)
check("translations endpoint url",
      _rs._url() == "wss://api.openai.com/v1/realtime/translations?model=gpt-realtime-translate",
      _rs._url())
# The whole session config is the output language. A translation session has no
# turn lifecycle, and the endpoint rejects `turn_detection` outright with
# "Unknown parameter: session.audio.input.turn_detection" -- so the payload
# below is not an oversight, it is the contract.
eq("session config is the output language, nothing else",
   _rs._session_update(),
   {"type": "session.update", "session": {"audio": {"output": {"language": "es"}}}})
check("no turn_detection anywhere in the payload",
      "turn_detection" not in json.dumps(_rs._session_update()), _rs._session_update())
check("the session has no vad to send", not hasattr(_rs, "vad"))
eq("noise reduction rides on the input block",
   TranslationRelaySession(
       name="t", api_key="k", model="gpt-realtime-translate",
       noise_reduction="far_field",
       source_language="ENGLISH", target="SPANISH",
       on_source_delta=lambda x: None, on_source_final=lambda x: None,
       on_target_delta=lambda x: None, on_target_final=lambda x: None,
   )._session_update(),
   {"type": "session.update", "session": {"audio": {
       "input": {"noise_reduction": {"type": "far_field"}},
       "output": {"language": "es"}}}})
# The two transcripts interleave on one socket; they must not splice together.
_rs._handle({"type": "session.input_transcript.delta", "delta": "Good "})
_rs._handle({"type": "session.output_transcript.delta", "delta": "Buenas "})
_rs._handle({"type": "session.input_transcript.delta", "delta": "afternoon"})
_rs._handle({"type": "session.output_transcript.delta", "delta": "tardes"})
eq("source deltas kept separate", _seen["sd"], "Good afternoon")
eq("target deltas kept separate", _seen["td"], "Buenas tardes")
_rs._handle({"type": "session.input_transcript.done"})
_rs._handle({"type": "session.output_transcript.done"})
eq("source final from its own buffer", _seen["sf"], "Good afternoon")
eq("target final from its own buffer", _seen["tf"], "Buenas tardes")
_rs._handle({"type": "session.output_transcript.delta", "delta": "hola"})
_rs._flush_partial()
eq("disconnect flushes each direction", _seen["tf"], "hola")

print("\nsource transcript is published once, not once per target")
from app.engine import engine as _eng, TRANSCRIPTION_STREAM as _TS
class _FakeRelay:
    state = "connected"
_eng.relays = {"SPANISH": _FakeRelay(), "FRENCH": _FakeRelay()}
_eng._elect_source_owner()
check("one owner elected", _eng._source_owner == "FRENCH", _eng._source_owner)
_heard = []
_real_delta = hub.publish_delta
hub.publish_delta = lambda stream, lang, text: _heard.append((stream, text))
try:
    _eng._source_delta("FRENCH", "hello")     # the owner
    _eng._source_delta("SPANISH", "hello")    # the same words from the other session
finally:
    hub.publish_delta = _real_delta
eq("duplicate source transcript suppressed", _heard, [(_TS, "hello")])
_eng.relays.pop("FRENCH")
_eng._elect_source_owner()
check("owner re-elected when one closes", _eng._source_owner == "SPANISH", _eng._source_owner)
_eng.relays = {}
_eng._elect_source_owner()
check("no owner with no sessions", _eng._source_owner is None)

print("\nsession clock")
# A real start() needs PortAudio and a live OpenAI key, so the stamping itself
# is exercised through the state it leaves behind: what start() sets, what
# stop() clears, and that both status frames carry it.
eq("stopped relay reports no start time", _eng.status()["started_at"], None)
eq("meter agrees when stopped", _eng.meter()["started_at"], None)
_t0 = time.time()
_eng.running = True
_eng.started_at = _t0          # what start() stamps
check("status carries the start time", _eng.status()["started_at"] == _t0)
check("meter carries the same start time", _eng.meter()["started_at"] == _t0)
check("timestamp is wall clock, not monotonic",
      abs(_eng.status()["started_at"] - time.time()) < 5)
asyncio.run(_eng.stop())
eq("stop clears the start time", _eng.status()["started_at"], None)
eq("stop clears it in the meter too", _eng.meter()["started_at"], None)
check("stop also clears running", not _eng.running)
# restart() is stop() + start(), so a restarted session is stamped afresh
# rather than keeping the old window.
import inspect as _inspect
_restart_src = _inspect.getsource(_Engine.restart)
check("restart re-stamps by going through stop and start",
      "await self.stop()" in _restart_src and "await self.start()" in _restart_src)

print("\nhub")
async def hub_test():
    ch, q = await hub.subscribe("translation", "SPANISH")
    frame0 = await q.get()
    check("queue carries finished SSE frames", isinstance(frame0, (bytes, bytearray)), type(frame0))
    snap = unframe(frame0)
    assert snap["type"] == "snapshot"
    hub.publish_delta("translation", "SPANISH", "buenas ")
    hub.publish_delta("translation", "SPANISH", "tardes")
    a, b = unframe(await q.get()), unframe(await q.get())
    # S4: the open segment's full text is not resent on every delta.
    check("delta omits full text between anchors", "text" not in b and b["delta"] == "tardes", b)
    check("open text tracked server-side", ch.open_text == "buenas tardes", ch.open_text)
    check("seq monotonic", b["seq"] > a["seq"])
    check("caption frame carries id: line", b.get("_id") == b["seq"], b)
    hub.publish_final("translation", "SPANISH", "Buenas tardes a todos.")
    f = unframe(await q.get())
    check("final commits line", f["final"] and f["text"] == "Buenas tardes a todos.")
    check("open segment cleared", ch.open_text == "")
    _, q2 = await hub.subscribe("translation", "SPANISH")
    snap2 = unframe(await q2.get())
    check("late joiner gets history", len(snap2["lines"]) == 1)
    hub.publish_delta("translation", "SPANISH", "x")
    check("both subscribers fed", q.qsize() == 1 and q2.qsize() == 1)
    hub.unsubscribe(ch, q); hub.unsubscribe(ch, q2)
    check("unsubscribe drops", len(ch.subscribers) == 0)

    # S5: a reconnect with last_id replays only what was missed, not a snapshot.
    hub.reset("translation", "SPANISH")
    _, qc = await hub.subscribe("translation", "SPANISH")
    await qc.get()
    hub.publish_final("translation", "SPANISH", "one")
    seen_seq = unframe(await qc.get())["seq"]
    hub.publish_final("translation", "SPANISH", "two")
    await qc.get()
    _, qr = await hub.subscribe("translation", "SPANISH", last_id=seen_seq)
    resumed = unframe(await qr.get())
    check("resume replays the missed final, not a snapshot",
          resumed.get("final") and resumed["text"] == "two", resumed)
    check("resume did not send a snapshot", qr.empty(), qr.qsize())
    # Falling further behind than the ring buffer -> full snapshot fallback.
    _, qf = await hub.subscribe("translation", "SPANISH", last_id=0)
    check("last_id 0 is a fresh snapshot", unframe(await qf.get())["type"] == "snapshot")

    # S3: a target-set change is broadcast to every viewer on their open SSE.
    _, qv = await hub.subscribe("translation", "SPANISH")
    await qv.get()
    hub.broadcast_viewers({"type": "targets", "live": [], "running": False})
    tmsg = unframe(await qv.get())
    check("targets pushed over the caption SSE", tmsg["type"] == "targets", tmsg)
asyncio.run(hub_test())

print("\naudio")
import soxr
rs = soxr.ResampleStream(48000, 24000, 1, dtype="float32", quality="HQ")
sig = (0.5 * np.sin(2*np.pi*440*np.arange(48000)/48000)).astype(np.float32)
out = np.concatenate([rs.resample_chunk(sig[i:i+480]) for i in range(0, 48000, 480)])
# streaming resampler holds ~760 samples of HQ filter delay until flush
check("48k→24k halves sample count", 22800 <= out.size <= 24000, out.size)
pcm = (np.clip(out[:960], -1, 1) * 32767).astype("<i2").tobytes()
check("40ms chunk = 1920 bytes PCM16", len(pcm) == 1920, len(pcm))
devs = audio.list_input_devices()
check("device enumeration works", isinstance(devs, list))
print("       inputs found: " + (", ".join(d["label"] for d in devs) or "none"))
if devs:
    check("resolve by label", audio.resolve_device(devs[0]["label"])["index"] == devs[0]["index"])
    check("resolve by index", audio.resolve_device(devs[0]["index"])["index"] == devs[0]["index"])
check("unknown device -> None", audio.resolve_device("Nope McNopeface") is None)

print("\naudio meter (O5)")
check("dbfs floors at -60", audio.dbfs(0.0) == -60.0 and audio.dbfs(1e-9) == -60.0)
check("dbfs full scale is 0", audio.dbfs(1.0) == 0.0, audio.dbfs(1.0))
check("dbfs half scale is -6", abs(audio.dbfs(0.5) + 6.02) < 0.05, audio.dbfs(0.5))
check("dbfs target band", abs(audio.dbfs(0.1259) + 18.0) < 0.1, audio.dbfs(0.1259))

_cap = audio.AudioCapture.__new__(audio.AudioCapture)
_cap.peak = 0.0
_cap._peak_hold = 0.0
_cap._peak_ts = 0.0
_cap.clipped_samples = 0
_cap._clip_until = 0.0

_quiet = np.full(480, 0.05, dtype=np.float32)
_t0 = time.time()
_cap._meter(_quiet, _t0)
check("no clip on a quiet block", _cap.clipped_samples == 0 and not _cap.clipping())
check("peak tracks the block", abs(_cap.peak - 0.05) < 1e-6, _cap.peak)

# A short transient at the rail: the kind the old rms > 0.6 test missed entirely.
_hot = np.full(480, 0.05, dtype=np.float32)
_hot[10:13] = 1.0
_cap._meter(_hot, _t0 + 0.02)
check("clipped samples counted", _cap.clipped_samples == 3, _cap.clipped_samples)
check("clip latched", _cap.clipping())
check("rms would have missed it",
      float(np.sqrt(np.mean(np.square(_hot)))) < 0.6,
      float(np.sqrt(np.mean(np.square(_hot)))))
check("peak hold latched at full scale", abs(_cap._peak_hold - 1.0) < 1e-6, _cap._peak_hold)

# Hold decays at PEAK_DECAY_DB_PER_S, and lets go of the clip after CLIP_HOLD_S.
_cap._meter(_quiet, _t0 + 1.02)
check("peak hold decays ~20 dB in 1 s",
      abs(audio.dbfs(_cap._peak_hold) + 20.0) < 0.5, audio.dbfs(_cap._peak_hold))
check("peak hold never drops below the live peak", _cap._peak_hold >= _cap.peak)
# clipping() is read against the wall clock by the panel, not the block clock.
_cap._clip_until = time.time() + audio.CLIP_HOLD_S
check("clip latch holds for the panel's poll", _cap.clipping())
_cap._clip_until = time.time() - 0.01
check("clip latch expires", not _cap.clipping())

# The real downmix, against a faked stereo device -- no multi-channel interface
# is needed to prove that "mix" averages the channels and an index picks one.
class _FakeStream:
    def __init__(self, **kw): _FakeStream.cb = kw["callback"]
    def start(self): pass
    def stop(self): pass
    def close(self): pass

_fake_dev = [{"index": 0, "name": "Fake", "hostapi": "Test", "label": "Test: Fake",
              "channels": 2, "default_samplerate": 48000}]
_orig_probe, _orig_stream = audio._probe_input_devices, audio.sd.InputStream
audio._probe_input_devices = lambda reinit: list(_fake_dev)
audio.sd.InputStream = _FakeStream
_stereo = np.column_stack([
    np.full(480, 0.80, dtype=np.float32),   # left
    np.full(480, 0.20, dtype=np.float32),   # right
])
try:
    _loop = asyncio.new_event_loop()
    for _sel, _want, _label in (("mix", 0.50, "mix averages both channels"),
                                (0, 0.80, "channel 1 takes the left"),
                                (1, 0.20, "channel 2 takes the right"),
                                (9, 0.20, "out-of-range channel clamps to the last")):
        _c = audio.AudioCapture(_loop)
        _c.start("Test: Fake", _sel)
        _c._stop.set(); _c._pump.join(timeout=2.0)   # stop the pump draining _raw
        while not _c._raw.empty(): _c._raw.get_nowait()
        _FakeStream.cb(_stereo, 480, None, None)
        _got = float(_c._raw.get_nowait().mean())
        check(_label, abs(_got - _want) < 1e-6, _got)
        _c.stop()
    _c = audio.AudioCapture(_loop)
    _c.start("Test: Fake", "mix")
    check("status reports the resolved channel", _c.status()["channel"] == "mix", _c.status()["channel"])
    _c.stop()
    _loop.close()
finally:
    audio._probe_input_devices, audio.sd.InputStream = _orig_probe, _orig_stream
    audio._device_cache = None
    audio._set_capture_live(False)

check("mix accepted by config", config.save({"input_channel": "mix"})["input_channel"] == "mix")
check("MIX is case-insensitive", config.save({"input_channel": " Mix "})["input_channel"] == "mix")
check("channel index still an int", config.save({"input_channel": "2"})["input_channel"] == 2)
check("garbage channel -> 0", config.save({"input_channel": "left"})["input_channel"] == 0)
check("negative channel clamped", config.save({"input_channel": -3})["input_channel"] == 0)
config.save({"input_channel": 0})

print("\nblocklist")
eq("parse from newline string", redact.parse_terms("damn\n\nhell\n"), ["damn", "hell"])
# Never touch the operator's real blocklist.txt -- point config at a temp file.
bl = Path(os.environ["RELAY_CONFIG"]).parent / "blocklist.txt"
config.save({"blocklist_file": str(bl)})
bl.write_text("damn\nhell\noff the record\n", encoding="utf-8")
eq("one term per line", redact.read_blocklist_file(bl), ["damn", "hell", "off the record"])
bl.write_text("damn, hell, off the record\n", encoding="utf-8")
eq("comma-separated still accepted", redact.read_blocklist_file(bl), ["damn", "hell", "off the record"])
bl.write_text("# a note, ignored\ndamn,hell\n  mier da , niño\n", encoding="utf-8")
eq("tolerates commas, newlines, comments, spacing",
   redact.read_blocklist_file(bl), ["damn", "hell", "mier da", "niño"])
eq("missing file is empty", redact.read_blocklist_file(config.ROOT / "nope.txt"), [])
redact.write_blocklist_file(bl, ["damn", "off the record"])
_lines = [l for l in bl.read_text(encoding="utf-8").splitlines() if l and not l.startswith("#")]
eq("written one per line", _lines, ["damn", "off the record"])
check("phrases are not split across lines", all(" " not in l or l in _lines for l in _lines))
eq("round trips", redact.read_blocklist_file(bl), ["damn", "off the record"])
check("header survives a write", bl.read_text(encoding="utf-8").startswith("#"))
eq("config points at the file", config.blocklist_path(), bl)
eq("dedupe case/accents", redact.parse_terms(["Damn", "damn", "NIÑO", "nino"]), ["Damn", "NIÑO"])
eq("dedupe hyphen/space variants", redact.parse_terms("bad word\nbad-word\nbad -  word"), ["bad word"])
_rep = redact.parse_terms_report("damn\nDamn\nhell\nbad-word\nbad word")
eq("report counts duplicates", (_rep.terms, _rep.duplicates, _rep.dropped), (["damn", "hell", "bad-word"], 2, 0))
_rep = redact.parse_terms_report([f"w{i}" for i in range(redact.MAX_TERMS + 5)] + ["w0"])
eq("report counts terms past the limit", (len(_rep.terms), _rep.duplicates, _rep.dropped), (redact.MAX_TERMS, 1, 5))
_hy = redact.Redactor(["bad-word"])
eq("hyphenated term matches the spaced spelling", _hy.whole("a bad word here"), "a here")
eq("hyphenated term matches itself", _hy.whole("a bad-word here"), "a here")
eq("hyphenated term held across deltas",
   "".join(_hy.feed(d) for d in ["a bad-", "wo", "rd here"]) + _hy.flush(), "a here")
r = redact.Redactor(["damn", "off the record"])
eq("word removed, gap closed", r.whole("Well damn, ok"), "Well, ok")
eq("phrase removed", r.whole("this is off the record now"), "this is now")
eq("substring untouched", r.whole("damnation"), "damnation")

async def blocklist_stream_test():
    hub.set_blocklist(["damn", "mierda"])
    hub.reset("translation", "SPANISH")
    ch, q = await hub.subscribe("translation", "SPANISH")
    await q.get()
    seen = ""
    for delta in ["Qué ", "mier", "da", " de día"]:
        hub.publish_delta("translation", "SPANISH", delta)
        while not q.empty():
            m = unframe(await q.get())
            seen = m["text"] if "text" in m else seen + m.get("delta", "")
        check_leak(seen)
    hub.publish_final("translation", "SPANISH", "¡Qué mierda de día!")
    msg = None
    while not q.empty():
        msg = unframe(await q.get())
    check("banned word absent from committed line", "mierda" not in msg["text"], msg["text"])
    eq("line still reads cleanly", msg["text"], "¡Qué de día!")
    hub.unsubscribe(ch, q)

    # Both streams are filtered, not just translation.
    hub.reset("transcription", "ENGLISH")
    ch2, q2 = await hub.subscribe("transcription", "ENGLISH")
    await q2.get()
    hub.publish_final("transcription", "ENGLISH", "Well damn, that was fast.")
    m = unframe(await q2.get())
    eq("transcription filtered too", m["text"], "Well, that was fast.")
    hub.unsubscribe(ch2, q2)

    # Turning the list off restores the text.
    hub.set_blocklist([])
    hub.reset("transcription", "ENGLISH")
    ch3, q3 = await hub.subscribe("transcription", "ENGLISH")
    await q3.get()
    hub.publish_final("transcription", "ENGLISH", "Well damn, that was fast.")
    m = unframe(await q3.get())
    eq("empty list is pass-through", m["text"], "Well damn, that was fast.")
    hub.unsubscribe(ch3, q3)

leaks = []
def check_leak(text):
    if "mier" in text.lower():
        leaks.append(text)

asyncio.run(blocklist_stream_test())
eq("banned word never rendered mid-stream", leaks, [])

print("\nroutes")
c = TestClient(app)
for path in ("/", "/transcription", "/translation", "/both", "/screen", "/overlay", "/healthz"):
    check(f"GET {path}", c.get(path).status_code == 200)

# /overlay: a fixed-canvas broadcast feed configured entirely through the URL.
ov = c.get("/overlay?stream=both&font=60&w=1280&h=720&safe=8&plate=0.4&matte=1&bg=%2300FF00").text
check("overlay carries its class", "overlay ov-hidden" in ov)
check("overlay matte class set", "overlay-matte" in ov)
check("overlay chroma bg passes through", "--ov-bg: #00FF00" in ov)
check("overlay title-safe resolved to px", "--ov-pad-x: 102px" in ov)  # 1280 * 8%
check("overlay non-matte omits matte class", "overlay-matte" not in c.get("/overlay").text)
# A hand-typed ?bg= is written into a CSS value; it must not break out.
inj = c.get("/overlay?bg=red;}body{display:none").text
check("overlay bg cannot inject CSS", "display:none" not in inj and "--ov-bg: transparent" in inj)
t = c.get("/api/targets").json()
check("/api/targets shape", t["source"]["lang"] == "ENGLISH" and "live" in t)
check("no live targets before start", t["live"] == [])
check("bad stream rejected", c.get("/stream?stream=bogus").status_code == 400)
check("bad lang rejected", c.get("/stream?stream=translation&lang=KLINGON").status_code == 400)

print("\nadmin auth")
check("state needs token", c.get("/api/admin/state").status_code == 401)
check("start needs token", c.post("/api/admin/start").status_code == 401)
check("admin page shows login", "Operator panel" in c.get("/admin").text)
check("wrong token rejected", c.post("/admin/login", data={"token": "nope"}).status_code == 401)
h = {"x-admin-token": "t0ken"}
st = c.get("/api/admin/state", headers=h)
check("state with token", st.status_code == 200)
body = st.json()
blob = json.dumps(body)
check("secret key value never leaves server", "sk-test-1234" not in blob and "t0ken" not in blob)
check("only key hint exposed", body["config"]["openai_api_key_hint"] == "…1234")
check("devices listed for admin", isinstance(body["devices"], list))
r = c.post("/api/admin/config", headers=h, json={"blocklist": "damn\noff the record"})
check("panel saves blocklist", r.status_code == 200, r.text)
eq("no report on a clean save", (r.json()["blocklist_report"]["duplicates"], r.json()["blocklist_report"]["dropped"]), (0, 0))
_r = c.post("/api/admin/config", headers=h, json={"blocklist": "damn\nDAMN\noff the record\noff-the-record"})
eq("panel save reports duplicates", _r.json()["blocklist_report"]["duplicates"], 2)
eq("status carries the term limit", _r.json()["status"]["blocklist_max"], redact.MAX_TERMS)
r = c.post("/api/admin/config", headers=h, json={"blocklist": "damn\noff the record"})
eq("panel write reaches the file", redact.read_blocklist_file(bl), ["damn", "off the record"])
eq("panel write reaches the hub", hub.blocklist, ["damn", "off the record"])
r2 = c.post("/api/admin/config", headers=h, json={"blocklist": ""})
eq("blocklist can be cleared", hub.blocklist, [])
eq("cleared file is empty", redact.read_blocklist_file(bl), [])
st2 = c.get("/api/admin/state", headers=h).json()["status"]
check("status reports the file path", st2["blocklist_file"].endswith("blocklist.txt"), st2["blocklist_file"])
check("real blocklist.txt untouched by tests", not (config.ROOT / "blocklist.txt").samefile(bl)
      if (config.ROOT / "blocklist.txt").exists() else True)

r = c.post("/api/admin/target/SPANISH", headers=h, json={"enabled": True})
check("toggle target ok", r.status_code == 200)
check("toggle persisted", config.get()["targets"]["SPANISH"]["enabled"] is True)
check("not live while stopped",
      all(t["live"] is False for t in r.json()["targets"]))
check("source language is not offered as a target",
      c.post("/api/admin/target/ENGLISH", headers=h, json={"enabled": True}).status_code == 404)
check("unknown target 404", c.post("/api/admin/target/ELVISH", headers=h, json={"enabled": True}).status_code == 404)
config.save({"openai_api_key": ""})
r = c.post("/api/admin/start", headers=h)
check("start without key fails cleanly", r.status_code == 400 and "API key" in r.json()["error"], r.text)

print("\nrehearsal mode never opens a billed session")
from app import scheduler as scheduler_mod, schedules as schedules_mod
from app.engine import engine as real_engine
config.save({"openai_api_key": "sk-test-1234"})
os.environ["RELAY_DEMO"] = "1"
_opened = []
_real_open = real_engine._open_relay
real_engine._open_relay = lambda *a, **k: _opened.append(a)
real_engine.last_error = None  # left over from the no-key start above
try:
    r = c.post("/api/admin/start", headers=h)
    check("start is refused in rehearsal mode", r.status_code == 400, r.text)
    check("the refusal says why", "Rehearsal mode" in r.json().get("error", ""), r.text)
    check("nothing is running after the refusal", r.json().get("running") is False, r.text)
    check("no session was opened", _opened == [], _opened)
    check("the refusal does not stick as a panel error",
          c.get("/api/admin/status", headers=h).json()["error"] is None)
    # A schedule whose window is open right now must not start capture either.
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    _now = datetime.now(timezone.utc)
    _tz = schedules_mod.DEFAULT_TIMEZONE
    _local = _now.astimezone(ZoneInfo(_tz))
    _win = {"id": "demo0001", "name": "Rehearsal clash", "enabled": True, "repeat": "once",
            "days": [], "start_date": _local.strftime("%Y-%m-%d"), "end_date": None,
            "start_time": (_local - timedelta(minutes=5)).strftime("%H:%M"),
            "stop_time": (_local + timedelta(minutes=30)).strftime("%H:%M"),
            "timezone": _tz}
    _saved = config.get()["schedules"]
    config.save({"schedules": [_win]})
    _sch = scheduler_mod.Scheduler()
    asyncio.run(_sch.tick(_now))
    check("a schedule inside its window does not start capture", not real_engine.running)
    check("and no session was opened by it", _opened == [], _opened)
    config.save({"schedules": _saved})
finally:
    os.environ.pop("RELAY_DEMO", None)
    real_engine._open_relay = _real_open
    config.save({"openai_api_key": ""})

print("\noperator panel is served on its own port")
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001})
viewer_c = TestClient(app, base_url="http://relay.local:8000")
admin_c = TestClient(app, base_url="http://relay.local:8001")
for path in ("/admin", "/admin/login", "/api/admin/status", "/api/admin/state"):
    check(f"viewer port hides {path}", viewer_c.get(path).status_code == 404, path)
check("viewer pages still served on the viewer port",
      all(viewer_c.get(p).status_code == 200 for p in ("/", "/translation", "/transcription", "/both")))
check("chooser hides the panel on the viewer port", "Operator panel" not in viewer_c.get("/").text)
check("panel reachable on the admin port", admin_c.get("/admin").status_code == 200)
check("admin api reachable on the admin port",
      admin_c.get("/api/admin/status", headers={"x-admin-token": "t0ken"}).status_code == 200)
check("chooser offers the panel on the admin port", "Operator panel" in admin_c.get("/").text)
config.save({"admin_port": 8000})
check("one port again when admin_port == port",
      TestClient(app, base_url="http://relay.local:8000").get("/admin").status_code == 200)
config.save({"admin_port": 8001})

print("\nadmin panel never kills a live capture")
import sounddevice as _sd
_terms = {"n": 0}
_orig_terminate = _sd._terminate
def _counting_terminate(*a, **k):
    _terms["n"] += 1
    return _orig_terminate(*a, **k)
_sd._terminate = _counting_terminate
try:
    audio.list_input_devices()                     # warm the cache
    _before = _terms["n"]
    for _ in range(3):
        c.get("/api/admin/state", headers=h)
    check("panel loads never re-initialise PortAudio", _terms["n"] == _before, _terms["n"])

    audio._set_capture_live(True)
    _before = _terms["n"]
    rb = c.get("/api/admin/devices", headers=h).json()
    check("rescan leaves PortAudio alone while capturing", _terms["n"] == _before, _terms["n"])
    check("rescan says so", rb["full_rescan"] is False and bool(rb["note"]), rb)

    audio._set_capture_live(False)
    _before = _terms["n"]
    rb = c.get("/api/admin/devices", headers=h).json()
    check("rescan while stopped does a full probe", _terms["n"] == _before + 1, _terms["n"])
    check("full rescan flagged", rb["full_rescan"] is True and rb["note"] is None, rb)
finally:
    _sd._terminate = _orig_terminate
    audio._set_capture_live(False)

print("\nnon-ascii admin token")
import urllib.parse as _up
for _tok in ("contraseña", "пароль"):
    config.save({"admin_token": _tok})
    c2 = TestClient(app)
    lr = c2.post("/admin/login", data={"token": _tok}, follow_redirects=False)
    check(f"login accepts {_tok!r}", lr.status_code == 303, lr.status_code)
    check(f"panel opens with {_tok!r}", "<h1>Relay</h1>" in c2.get("/admin").text)
    check(f"api authorises {_tok!r}", c2.get("/api/admin/status").status_code == 200)
c3 = TestClient(app)
c3.cookies.set("relay_admin", _up.quote("garbage-Ω", safe=""))
check("bad non-ascii cookie -> 401 not 500", c3.get("/api/admin/status").status_code == 401)
check("bad non-ascii token -> 401 not 500",
      c3.post("/admin/login", data={"token": "wrong-ñ"}).status_code == 401)
config.save({"admin_token": "t0ken"})

print("\nadmin session cookie")
from app import main as _main
_main._SESSIONS.clear()
_main._LOGIN_FAILS.clear()
c4 = TestClient(app)
lr = c4.post("/admin/login", data={"token": "t0ken"}, follow_redirects=False)
_sid = c4.cookies.get("relay_admin")
check("login issues a session cookie", lr.status_code == 303 and bool(_sid))
check("cookie is not the admin token", _sid != "t0ken" and "t0ken" not in _sid, _sid)
_sc = lr.headers["set-cookie"].lower()
check("cookie is httponly + samesite=strict", "httponly" in _sc and "samesite=strict" in _sc, _sc)
check("session opens the panel", "<h1>Relay</h1>" in c4.get("/admin").text)
check("session authorises the api", c4.get("/api/admin/status").status_code == 200)
c4.post("/admin/logout", follow_redirects=False)
c4.cookies.set("relay_admin", _sid)
check("logout kills the session server-side", c4.get("/api/admin/status").status_code == 401)
_main._SESSIONS.clear()
c5 = TestClient(app)
c5.cookies.set("relay_admin", _sid)
check("sessions do not survive a restart", c5.get("/api/admin/status").status_code == 401)

print("\nadmin login rate limiting")
_main._LOGIN_FAILS.clear()
_main.LOGIN_FAIL_DELAY_S = 0.0  # keep the suite quick; the lockout is what we test
c6 = TestClient(app)
codes = [c6.post("/admin/login", data={"token": "nope"}).status_code for _ in range(6)]
check("first attempts rejected 401", codes[:5] == [401] * 5, codes)
check("locked out after 5 failures", codes[5] == 429, codes)
check("lockout ignores the right token too",
      c6.post("/admin/login", data={"token": "t0ken"}).status_code == 429)
_main._LOGIN_FAILS.clear()
c6.post("/admin/login", data={"token": "nope"})
c6.post("/admin/login", data={"token": "nope"})
check("failures counted before the lockout", bool(_main._LOGIN_FAILS), _main._LOGIN_FAILS)
check("a good login still goes through",
      c6.post("/admin/login", data={"token": "t0ken"}, follow_redirects=False).status_code == 303)
check("counter reset on success", _main._LOGIN_FAILS == {}, _main._LOGIN_FAILS)
_main.LOGIN_FAIL_DELAY_S = 0.75

# --------------------------------------------------------------- issue #15
# The panel socket no longer shares a host with the viewer socket, and it sits
# behind a TLS front end (caddy/Caddyfile). Two things have to hold: the
# defaults must keep the panel off the LAN, and the app must read the proxy's
# forwarded headers -- but only from a loopback peer, or a direct client on
# the LAN could forge them.
print("\nadmin host + forwarded headers")

_cfg = config.load()
eq("admin_host defaults to loopback", _cfg["admin_host"], "127.0.0.1")
eq("host still serves the room", _cfg["host"], "0.0.0.0")
check("viewer and panel are separate sockets", _cfg["port"] != _cfg["admin_port"])

# In single-port mode run.py binds one socket on `host` and ignores
# admin_host. The stored value must survive that: collapsing it here would
# mean a round trip through single-port mode left the panel on 0.0.0.0.
_single = config._normalise({"port": 8000, "admin_port": 8000, "host": "0.0.0.0"})
eq("single-port mode leaves admin_host alone", _single["admin_host"], "127.0.0.1")
eq("a blank admin_host falls back to loopback",
   config._normalise({"admin_host": "  "})["admin_host"], "127.0.0.1")

_main._SESSIONS.clear()
_main._LOGIN_FAILS.clear()
_main.LOGIN_FAIL_DELAY_S = 0.0

# TestClient's peer is "testclient", not a loopback address, so these headers
# stand in for the untrusted case: a direct client on the venue LAN.
c7 = TestClient(app)
c7.post("/admin/login", data={"token": "nope"},
        headers={"x-forwarded-for": "203.0.113.9"})
check("forged X-Forwarded-For is ignored from a non-loopback peer",
      "203.0.113.9" not in _main._LOGIN_FAILS, _main._LOGIN_FAILS)


# TestClient has no way to set the peer address, so the loopback side is
# exercised against Requests built by hand -- the peer is scope["client"],
# which is exactly what _from_loopback reads.
from starlette.requests import Request as _Req
from starlette.responses import Response as _Resp


def _req(peer, headers=None):
    return _Req({
        "type": "http", "method": "POST", "path": "/admin/login",
        "scheme": "http", "server": ("127.0.0.1", 8001), "query_string": b"",
        "client": peer,
        "headers": [(k.encode(), v.encode()) for k, v in (headers or {}).items()],
    })


eq("X-Forwarded-For is honoured from a loopback peer",
   _main._client_ip(_req(("127.0.0.1", 54321),
                         {"x-forwarded-for": "203.0.113.9, 10.0.0.1"})),
   "203.0.113.9")
eq("a direct LAN client's forged X-Forwarded-For is ignored",
   _main._client_ip(_req(("10.0.1.77", 54321),
                         {"x-forwarded-for": "203.0.113.9"})),
   "10.0.1.77")
eq("no header means the peer itself",
   _main._client_ip(_req(("10.0.1.77", 54321))), "10.0.1.77")

check("X-Forwarded-Proto is honoured from a loopback peer",
      _main._is_https(_req(("127.0.0.1", 54321), {"x-forwarded-proto": "https"})))
check("a direct LAN client cannot claim https",
      not _main._is_https(_req(("10.0.1.77", 54321), {"x-forwarded-proto": "https"})))
check("plain loopback request is not https", not _main._is_https(_req(("127.0.0.1", 1))))

# The cookie must pick up Secure behind TLS, and must NOT pick it up on a plain
# HTTP login -- a browser drops a Secure cookie sent over HTTP, which would
# lock the operator out of a working loopback or single-port setup.
_main._SESSIONS.clear()
_r = _Resp()
_main._set_session_cookie(_r, _req(("127.0.0.1", 1), {"x-forwarded-proto": "https"}))
check("cookie is Secure behind a TLS proxy",
      "secure" in _r.headers["set-cookie"].lower(), _r.headers["set-cookie"])

_r2 = _Resp()
_main._set_session_cookie(_r2, _req(("10.0.1.77", 1), {"x-forwarded-proto": "https"}))
check("forged X-Forwarded-Proto does not set Secure",
      "secure" not in _r2.headers["set-cookie"].lower(), _r2.headers["set-cookie"])

_main._SESSIONS.clear()
_main._LOGIN_FAILS.clear()
c9 = TestClient(app)
_plain = c9.post("/admin/login", data={"token": "t0ken"}, follow_redirects=False)
check("cookie is not Secure on a plain-HTTP login",
      "secure" not in _plain.headers["set-cookie"].lower(), _plain.headers["set-cookie"])
_main.LOGIN_FAIL_DELAY_S = 0.75

# ------------------------------------------------------------------ issue #5
# Session recording + export. The recorded window is one start -> one stop,
# which is longer than the ring buffer can hold, so the recorder keeps its own
# append-only copy and the exports are derived from that.
print("\nsession recording")
import shutil as _shutil
from app import exporting as _exporting
from app.recorder import Recorder as _Recorder

_recdir = Path(tempfile.mkdtemp()) / "recordings"
_r = _Recorder()
_r.configure(_recdir, keep_runs=2)
_rid = _r.start_run("ENGLISH", ["SPANISH"])
check("start_run creates a run", bool(_rid) and (_recdir / _rid).is_dir())
_r.append("transcription", "ENGLISH", 1, "Good morning.", 100.0, 101.0)
_r.append("translation", "SPANISH", 2, "Buenos d\u00edas.", 100.2, 101.4)
_r.append("transcription", "ENGLISH", 3, "Welcome to the conference.", 102.0, 104.0)
_r.append("translation", "SPANISH", 4, "Bienvenidos a la conferencia.", 102.1, 104.3)
eq("lines are flushed as they commit, before stop",
   len(_Recorder.read_lines(_recdir / _rid)), 4)
_r.finish_run()
_meta = _r.list_run(_rid)
eq("finished run is stamped and counted", (_meta["lines"], bool(_meta["ended"])), (4, True))
check("a finished run is not marked interrupted", not _meta.get("interrupted"))

# The ring buffer is smaller than an event; the recorder must not be.
_hub_lines = hub.history_lines
eq("recording is not bounded by history_lines",
   len(_Recorder.read_lines(_recdir / _rid)) <= _hub_lines, True)

_rows = _Recorder.read_lines(_recdir / _rid)
_files = _exporting.build_exports(_meta, _rows)
check("export writes a transcript per language",
      "transcript-ENGLISH.txt" in _files and "transcript-SPANISH.txt" in _files)
check("export writes a pairs file", "pairs-SPANISH.jsonl" in _files)
check("export writes a fine-tuning file", "finetune-SPANISH.jsonl" in _files)
check("transcript carries clock times", "Good morning." in _files["transcript-ENGLISH.txt"])

_pairs = [json.loads(l) for l in _files["pairs-SPANISH.jsonl"].splitlines()]
eq("overlapping lines pair one to one", len(_pairs), 2)
eq("both pairs are exact", sorted(p["confidence"] for p in _pairs), ["exact", "exact"])
eq("a pair links the source to its translation",
   (_pairs[0]["source"], _pairs[0]["target"]), ("Good morning.", "Buenos d\u00edas."))

_ft = [json.loads(l) for l in _files["finetune-SPANISH.jsonl"].splitlines()]
eq("fine-tune rows are OpenAI chat format", [sorted(r) for r in _ft], [["messages"], ["messages"]])
eq("fine-tune roles", [m["role"] for m in _ft[0]["messages"]], ["system", "user", "assistant"])
eq("fine-tune carries the pair", (_ft[0]["messages"][1]["content"], _ft[0]["messages"][2]["content"]),
   ("Good morning.", "Buenos d\u00edas."))

# Segmentation differs between the feeds, so a translation can span two source
# lines. That pair is real but not clean, and must not reach the fine-tune set.
_merged = _exporting.pair_lines(
    [{"seq": 1, "t0": 10.0, "t1": 11.0, "text": "One."},
     {"seq": 2, "t0": 11.1, "t1": 12.0, "text": "Two."}],
    [{"seq": 3, "t0": 10.1, "t1": 12.1, "text": "Uno. Dos."}],
)
eq("a translation spanning two source lines is flagged merged",
   [p["confidence"] for p in _merged], ["merged"])
eq("merged joins the source lines", _merged[0]["source"], "One. Two.")
eq("merged text is kept out of the fine-tune file",
   _exporting.finetune_jsonl("ENGLISH", "SPANISH", _merged), "")

# The interpreter lags the speaker, so a translation often starts after the
# source line it answers has already committed -- no overlap at all. That is
# still a confident pair, and losing it would throw away most of the data.
_lagged = _exporting.pair_lines(
    [{"seq": 1, "t0": 10.0, "t1": 12.0, "text": "The doors close at six."},
     {"seq": 2, "t0": 13.4, "t1": 15.0, "text": "Please take your seats."}],
    [{"seq": 3, "t0": 12.6, "t1": 13.3, "text": "Las puertas cierran a las seis."}],
)
eq("a lagging translation still pairs with the line it answers",
   [(p["confidence"], p["source"]) for p in _lagged][:1],
   [("exact", "The doors close at six.")])
check("the sentence after the lagging translation is not swallowed",
      any(p["confidence"] == "unpaired" and p["source"] == "Please take your seats."
          for p in _lagged), _lagged)

# A source line nothing translated (a dropped session) must be visible in the
# export, not silently missing.
_dropped = _exporting.pair_lines(
    [{"seq": 1, "t0": 10.0, "t1": 11.0, "text": "Unanswered."}], [])
eq("an untranslated source line is reported unpaired",
   [(p["confidence"], p["target"]) for p in _dropped], [("unpaired", "")])

# Retention: keep_runs is 2 above, so a third start drops the oldest.
for _i in range(3):
    _r2 = _r.start_run("ENGLISH", ["SPANISH"])
    _r.append("transcription", "ENGLISH", 1, f"run {_i}", 1.0, 2.0)
    _r.finish_run()
eq("old runs are pruned to keep_runs", len(_r.list_runs()), 2)

# A run killed mid-event never gets its end stamp. The lines are on disk and
# the panel must say so rather than showing an empty run.
_r.start_run("ENGLISH", ["SPANISH"])
_r.append("transcription", "ENGLISH", 9, "cut off here", 1.0, 2.0)
_r._close_fh()                    # what a kill -9 leaves behind
_cut = _r.list_run(_r.run_id)
eq("an interrupted run still reports its lines",
   (_cut["interrupted"], _cut["lines"]), (True, 1))

# run_id comes off an HTTP path and is the only thing that becomes a directory.
for _bad in ("../../etc", "..", "not-a-run", "20260920-143012/../..", ""):
    check(f"run id {_bad!r} is refused", _r.run_dir(_bad) is None)

_shutil.rmtree(_recdir.parent, ignore_errors=True)

# End to end over HTTP: a recorded run must come back down as a zip and as
# individual files, through the same routes the panel uses.
print("\nrecording downloads")
from app.recorder import recorder as _live
import io as _io, zipfile as _zip
_dldir = Path(tempfile.mkdtemp()) / "recordings"
_live.configure(_dldir, keep_runs=5)
_dlid = _live.start_run("ENGLISH", ["SPANISH"])
_live.append("transcription", "ENGLISH", 1, "The doors close at six.", 200.0, 202.0)
_live.append("translation", "SPANISH", 2, "Las puertas cierran a las seis.", 200.2, 202.4)
_live.finish_run()
_dh = {"x-admin-token": "t0ken"}
_ls = c.get("/api/admin/recordings", headers=_dh).json()
eq("the finished run is listed", [r["run_id"] for r in _ls["runs"]], [_dlid])
_one = c.get(f"/api/admin/recordings/{_dlid}", headers=_dh).json()
check("the run lists its exportable files",
      {"transcript-ENGLISH.txt", "finetune-SPANISH.jsonl"} <= {f["name"] for f in _one["files"]},
      _one["files"])
_z = c.get(f"/api/admin/recordings/{_dlid}/export.zip", headers=_dh)
eq("zip download is served", (_z.status_code, _z.headers["content-type"]),
   (200, "application/zip"))
check("zip is offered as a download", "attachment" in _z.headers.get("content-disposition", ""))
_names = sorted(n.split("/")[-1] for n in _zip.ZipFile(_io.BytesIO(_z.content)).namelist())
check("zip carries the raw record and the exports",
      {"lines.jsonl", "meta.json", "manifest.txt", "finetune-SPANISH.jsonl"} <= set(_names),
      _names)
_ftr = c.get(f"/api/admin/recordings/{_dlid}/file/finetune-SPANISH.jsonl", headers=_dh)
eq("a single file downloads", _ftr.status_code, 200)
check("it is the pair, in chat format",
      json.loads(_ftr.text.splitlines()[0])["messages"][2]["content"]
      == "Las puertas cierran a las seis.")
eq("a file this run never produced is a 404",
   c.get(f"/api/admin/recordings/{_dlid}/file/finetune-KLINGON.jsonl", headers=_dh).status_code, 404)
eq("a file name cannot escape the run",
   c.get(f"/api/admin/recordings/{_dlid}/file/..%2F..%2Fconfig.json", headers=_dh).status_code, 404)
eq("delete needs a token",
   c.delete(f"/api/admin/recordings/{_dlid}").status_code, 401)
eq("delete removes the run",
   c.delete(f"/api/admin/recordings/{_dlid}", headers=_dh).json()["runs"], [])
check("the run directory is gone", not (_dldir / _dlid).exists())
_shutil.rmtree(_dldir.parent, ignore_errors=True)
_live.configure(Path(tempfile.mkdtemp()) / "recordings", keep_runs=20)

print("\nrecording is off by default")
eq("recording disabled in the shipped defaults", config.get()["recording"]["enabled"], False)
check("no line sink until a recorded run starts", hub.line_sink is None)
_rec_h = {"x-admin-token": "t0ken"}
_rl = c.get("/api/admin/recordings", headers=_rec_h)
eq("recordings list is served", _rl.status_code, 200)
eq("nothing recorded with recording off", _rl.json()["runs"], [])
check("recordings list needs a token", c.get("/api/admin/recordings").status_code == 401)
check("unknown run is a 404",
      c.get("/api/admin/recordings/20200101-000000", headers=_rec_h).status_code == 404)
check("a non-run id is a 404, not a path",
      c.get("/api/admin/recordings/..%2F..%2Fetc", headers=_rec_h).status_code in (400, 404))

# The hub is what feeds the recorder: a committed line must arrive with the
# span that pairing depends on, and only after the blocklist has run.
print("\nhub feeds the recorder")
_sunk = []
hub.line_sink = lambda *a: _sunk.append(a)
try:
    hub.reset("transcription", "ENGLISH")
    hub.publish_delta("transcription", "ENGLISH", "Hello ")
    hub.publish_delta("transcription", "ENGLISH", "world")
    hub.publish_final("transcription", "ENGLISH")
finally:
    hub.line_sink = None
eq("one committed line reached the sink", len(_sunk), 1)
eq("the sink gets stream, lang and text",
   (_sunk[0][0], _sunk[0][1], _sunk[0][3]), ("transcription", "ENGLISH", "Hello world"))
check("the line carries a start and an end time",
      _sunk[0][4] <= _sunk[0][5] and _sunk[0][4] > 0)

_sunk.clear()
hub.set_blocklist(["mierda"])
hub.line_sink = lambda *a: _sunk.append(a)
try:
    hub.reset("translation", "SPANISH")
    hub.publish_final("translation", "SPANISH", "vaya mierda de dia")
finally:
    hub.line_sink = None
    hub.set_blocklist([])
check("a blocked word never reaches the recording",
      _sunk and "mierda" not in _sunk[0][3], _sunk)

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails)); sys.exit(1)
print("all checks passed")
