"""NDI test patterns (#302): colour bars, tone and your own picture sent as NDI sources, for lining up projectors, TVs,
switchers and sound before a service.

A **test source** is {"id", "name", "pattern", "burn", "audio", "level", "size", "fps"}. `pattern` is one of PATTERNS;
`burn` writes the source's name, size and a running clock (and the frame number) over the picture; `audio` is one of
AUDIO ("" sends no audio at all, "silence" sends zeros) and `level` the tone's dBFS. Each one that is switched on
(`tests_on` in the settings) has a thread that draws a frame, sends it, and sleeps to its frame rate, like a page
output (outputs.py), and is a source other NDI users, and the router (#298), can pick: it is registered through the same
`open_output` as a page output. The pictures are drawn in Rust (relay_ndi, #606; the burned-in text and your own picture by Pillow), so nothing here needs a browser.
"""
from __future__ import annotations

import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np
import relay_ndi

from ...text import same_name
from . import outputs

log = logging.getLogger("relay.ndi.testpatterns")

PATTERNS = {"bars": "Colour bars", "grid": "Grid and crosshair", "ramp": "Greyscale ramp", "white": "White",
            "black": "Black", "red": "Red", "green": "Green", "blue": "Blue", "motion": "Moving element",
            "alpha": "Alpha checker", "image": "Your own picture"}
AUDIO = {"": "No audio", "tone": "1 kHz tone", "id": "Left, then right", "silence": "Silence"}
LEVELS = (0, -3, -9, -12, -18, -20, -30, -40)
SIZES = {"640x360": (640, 360), "1280x720": (1280, 720), "1920x1080": (1920, 1080), "3840x2160": (3840, 2160)}
MAX_TESTS = 12
MAX_ON = 4
MAX_IMAGE = 5 * 1024 * 1024
RATE = 48000
_NAME = re.compile(r"[^\W_][\w .\-()]{0,59}")


def clean_tests(raw: Any) -> list[dict]:
    """The saved test sources, made safe: unknown values fall back to a default, names are made unique, and at most
    MAX_TESTS are kept."""
    out: list[dict] = []
    for t in raw if isinstance(raw, list) else []:
        if not isinstance(t, dict) or len(out) >= MAX_TESTS:
            continue
        tid = t.get("id") if isinstance(t.get("id"), str) and re.fullmatch(r"[a-z0-9]{4,12}", t["id"]) else ""
        name = " ".join(t["name"].split()) if isinstance(t.get("name"), str) else ""
        if not tid or not _NAME.fullmatch(name) or any(x["id"] == tid for x in out):
            continue
        if any(same_name(x["name"], name) for x in out):
            continue
        lvl = t.get("level")
        fps = t.get("fps")
        out.append({"id": tid, "name": name,
                    "pattern": t["pattern"] if t.get("pattern") in PATTERNS else "bars",
                    "burn": t["burn"] if isinstance(t.get("burn"), bool) else True,
                    "audio": t["audio"] if t.get("audio") in AUDIO else "",
                    "level": lvl if lvl in LEVELS and not isinstance(lvl, bool) else -18,
                    "size": t["size"] if t.get("size") in SIZES else "1280x720",
                    "fps": max(5, min(60, fps)) if isinstance(fps, int) and not isinstance(fps, bool) else 30})
    return out


def new_id() -> str:
    return uuid.uuid4().hex[:8]


def ndi_name(test: dict) -> str:
    return outputs.ndi_name("Test " + test["name"])


# ---------------------------------------------------------------------------------------------------- pictures
def _font(size: int):
    from PIL import ImageFont

    return ImageFont.load_default(size)


def load_image(path: Path, w: int, h: int) -> np.ndarray:
    """The user's picture fitted inside w x h and centred on a transparent canvas, as RGBA."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGBA")
        scale = min(w / im.width, h / im.height)
        im = im.resize((max(1, round(im.width * scale)), max(1, round(im.height * scale))), Image.LANCZOS)
    canvas = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    canvas.paste(im, ((w - im.width) // 2, (h - im.height) // 2))
    return np.asarray(canvas)


def check_image(raw: bytes) -> str:
    """'' when `raw` is a PNG Relay will use, else the sentence for the operator."""
    from PIL import Image

    if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "That is not a PNG picture."
    if len(raw) > MAX_IMAGE:
        return "That picture is over 5 MB."
    try:
        import io

        with Image.open(io.BytesIO(raw)) as im:
            im.verify()
            if im.width * im.height > 5000 * 5000:
                return "That picture is too large."
    except Exception:
        return "That PNG could not be read."
    return ""


def base_picture(test: dict, w: int, h: int, image: Path | None = None) -> np.ndarray:
    """The picture that does not change from frame to frame, as BGRA (h, w, 4). Drawn in Rust (#606), except your own
    picture, which Pillow reads."""
    p = test["pattern"]
    if p == "image":
        if image is not None and image.is_file():
            rgba = load_image(image, w, h)
        else:
            rgba = np.zeros((h, w, 4), np.uint8)
            rgba[..., :3] = 24
            rgba[..., 3] = 255
            rgba = _text_on(rgba, ["Upload a PNG for this source"])
        return np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])
    return np.frombuffer(relay_ndi.base_picture(p, w, h), np.uint8).reshape(h, w, 4)


def _text_on(rgba: np.ndarray, lines: list[str]) -> np.ndarray:
    from PIL import Image, ImageDraw

    h, w = rgba.shape[:2]
    im = Image.fromarray(rgba, "RGBA")
    d = ImageDraw.Draw(im)
    f = _font(max(14, h // 18))
    y = h // 2 - len(lines) * f.size
    for ln in lines:
        d.text((w // 2, y), ln, fill=(255, 255, 255, 255), font=f, anchor="mm")
        y += int(f.size * 1.4)
    return np.asarray(im)


class Burn:
    """The text written over the picture: its name, size and clock, in a box on the picture's lower third, drawn on a
    dark plate so it reads on any pattern. The box is drawn again only when its text changes (once a second)."""

    def __init__(self, test: dict, w: int, h: int) -> None:
        self.name, self.w, self.h = test["name"], w, h
        self._font = _font(max(12, h // 22))
        self._cache: tuple[tuple, np.ndarray] | None = None

    def patch(self, now: float, frame: int, fps: int, channel: str = "") -> tuple[int, int, np.ndarray]:
        from PIL import Image, ImageDraw

        clock = time.strftime("%H:%M:%S", time.localtime(now))
        key = (clock, channel, frame // fps)
        if self._cache is None or self._cache[0] != key:
            lines = [self.name, f"{self.w} x {self.h}  {fps} fps", clock + (f"   {channel}" if channel else "")]
            line_h = int(self._font.size * 1.3)
            bw = max(int(self._font.getlength(x)) for x in lines) + self._font.size
            bh = line_h * len(lines) + self._font.size // 2
            im = Image.new("RGBA", (bw, bh), (0, 0, 0, 200))
            d = ImageDraw.Draw(im)
            for i, x in enumerate(lines):
                d.text((self._font.size // 2, self._font.size // 4 + i * line_h), x, fill=(255, 255, 255, 255), font=self._font)
            arr = np.asarray(im)
            self._cache = (key, np.ascontiguousarray(arr[..., [2, 1, 0, 3]]))
        patch = self._cache[1]
        return (self.w - patch.shape[1]) // 2, int(self.h * 0.62), patch


def draw_frame(test: dict, base: Any, burn: Burn | None, now: float, frame: int, channel: str = "") -> bytes:
    """One frame, BGRA bytes: the base picture plus the moving element and the burned-in text. Drawn in Rust (#606);
    `base` is the base picture from `base_picture`."""
    patch = None
    if burn is not None:
        x, y, arr = burn.patch(now, frame, test["fps"], channel)
        patch = (x, y, int(arr.shape[1]), int(arr.shape[0]), arr.tobytes())
    return relay_ndi.draw_frame(test["pattern"], base, now, frame, patch)


# ------------------------------------------------------------------------------------------------------ sound
class Tone:
    """The test audio, one video frame's worth at a time: 48 kHz, two channels, float32 as NDI wants it. Frame lengths
    follow the exact rate (1600, 1600, ... or 1601, 1602 at 29.97), so sound and picture stay together however long it
    runs. Generated in Rust (#606)."""

    def __init__(self, mode: str, level: float, fps: float) -> None:
        self._t = relay_ndi.Tone(mode, float(level), float(fps))

    def channel(self) -> str:
        """Which side is sounding now, for the on-screen label ('' unless the mode names one)."""
        return self._t.channel()

    def next(self) -> np.ndarray:
        """The audio for the next video frame, shaped (2, samples)."""
        return self._t.next()


# ----------------------------------------------------------------------------------------------------- running
class _Run:
    def __init__(self, test: dict) -> None:
        self.test = test
        self.state, self.error = "starting", ""
        self.achieved, self.cpu = 0.0, 0.0
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


class Tests:
    def __init__(self, open_output: Callable[..., Any], settings: Callable[[], dict], folder: Callable[[], Path],
                 notify: Callable[[], None]) -> None:
        self._open_output, self._settings, self._folder, self._notify = open_output, settings, folder, notify
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    def image_path(self, tid: str) -> Path:
        return self._folder() / "tests" / (tid + ".png")

    def status(self) -> list[dict]:
        s = self._settings()
        rows = []
        for t in s["tests"]:
            run = self._runs.get(t["id"])
            rows.append({**t, "ndi": ndi_name(t), "on": t["id"] in s["tests_on"],
                         "has_image": self.image_path(t["id"]).is_file(),
                         "state": run.state if run else "off", "error": run.error if run else "",
                         "fps_now": round(run.achieved, 1) if run else 0, "cpu": round(run.cpu, 1) if run else 0})
        return rows

    def sync(self) -> None:
        """Start the test sources that are on and stop the rest. One whose settings changed starts again."""
        s = self._settings()
        want = {t["id"]: t for t in s["tests"] if t["id"] in s["tests_on"]}
        with self._lock:
            for tid in [k for k in self._runs if k not in want]:
                self._stop(self._runs.pop(tid))
            for tid, t in want.items():
                run = self._runs.get(tid)
                if run and run.test == t and run.state != "error":
                    continue
                if run:
                    self._stop(self._runs.pop(tid))
                run = _Run(t)
                self._runs[tid] = run
                run.thread = threading.Thread(target=self._loop, args=(run,), name="ndi-test-" + tid, daemon=True)
                run.thread.start()
        self._notify()

    def close(self) -> None:
        with self._lock:
            runs, self._runs = list(self._runs.values()), {}
        for r in runs:
            self._stop(r)

    def restart(self, tid: str) -> None:
        """Start one source again (its picture changed)."""
        with self._lock:
            run = self._runs.pop(tid, None)
        if run:
            self._stop(run)
        self.sync()

    def forget(self, tid: str) -> None:
        """A test source was deleted: its picture goes with it."""
        try:
            self.image_path(tid).unlink()
        except OSError:
            pass

    @staticmethod
    def _stop(run: _Run) -> None:
        run.stop.set()
        if run.thread and run.thread is not threading.current_thread():
            run.thread.join(timeout=5)

    def _loop(self, run: _Run) -> None:
        t = run.test
        out = None
        try:
            w, h = SIZES[t["size"]]
            fps = t["fps"]
            base = base_picture(t, w, h, self.image_path(t["id"]))
            burn = Burn(t, w, h) if t["burn"] else None
            tone = Tone(t["audio"], t["level"], fps) if t["audio"] else None
            out = self._open_output(ndi_name(t), w, h, fps, audio=True) if tone else self._open_output(ndi_name(t), w, h, fps)
            if out is None:
                raise RuntimeError("NDI would not open this source (is the name already in use?).")
            run.state = "on"
            self._notify()
            period, frames, n, t0, cpu0 = 1.0 / fps, 0, 0, time.monotonic(), time.thread_time()
            while not run.stop.is_set():
                start = time.monotonic()
                channel = tone.channel() if tone else ""
                frame = {"width": w, "height": h, "fourcc": "BGRA",
                         "data": draw_frame(t, base, burn, time.time(), n, channel)}
                if tone:
                    frame["audio"], frame["rate"] = tone.next(), RATE
                out.send(frame)
                frames += 1
                n += 1
                now = time.monotonic()
                if now - t0 >= 2.0:
                    run.achieved = frames / (now - t0)
                    run.cpu = 100 * (time.thread_time() - cpu0) / (now - t0)
                    frames, t0, cpu0 = 0, now, time.thread_time()
                    self._notify()
                run.stop.wait(max(0.0, period - (time.monotonic() - start)))
        except Exception as exc:
            log.warning("NDI test source %s stopped: %s", t["id"], exc)
            run.state, run.error = "error", str(exc)[:200]
            self._notify()
        finally:
            if out is not None:
                try:
                    out.close()
                except Exception:
                    pass
            if run.state == "on":
                run.state = "off"
