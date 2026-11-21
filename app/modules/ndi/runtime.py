"""The NDI runtime (#294): the one place that touches the NDI SDK.

Everything that talks to NDI runs in Rust (`relay_ndi`, native/relay-ndi, #606): the finder, one receiver thread per
source, senders and routers, and the picture scopes and audio meter that work on what they carry. The Rust engine opens
the NDI runtime itself with dlopen, so a build needs neither the SDK nor its headers, and Relay ships none of NDI's
libraries (NDI's licence allows that only under an EULA carrying NDI's terms). The operator installs the NDI runtime
from ndi.video (set `NDI_RUNTIME_DIR_V6` for a non-standard place). With it missing the module says so on its page and
stays off; Relay itself starts.

A receiver is a Rust thread that queues what arrives, so Python never sits in the capture call holding the GIL: a
30 fps 1080p source arrives at 30 fps where the old Python binding delivered 13 to 20 (#566).

A backend has `connect(name, address, low=False) -> connection` (`low` asks the sender for its low-bandwidth stream),
`open_sender(name, width, height, fps, audio=False) -> sender` and `open_router(name) -> router` (`change(source)`,
`clear()`, `close()`: the NDI routing API, #298). A connection has `receive(timeout_ms) -> frame dict | None` and
`close()`; a frame is {"width", "height", "fps", "fourcc", "data", "age_ms"} (`age_ms` is how long it waited in the
queue); audio is {"audio": float32 (channels, samples), "rate", "audio_db"} and a sender's connection metadata is
{"meta": xml text}; `perf()` is the SDK's dropped-frame counts. A sender has `send(frame)` and `close()`. Tests replace
`load`.
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


def _search_dirs() -> list[str]:
    """Where the NDI runtime may be besides NDI_RUNTIME_DIR_V6 and its usual places: the copy the optional `cyndilib`
    package carries, when that happens to be installed."""
    import importlib.util
    from pathlib import Path

    spec = importlib.util.find_spec("cyndilib")
    if spec is None or not spec.origin:
        return []
    return [str(Path(spec.origin).parent / "wrapper" / "bin")]


def load() -> Any:
    """The backend, or None with `reason()` saying why not. Tried once."""
    global _backend, _reason, _tried
    if _tried:
        return _backend
    _tried = True
    try:
        import relay_ndi
    except ImportError:
        _reason = "Relay's NDI engine (relay_ndi) is not built. Run: python tools/build_bus_wheel.py"
        return None
    try:
        relay_ndi.set_search_dirs(_search_dirs())
        log.info("NDI runtime: %s", relay_ndi.library())
    except OSError as exc:
        log.info("NDI runtime load failed: %s", exc)
        _reason = "The NDI runtime is not installed. Install it from ndi.video, then restart Relay."
        return None
    _backend = RustBackend()
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


class RustBackend:
    def connect(self, name: str, address: str = "", low: bool = False) -> Any:
        import relay_ndi

        return relay_ndi.Connection(name, low)

    def open_sender(self, name: str, width: int = 1280, height: int = 720, fps: float = 30.0, audio: bool = False) -> "_Sender":
        return _Sender(name, width, height, fps)

    def open_router(self, name: str) -> Any:
        import relay_ndi

        return relay_ndi.Router(name)

    def find(self, seconds: float = 3.0) -> list[dict]:
        """Every source the SDK sees (through a discovery server, extra addresses, groups and mDNS): [{"name",
        "address"}]."""
        import relay_ndi

        return relay_ndi.find(seconds)


class _Sender:
    """An NDI source Relay publishes. `send` takes {"data": BGRA bytes of width x height x 4} and optionally {"audio":
    float32 (channels, samples)}; the size is fixed when the sender opens."""

    def __init__(self, name: str, width: int, height: int, fps: float) -> None:
        import relay_ndi

        self._sender = relay_ndi.Sender(name, width, height, fps)

    def send(self, frame: dict) -> None:
        self._sender.send(frame["data"], frame.get("audio"))

    def close(self) -> None:
        self._sender.close()
