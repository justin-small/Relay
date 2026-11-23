"""OBS Studio module (#250) against a fake obs-websocket 5 server on localhost: the password handshake, scenes and
events, the health rule, captions into the stream (#140), the preview, the admin routes and the scene screen.
No real OBS. Run: python tests/test_obs.py
"""
import asyncio
import base64
import json
import os
import sys
import tempfile
import time
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

import websockets  # noqa: E402

import app.main as main  # noqa: E402,F401
from app import config  # noqa: E402
from app.hub import hub  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as _modules  # noqa: E402
from app.modules.obs import client as cl  # noqa: E402
from app.modules.obs import routes as rt  # noqa: E402
from app.modules.obs import service as svc  # noqa: E402
from app import modules as registry  # noqa: E402
from app import tally  # noqa: E402
from app.streamstatus import streams  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from _harness import check, done, eq, section  # noqa: E402

JPEG = b"\xff\xd8\xff\xe0fakejpeg"


class FakeObs:
    """Just enough obs-websocket 5 to follow, switch, caption and preview."""

    def __init__(self, password=""):
        self.password = password
        self.scenes = ["Walk-in", "Worship", "Message"]     # OBS's own top-to-bottom order
        self.program = "Walk-in"
        self.stream = {"outputActive": False, "outputReconnecting": False, "outputDuration": 0, "outputBytes": 0,
                       "outputCongestion": 0.0, "outputSkippedFrames": 0, "outputTotalFrames": 0}
        self.record = {"outputActive": False, "outputPaused": False, "outputDuration": 0, "outputBytes": 0}
        self.stats = {"cpuUsage": 3.0, "memoryUsage": 300.0, "activeFps": 30.0, "averageFrameRenderTime": 4.0,
                      "availableDiskSpace": 100000.0, "renderSkippedFrames": 0, "renderTotalFrames": 0,
                      "outputSkippedFrames": 0, "outputTotalFrames": 0}
        self.captions = []
        self.clients = []
        self.identified = 0
        self.studio = False
        self.preview = None
        self.replay_on = True           # the replay buffer is switched on in OBS's output settings
        self.replay_active = False
        self.saved = 0
        self.can_pause = True
        self.inputs = {"Pastor mic": False, "Music": False, "Camera 1": None}   # None: no audio
        self.service = {"streamServiceType": "rtmp_common",
                        "streamServiceSettings": {"service": "YouTube - RTMPS", "key": "SECRET-KEY-abcd",
                                                  "server": "rtmps://a.rtmps.youtube.com:443/live2",
                                                  "broadcast_id": "dQw4w9WgXcQ"}}
        self.requests = []
        self.subscriptions = None

    async def handler(self, ws):
        hello = {"obsWebSocketVersion": "5.7.6", "rpcVersion": 1}
        if self.password:
            hello["authentication"] = {"challenge": "chal", "salt": "salt"}
        await ws.send(json.dumps({"op": 0, "d": hello}))
        try:
            ident = json.loads(await ws.recv())
        except websockets.ConnectionClosed:
            return          # Relay gave up before identifying (no password)
        if self.password and ident["d"].get("authentication") != cl.auth_string(self.password, "salt", "chal"):
            await ws.close(4009, "Authentication failed.")
            return
        self.identified += 1
        self.subscriptions = ident["d"].get("eventSubscriptions")
        await ws.send(json.dumps({"op": 2, "d": {"negotiatedRpcVersion": 1}}))
        self.clients.append(ws)
        try:
            async for raw in ws:
                d = json.loads(raw)["d"]
                kind, data = d["requestType"], d.get("requestData") or {}
                self.requests.append(kind)
                ok, out = True, {}
                if kind == "GetVersion":
                    out = {"obsVersion": "33.0.1", "obsWebSocketVersion": "5.7.6"}
                elif kind == "GetSceneList":
                    n = len(self.scenes)
                    out = {"currentProgramSceneName": self.program, "currentPreviewSceneName": None,
                           "scenes": [{"sceneName": s, "sceneIndex": n - 1 - i} for i, s in reversed(list(enumerate(self.scenes)))]}
                elif kind == "GetStreamStatus":
                    out = dict(self.stream)
                elif kind == "GetRecordStatus":
                    out = dict(self.record)
                elif kind == "GetStats":
                    out = dict(self.stats)
                elif kind == "SetCurrentProgramScene":
                    ok = data.get("sceneName") in self.scenes
                    if ok:
                        self.program = data["sceneName"]
                        await self.event("CurrentProgramSceneChanged", {"sceneName": self.program})
                elif kind == "SendStreamCaption":
                    ok = self.stream["outputActive"]
                    if ok:
                        self.captions.append(data.get("captionText"))
                elif kind == "GetStudioModeEnabled":
                    out = {"studioModeEnabled": self.studio}
                elif kind == "SetStudioModeEnabled":
                    self.studio = data["studioModeEnabled"]
                    self.preview = self.program if self.studio else None
                    await self.event("StudioModeStateChanged", {"studioModeEnabled": self.studio})
                elif kind == "SetCurrentPreviewScene":
                    ok = self.studio and data.get("sceneName") in self.scenes
                    if ok:
                        self.preview = data["sceneName"]
                        await self.event("CurrentPreviewSceneChanged", {"sceneName": self.preview})
                elif kind == "TriggerStudioModeTransition":
                    ok = self.studio
                    if ok:
                        self.program, self.preview = self.preview, self.program
                        await self.event("CurrentProgramSceneChanged", {"sceneName": self.program})
                        await self.event("CurrentPreviewSceneChanged", {"sceneName": self.preview})
                elif kind == "GetReplayBufferStatus":
                    ok = self.replay_on
                    out = {"outputActive": self.replay_active}
                elif kind in ("StartReplayBuffer", "StopReplayBuffer"):
                    self.replay_active = kind == "StartReplayBuffer"
                    await self.event("ReplayBufferStateChanged", {"outputActive": self.replay_active})
                elif kind == "SaveReplayBuffer":
                    ok = self.replay_active
                    if ok:
                        self.saved += 1
                        await self.event("ReplayBufferSaved", {"savedReplayPath": "C:\\Videos\\Replay 1.mkv"})
                elif kind == "GetStreamServiceSettings":
                    out = json.loads(json.dumps(self.service))
                elif kind == "GetInputList":
                    out = {"inputs": [{"inputName": n, "inputKind": "x"} for n in self.inputs]}
                elif kind == "GetInputMute":
                    ok = self.inputs.get(data.get("inputName")) is not None
                    out = {"inputMuted": bool(self.inputs.get(data.get("inputName")))}
                elif kind == "SetInputMute":
                    ok = self.inputs.get(data.get("inputName")) is not None
                    if ok:
                        self.inputs[data["inputName"]] = data["inputMuted"]
                        await self.event("InputMuteStateChanged", {"inputName": data["inputName"], "inputMuted": data["inputMuted"]})
                elif kind in ("StartStream", "StopStream"):
                    on = kind == "StartStream"
                    await self.event("StreamStateChanged", {"outputActive": False if not on else False,
                                                            "outputState": "OBS_WEBSOCKET_OUTPUT_STARTING" if on else "OBS_WEBSOCKET_OUTPUT_STOPPING"})
                    self.stream["outputActive"] = on
                    await self.event("StreamStateChanged", {"outputActive": on,
                                                            "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED" if on else "OBS_WEBSOCKET_OUTPUT_STOPPED"})
                elif kind in ("StartRecord", "StopRecord"):
                    self.record.update(outputActive=kind == "StartRecord", outputPaused=False)
                elif kind in ("PauseRecord", "ResumeRecord"):
                    if self.can_pause:
                        self.record["outputPaused"] = kind == "PauseRecord"
                elif kind == "GetSourceScreenshot":
                    out = {"imageData": "data:image/jpg;base64," + base64.b64encode(JPEG).decode()}
                await ws.send(json.dumps({"op": 7, "d": {"requestType": kind, "requestId": d["requestId"],
                                                         "requestStatus": {"result": ok, "code": 100 if ok else 501},
                                                         "responseData": out}}))
        except websockets.ConnectionClosed:
            pass

    async def event(self, kind, data):
        for c in list(self.clients):
            try:
                await c.send(json.dumps({"op": 5, "d": {"eventType": kind, "eventIntent": 4, "eventData": data}}))
            except websockets.ConnectionClosed:
                pass


async def wait_for_async(pred, timeout=5.0):
    end = asyncio.get_running_loop().time() + timeout
    while not pred() and asyncio.get_running_loop().time() < end:
        await asyncio.sleep(0.02)
    return pred()


with section("settings are cleaned and secrets kept out of a read"):
    s = svc.clean_settings({"machines": [
        {"id": "a1", "name": "  Stream   PC ", "host": "10.0.0.5", "port": 99999, "captions": True, "caption_lang": "SPANISH",
         "key": "f" * 32, "screen_on": True},
        {"id": "a1", "host": "bad host!", "caption_lang": "KLINGON"}] + [{}] * 5,
        "passwords": json.dumps({"a1": "pw", "zz": "gone"})})
    eq("at most four computers", len(s["machines"]), svc.MAX_MACHINES)
    m0, m1 = s["machines"][:2]
    eq("name, port and language cleaned", (m0["name"], m0["port"], m0["caption_lang"]), ("Stream PC", 4455, "SPANISH"))
    check("a repeated id gets a new one", m1["id"] != "a1")
    eq("a bad address and language fall back", (m1["host"], m1["caption_lang"]), ("", "source"))
    eq("a password for a computer that is gone is dropped", json.loads(s["passwords"]), {"a1": "pw"})
    p = svc.public(s)
    check("public carries no password and no key", "passwords" not in p and all("key" not in m for m in p["machines"]))
    eq("but says one is saved", (p["machines"][0]["has_password"], p["machines"][0]["has_key"]), (True, True))

with section("captions split into 608-sized lines"):
    eq("short stays whole", svc.caption_lines("Hello there"), ["Hello there"])
    lines = svc.caption_lines("The grace of the Lord Jesus Christ be with you all, now and forevermore. Amen.")
    check("every line fits 32 characters", all(len(x) <= 32 for x in lines), lines)
    eq("nothing is lost", " ".join(lines), "The grace of the Lord Jesus Christ be with you all, now and forevermore. Amen.")
    eq("a long word is cut", svc.caption_lines("x" * 70), ["x" * 32, "x" * 32, "x" * 6])


async def connection_tests():
    fake = FakeObs(password="hunter2")
    server = await websockets.serve(fake.handler, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    cfg = svc.clean_machine({"id": "m1", "name": "Stream PC", "host": "127.0.0.1", "port": port}, set())

    with section("password"):
        r = await rt.test_machine(cfg, "wrong")
        check("a wrong password is refused, safely worded", not r["ok"] and "refused the password" in r["detail"], r)
        r = await rt.test_machine(cfg, "")
        check("no password when OBS wants one says so", not r["ok"] and "asks for a password" in r["detail"], r)
        r = await rt.test_machine(cfg, "hunter2")
        check("the right one connects and counts scenes", r["ok"] and "3 scenes" in r["detail"], r)

    with section("unreachable"):
        r = await rt.test_machine({**cfg, "port": 1}, "")
        check("a closed port is reported, not raised", not r["ok"] and "Could not reach OBS" in r["detail"], r)

    m = svc.Machine(cfg, "hunter2")
    m.start()
    try:
        with section("follows OBS"):
            check("connects", await wait_for_async(lambda: m.state == "connected"))
            eq("scenes in OBS's own order", m.scenes, ["Walk-in", "Worship", "Message"])
            eq("program scene", m.program, "Walk-in")
            eq("idle when not streaming", m.health()[0], "idle")
            e = next(x for x in streams.entries(admin=True) if x["source"] == "obs_m1")
            eq("published to the stream status", (e["label"], e["on_air"], e["health"]), ("Stream PC", False, "idle"))

        with section("switching scenes"):
            await m.set_scene("Worship")
            eq("OBS switched", fake.program, "Worship")
            fake.program = "Message"
            await fake.event("CurrentProgramSceneChanged", {"sceneName": "Message"})
            check("a switch made in OBS is followed", await wait_for_async(lambda: m.program == "Message"))
            try:
                await m.set_scene("Nope")
                check("an unknown scene is refused", False)
            except cl.ObsError:
                check("an unknown scene is refused", True)

        with section("health while streaming"):
            fake.stream.update(outputActive=True, outputDuration=65000, outputTotalFrames=1000)
            await fake.event("StreamStateChanged", {"outputActive": True, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"})
            check("ok when streaming cleanly", await wait_for_async(lambda: m.health()[0] == "ok"), m.health())
            check("the minute's first sample is taken", await wait_for_async(lambda: len(m.samples) >= 1, 6))

            def entry():
                return next(x for x in streams.entries() if x["source"] == "obs_m1")
            check("on air, live since about 65 s ago",
                  await wait_for_async(lambda: entry()["on_air"] and abs(time.time() - 65 - entry()["live_since"]) < 5, 6), entry())
            e = entry()
            check("the public entry has no detail", "detail" not in e)
            fake.stream.update(outputTotalFrames=2000, outputSkippedFrames=50)   # 5% of the new frames dropped
            check("warn over 1% dropped", await wait_for_async(lambda: m.health()[0] == "warn", 6), m.health())
            check("says why", "dropped (network)" in m.health()[1], m.health())
            fake.stream.update(outputTotalFrames=10, outputSkippedFrames=0)     # counters reset: a new stream
            check("a reset starts the minute again", await wait_for_async(lambda: m.health()[0] == "ok", 6), m.health())
            fake.stream["outputReconnecting"] = True
            check("warn while reconnecting", await wait_for_async(lambda: "reconnecting" in m.health()[1], 6), m.health())
            fake.stream["outputReconnecting"] = False

        with section("outputs that follow Start and Stop"):
            check("off by default", not m.cfg["start_record"] and not m.cfg["start_stream"])
            await m.follow_start()
            check("nothing starts while it is not asked", not fake.record["outputActive"] and "StartStream" not in fake.requests)
            m.cfg["start_record"] = True
            await m.follow_start()
            check("Start begins the recording", await wait_for_async(lambda: fake.record["outputActive"]))
            eq("and remembers it began it", m.started_by_start, {"record"})
            await m.follow_stop()
            check("Stop ends the recording it began", await wait_for_async(lambda: not fake.record["outputActive"]))
            check("and leaves a stream nobody asked it to touch", fake.stream["outputActive"])
            fake.record.update(outputActive=True)
            m.cfg["start_record"] = False
            await m.follow_stop()
            check("an output the operator started by hand is left running", fake.record["outputActive"])
            fake.record.update(outputActive=False)

        with section("captions"):
            hub.publish_final("transcription", "ENGLISH", "This line is for the people who watch at home today.")
            check("off by default: nothing sent", not await wait_for_async(lambda: fake.captions, 0.6))
            m.cfg["captions"] = True
            svc.service.machines["m1"] = m
            svc.service._loop = asyncio.get_running_loop()
            svc.attach_hooks()
            hub.publish_final("transcription", "ENGLISH", "This line is for the people who watch at home today.")
            check("sent in short lines", await wait_for_async(lambda: len(fake.captions) >= 2, 4), fake.captions)
            check("each fits 608", all(len(x) <= 32 for x in fake.captions), fake.captions)
            n = len(fake.captions)
            hub.publish_final("translation", "SPANISH", "Hola")
            check("another language is not sent", not await wait_for_async(lambda: len(fake.captions) > n, 0.6))
            m.cfg["caption_lang"] = "SPANISH"
            hub.publish_final("translation", "SPANISH", "Hola a todos")
            check("the chosen language is", await wait_for_async(lambda: fake.captions[-1:] == ["Hola a todos"], 4), fake.captions)
            svc.detach_hooks()
            check("detach removes the sink", svc.service.line_sink not in hub.line_sinks)
            svc.service.machines.clear()

        with section("unexpected stop"):
            fake.stream.update(outputActive=False)
            await fake.event("StreamStateChanged", {"outputActive": False, "outputState": "OBS_WEBSOCKET_OUTPUT_STOPPED"})
            check("a stop with no 'stopping' first is an error", await wait_for_async(lambda: m.health()[0] == "error"),
                  m.health())
            check("says it stopped by itself", "stopped by itself" in m.health()[1])
            fake.stream.update(outputActive=True)
            await fake.event("StreamStateChanged", {"outputActive": True, "outputState": "OBS_WEBSOCKET_OUTPUT_STARTED"})
            check("starting again clears it", await wait_for_async(lambda: m.health()[0] != "error", 6), m.health())
            await fake.event("StreamStateChanged", {"outputActive": True, "outputState": "OBS_WEBSOCKET_OUTPUT_STOPPING"})
            fake.stream.update(outputActive=False)
            await fake.event("StreamStateChanged", {"outputActive": False, "outputState": "OBS_WEBSOCKET_OUTPUT_STOPPED"})
            check("an operator's stop is idle", await wait_for_async(lambda: m.health()[0] == "idle", 6), m.health())

        with section("preview"):
            eq("a JPEG of the program scene", await m.screenshot(), JPEG)

        with section("YouTube: the broadcast, never the key"):
            snap = m.snapshot()
            eq("the service and broadcast", (snap["service"], snap["youtube"]["id"]), ("YouTube - RTMPS", "dQw4w9WgXcQ"))
            check("a Studio link", snap["youtube"]["studio"] == "https://studio.youtube.com/video/dQw4w9WgXcQ/livestreaming")
            check("the key and server never reach the snapshot", "SECRET-KEY" not in json.dumps(snap) and "rtmps://" not in json.dumps(snap))
            check("nor the machine", "SECRET-KEY" not in repr(vars(m)))
            eq("audio inputs only", m.inputs, ["Pastor mic", "Music"])
            check("no meters without watched audio", not fake.subscriptions & cl.METERS)

        await features(fake, port)

        with section("OBS closes"):
            for c in list(fake.clients):
                await c.close()
            server.close()
            await server.wait_closed()
            check("an error once it cannot reconnect", await wait_for_async(lambda: m.health()[0] == "error", 8), m.health())
    finally:
        await m.stop()
        streams.clear(m.source)


class FakeAlerts:
    def __init__(self):
        self.presses = []
        self.latest = {}

    def button(self, bid):
        return {"id": bid, "name": bid} if bid in ("bad", "meh") else None

    def press(self, bid, sender):
        self.presses.append((bid, sender))


async def features(fake, port):
    fake_alerts = FakeAlerts()
    real_alerts, svc.alerts = svc.alerts, fake_alerts
    cfg = svc.clean_machine({"id": "m2", "name": "Record PC", "host": "127.0.0.1", "port": port,
                             "audio": [{"name": "Pastor mic", "warn_muted": True}],
                             "tally": [{"n": 1, "scene": "Walk-in"}, {"n": 2, "scene": "Worship"}],
                             "alert_error": "bad", "alert_warn": "meh"}, set())
    m = svc.Machine(cfg, "hunter2")
    fake.program = "Walk-in"
    fake.stream.update(outputActive=False, outputReconnecting=False, outputBytes=0, outputSkippedFrames=0, outputTotalFrames=0)
    m.start()
    try:
        with section("audio meters"):
            check("connects", await wait_for_async(lambda: m.state == "connected"))
            check("meters asked for when audio is watched", fake.subscriptions & cl.METERS)
            await fake.event("InputVolumeMeters", {"inputs": [
                {"inputName": "Pastor mic", "inputLevelsMul": [[0.1, 0.5, 0.5], [0.1, 0.25, 0.25]]},
                {"inputName": "Music", "inputLevelsMul": [[1.0, 1.0, 1.0]]}]})
            check("the loudest channel, in dB", await wait_for_async(lambda: m.audio()[0]["db"] == -6.0), m.audio())
            eq("only watched inputs", [a["name"] for a in m.audio()], ["Pastor mic"])

        with section("tally"):
            def cams():
                return {c["n"]: (c["program"], c["preview"]) for c in tally.tally._sources.get("obs_m2", {}).values()} \
                    if isinstance(tally.tally._sources.get("obs_m2"), dict) else \
                    {c["n"]: (c["program"], c["preview"]) for c in tally.tally._sources.get("obs_m2", [])}
            eq("the program scene's camera is on air", cams(), {1: (True, False), 2: (False, False)})

        with section("studio mode"):
            await m.set_studio(True)
            await m.set_preview("Worship")
            check("preview shows on tally", await wait_for_async(lambda: cams().get(2) == (False, True)), cams())
            await m.take()
            check("take puts preview on air", await wait_for_async(lambda: m.program == "Worship"))
            check("and the tally follows", await wait_for_async(lambda: cams().get(2) == (True, False)), cams())
            await m.set_studio(False)
            try:
                await m.set_preview("Message")
                check("preview needs studio mode", False)
            except cl.ObsError:
                check("preview needs studio mode", True)

        with section("start and stop the stream"):
            await m.output("stream", "start")
            check("live", await wait_for_async(lambda: m.stream.get("outputActive")))
            check("idle or ok while streaming", await wait_for_async(lambda: m.health()[0] == "ok", 6), m.health())

        with section("a muted microphone while live"):
            await m.set_mute("Pastor mic", True)
            eq("OBS muted it", fake.inputs["Pastor mic"], True)
            check("warn", await wait_for_async(lambda: m.health()[0] == "warn"), m.health())
            check("says which", "Pastor mic is muted while live" in m.health()[1], m.health())
            eq("the warn alert is raised once", fake_alerts.presses, [("meh", "OBS (Record PC)")])
            m.publish()
            m.publish()
            eq("not again while it lasts", len(fake_alerts.presses), 1)
            await m.set_mute("Pastor mic", False)
            check("ok again", await wait_for_async(lambda: m.health()[0] == "ok", 6), m.health())
            eq("the problem is over", m.alerted, "")

        with section("bitrate history"):
            fake.stream.update(outputBytes=0, outputTotalFrames=100)
            await wait_for_async(lambda: m._last_bytes is not None, 6)
            fake.stream.update(outputBytes=1_000_000, outputTotalFrames=200, outputSkippedFrames=2)
            check("a point is added", await wait_for_async(lambda: len(m.history) >= 1, 6), list(m.history))
            h = m.history[-1]
            check("kb/s and dropped % this interval", h[1] > 0 and h[3] == 2.0, h)

        with section("caption delay"):
            m.cfg.update(captions=True, caption_delay=0.8)
            svc.service.machines = {"m2": m}
            svc.service._loop = asyncio.get_running_loop()
            svc.attach_hooks()
            n = len(fake.captions)
            hub.publish_final("transcription", "ENGLISH", "Held back a moment")
            check("not sent at once", not await wait_for_async(lambda: len(fake.captions) > n, 0.4))
            check("sent after the delay", await wait_for_async(lambda: fake.captions[-1:] == ["Held back a moment"], 3))

        with section("slide tags switch scenes through core"):
            r = await registry.switch_scene("  message ", source="a test")
            eq("any case, the computer that has it", (r, fake.program), ({"ok": True}, "Message"))
            r = await registry.switch_scene("Nope")
            check("no such scene is refused", not r["ok"] and "Nope" in r["error"], r)
            svc.detach_hooks()
            eq("off: refused", (await registry.switch_scene("Message"))["ok"], False)
            svc.service.machines = {}

        with section("an operator's stop is not an error"):
            await m.output("stream", "stop")
            check("idle, not error", await wait_for_async(lambda: m.health()[0] == "idle", 6), m.health())
            eq("no error alert", [p for p in fake_alerts.presses if p[0] == "bad"], [])

        with section("recording"):
            await m.output("record", "start")
            check("recording", await wait_for_async(lambda: m.record.get("outputActive"), 6))
            fake.can_pause = False
            try:
                await m.output("record", "pause")
                check("a pause OBS ignores is reported", False)
            except cl.ObsError as e:
                check("a pause OBS ignores is reported", "did not pause" in str(e), str(e))
            fake.can_pause = True
            await m.output("record", "pause")
            eq("paused", m.record.get("outputPaused"), True)
            await m.output("record", "stop")
            try:
                await m.output("record", "explode")
                check("an unknown action is refused", False)
            except cl.ObsError:
                check("an unknown action is refused", True)

        with section("replay buffer"):
            eq("available", m.replay["available"], True)
            await m.output("replay", "start")
            check("running", await wait_for_async(lambda: m.replay["active"]))
            await m.output("replay", "save")
            check("the saved file's name only", await wait_for_async(lambda: m.replay["last"] == "Replay 1.mkv"), m.replay)
            m.replay["available"] = False
            try:
                await m.output("replay", "save")
                check("off in OBS says where to switch it on", False)
            except cl.ObsError as e:
                check("off in OBS says where to switch it on", "Settings > Output" in str(e))

        with section("error alert after a lost connection"):
            m.ever_connected = True
            m.state, m.error = "unreachable", "gone"
            m.publish()
            eq("raised once", [p for p in fake_alerts.presses if p[0] == "bad"], [("bad", "OBS (Record PC)")])
            fresh = svc.Machine(cfg, "")
            fresh.state = "unreachable"
            n = len(fake_alerts.presses)
            fresh._alert("error")
            eq("never connected in this run: no alert", len(fake_alerts.presses), n)
            m.state = "connected"
    finally:
        await m.stop()
        svc.alerts = real_alerts
        check("stopping clears the tally", "obs_m2" not in tally.tally._sources)


asyncio.run(connection_tests())

# ------------------------------------------------------------------------------------------------ routes
config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001, "modules": {"obs": {"enabled": True}}})
_modules.attach("obs")
svc.service._running = True       # as after start(); the computers below have no address, so nothing connects
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", username="viewer1", base_url="http://relay.local:8001")
A = "/api/admin/obs"

with section("settings route"):
    r = admin.put(A + "/settings", json={"machines": [{"name": "Stream PC", "password": "pw1"}]})
    eq("saved", r.status_code, 200)
    mid = r.json()["machines"][0]["id"]
    check("the answer never carries the password", "pw1" not in r.text and r.json()["machines"][0]["has_password"], r.text)
    eq("stored", json.loads(svc.service.settings()["passwords"]), {mid: "pw1"})
    r = admin.put(A + "/settings", json={"machines": [{"id": mid, "name": "Stream PC", "password": ""}]})
    eq("a blank password keeps it", json.loads(svc.service.settings()["passwords"]), {mid: "pw1"})
    r = admin.put(A + "/settings", json={"machines": [{"id": mid, "name": "Stream PC", "clear_password": True}]})
    eq("forget clears it", svc.service.settings()["passwords"], "")
    eq("a bad address is refused", admin.put(A + "/settings", json={"machines": [{"host": "not a host!"}]}).status_code, 400)
    eq("five computers are refused", admin.put(A + "/settings", json={"machines": [{}] * 5}).status_code, 400)
    eq("a viewer cannot change it", viewer.put(A + "/settings", json={"machines": []}).status_code, 403)
    eq("a viewer can read it", viewer.get(A).status_code, 200)
    check("the config read carries no password", "pw1" not in admin.get("/api/admin/state").text)

with section("preview is off by default"):
    eq("404 while off", admin.get(f"{A}/preview/{mid}").status_code, 404)

with section("scene screen"):
    eq("off: the same plain 404", viewer.get(f"/screen?obs={mid}&key=" + "0" * 32).status_code, 404)
    r = admin.put(f"{A}/screen/{mid}", json={"on": True})
    link = r.json()["link"]
    key = link.split("key=")[1]
    check("turning on gives a link with a 32-digit key", r.json()["on"] and len(key) == 32, r.json())
    check("the key never reaches a read", key not in admin.get(A).text and key not in admin.get("/api/admin/state").text)
    plain = TestClient(app, base_url="http://relay.local:8000")     # a tablet: the plain viewer port, no sign-in
    eq("the screen opens on the viewer port", plain.get(f"/screen?obs={mid}&key={key}").status_code, 200)
    eq("a wrong key is a 404", plain.get(f"/screen?obs={mid}&key=" + "1" * 32).status_code, 404)
    m = svc.service.machine(mid)

    class FakeConn:
        sent = []
        closed = asyncio.Event()

        async def request(self, kind, data=None, timeout=5):
            FakeConn.sent.append((kind, data))
            return {}

    m.conn, m.state, m.scenes, m.program = FakeConn(), "connected", ["A", "B"], "A"
    st = plain.get(f"/obs/_/state/{mid}?key={key}").json()
    eq("state for the screen", (st["scenes"], st["program"], st["connected"]), (["A", "B"], "A", True))
    r = plain.post(f"/obs/_/scene/{mid}?key={key}", json={"scene": "B"})
    eq("a tap switches", (r.status_code, FakeConn.sent[-1]), (200, ("SetCurrentProgramScene", {"sceneName": "B"})))
    eq("a scene OBS lacks is refused", plain.post(f"/obs/_/scene/{mid}?key={key}", json={"scene": "Z"}).status_code, 404)
    eq("a wrong key cannot switch", plain.post(f"/obs/_/scene/{mid}?key=" + "1" * 32, json={"scene": "A"}).status_code, 404)
    r = admin.put(f"{A}/screen/{mid}", json={"on": True, "rotate": True})
    eq("a new link stops the old one", plain.get(f"/obs/_/state/{mid}?key={key}").status_code, 404)
    m.conn = None

with section("operator routes"):
    body = {"machine": mid, "output": "stream", "action": "stop"}
    eq("a viewer cannot stop the stream", viewer.post(A + "/output", json=body).status_code, 403)
    eq("no such computer", admin.post(A + "/output", json={**body, "machine": "zz"}).status_code, 404)
    r = admin.post(A + "/output", json=body)
    check("not connected is said, not hidden", r.status_code == 502 and "not connected" in r.json()["error"], r.text)
    eq("a bad action is refused", admin.post(A + "/output", json={**body, "action": 7}).status_code, 400)
    eq("studio needs an action", admin.post(A + "/studio", json={"machine": mid}).status_code, 400)
    eq("mute needs a yes or no", admin.post(A + "/mute", json={"machine": mid, "input": "Mic"}).status_code, 400)
    eq("meters for no watched audio", admin.get(f"{A}/audio/{mid}").json(), {"audio": []})
    check("the page gets the alert buttons to choose from", isinstance(admin.get(A).json()["alert_buttons"], list))

with section("module off"):
    _modules.get("obs").detach()
    check("captions sink removed", svc.service.line_sink not in hub.line_sinks)

done()
