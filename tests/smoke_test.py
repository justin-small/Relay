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

print()
if fails:
    print(f"{len(fails)} FAILED: " + "; ".join(fails)); sys.exit(1)
print("all checks passed")
