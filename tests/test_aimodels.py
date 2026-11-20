"""AI models (app/aimodels.py, issues 495 and 496): which provider each AI task runs on and the fallback to the
OpenAI key, an older config's translation model carried over, the realtime translation engine's settings dropped,
the translator moved to another provider without restarting the captions, Vercel's model list with prices and free
models read from a real local HTTP server, and the AI models page's routes. Offline.
Run: python tests/test_aimodels.py
"""
import asyncio
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import await_for, check, done, eq, section, tmpdir  # noqa: E402

TMP = tmpdir()
os.environ["RELAY_STATE_DIR"] = TMP
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"

import _db  # noqa: E402

_db.start()
_db.reset()

from app import aimodels, config, engine as engine_mod, texttranslate, transcribe  # noqa: E402
from app.main import app  # noqa: E402


def cfg_with(**over) -> dict:
    base = {"openai_api_key": "", "vercel_api_key": "", "realtime": {}, "ai": {}}
    base.update(over)
    return base


CATALOGUE = {"data": [
    {"id": "openai/gpt-6-luna", "name": "GPT-6 Luna", "type": "language",
     "pricing": {"input": "0.0000001", "output": "0.0000005", "regional": {"eu": {"input": "1"}}}},
    {"id": "reflection/beam-free", "name": "Beam", "type": "language", "tags": ["free"],
     "pricing": {"input": "0.0000001", "output": "0.0000001"}},
    {"id": "nvidia/nemotron-free", "name": "Nemotron", "type": "language", "pricing": {"input": "0", "output": "0"}},
    {"id": "elevenlabs/scribe-v2-realtime", "name": "Scribe v2 Realtime", "type": "transcription", "pricing": {}},
    {"id": "openai/gpt-transcribe", "name": "GPT Transcribe", "type": "transcription",
     "pricing": {"input": "0", "transcription_duration_cost_per_second": "0.000075"}},
    {"id": "openai/tts-1", "name": "TTS 1", "type": "speech",
     "pricing": {"input": "0.000015", "speech_input_character_cost": "0.000015"}},
    {"id": "google/veo", "name": "Veo", "type": "video", "pricing": {"input": "0"}},
]}


class Gateway(BaseHTTPRequestHandler):
    """Stands in for both providers' chat endpoints (/vercel, /openai) and Vercel's model list (/models)."""

    seen: list = []
    models_status = 200

    def log_message(self, *a):
        pass

    def _send(self, status, body):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        if self.path.startswith("/models"):
            self._send(Gateway.models_status, CATALOGUE if Gateway.models_status == 200 else {"error": "down"})
        else:
            self._send(404, {})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Gateway.seen.append({"path": self.path, "auth": self.headers["Authorization"], "model": body["model"]})
        codes = list(body["response_format"]["json_schema"]["schema"]["properties"])
        reply = {c: f"[{self.path.strip('/')}:{c}] {json.loads(body['messages'][1]['content'])['text']}" for c in codes}
        self._send(200, {"choices": [{"message": {"content": json.dumps(reply)}}]})


server = ThreadingHTTPServer(("127.0.0.1", 0), Gateway)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


with section("which provider each task runs on"):
    eq("every task is listed", aimodels.TASK_IDS, ("translation", "command_center", "summaries", "speech"))
    d = config.get()["ai"]["tasks"]
    eq("translation defaults to gpt-6-luna through Vercel", d["translation"],
       {"provider": "vercel", "model": "openai/gpt-6-luna", "openai_model": "gpt-6-luna"})
    eq("Command Center defaults to Claude Sonnet 5.5 through Vercel", d["command_center"]["model"],
       "anthropic/claude-sonnet-5.5")
    check("every task defaults to Vercel", all(t["provider"] == "vercel" for t in d.values()), d)

    r = aimodels.resolve(cfg_with(vercel_api_key="vk_1", openai_api_key="sk-1"), "translation")
    check("with the Vercel key it runs on Vercel, no warning",
          (r.provider, r.model, r.key, r.url, r.warning) ==
          ("vercel", "openai/gpt-6-luna", "vk_1", aimodels.VERCEL_CHAT_URL, None), r)
    r = aimodels.resolve(cfg_with(openai_api_key="sk-1"), "translation")
    check("an install with only the OpenAI key keeps translating on it, with the OpenAI model and a warning",
          r.provider == "openai" and r.model == "gpt-6-luna" and r.key == "sk-1" and r.url == aimodels.OPENAI_CHAT_URL
          and "Vercel" in (r.warning or ""), r)
    r = aimodels.resolve(cfg_with(vercel_api_key="vk_1", openai_api_key="sk-1",
                                  ai={"tasks": {"translation": {"provider": "openai", "openai_model": "gpt-5.6-luna"}}}),
                         "translation")
    check("a task switched to the OpenAI key stays on it even with the Vercel key saved",
          (r.provider, r.model, r.warning) == ("openai", "gpt-5.6-luna", None), r)
    r = aimodels.resolve(cfg_with(vercel_api_key="vk_1", ai={"tasks": {"translation": {"provider": "openai"}}}),
                         "translation")
    check("…and with no OpenAI key it is not moved back to Vercel: the key is reported missing",
          r.provider == "openai" and r.key == "", r)
    r = aimodels.resolve(cfg_with(), "command_center")
    check("with no key at all the chosen provider comes back with no key", (r.provider, r.key) == ("vercel", ""), r)
    r = aimodels.resolve(cfg_with(vercel_api_key="vk_1", openai_api_key="sk-1",
                                  ai={"tasks": {"summaries": {"provider": "openai"}}}), "translation")
    check("tasks are independent: summaries on OpenAI leaves translation on Vercel", r.provider == "vercel", r)
    rows = aimodels.routes(cfg_with(openai_api_key="sk-1"))
    check("the page rows say what each task runs on and why",
          [x["running_provider"] for x in rows] == ["openai"] * 4 and all(x["warning"] for x in rows), rows)


with section("saved settings"):
    n = aimodels.normalize({"tasks": {"translation": {"provider": "carrier pigeon", "model": "has spaces in it"},
                                      "speech": {"provider": "openai", "model": "x" * 500}}})
    eq("an unknown provider and a bad model id fall back to the defaults", n["tasks"]["translation"],
       {"provider": "vercel", "model": "openai/gpt-6-luna", "openai_model": "gpt-6-luna"})
    eq("an over-long model id is not kept", n["tasks"]["speech"]["model"], "openai/tts-1")
    cfg = config._normalise({"realtime": {"translation_mode": "realtime", "translate_model": "gpt-realtime-translate",
                                          "text_model": "gpt-5.6-luna"}})
    check("an old config on the realtime engine loads as text mode: the engine settings are dropped",
          not {"translation_mode", "translate_model", "text_model"} & set(cfg["realtime"]), cfg["realtime"])
    eq("its translation model is carried over for both providers", cfg["ai"]["tasks"]["translation"],
       {"provider": "vercel", "model": "openai/gpt-5.6-luna", "openai_model": "gpt-5.6-luna"})
    cfg = config._normalise({"realtime": {"text_model": "gpt-6-luna"}})
    eq("the old default model maps to the new default", cfg["ai"]["tasks"]["translation"]["model"],
       "openai/gpt-6-luna")
    saved = config.save({"ai": {"tasks": {"summaries": {"provider": "openai"}}}})
    check("saving one task's provider keeps the others",
          saved["ai"]["tasks"]["summaries"]["provider"] == "openai"
          and saved["ai"]["tasks"]["translation"]["provider"] == "vercel", saved["ai"])
    config.save({"ai": {"tasks": {"summaries": {"provider": "vercel"}}}})


async def translator_moves_provider():
    published = []
    tr = texttranslate.TextTranslator(url=f"{BASE}/vercel", api_key="vk_1", model="openai/gpt-6-luna",
                                      source_language="ENGLISH", on_final=lambda t, x: published.append(x))
    tr.targets = {"SPANISH"}
    tr.start()
    tr.submit("Good morning.")
    check("a line goes to the provider it was set to", await await_for(lambda: len(published) == 1))
    tr.use(f"{BASE}/openai", "sk-1", "gpt-6-luna")
    tr.submit("Welcome.")
    check("after use(), the next line goes to the new provider", await await_for(lambda: len(published) == 2))
    await tr.stop()
    return published, tr.name


with section("the translator moves provider without a restart"):
    Gateway.seen.clear()
    pub, name = asyncio.run(translator_moves_provider())
    eq("each provider got its own key and model", [(s["path"], s["auth"], s["model"]) for s in Gateway.seen],
       [("/vercel", "Bearer vk_1", "openai/gpt-6-luna"), ("/openai", "Bearer sk-1", "gpt-6-luna")])
    eq("both translations were published", pub, ["[vercel:es] Good morning.", "[openai:es] Welcome."])
    eq("its health names the new model", name, "text:gpt-6-luna")


class Idle:
    """A transcription session that opens nothing: the engine's own wiring is what is under test."""

    def __init__(self, **kw):
        self.name, self.state = kw["name"], "connected"

    def start(self, q):
        pass

    async def stop(self):
        pass

    def health(self):
        return {"name": self.name, "state": self.state}


class Capture:
    error = None

    def start(self, *a, **kw):
        return {}

    def stop(self):
        pass

    def status(self):
        return {}

    def add_consumer(self):
        return asyncio.Queue()

    def remove_consumer(self, q):
        pass


async def engine_switches_translation():
    transcribe.register_backend(transcribe.Backend(id="idle", label="Idle", session=lambda cfg, key, **kw: Idle(**kw)))
    try:
        config.save({"realtime": {"backend": "idle"}, "openai_api_key": "sk-1", "vercel_api_key": "vk_1",
                     "targets": {"SPANISH": {"enabled": True}}, "ai": {"tasks": {"translation": {"provider": "vercel"}}}})
        eng = engine_mod.Engine()
        eng.bind(asyncio.get_running_loop())
        eng.capture = Capture()
        status = await eng.start()
        first = (eng.transcriber, eng.translator, status["translation"])
        config.save({"ai": {"tasks": {"translation": {"provider": "openai"}}}})
        status = await eng.apply_ai()
        after = (eng.transcriber, eng.translator, status["translation"], eng.translator.url, eng.translator.api_key)
        await eng.stop()
        config.save({"vercel_api_key": "", "openai_api_key": "", "ai": {"tasks": {"translation": {"provider": "vercel"}}}})
        status = await eng.start()
        refused = status["error"]
        await eng.stop()
        return first, after, refused
    finally:
        transcribe.unregister_backend("idle")
        config.save({"realtime": {"backend": "scribe"}})


with section("switching translation keeps the captions running"):
    first, after, refused = asyncio.run(engine_switches_translation())
    eq("translation started on Vercel", (first[2]["provider"], first[2]["model"]), ("vercel", "openai/gpt-6-luna"))
    check("switching to the OpenAI key kept the same transcription session and translator",
          after[0] is first[0] and after[1] is first[1])
    eq("…now pointed at OpenAI", (after[2]["provider"], after[3], after[4]),
       ("openai", aimodels.OPENAI_CHAT_URL, "sk-1"))
    check("with no key for translation the captions still start and say what is missing",
          refused == engine_mod.START_MESSAGES["no_translation_key"], refused)


with section("Vercel's model list"):
    eq("a language model's price is per 1M tokens", aimodels.price("language", {"input": "0.0000001", "output": "0.0000005"}),
       ("$0.10 in, $0.50 out per 1M tokens", False))
    eq("a transcription model is priced per hour of audio",
       aimodels.price("transcription", {"transcription_duration_cost_per_second": "0.000075"}),
       ("$0.27 per hour of audio", False))
    eq("all-zero prices are free", aimodels.price("language", {"input": "0", "output": "0"}), ("free", True))
    eq("no price listed is said so", aimodels.price("transcription", {}), ("price not listed", None))
    cat = aimodels.Catalogue(url=f"{BASE}/models")
    got = cat.get()
    ids = [m["id"] for m in got["models"]]
    check("only language, transcription and speech models are offered", "google/veo" not in ids and len(ids) == 6, ids)
    eq("free ones first within a type, by tag or by price",
       [m["id"] for m in got["models"] if m["type"] == "language"],
       ["nvidia/nemotron-free", "reflection/beam-free", "openai/gpt-6-luna"])
    scribe = next(m for m in got["models"] if m["id"] == "elevenlabs/scribe-v2-realtime")
    check("a streaming transcription model is marked so", scribe["streaming"] and scribe["price"] == "price not listed",
          scribe)
    check("a non-streaming one is not", not next(m for m in got["models"] if m["id"] == "openai/gpt-transcribe")["streaming"])
    Gateway.models_status = 503
    later = cat.get(refresh=True)
    check("a failed refresh keeps the last list and says so", later["models"] == got["models"] and later["error"],
          later["error"])
    Gateway.models_status = 200
    empty = aimodels.Catalogue(url=f"{BASE}/nowhere").get()
    check("no list at all is an empty list with a sentence", empty["models"] == [] and empty["error"], empty)


with section("the AI models page's routes"):
    owner = _db.client(app)
    config.save({"openai_api_key": "sk-1", "vercel_api_key": ""})
    page = owner.get("/api/admin/ai").json()
    check("the page lists every task with what it runs on",
          [t["id"] for t in page["tasks"]] == list(aimodels.TASK_IDS)
          and page["tasks"][0]["running_provider"] == "openai" and page["tasks"][0]["warning"], page["tasks"][0])
    check("…the transcription backends and the saved keys",
          page["transcription"]["backend"] == "scribe" and page["transcription"]["running"] == "openai"
          and page["keys"] == {"vercel": False, "openai": True}, page["transcription"])
    r = owner.post("/api/admin/config", json={"ai": {"tasks": {"translation": {"provider": "openai",
                                                                               "openai_model": "gpt-5.6-luna"}}}})
    check("a task's provider and model save through the config route",
          r.status_code == 200 and config.get()["ai"]["tasks"]["translation"]["openai_model"] == "gpt-5.6-luna", r.text)
    operator = _db.client(app, role="operator")
    eq("an operator may not change them", operator.post("/api/admin/config", json={"ai": {}}).status_code, 403)
    eq("…or read the page", operator.get("/api/admin/ai").status_code, 403)
    check("no key reaches the page", "sk-1" not in json.dumps(page))

server.shutdown()
done()
