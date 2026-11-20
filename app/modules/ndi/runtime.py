"""The NDI runtime (#294): the one place that touches the NDI SDK.

**Spike result (2026-11-17).** Binding: `cyndilib` (MIT, Cython, wheels for macOS, Windows and Linux, a Finder, Receiver
and Sender). It is an optional install, not in requirements.txt: NDI's SDK licence lets an app ship the NDI libraries
only under an EULA that carries NDI's terms, so Relay does not bundle them. The operator installs the NDI runtime from
ndi.video (set `NDI_RUNTIME_DIR_V6` for a non-standard place) and `pip install cyndilib`. With either missing the module
says so on its page and stays off; Relay itself starts. NOT yet proved here: send and receive in the Tauri desktop app on
macOS, Windows and the headless Linux service. The calls below follow cyndilib's documented API and are covered only
against a fake backend (tests/test_ndi.py), so check them against a real source before relying on them.

A backend has `connect(name, address, low=False) -> connection` (`low` asks the sender for its low-bandwidth stream), `open_sender(name, width, height, fps) -> sender` and
`open_router(name) -> router` (`change(source)`, `clear()`, `close()`: the NDI routing API through ctypes, #298). A connection has
`receive(timeout_ms) -> frame dict | None` and `close()`; a frame is {"width", "height", "fps", "fourcc", "data"}; audio is {"audio": float32 (channels, samples), "rate",
"audio_db"} and a sender's connection metadata is {"meta": xml text}; `perf()` is the SDK's dropped-frame counts. A
sender has `send(frame)` and `close()`. Tests replace `load`.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("relay.ndi")

_backend: Any = None
_reason = ""
_tried = False
_applied: dict | None = None     # the network settings the SDK was loaded with


def sdk_config(net: dict) -> dict:
    """The NDI SDK's own config (ndi-config.v1.json) for Relay's network settings: discovery servers, mDNS on or off,
    and the groups to receive. Written to Relay's state folder and pointed at with NDI_CONFIG_DIR, so Relay never
    touches the machine's own NDI config. Key names follow NDI's config documentation; unverified on a real install."""
    return {"ndi": {"networks": {"ips": ",".join(net.get("extra_ips", [])), "discovery": ",".join(net.get("servers", []))},
                    "mdns": {"enable": bool(net.get("mdns", True))},
                    "groups": {"recv": ",".join(net.get("groups", []))}}}


def configure(net: dict, folder: Any) -> None:
    """Write the SDK config and point the SDK at it. Must run before the first `load()`: the SDK reads it once, when
    it starts, so a later change shows as `restart_needed`."""
    import json
    import os
    from pathlib import Path

    global _applied
    if _tried:
        return
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "ndi-config.v1.json").write_text(json.dumps(sdk_config(net), indent=2), encoding="utf-8")
    os.environ["NDI_CONFIG_DIR"] = str(folder)
    _applied = {k: list(net.get(k, [])) if isinstance(net.get(k), list) else net.get(k) for k in ("servers", "mdns", "groups", "extra_ips")}


def restart_needed(net: dict) -> bool:
    """True when the settings differ from what the loaded SDK started with."""
    if _applied is None:
        return False
    now = {k: list(net.get(k, [])) if isinstance(net.get(k), list) else net.get(k) for k in _applied}
    return now != _applied


def load() -> Any:
    """The backend, or None with `reason()` saying why not. Tried once."""
    global _backend, _reason, _tried
    if _tried:
        return _backend
    _tried = True
    try:
        import cyndilib  # noqa: F401
    except ImportError:
        _reason = "The NDI binding (cyndilib) is not installed. Run: pip install cyndilib"
        return None
    except Exception as exc:  # a missing runtime library surfaces as an OSError at import
        log.info("NDI runtime load failed: %s", exc)
        _reason = "The NDI runtime is not installed. Install it from ndi.video, then restart Relay."
        return None
    _backend = CyndiBackend()
    return _backend


def reason() -> str:
    return _reason


def install(backend: Any) -> None:
    """For tests: use this backend instead of loading the SDK."""
    global _backend, _reason, _tried
    _backend, _reason, _tried = backend, ("" if backend else "The NDI runtime is not installed."), True


def reset() -> None:
    global _backend, _reason, _tried, _applied
    _backend, _reason, _tried, _applied = None, "", False, None


class CyndiBackend:
    def connect(self, name: str, address: str = "", low: bool = False) -> "_Connection":
        return _Connection(name, low)

    def open_sender(self, name: str, width: int = 1280, height: int = 720, fps: float = 30.0) -> "_Sender":
        return _Sender(name, width, height, fps)

    def open_router(self, name: str) -> "_Router":
        return _Router(name)

    def find(self, seconds: float = 3.0) -> list[dict]:
        """Every source the SDK sees (through a discovery server, extra addresses, groups and mDNS): [{"name",
        "address"}]."""
        from cyndilib.finder import Finder

        finder = Finder()
        finder.open()
        try:
            finder.wait_for_sources(seconds)
            out = []
            for name in finder.get_source_names():
                src = finder.get_source(name)
                out.append({"name": str(name), "address": str(getattr(src, "host_name", "") or "")})
            return out
        finally:
            try:
                finder.close()
            except Exception:
                pass


class _Connection:
    def __init__(self, name: str, low: bool = False) -> None:
        from cyndilib.finder import Finder
        from cyndilib.receiver import ReceiveFrameType, Receiver
        from cyndilib.video_frame import VideoRecvFrame

        self._kind = ReceiveFrameType.recv_video
        self._audio = None
        self._audio_kind = None
        self._meta = None
        self._meta_kind = ReceiveFrameType.recv_metadata
        self._want = self._kind
        try:       # Audio is for the meters and analysis. Any trouble just means no audio.
            from cyndilib.audio_frame import AudioRecvFrame

            self._audio = AudioRecvFrame()
            self._audio_kind = ReceiveFrameType.recv_audio
            self._want = self._kind | self._audio_kind
        except Exception:
            self._audio = None
        self._finder = Finder()
        self._finder.open()
        self._finder.wait_for_sources(5.0)
        src = self._finder.get_source(name)
        if src is None:
            self._finder.close()
            raise OSError("That NDI source is not on the network.")
        self._frame = VideoRecvFrame()
        if low:
            from cyndilib.wrapper.ndi_recv import RecvBandwidth

            self._recv = Receiver(bandwidth=RecvBandwidth.lowest)
        else:
            self._recv = Receiver()
        self._recv.set_video_frame(self._frame)
        try:       # what the sender says about itself (product, capabilities); arrives once, when it connects
            from cyndilib.metadata_frame import MetadataRecvFrame

            self._meta = MetadataRecvFrame()
            self._recv.set_metadata_frame(self._meta)
            self._want = self._want | self._meta_kind
        except Exception:
            self._meta = None
        if self._audio is not None:
            try:
                self._recv.set_audio_frame(self._audio)
            except Exception:
                self._audio = None
        self._recv.set_source(src)

    def receive(self, timeout_ms: int) -> dict | None:
        got = self._recv.receive(self._want, timeout_ms)
        if self._audio is not None and got == self._audio_kind:
            return self._audio_level()
        if self._meta is not None and got == self._meta_kind:
            return self._metadata()
        if got != self._kind:
            return None
        f = self._frame
        w, h = f.get_resolution()
        rate = f.get_frame_rate()
        return {"width": int(w), "height": int(h), "fps": round(float(rate), 3) if rate else 0.0,
                "fourcc": str(f.get_fourcc()), "data": bytes(f)}

    def _audio_level(self) -> dict | None:
        """The audio just received: {"audio_db": peak dBFS, "audio": float32 (channels, samples), "rate"}. Never raises."""
        try:
            import math

            import numpy as np

            arr = np.array(self._audio.get_all_read_data()[0], dtype=np.float32)      # (channels, samples); a copy
            peak = float(np.max(np.abs(arr))) if arr.size else 0.0
            return {"audio_db": round(20 * math.log10(peak), 1) if peak > 1e-6 else -120.0, "audio": arr,
                    "rate": int(self._audio.sample_rate)}
        except Exception:
            self._audio = None      # a build whose audio frame reads differently: stop trying, keep the video
            return None

    def _metadata(self) -> dict | None:
        try:
            xml = self._meta.xml_doc
            return {"meta": xml if isinstance(xml, str) else str(xml)} if xml else None
        except Exception:
            self._meta = None
            return None

    def perf(self) -> dict:
        """The SDK's frame counts: {"video": {"frames_total", "frames_dropped"}, "audio": ..., "metadata": ...}."""
        try:
            return dict(self._recv.get_performance_data())
        except Exception:
            return {}

    def close(self) -> None:
        for obj in (self._recv, self._finder):
            try:
                obj.close() if hasattr(obj, "close") else None
            except Exception:
                pass


class _Sender:
    """An NDI source Relay publishes. `send` takes {"data": BGRA bytes of width x height x 4}; the size is fixed when
    the sender opens (cyndilib refuses to open one with no video frame attached)."""

    def __init__(self, name: str, width: int = 1280, height: int = 720, fps: float = 30.0) -> None:
        from cyndilib.sender import Sender
        from cyndilib.video_frame import VideoSendFrame
        from cyndilib.wrapper.ndi_structs import FourCC

        self._sender = Sender(name)
        frame = VideoSendFrame()
        frame.set_resolution(width, height)
        frame.set_frame_rate(fps)
        frame.set_fourcc(FourCC.BGRA)
        self._sender.set_video_frame(frame)
        self._sender.open()

    def send(self, frame: dict) -> None:
        data = frame["data"]
        # cyndilib wants a writable buffer (bytes is refused with "Object is not writable"): found sending real frames.
        self._sender.write_video(bytearray(data) if isinstance(data, bytes) else data)

    def close(self) -> None:
        try:
            self._sender.close()
        except Exception:
            pass


def _libndi() -> Any:
    """The NDI library as a ctypes handle: the NDI runtime's if NDI_RUNTIME_DIR_V6 names one, else the copy cyndilib
    ships. cyndilib wraps no routing calls, so the router talks to the library directly."""
    import ctypes
    import os
    import sys
    from pathlib import Path

    names = {"darwin": "libndi.dylib", "win32": "Processing.NDI.Lib.x64.dll"}.get(sys.platform, "libndi.so.6")
    spots = []
    if os.environ.get("NDI_RUNTIME_DIR_V6"):
        spots.append(Path(os.environ["NDI_RUNTIME_DIR_V6"]) / names)
    try:
        import cyndilib

        spots.append(Path(cyndilib.__file__).parent / "wrapper" / "bin" / names)
    except ImportError:
        pass
    last: Exception | None = None
    for spot in spots:
        try:
            return ctypes.CDLL(str(spot))
        except OSError as exc:
            last = exc
    if sys.platform not in ("darwin", "win32"):
        return ctypes.CDLL(names)
    raise OSError(str(last) if last else "The NDI library was not found.")


class _Router:
    """A routed NDI output (#298): a source on the network that shows whatever it is pointed at, switched inside the
    NDI library with no decoding or re-encoding. Proved on macOS 2026-11-17: the router appeared as a source, `change`
    pointed it at a sender, and a receiver on the router got that sender's video."""

    def __init__(self, name: str) -> None:
        import ctypes

        class Create(ctypes.Structure):
            _fields_ = [("name", ctypes.c_char_p), ("groups", ctypes.c_char_p)]

        class Source(ctypes.Structure):
            _fields_ = [("name", ctypes.c_char_p), ("url", ctypes.c_char_p)]

        self._Source = Source
        self._lib = lib = _libndi()
        lib.NDIlib_initialize.restype = ctypes.c_bool
        if not lib.NDIlib_initialize():
            raise OSError("This computer cannot run NDI.")
        lib.NDIlib_routing_create.restype = ctypes.c_void_p
        lib.NDIlib_routing_create.argtypes = [ctypes.POINTER(Create)]
        lib.NDIlib_routing_change.restype = ctypes.c_bool
        lib.NDIlib_routing_change.argtypes = [ctypes.c_void_p, ctypes.POINTER(Source)]
        lib.NDIlib_routing_clear.restype = ctypes.c_bool
        lib.NDIlib_routing_clear.argtypes = [ctypes.c_void_p]
        lib.NDIlib_routing_destroy.argtypes = [ctypes.c_void_p]
        self._h = lib.NDIlib_routing_create(ctypes.byref(Create(name.encode(), None)))
        if not self._h:
            raise OSError("NDI would not create the output.")

    def change(self, source: str, address: str = "") -> bool:
        import ctypes

        if not self._h:
            return False
        src = self._Source(source.encode(), address.encode() if address else None)
        return bool(self._lib.NDIlib_routing_change(self._h, ctypes.byref(src)))

    def clear(self) -> bool:
        return bool(self._h and self._lib.NDIlib_routing_clear(self._h))

    def close(self) -> None:
        h, self._h = self._h, None
        if h:
            self._lib.NDIlib_routing_destroy(h)
