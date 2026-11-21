"""Audio Mixers (#150, #486): mirror and control the sound desks, Allen & Heath Avantis, dLive, GLD, Qu and SQ,
Behringer X32 / Midas M32 and WING, and Yamaha CL, QL, TF and DM, several at once.

Each desk's channel names, colours, mutes, faders and scene are mirrored on one model (models.py, mirror.py). Its inputs
and mixes are core audio channels (so Planning Center's board and the Network page see them), a mix's sends are core
routes, a watchdog notifies one chosen person when the desk drifts from a saved baseline, and the next service's mic
names from Planning Center go to the desk after a preview and a confirm. Moving a desk always needs
`audio_mixers.control`; nothing moves a desk on its own. Every driver is built from the published protocols and is
untested until someone runs it against the real desk.
"""
from __future__ import annotations

import re
from typing import Any

from .. import AdminPage, Health, Manifest, Module, Traffic
from ...users import Permission

MUTE_STYLE = {"bg": "#22252b", "fg": "#ffffff", "pressed": "#dc2626", "shape": "rounded", "icon": "mic"}
SCENE_STYLE = {"bg": "#1d2a3a", "fg": "#ffffff", "pressed": "#2f81f7", "shape": "rounded", "icon": "play"}
_RANGE = re.compile(r"(in|mix|main|dca)(\d{1,3})(?:-(\d{1,3}))?")
MAX_FADERS = 16
MAX_ROWS = 12


def strip_list(spec: Any, model) -> list[str]:
    """"main1, in1-8" -> ["main1", "in1", ..., "in8"], only strips the desk has, at most MAX_FADERS."""
    from .models import has_strip

    out: list[str] = []
    for part in str(spec or "").replace(";", ",").split(","):
        m = _RANGE.fullmatch(part.strip().lower().replace(" ", ""))
        if not m:
            continue
        kind, a = m.group(1), int(m.group(2))
        b = int(m.group(3)) if m.group(3) else a
        for n in range(a, min(b, a + MAX_FADERS) + 1):
            key = f"{kind}{n}"
            if has_strip(model, key) and key not in out:
                out.append(key)
    return out[:MAX_FADERS]


class AudioMixers(Module):
    manifest = Manifest(
        id="audio_mixers",
        name="Audio Mixers",
        summary="Mirrors and controls your sound desks (Allen & Heath, Behringer, Midas, Yamaha): channel names, mutes, "
                "faders and scenes on screens and boards, a watchdog for unexpected changes, and mic names from "
                "Planning Center.",
        settings_key="audio_mixers",
        section="Sound",
        guide="https://github.com/justin-small/relay#audio-mixers",
        version="1.0.0",
        uses=("planning_center", "crew"),
        traffic=(Traffic("audio_mixers", "Audio mixers (MIDI, OSC, RCP)"),),
    )
    defaults = {"desks": [], "watchdog": {"on": False, "user_id": 0, "names": True, "scene": True, "mutes": []},
                "baselines": {}, "patches": {}, "names": {"style": "first"}}
    network_map_adds = "Adds the sound desks it controls."
    admin_pages = (AdminPage(
        id="audio_mixers", title="Audio Mixers", template="audio_mixers.html",
        icon='<path d="M4 3v10M8 3v10M12 3v10"/><rect x="2.5" y="5" width="3" height="2" rx=".5"/>'
             '<rect x="6.5" y="9" width="3" height="2" rx=".5"/><rect x="10.5" y="6" width="3" height="2" rx=".5"/>'),)
    admin_scripts = ("audio_mixers.js",)
    admin_styles = ("audio_mixers.css",)
    route_prefixes = ("/api/admin/audio_mixers",)
    permissions = (
        Permission("audio_mixers.view", "See the sound desks' channels, mutes, faders and scenes", "Audio Mixers",
                   "viewer"),
        Permission("audio_mixers.control", "Mute, move faders, rename channels, recall scenes and send mic names to "
                   "the sound desks", "Audio Mixers", "operator"),
        Permission("audio_mixers.manage", "Set up the sound desks, the watchdog and its baseline", "Audio Mixers",
                   "admin"),
    )

    def clean(self, raw: Any) -> dict:
        from .service import clean_settings

        return clean_settings(raw)

    def configured(self, settings: dict) -> bool:
        return False         # new: nobody had it before, so it starts off

    def routers(self) -> list:
        from .routes import router

        return [router]

    def attach(self) -> None:
        from ... import modules
        from .service import service

        modules.provide("gear.audio_mixers", {"list": service.gear_list, "can_rename": service.can_rename,
                                              "rename": service.rename})
        modules.provide("routes.audio_mixers", service.routes_api())
        modules.provide("patch.audio_mixers", service.patch_list)

    def detach(self) -> None:
        from ... import modules

        modules.withdraw("gear.audio_mixers")
        modules.withdraw("routes.audio_mixers")
        modules.withdraw("patch.audio_mixers")

    async def start(self) -> None:
        from .service import service

        await service.start()

    async def stop(self) -> None:
        from .service import service

        await service.stop()

    def network_changed(self, resolved: dict) -> None:
        import asyncio

        from .service import service

        async def again() -> None:
            await service.stop()
            await service.start()

        asyncio.ensure_future(again())

    def health(self) -> Health:
        from .service import service

        level, detail = service.health()
        return Health(level, detail)

    async def test(self) -> dict:
        from .service import service

        if not service.desks:
            return {"ok": False, "detail": "Add a desk first."}
        parts = []
        for d in service.desks.values():
            if d.driver.connected():
                parts.append(f"{d.cfg['name']}: connected.")
            else:
                parts.append(f"{d.cfg['name']}: {d.driver.error or 'not connected yet.'}")
        return {"ok": all(d.driver.connected() for d in service.desks.values()), "detail": " ".join(parts)}

    def status(self) -> dict:
        from .service import service

        if not service.desks:
            return {}
        return {"audio_mixers": {
            "desks": [{"name": d.cfg["name"], "connected": d.driver.connected(), "scene": d.mirror.scene,
                       "scene_name": d.mirror.scene_name()} for d in service.desks.values()],
            "mics": service.mic_lines()[:24]}}

    def pills(self) -> list[dict]:
        from .service import service

        n = sum(len(service.differences(d)) for d in service.desks.values())
        return [{"id": "audio_mixers", "text": f"Desk changed ({n})", "href": "#audio_mixers"}] if n else []

    # -- boards --------------------------------------------------------------------------------------------------------
    def board_widgets(self) -> list[dict]:
        from .service import settings

        desks = [[d["id"], d["name"]] for d in settings()["desks"]]
        first = desks[0][0] if desks else ""
        desk = {"key": "desk", "label": "Desk", "type": "select", "options": desks, "default": first}
        ctl = "audio_mixers.control"
        return [
            {"type": "audio_mixers.faders", "name": "Fader bank", "group": "Audio Mixers", "kind": "fader", "w": 6,
             "h": 4, "style": {"label": "Faders"}, "press_permission": ctl,
             "fields": [desk, {"key": "strips", "label": "Strips (like main1, mix1-4, in1-8)", "type": "text",
                               "default": "main1, in1-8"}]},
            {"type": "audio_mixers.mute", "name": "Mute button", "group": "Audio Mixers", "kind": "button", "w": 2,
             "h": 1, "style": {**MUTE_STYLE, "label": "Mute"}, "press_permission": ctl,
             "fields": [desk, {"key": "strip", "label": "Strip (like in3, mix1, main1)", "type": "text",
                               "default": "in1"},
                        {"key": "_", "type": "note", "label": "Lit while muted. A press mutes or unmutes it."}]},
            {"type": "audio_mixers.scene", "name": "Recall scene", "group": "Audio Mixers", "kind": "button", "w": 2,
             "h": 1, "style": {**SCENE_STYLE, "label": "Scene"}, "press_permission": ctl,
             "fields": [desk, {"key": "scene", "label": "Scene number", "type": "text", "default": "1"},
                        {"key": "_", "type": "note", "label": "Lit while that scene is the desk's current one."}]},
            {"type": "audio_mixers.wall", "name": "Channel wall", "group": "Audio Mixers", "kind": "list", "w": 4,
             "h": 4, "style": {"label": "Channels"},
             "fields": [desk, {"key": "strips", "label": "Strips (like in1-12)", "type": "text", "default": "in1-12"}]},
            {"type": "audio_mixers.mics", "name": "Mic states", "group": "Audio Mixers", "kind": "list", "w": 3, "h": 4,
             "style": {"label": "Mics"}, "fields": []},
            {"type": "audio_mixers.meters", "name": "Desk meters", "group": "Audio Mixers", "kind": "list", "w": 4,
             "h": 4, "style": {"label": "Meters"},
             "fields": [desk, {"key": "strips", "label": "Strips (like in1-8; X32 and M32 only)", "type": "text",
                               "default": "in1-8"}]},
        ]

    async def board_data(self, items: list[dict]) -> dict[str, dict]:
        from .models import db_text, db_to_pos
        from .service import service

        out: dict[str, dict] = {}
        for it in items:
            t, p = it.get("type"), it.get("props") or {}
            d = service.desk(p.get("desk")) or (next(iter(service.desks.values()), None) if t != "audio_mixers.mics"
                                                 else None)
            if t == "audio_mixers.mics":
                lines = service.mic_lines()
                out[it["id"]] = {"rows": [{"text": x} for x in lines[:MAX_ROWS]] or [{"text": "No named mics"}]}
                continue
            if d is None:
                out[it["id"]] = {"lines": ["No desk set up"], "rows": [{"text": "No desk set up"}], "strips": []}
                continue
            m = d.mirror
            if t == "audio_mixers.mute":
                s = m.strips.get(str(p.get("strip") or ""))
                out[it["id"]] = {"lit": bool(s and s.muted)}
            elif t == "audio_mixers.scene":
                out[it["id"]] = {"lit": str(m.scene) == str(p.get("scene") or "").strip()}
            elif t == "audio_mixers.faders":
                keys = strip_list(p.get("strips"), m.model)
                out[it["id"]] = {"strips": [{"id": k, "label": m.strips[k].shown()[:12],
                                             "pos": db_to_pos(m.strips[k].db), "db": db_text(m.strips[k].db),
                                             "muted": m.strips[k].muted} for k in keys] if d.driver.connected() else []}
            elif t == "audio_mixers.wall":
                if not d.driver.connected():
                    out[it["id"]] = {"rows": [{"text": f"{d.cfg['name']}: not connected"}]}
                    continue
                rows = []
                for k in strip_list(p.get("strips"), m.model)[:MAX_ROWS]:
                    s = m.strips[k]
                    state = "?" if s.muted is None else ("MUTED" if s.muted else "")
                    rows.append({"text": f"{s.shown()[:12]}  {db_text(s.db)}  {state}".rstrip(), "current": s.muted is False})
                out[it["id"]] = {"rows": rows}
            elif t == "audio_mixers.meters":
                rows = []
                for k in strip_list(p.get("strips"), m.model)[:MAX_ROWS]:
                    s = m.strips[k]
                    v = s.meter
                    bar = "" if v is None else "▮" * max(0, min(12, round((v + 60) / 5)))
                    rows.append({"text": f"{s.shown()[:10]:<10} {bar or '·'} {'' if v is None else f'{v:.0f}'}"})
                out[it["id"]] = {"rows": rows or [{"text": "This desk sends no meters"}]}
        return out

    async def board_press(self, item: dict, request) -> dict:
        from ... import audit
        from .models import pos_to_db
        from .service import service

        t, p = item.get("type"), item.get("props") or {}
        d = service.desk(p.get("desk")) or next(iter(service.desks.values()), None)
        if d is None:
            return {"ok": False, "error": "No desk is set up."}
        if t == "audio_mixers.mute":
            strip = str(p.get("strip") or "")
            s = d.mirror.strips.get(strip)
            if s is None:
                return {"ok": False, "error": "Set which channel this button mutes."}
            said = service.control(d.id, "mute", strip, not s.muted)
            what = f"{'muted' if not s.muted else 'unmuted'} {s.shown()}"
        elif t == "audio_mixers.scene":
            raw = str(p.get("scene") or "").strip()
            if not raw.isdigit():
                return {"ok": False, "error": "Set the scene number for this button."}
            said = service.control(d.id, "scene", None, int(raw))
            what = f"recalled scene {int(raw)}"
        elif t == "audio_mixers.faders":
            try:
                body = await request.json()
            except ValueError:
                body = None
            if not isinstance(body, dict) or body.get("strip") not in strip_list(p.get("strips"), d.mirror.model):
                return {"ok": False, "error": "That fader is not on this bank."}
            strip = body["strip"]
            if isinstance(body.get("mute"), bool):
                said = service.control(d.id, "mute", strip, body["mute"])
                what = f"{'muted' if body['mute'] else 'unmuted'} {d.mirror.strips[strip].shown()}"
            elif isinstance(body.get("pos"), (int, float)) and not isinstance(body.get("pos"), bool):
                db = pos_to_db(body["pos"])
                said = service.control(d.id, "level", strip, db)
                what = f"set {d.mirror.strips[strip].shown()} to {db:.1f} dB"
            else:
                return {"ok": False, "error": "Move the fader or press its mute."}
        else:
            return {"ok": False, "error": "That button does nothing."}
        if said:
            return {"ok": False, "error": said}
        audit.note(request, f"audio_mixers.{t.split('.', 1)[1]}", f"{d.cfg['name']}: {what} (from a board)")
        return {"ok": True}


MODULE = AudioMixers()
