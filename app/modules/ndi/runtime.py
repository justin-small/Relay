"""The NDI runtime (#294): the one place that touches the NDI SDK.

**Spike result (2026-11-17).** Binding: `cyndilib` (MIT, Cython, wheels for macOS, Windows and Linux, a Finder, Receiver
and Sender). It is an optional install, not in requirements.txt: NDI's SDK licence lets an app ship the NDI libraries
only under an EULA that carries NDI's terms, so Relay does not bundle them. The operator installs the NDI runtime from
ndi.video (set `NDI_RUNTIME_DIR_V6` for a non-standard place) and `pip install cyndilib`. With either missing the module
says so on its page and stays off; Relay itself starts. NOT yet proved here: send and receive in the Tauri desktop app on
macOS, Windows and the headless Linux service. The calls below follow cyndilib's documented API and are covered only
against a fake backend (tests/test_ndi.py), so check them against a real source before relying on them.

A backend has `connect(name, address) -> connection` and `open_sender(name) -> sender`. A connection has
`receive(timeout_ms) -> frame dict | None` and `close()`; a frame is {"width", "height", "fps", "fourcc", "data"}. A
sender has `send(frame)` and `close()`. Tests replace `load`.
"""
from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("relay.ndi")

_backend: Any = None
_reason = ""
_tried = False


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
    global _backend, _reason, _tried
    _backend, _reason, _tried = None, "", False


class CyndiBackend:
    def connect(self, name: str, address: str = "") -> "_Connection":
        return _Connection(name)

    def open_sender(self, name: str) -> "_Sender":
        return _Sender(name)


class _Connection:
    def __init__(self, name: str) -> None:
        from cyndilib.finder import Finder
        from cyndilib.receiver import ReceiveFrameType, Receiver
        from cyndilib.video_frame import VideoRecvFrame

        self._kind = ReceiveFrameType.recv_video
        self._finder = Finder()
        self._finder.open()
        self._finder.wait_for_sources(5.0)
        src = self._finder.get_source(name)
        if src is None:
            self._finder.close()
            raise OSError("That NDI source is not on the network.")
        self._frame = VideoRecvFrame()
        self._recv = Receiver()
        self._recv.set_video_frame(self._frame)
        self._recv.set_source(src)

    def receive(self, timeout_ms: int) -> dict | None:
        got = self._recv.receive(self._kind, timeout_ms)
        if got != self._kind:
            return None
        f = self._frame
        w, h = f.get_resolution()
        rate = f.get_frame_rate()
        return {"width": int(w), "height": int(h), "fps": round(float(rate), 3) if rate else 0.0,
                "fourcc": str(f.get_fourcc()), "data": bytes(f)}

    def close(self) -> None:
        for obj in (self._recv, self._finder):
            try:
                obj.close() if hasattr(obj, "close") else None
            except Exception:
                pass


class _Sender:
    def __init__(self, name: str) -> None:
        from cyndilib.sender import Sender

        self._sender = Sender(name)
        self._sender.open()

    def send(self, frame: dict) -> None:
        self._sender.write_video(frame["data"])

    def close(self) -> None:
        try:
            self._sender.close()
        except Exception:
            pass
