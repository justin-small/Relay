"""NDI test patterns (#302): colour bars, tone and your own picture sent as NDI sources, for lining up projectors, TVs,
switchers and sound before a service.

A **test source** is {"id", "name", "pattern", "burn", "audio", "level", "size", "fps"}. `pattern` is one of PATTERNS;
`burn` writes the source's name, size and a running clock (and the frame number) over the picture; `audio` is one of
AUDIO ("" sends no audio at all, "silence" sends zeros) and `level` the tone's dBFS. Each one that is switched on
(`tests_on` in the settings) has a thread that draws a frame, sends it, and sleeps to its frame rate, like a page
output (outputs.py), and is a source other NDI users, and the router (#298), can pick: it is registered through the same
`open_output` as a page output. The pictures are drawn with numpy and Pillow, so nothing here needs a browser.
"""
from __future__ import annotations

import logging
import math
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

import numpy as np

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
TONE_HZ = 1000.0
SOLID = {"white": (255, 255, 255), "black": (0, 0, 0), "red": (255, 0, 0), "green": (0, 255, 0), "blue": (0, 0, 255)}
BARS = [(191, 191, 191), (191, 191, 0), (0, 191, 191), (0, 191, 0), (191, 0, 191), (191, 0, 0), (0, 0, 191)]
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


def _bars(w: int, h: int) -> np.ndarray:
    """SMPTE-style bars: seven 75% bars over a strip of reverse blue bars, then a ramp strip."""
    img = np.zeros((h, w, 3), np.uint8)
    top, mid = int(h * 0.67), int(h * 0.75)
    edges = [round(i * w / 7) for i in range(8)]
    for i, c in enumerate(BARS):
        img[:top, edges[i]:edges[i + 1]] = c
    rev = [BARS[6], (19, 19, 19), BARS[4], (19, 19, 19), BARS[2], (19, 19, 19), BARS[0]]
    for i, c in enumerate(rev):
        img[top:mid, edges[i]:edges[i + 1]] = c
    img[mid:] = (16, 16, 16)
    q = round(w * 5 / 7 / 4)                 # the bottom strip: -I, white, +Q, black, then the pluge
    for i, c in enumerate([(0, 33, 76), (255, 255, 255), (50, 0, 106), (16, 16, 16)]):
        img[mid:, i * q:(i + 1) * q] = c
    return img


def _ramp(w: int, h: int) -> np.ndarray:
    row = np.linspace(0, 255, w).round().astype(np.uint8)
    img = np.repeat(row[None, :, None], 3, axis=2)
    img = np.repeat(img, h, axis=0)
    steps = np.repeat((np.arange(11) * 25.5).round().astype(np.uint8), math.ceil(w / 11))[:w]     # ten steps below
    img[int(h * 0.75):] = steps[None, :, None]
    return img


def _grid(w: int, h: int) -> np.ndarray:
    """A 16 x 9 grid, a crosshair, a circle, and the action-safe (90%) and title-safe (80%) lines."""
    img = np.full((h, w, 3), 40, np.uint8)
    cx, cy = w // 2, h // 2
    t = max(1, h // 540)
    for i in range(17):
        x = min(w - t, round(i * w / 16))
        img[:, x:x + t] = (120, 120, 120)
    for j in range(10):
        y = min(h - t, round(j * h / 9))
        img[y:y + t, :] = (120, 120, 120)
    img[:, cx - t:cx + t] = (255, 255, 255)
    img[cy - t:cy + t, :] = (255, 255, 255)
    yy, xx = np.ogrid[:h, :w]
    r = h * 0.45
    ring = np.abs(np.hypot(xx - cx, yy - cy) - r) < t * 1.5
    img[ring] = (255, 255, 255)
    for frac, colour in ((0.9, (255, 220, 0)), (0.8, (0, 220, 255))):         # action safe, then title safe
        x0, x1, y0, y1 = round(w * (1 - frac) / 2), round(w * (1 + frac) / 2), round(h * (1 - frac) / 2), round(h * (1 + frac) / 2)
        img[y0:y0 + 2 * t, x0:x1] = colour
        img[y1 - 2 * t:y1, x0:x1] = colour
        img[y0:y1, x0:x0 + 2 * t] = colour
        img[y0:y1, x1 - 2 * t:x1] = colour
    return img


def _alpha(w: int, h: int) -> np.ndarray:
    """RGBA: a checker of opaque colour and fully clear squares, so a keyer shows what it keeps."""
    size = max(8, h // 18)
    yy, xx = np.indices((h, w))
    odd = ((yy // size) + (xx // size)) % 2 == 1
    img = np.zeros((h, w, 4), np.uint8)
    img[odd] = (255, 255, 255, 255)
    img[~odd] = (0, 0, 0, 0)
    band = slice(h // 3, 2 * h // 3)
    img[band, w // 4:3 * w // 4, :] = (255, 40, 40, 128)                          # half transparent, to see a mix
    return img


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
    """The picture that does not change from frame to frame, as BGRA (h, w, 4)."""
    p = test["pattern"]
    if p == "alpha":
        rgba = _alpha(w, h)
    elif p == "image":
        if image is not None and image.is_file():
            rgba = load_image(image, w, h)
        else:
            rgba = np.zeros((h, w, 4), np.uint8)
            rgba[..., :3] = 24
            rgba[..., 3] = 255
            rgba = _text_on(rgba, ["Upload a PNG for this source"])
    else:
        rgb = {"bars": _bars, "ramp": _ramp, "grid": _grid}.get(p, lambda W, H: np.full((H, W, 3), SOLID.get(p, (16, 16, 16)), np.uint8))(w, h)
        rgba = np.concatenate([rgb, np.full((h, w, 1), 255, np.uint8)], axis=2)
    return np.ascontiguousarray(rgba[..., [2, 1, 0, 3]])


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


def _blend(frame: np.ndarray, x: int, y: int, patch: np.ndarray) -> None:
    """Draw a BGRA `patch` over `frame` (in place) with its alpha, clipped to the frame."""
    h, w = frame.shape[:2]
    ph, pw = patch.shape[:2]
    x0, y0, x1, y1 = max(0, x), max(0, y), min(w, x + pw), min(h, y + ph)
    if x1 <= x0 or y1 <= y0:
        return
    src = patch[y0 - y:y1 - y, x0 - x:x1 - x].astype(np.float32)
    dst = frame[y0:y1, x0:x1]
    a = src[..., 3:4] / 255.0
    dst[..., :3] = (src[..., :3] * a + dst[..., :3] * (1 - a)).astype(np.uint8)
    dst[..., 3] = np.maximum(dst[..., 3], src[..., 3]).astype(np.uint8)


def draw_frame(test: dict, base: np.ndarray, burn: Burn | None, now: float, frame: int, channel: str = "") -> np.ndarray:
    """One frame, BGRA, flat and writable (what the sender takes): the base picture plus the moving element and the
    burned-in text."""
    out = base.copy()
    h, w = out.shape[:2]
    if test["pattern"] == "motion":
        out[:] = (24, 24, 24, 255)
        bw = max(8, w // 16)
        x = int((now / 2.0 % 1.0) * (w - bw))
        out[:, x:x + bw] = (255, 255, 255, 255)                    # a bar sweeping across every two seconds
        out[h // 3:2 * h // 3, :] = (40, 40, 40, 255)
        out[h // 3:2 * h // 3, x:x + bw] = (0, 220, 255, 255)
        step = max(1, w // 40)
        for i in range(0, w, step * 2):                            # a fixed ruler, to see the bar's position against
            out[h - h // 12:, i:i + step] = (90, 90, 90, 255)
    if burn is not None:
        x, y, patch = burn.patch(now, frame, test["fps"], channel)
        _blend(out, x, y, patch)
        if test["pattern"] == "motion":
            _blend(out, x, y - int(h * 0.1), _frame_label(frame, h))
    return out.reshape(-1)


def _frame_label(frame: int, h: int) -> np.ndarray:
    from PIL import Image, ImageDraw

    f = _font(max(12, h // 22))
    text = f"frame {frame}"
    im = Image.new("RGBA", (int(f.getlength(text)) + f.size, int(f.size * 1.6)), (0, 0, 0, 200))
    ImageDraw.Draw(im).text((f.size // 2, f.size // 5), text, fill=(255, 255, 255, 255), font=f)
    return np.ascontiguousarray(np.asarray(im)[..., [2, 1, 0, 3]])


# ------------------------------------------------------------------------------------------------------ sound
class Tone:
    """The test audio, one video frame's worth at a time: 48 kHz, two channels, float32 as NDI wants it."""

    def __init__(self, mode: str, level: float, fps: int) -> None:
        self.mode, self.fps = mode, fps
        self.gain = 10 ** (level / 20.0)
        self.n = 0                      # samples sent so far: keeps the tone's phase
        self.frames = 0

    def channel(self) -> str:
        """Which side is sounding now, for the on-screen label ('' unless the mode names one)."""
        if self.mode != "id":
            return ""
        return "LEFT" if int(self.n / RATE) % 2 == 0 else "RIGHT"

    def next(self) -> np.ndarray:
        """The audio for the next video frame. Frame lengths follow the exact rate (1600, 1600, ... or 1601, 1602 at
        29.97), so sound and picture stay together however long it runs."""
        count = math.floor((self.frames + 1) * RATE / self.fps) - math.floor(self.frames * RATE / self.fps)
        t = (self.n + np.arange(count)) / RATE
        out = np.zeros((2, count), np.float32)
        if self.mode in ("tone", "id"):
            wave = (np.sin(2 * math.pi * TONE_HZ * t) * self.gain).astype(np.float32)
            if self.mode == "tone":
                out[0] = out[1] = wave
            else:
                left = np.floor(t) % 2 == 0
                out[0] = np.where(left, wave, 0)
                out[1] = np.where(left, 0, wave)
        self.n += count
        self.frames += 1
        return out


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
