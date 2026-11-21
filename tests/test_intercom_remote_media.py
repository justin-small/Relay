"""The remote intercom's mixer against a real livekit-server and the real LiveKit SDK (#605).

A real livekit-server runs on loopback. Two participants publish tones into a channel's room; the bridge's own
`LiveMedia._hear` joins it, hands the tracks to the Rust mixer, and the test pulls the channel's mix the way aiortc's
track does, every 20 ms, and looks at its spectrum. Then audio from a "main bus" (an engine with a fake microphone)
is fed in through the Rust link, the Python event loop is held busy, and a publisher leaves.

Only the Cloudflare half (aiortc and relay-cloud) is not here; it needs a Cloudflare session.
Skipped without a livekit-server binary. Run: python tests/test_intercom_remote_media.py
"""
import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ["RELAY_LIVEKIT_NO_DOWNLOAD"] = "1"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import numpy as np  # noqa: E402

from app import audiobus  # noqa: E402
from app.modules.intercom import livekit as lk, rooms  # noqa: E402
from app.modules.intercom_remote import media as media_mod, mixbus  # noqa: E402

from _harness import await_for, check, done, section, skip  # noqa: E402

BINARY = lk.find_binary() or shutil.which("livekit-server")
if not BINARY and lk.asset():       # Linux and Windows: the pinned, checksummed release the app itself fetches
    try:
        BINARY = lk.download()
    except Exception as e:          # offline: skip rather than fail
        print(f"could not download livekit-server: {e}")
if not BINARY:
    skip("no livekit-server binary on this computer")
try:
    from livekit import rtc  # noqa: E402
except ImportError:
    skip("the livekit Python SDK is not installed")

KEY, SECRET = "testkey", "t" * 40
ROOM = "intercom-test-1"
FR = mixbus.FRAME
RATE = mixbus.RATE


def free_ports(n: int) -> list[int]:
    socks = [socket.socket() for _ in range(n)]
    for s in socks:
        s.bind(("127.0.0.1", 0))
    ports = [s.getsockname()[1] for s in socks]
    for s in socks:
        s.close()
    return ports


def token(identity: str, publish: bool) -> str:
    now = int(time.time())
    return rooms.sign({"iss": KEY, "sub": identity, "name": identity, "nbf": now - 5, "exp": now + 600,
                       "video": {"roomJoin": True, "room": ROOM, "canPublish": publish, "canSubscribe": not publish,
                                 "canPublishData": False}}, SECRET)


class TestMedia(media_mod.LiveMedia):
    """The real bridge, joined to this test's server instead of the intercom module's."""

    url = ""

    async def _join(self, room, channel_id):
        await room.connect(self.url, token("bridge", False))


def spectrum(pcm: np.ndarray) -> dict[int, float]:
    """The level (0..1 of full scale) of each tone the test publishes, from a windowed FFT of `pcm`."""
    x = pcm.astype(np.float64) / 32768.0
    mag = np.abs(np.fft.rfft(x * np.hanning(len(x)))) * 2 / (len(x) / 2)
    hz = np.fft.rfftfreq(len(x), 1 / RATE)
    # The engine trims the mix's rate by up to 0.5 % to follow the clocks of what feeds it, which moves a tone a few
    # Hz, so each tone is the strongest bin within 2 % of its frequency.
    return {f: float(mag[(hz > f * 0.98) & (hz < f * 1.02)].max()) for f in TONES}


TONES = {440: 0.2, 1000: 0.25, 2500: 0.2}      # Hz -> amplitude published (1000 Hz is the main bus's fake microphone)


class Puller:
    """The pull clock: a 20 ms frame every 20 ms, as aiortc's MixTrack asks for it, kept for the test to read."""

    def __init__(self, mix):
        self.mix, self.frames, self.stop = mix, [], False

    async def run(self):
        start, n = time.monotonic(), 0
        while not self.stop:
            self.frames.append(self.mix.pull(1))
            n += 1
            wait = start + n * FR / RATE - time.monotonic()
            await asyncio.sleep(max(0.0, wait))  # allow-sleep: the pull clock aiortc keeps in production

    def last(self, seconds: float) -> np.ndarray:
        n = int(seconds * RATE / FR)
        return np.concatenate(self.frames[-n:]) if len(self.frames) >= n else np.zeros(0, np.int16)


async def publish(identity: str, hz: int, amp: float):
    room = rtc.Room()
    await room.connect(URL, token(identity, True))
    source = rtc.AudioSource(RATE, 1)
    track = rtc.LocalAudioTrack.create_audio_track(identity, source)
    await room.local_participant.publish_track(track, rtc.TrackPublishOptions(source=rtc.TrackSource.SOURCE_MICROPHONE))
    state = {"on": True}

    async def feed():
        t = 0
        while state["on"]:
            n = np.arange(t, t + RATE // 100)
            pcm = (np.sin(2 * np.pi * hz * n / RATE) * amp * 32767).astype(np.int16)
            await source.capture_frame(rtc.AudioFrame(pcm.tobytes(), RATE, 1, len(pcm)))
            t += len(pcm)

    return room, state, asyncio.create_task(feed())


async def main():
    global URL
    p_http, p_tcp, p_udp = free_ports(3)
    URL = f"ws://127.0.0.1:{p_http}"
    cfg = Path(TMP) / "livekit.yaml"
    cfg.write_text(f"port: {p_http}\nbind_addresses: ['127.0.0.1']\nrtc:\n  tcp_port: {p_tcp}\n  udp_port: {p_udp}\n"
                   f"  use_external_ip: false\n  node_ip: 127.0.0.1\n", encoding="utf-8")
    server = subprocess.Popen([BINARY, "--config", str(cfg)], env={**os.environ, **lk.env_keys(KEY, SECRET)},
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    media, pub = TestMedia(), []
    media.url = URL
    live = audiobus.Bus("fake")
    puller = None
    try:
        def up():
            with socket.socket() as s:
                s.settimeout(0.2)
                return s.connect_ex(("127.0.0.1", p_http)) == 0
        check("livekit-server is up", await await_for(up, 10.0, 0.1))

        with section("two published microphones are mixed by the Rust engine"):
            await media._hear(1)
            check("the bridge joined the room", media.listens[1].room.isconnected())
            pub.append(await publish("alice", 440, TONES[440]))
            pub.append(await publish("bob", 2500, TONES[2500]))
            puller = Puller(media.mix)
            task = asyncio.create_task(puller.run())

            def both():
                s = spectrum(puller.last(1.0)) if len(puller.frames) > 50 else {}
                return bool(s) and s[440] > 0.15 and s[2500] > 0.15
            ok = await await_for(both, 20.0, 0.1)
            s = spectrum(puller.last(1.0))
            check("both tones are in the mix at about the level published", ok, f"{s}")
            check("and nothing is at the third tone's frequency yet", s[1000] < 0.03, f"{s[1000]:.3f}")
            check("no lane is lost: two tracks, two lanes", len([v for v in media.mix._lanes.values()]) == 2)

        with section("main-bus audio reaches the room's mix through the Rust link"):
            live.apply({"enabled": True, "lanes": [{"lane": 1, "source": "portaudio", "device": "Fake Mic", "channel": 0}]})
            check("the feed is accepted", media.feed(1, live.engine, 1, 0.0))

            def three():
                s = spectrum(puller.last(1.0))
                return s[1000] > 0.2 and s[440] > 0.15 and s[2500] > 0.15
            check("all three are in the mix", await await_for(three, 10.0, 0.1), f"{spectrum(puller.last(1.0))}")
            st = media.mix.feed_status(1, 1)
            check("the link lost nothing", st["dropped"] == 0, str(st))

        with section("the mix survives the Python event loop being busy"):
            before = media.mix.feed_status(1, 1)["carried"]
            until = time.monotonic() + 0.5
            while time.monotonic() < until:     # nothing on this loop runs: no push, no pull, no timers
                sum(range(1000))
            after = media.mix.feed_status(1, 1)["carried"]
            check("Rust carried the main bus's audio through the stall", after - before >= 0.45 * RATE,
                  f"{after - before} frames")
            mark = len(puller.frames)

            t0 = time.monotonic()

            def recovered():
                s = spectrum(puller.last(1.0))
                return len(puller.frames) - mark >= 50 and s[440] > 0.15 and s[1000] > 0.2 and s[2500] > 0.15
            ok = await await_for(recovered, 8.0, 0.05)
            took = time.monotonic() - t0
            print(f"       every tone back after {took:.2f} s")
            check("every tone is back in the mix within 2 s", ok and took < 2.0, f"{took:.2f} s {spectrum(puller.last(1.0))}")
            s = spectrum(puller.last(1.0))
            check("all three still there and at level", s[440] > 0.15 and s[1000] > 0.2 and s[2500] > 0.15, f"{s}")
            await await_for(lambda: len(puller.frames) - mark >= 150, 5.0, 0.1)
            burst = sum(1 for f in puller.frames[mark:mark + 40] if not np.abs(f).max())
            print(f"       {burst} frames of the 25 the pull clock owed after the stall had nothing to play")
            gaps = [i for i, f in enumerate(puller.frames[mark + 40:]) if not np.abs(f).max()]
            check("then no silent 20 ms frames", not gaps, f"{len(gaps)} silent frames")
            under = media.mix.status()["outputs"][0]["underruns"]
            check("the mix underran only around the stall", under <= 10, f"{under} underruns")

        with section("a publisher that leaves drops out of the mix"):
            room, state, feeder = pub.pop()           # bob, 2500 Hz
            state["on"] = False
            await feeder
            await room.disconnect()
            gone = await await_for(lambda: spectrum(puller.last(1.0))[2500] < 0.03, 10.0, 0.1)
            check("his tone is gone, the others stay", gone and spectrum(puller.last(1.0))[440] > 0.15,
                  f"{spectrum(puller.last(1.0))}")
            check("his lane is free again", await await_for(lambda: len(media.mix._lanes) == 2, 5.0, 0.05),
                  f"{sorted(media.mix._lanes)}")

        with section("leaving the channel closes the engine's lanes and link"):
            puller.stop = True
            await task
            await media.unlisten(1)
            eq_lanes = len(media.mix._lanes)
            check("nothing is left in the mixer", eq_lanes == 0, f"{eq_lanes} lanes")
            check("and the main bus's tap is released", not live.engine.status()["taps"])
    finally:
        if puller is not None:
            puller.stop = True
        for room, state, feeder in pub:
            state["on"] = False
            feeder.cancel()
            await room.disconnect()
        await media.close()
        live.close()
        server.terminate()
        try:
            server.wait(5)
        except subprocess.TimeoutExpired:
            server.kill()


asyncio.run(main())
done()
