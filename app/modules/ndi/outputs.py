"""NDI outputs (#299): Relay pages sent out as NDI sources.

An **output definition** is {"id", "name", "group", "url", "alpha"}: a page path on this server, drawn off screen at the
chosen size and frame rate and published as the NDI source "Relay - <name>". Screens (each language, plus the source
language) and overlays (the default and every saved one, with alpha) are built in; another module adds its own through
core's `ndi_output_providers` (signage, boards), so nothing here imports it. The operator switches each output on in
admin; `on` is saved, so outputs come back after a restart. Each running output has a thread that renders a frame,
sends it, and sleeps to its frame rate; the status carries the rate it achieved and the CPU time Relay spent on it.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from . import render

log = logging.getLogger("relay.ndi.outputs")

PREFIX = "Relay - "
SIZES = {"1280x720": (1280, 720), "1920x1080": (1920, 1080)}
MAX_ON = 8


def ndi_name(name: str) -> str:
    return PREFIX + " ".join(str(name).split())[:70]


def builtin_defs(cfg: dict) -> list[dict]:
    """Screens and overlays from the operator's configuration."""
    from ... import languages

    out = [{"id": "screen:source", "name": "Screen (source language)", "group": "Screens",
            "url": "/screen?stream=transcription", "alpha": False}]
    for t in languages.target_names(cfg.get("source_language")):
        label = languages.TARGETS[t].get("label", t.title())
        out.append({"id": "screen:" + t, "name": f"Screen ({label})", "group": "Screens",
                    "url": "/screen?lang=" + t, "alpha": False})
    out.append({"id": "overlay:default", "name": "Overlay", "group": "Overlays", "url": "/overlay", "alpha": True})
    for o in cfg.get("overlays") or []:
        if isinstance(o, dict) and o.get("id"):
            out.append({"id": "overlay:" + str(o["id"]), "name": "Overlay (" + str(o.get("name") or o["id"]) + ")",
                        "group": "Overlays", "url": "/overlay/" + str(o["id"]), "alpha": True})
    return out


class _Run:
    def __init__(self, d: dict, w: int, h: int, fps: int) -> None:
        self.d, self.w, self.h, self.fps = d, w, h, fps
        self.state, self.error = "starting", ""
        self.achieved, self.cpu = 0.0, 0.0
        self.stop = threading.Event()
        self.thread: threading.Thread | None = None


class Outputs:
    def __init__(self, open_output: Callable[..., Any], settings: Callable[[], dict], config_get: Callable[[], dict],
                 providers: Callable[[], dict], base: Callable[[], str], notify: Callable[[], None]) -> None:
        self._open_output, self._settings, self._config, self._providers = open_output, settings, config_get, providers
        self._base, self._notify = base, notify
        self.make = render.make              # tests replace this
        self._runs: dict[str, _Run] = {}
        self._lock = threading.Lock()

    # -------------------------------------------------------------------------------------------- definitions
    def defs(self) -> list[dict]:
        out = builtin_defs(self._config())
        seen = {d["id"] for d in out}
        for key, fn in list(self._providers().items()):
            try:
                for d in fn() or []:
                    if isinstance(d, dict) and d.get("id") and d.get("url") and d["id"] not in seen:
                        out.append({"id": str(d["id"]), "name": str(d.get("name") or d["id"]),
                                    "group": str(d.get("group") or key), "url": str(d["url"]),
                                    "alpha": bool(d.get("alpha"))})
                        seen.add(d["id"])
            except Exception:
                log.exception("NDI output provider %s failed", key)
        return out

    def status(self) -> list[dict]:
        s = self._settings()
        rows = []
        for d in self.defs():
            run = self._runs.get(d["id"])
            rows.append({**d, "ndi": ndi_name(d["name"]), "on": d["id"] in s["out_on"],
                         "state": run.state if run else "off", "error": run.error if run else "",
                         "fps": round(run.achieved, 1) if run else 0, "cpu": round(run.cpu, 1) if run else 0})
        return rows

    # -------------------------------------------------------------------------------------------- running
    def sync(self) -> None:
        """Start the outputs that are on and stop the rest."""
        s = self._settings()
        want = {d["id"]: d for d in self.defs() if d["id"] in s["out_on"]}
        w, h = SIZES.get(s["out_size"], SIZES["1280x720"])
        with self._lock:
            for oid in [k for k in self._runs if k not in want]:
                self._stop(self._runs.pop(oid))
            for oid, d in want.items():
                run = self._runs.get(oid)
                if run and (run.w, run.h, run.fps) == (w, h, s["out_fps"]) and run.d["url"] == d["url"] \
                        and run.state != "error":
                    continue
                if run:
                    self._stop(self._runs.pop(oid))
                run = _Run(d, w, h, s["out_fps"])
                self._runs[oid] = run
                run.thread = threading.Thread(target=self._loop, args=(run,), name="ndi-out-" + oid, daemon=True)
                run.thread.start()
        self._notify()

    def close(self) -> None:
        with self._lock:
            runs, self._runs = list(self._runs.values()), {}
        for r in runs:
            self._stop(r)

    @staticmethod
    def _stop(run: _Run) -> None:
        run.stop.set()
        if run.thread and run.thread is not threading.current_thread():
            run.thread.join(timeout=5)

    def _loop(self, run: _Run) -> None:
        renderer = out = None
        try:
            why = render.available() if self.make is render.make else ""
            if why:
                raise RuntimeError(why)
            renderer = self.make(self._base() + run.d["url"], run.w, run.h, run.d["alpha"])
            out = self._open_output(ndi_name(run.d["name"]), run.w, run.h, run.fps)
            if out is None:
                raise RuntimeError("NDI would not open this output (is the name already in use?).")
            run.state = "on"
            self._notify()
            period, frames, t0, cpu0, last = 1.0 / run.fps, 0, time.monotonic(), time.thread_time(), 0.0
            while not run.stop.is_set():
                start = time.monotonic()
                out.send({"width": run.w, "height": run.h, "fourcc": "BGRA", "data": renderer.frame()})
                frames += 1
                now = time.monotonic()
                if now - t0 >= 2.0:
                    run.achieved = frames / (now - t0)
                    run.cpu = 100 * (time.thread_time() - cpu0) / (now - t0)
                    frames, t0, cpu0 = 0, now, time.thread_time()
                    self._notify()
                run.stop.wait(max(0.0, period - (time.monotonic() - start)))
        except Exception as exc:
            log.warning("NDI output %s stopped: %s", run.d["id"], exc)
            run.state, run.error = "error", str(exc)[:200]
            self._notify()
        finally:
            for obj in (out, renderer):
                try:
                    obj.close() if obj is not None else None
                except Exception:
                    pass
            if run.state == "on":
                run.state = "off"
