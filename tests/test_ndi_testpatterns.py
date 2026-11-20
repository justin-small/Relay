"""NDI test patterns (#302): the pictures, the tone, the saved settings, the running sources and the admin routes.
A fake NDI backend stands in for the SDK. Run: python tests/test_ndi_testpatterns.py
"""
import asyncio
import io
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
TMP = tempfile.mkdtemp()
os.environ["RELAY_CONFIG"] = os.path.join(TMP, "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

import numpy as np  # noqa: E402

import app.main as main  # noqa: E402,F401
from app.modules.ndi import runtime, testpatterns as tp  # noqa: E402
from app.modules.ndi import service as svc  # noqa: E402
from _harness import check, done, eq, section, tmpdir, wait_for  # noqa: E402


def test(**kw):
    return {**{"id": "abcd1234", "name": "Lobby bars", "pattern": "bars", "burn": True, "audio": "", "level": -18,
               "size": "640x360", "fps": 30}, **kw}


with section("settings are made safe"):
    got = tp.clean_tests([test(), test(id="zzzz9999"), test(id="bad id", name="x"), {"id": "qqqq1111", "name": "Two",
                          "pattern": "nope", "audio": "x", "level": 7, "size": "9x9", "fps": 500}, "junk"])
    eq("a duplicate name is dropped, a bad id too", [t["name"] for t in got], ["Lobby bars", "Two"])
    eq("unknown values fall back", (got[1]["pattern"], got[1]["audio"], got[1]["level"], got[1]["size"], got[1]["fps"]),
       ("bars", "", -18, "1280x720", 60))
    eq("tests_on keeps known ids once", svc.clean_settings({"tests": [test()], "tests_on": ["abcd1234", "abcd1234", "x"]})["tests_on"],
       ["abcd1234"])

with section("pictures"):
    for p in tp.PATTERNS:
        if p == "image":
            continue
        t = test(pattern=p, burn=False)
        base = tp.base_picture(t, 640, 360)
        frame = tp.draw_frame(t, base, None, 100.0, 0)
        check(f"{p} is BGRA of the right size and writable", frame.size == 640 * 360 * 4 and frame.flags.writeable)
    bars = tp.base_picture(test(), 700, 360)
    eq("the first bar is 75% white (BGRA)", tuple(bars[10, 5]), (191, 191, 191, 255))
    eq("the second bar is yellow", tuple(bars[10, 150][:3]), (0, 191, 191))
    al = tp.base_picture(test(pattern="alpha"), 640, 360)
    check("the alpha checker has clear and opaque squares", (al[..., 3] == 0).any() and (al[..., 3] == 255).any())
    ramp = tp.base_picture(test(pattern="ramp"), 640, 360)
    check("the ramp rises", ramp[10, 600, 0] > ramp[10, 40, 0])
    t = test(pattern="motion", burn=False)
    base = tp.base_picture(t, 640, 360)
    a, b = (tp.draw_frame(t, base, None, x, 0) for x in (0.0, 0.5))
    check("the moving element moves", not np.array_equal(a, b))
    t = test(pattern="black")
    burn = tp.Burn(t, 640, 360)
    plain = tp.draw_frame(t, tp.base_picture(t, 640, 360), None, 100.0, 0)
    burned = tp.draw_frame(t, tp.base_picture(t, 640, 360), burn, 100.0, 0)
    check("burn-in writes over the picture", not np.array_equal(plain, burned))
    later = tp.draw_frame(t, tp.base_picture(t, 640, 360), burn, 101.0, 30)
    check("and the clock changes it each second", not np.array_equal(burned, later))

with section("custom picture"):
    from PIL import Image

    folder = Path(tmpdir())
    buf = io.BytesIO()
    Image.new("RGBA", (100, 50), (255, 0, 0, 255)).save(buf, "PNG")
    raw = buf.getvalue()
    eq("a PNG is accepted", tp.check_image(raw), "")
    check("a non-PNG is refused", tp.check_image(b"GIF89a") != "")
    check("a broken PNG is refused", tp.check_image(raw[:40]) != "")
    (folder / "p.png").write_bytes(raw)
    img = tp.base_picture(test(pattern="image"), 640, 360, folder / "p.png")
    eq("it is centred with the corners clear", (img[0, 0, 3], tuple(img[180, 320])), (0, (0, 0, 255, 255)))

with section("tone"):
    tone = tp.Tone("tone", -20, 30)
    audio = [tone.next() for _ in range(30)]
    eq("a second of frames is a second of samples", sum(a.shape[1] for a in audio), 48000)
    eq("stereo float32", (audio[0].shape[0], audio[0].dtype), (2, np.float32))
    allv = np.concatenate(audio, axis=1)
    check("the peak is the level", abs(float(allv.max()) - 0.1) < 0.002)
    ids = tp.Tone("id", 0, 30)
    first = np.concatenate([ids.next() for _ in range(30)], axis=1)
    eq("left sounds first", (bool(first[0].any()), bool(first[1].any())), (True, False))
    eq("and the label says so", ids.channel(), "RIGHT")
    second = np.concatenate([ids.next() for _ in range(30)], axis=1)
    eq("then right", (bool(second[0].any()), bool(second[1].any())), (False, True))
    eq("silence is zeros", float(np.abs(tp.Tone("silence", 0, 30).next()).max()), 0.0)
    odd = tp.Tone("tone", 0, 29.97)
    total = sum(odd.next().shape[1] for _ in range(30))
    check("29.97 stays exact", abs(total - 48048) <= 1)


class FakeBackend:
    def __init__(self):
        self.senders = []

    def connect(self, name, address="", low=False):
        raise OSError("none")

    def open_sender(self, name, width=1280, height=720, fps=30.0, audio=False):
        s = type("S", (), {"sent": [], "name": name, "audio": audio, "send": lambda self, f: self.sent.append(f),
                           "close": lambda self: None})()
        self.senders.append(s)
        return s

    def open_router(self, name):
        raise OSError("none")


with section("running sources"):
    backend = FakeBackend()
    runtime.install(backend)
    s = svc.service
    asyncio.run(s.start())
    s.save_settings({"tests": [test(audio="tone", fps=20), test(id="efgh5678", name="Quiet", pattern="red")],
                     "tests_on": ["abcd1234", "efgh5678"]})
    s.tests.sync()
    check("both come on", wait_for(lambda: sum(1 for r in s.tests.status() if r["state"] == "on") == 2))
    loud = next(x for x in backend.senders if x.name == "Relay - Test Lobby bars")
    quiet = next(x for x in backend.senders if x.name == "Relay - Test Quiet")
    check("the one with sound opened with audio", loud.audio and not quiet.audio)
    check("frames are sent", wait_for(lambda: loud.sent and quiet.sent))
    f = loud.sent[0]
    eq("a frame is BGRA with its audio", (len(f["data"]), f["audio"].shape[0], f["rate"]), (640 * 360 * 4, 2, 48000))
    check("a silent source carries none", "audio" not in quiet.sent[0])
    check("they can be routed from: listed as inputs", {"Relay - Test Quiet", "Relay - Test Lobby bars"} <= {i["name"] for i in s.inputs()})
    s.save_settings({"tests_on": ["efgh5678"]})
    s.tests.sync()
    check("one switched off stops and frees its name", wait_for(lambda: "Relay - Test Lobby bars" not in s.outputs))
    s.save_settings({"tests_on": []})
    s.tests.sync()
    check("all off", wait_for(lambda: not s.outputs))

with section("a picture and deleting"):
    s.save_settings({"tests": [test(pattern="image")]})
    path = s.tests.image_path("abcd1234")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    check("status says a picture is set", s.tests.status()[0]["has_image"])
    s.tests.forget("abcd1234")
    check("forgetting removes it", not path.exists())

asyncio.run(s.stop())
done()
