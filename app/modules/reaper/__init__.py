"""REAPER (#413): start and stop recording, markers, any REAPER action, track arm and mute, and alarms that guard the
recording, over REAPER's OSC control surface.

Auto-record follows the service (the stream on air, a schedule window, or both), a marker is dropped for each new
ProPresenter presentation and, if wanted, each stage message, and ProPresenter slide tags (`[relay:reaper=record]`)
and board buttons drive it through core's `control_recording` hook. One UDP socket per REAPER computer.
"""
from __future__ import annotations

from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission

REC_STYLE = {"bg": "#3a1214", "fg": "#ffffff", "pressed": "#d1242f", "shape": "rounded", "icon": ""}
STOP_STYLE = {"bg": "#22252b", "fg": "#ffffff", "pressed": "#5b6070", "shape": "rounded", "icon": ""}
MARK_STYLE = {"bg": "#1d2a3a", "fg": "#ffffff", "pressed": "#2f81f7", "shape": "rounded", "icon": ""}


def _addr(host: Any) -> dict:
    import ipaddress

    h = host.strip() if isinstance(host, str) else ""
    try:
        return {"ip": str(ipaddress.ip_address(h))}
    except ValueError:
        return {"host": h} if h else {}


def _bar(vu: float) -> str:
    n = max(0, min(10, round(vu * 10)))
    return "▮" * n + "▯" * (10 - n)


class Reaper(Module):
    manifest = Manifest(
        id="reaper",
        name="REAPER",
        summary="Starts and stops REAPER recording with the service, drops a marker for each song, and warns when a "
                "track is not armed or goes silent.",
        settings_key="reaper",
        section="Show",
        guide="https://github.com/justin-small/relay#reaper",
        version="1.0.0",
        traffic=(Traffic("reaper", "REAPER (OSC)"),),
    )
    defaults = {"machines": []}
    network_map_adds = "Marks the computers it records on as running REAPER."
    admin_pages = (AdminPage(
        id="reaper", title="REAPER", template="reaper.html",
        icon='<circle cx="8" cy="8" r="5.5"/><circle cx="8" cy="8" r="2.5" fill="currentColor"/>'),)
    admin_scripts = ("reaper.js",)
    admin_styles = ("reaper.css",)
    route_prefixes = ("/api/admin/reaper",)
    permissions = (
        Permission("reaper.view", "See whether REAPER is recording, its tracks and alarms", "REAPER", "viewer"),
        Permission("reaper.control", "Start and stop REAPER recording, add markers, run actions and arm or mute "
                   "tracks", "REAPER", "operator"),
        Permission("reaper.manage", "Set up REAPER computers, auto-record and alarms", "REAPER", "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False  # New: nobody had it before, so it starts off.

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from .service import attach_hooks

        attach_hooks()      # core's recording hook (slide tags, boards), presentation markers, stage-message markers

    def detach(self) -> None:
        from .service import detach_hooks

        detach_hooks()

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def network_changed(self, resolved: dict) -> None:
        import asyncio

        from .service import service

        asyncio.ensure_future(service.restart())

    def network_map(self) -> list[dict]:
        from ... import config

        machines = (config.get().get("reaper") or {}).get("machines") or []
        return [{**_addr(m.get("host")), "name": m.get("name") or "", "kind": "computer", "tags": ["REAPER"]}
                for m in machines if isinstance(m, dict) and m.get("host")]

    def health(self) -> Health:
        from .service import service

        level, detail = service.health()
        return Health(level, detail)

    async def test(self) -> dict:
        from .routes import test_machine
        from .service import service

        if not service.machines:
            return {"ok": False, "detail": "Add a REAPER computer first."}
        results = [await test_machine(m) for m in service.machines.values()]
        return {"ok": all(r["ok"] for r in results), "detail": " ".join(r["detail"] for r in results)}

    def pills(self) -> list[dict]:
        from .service import service

        if not service.recording():
            return []
        return [{"id": "reaper", "text": "REC", "href": "#reaper"}]

    # -- boards ----------------------------------------------------------------------------------------------------
    def board_widgets(self) -> list[dict]:
        ctl = "reaper.control"
        return [
            {"type": "reaper.record", "name": "Record", "group": "REAPER", "kind": "button", "w": 2, "h": 1,
             "style": {**REC_STYLE, "label": "Record"}, "press_permission": ctl,
             "fields": [{"key": "_", "type": "note", "label": "Starts recording on every REAPER computer that "
                         "answers; lit while recording. A computer already recording is left alone."}]},
            {"type": "reaper.stop", "name": "Stop recording", "group": "REAPER", "kind": "button", "w": 2, "h": 1,
             "style": {**STOP_STYLE, "label": "Stop"}, "press_permission": ctl, "fields": []},
            {"type": "reaper.marker", "name": "Marker", "group": "REAPER", "kind": "button", "w": 2, "h": 1,
             "style": {**MARK_STYLE, "label": "Marker"}, "press_permission": ctl,
             "fields": [{"key": "name", "label": "Marker name (blank: unnamed)", "type": "text"}],
             "presets": [{"name": "Sermon", "props": {"name": "Sermon"}, "style": {**MARK_STYLE, "label": "Sermon"}},
                         {"name": "Offering", "props": {"name": "Offering"},
                          "style": {**MARK_STYLE, "label": "Offering"}}]},
            {"type": "reaper.action", "name": "REAPER action", "group": "REAPER", "kind": "button", "w": 2, "h": 1,
             "style": {**STOP_STYLE, "label": "Action"}, "press_permission": ctl,
             "fields": [{"key": "action", "label": "Action number (REAPER: Actions list, Command ID)", "type": "text"}]},
            {"type": "reaper.status", "name": "Recording status", "group": "REAPER", "kind": "lines", "w": 3, "h": 2,
             "style": {"label": "REAPER"}, "fields": []},
            {"type": "reaper.meters", "name": "Armed track meters", "group": "REAPER", "kind": "lines", "w": 3, "h": 3,
             "style": {"label": "Tracks"}, "fields": []},
        ]

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        import time

        from .service import _clock, service

        out: dict[str, dict] = {}
        ms = list(service.machines.values())
        rec = [m for m in ms if m.link.state.recording]
        for it in items:
            t = it.get("type")
            if t == "reaper.record":
                out[it["id"]] = {"lit": bool(rec)}
            elif t == "reaper.status":
                lines = []
                for m in ms:
                    st = m.link.state
                    if not m.link.reached():
                        lines.append(f"{m.cfg['name']}: not answering")
                    elif st.recording:
                        lines.append(f"● REC {_clock(time.time() - (st.record_since or time.time()))}"
                                     + (f"  {m.cfg['name']}" if len(ms) > 1 else ""))
                    else:
                        lines.append("Stopped" + (f"  {m.cfg['name']}" if len(ms) > 1 else ""))
                    real = [x for x in m.tracks()]
                    lines.append(f"Armed {sum(1 for x in real if x['armed'])} of {len(real)}")
                out[it["id"]] = {"lines": lines or ["No REAPER set up"]}
            elif t == "reaper.meters":
                lines = []
                for m in ms:
                    for x in m.tracks():
                        if x["armed"]:
                            lines.append(f"{x['name'][:14]:<14} {_bar(x['vu'])}")
                out[it["id"]] = {"lines": lines or ["No armed tracks"]}
        return out

    async def board_press(self, item: dict, request) -> dict:
        from ... import audit
        from .service import clean_marker, service

        t = item.get("type")
        props = item.get("props") or {}
        if t == "reaper.record":
            done, what = service.record(), "started recording"
        elif t == "reaper.stop":
            done, what = service.stop_recording(), "stopped recording"
        elif t == "reaper.marker":
            name = clean_marker(props.get("name"))
            done, what = await service.marker(name), (f"marker {name}" if name else "marker")
        elif t == "reaper.action":
            a = str(props.get("action") or "").strip()
            if not a.isdigit() or not 1 <= int(a) <= 99_999_999:
                return {"ok": False, "error": "Set REAPER's action number for this button."}
            done, what = service.action(int(a)), f"ran action {int(a)}"
        else:
            return {"ok": False, "error": "That button does nothing."}
        if not done:
            return {"ok": False, "error": "No REAPER computer answered."}
        audit.note(request, f"reaper.{t.split('.', 1)[1]}", f"REAPER ({', '.join(done)}): {what} (from a board)")
        return {"ok": True}


MODULE = Reaper()
