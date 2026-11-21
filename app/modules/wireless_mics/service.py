"""Wireless mics (#153), slice 1: Shure receivers and manual mics and in-ear packs, each tied to a Planning Center position.

UNPROVEN ON HARDWARE: the Shure client was built from Shure's published command strings and a fake receiver, not a
real one (see shure.py). Manual gear has no network and shows no telemetry; it is only a name and a position.
Settings live under config "wireless_mics"; what a receiver says is kept in memory only.
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Any

from ... import config, directory, modules, text
from .shure import DEFAULT_PORT, Receiver, audio_percent, rf_percent

MAX_RECEIVERS = 16
MAX_MANUAL = 64
KINDS = ("mic", "iem")
_HOST_RE = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.\-]{0,251}[A-Za-z0-9])?")
_ID_RE = re.compile(r"[a-z0-9]{1,12}")
LOW_BATTERY = 20
CRITICAL_BATTERY = 10
WEAK_RF_DBM = -85
PERSON_CACHE_S = 5.0


class Invalid(ValueError):
    """A setting the operator typed is wrong; the message is a fixed sentence for the page."""


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _int(value: Any, lo: int, hi: int, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
        return default
    return value


def _clean_assign(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, v in raw.items():
        if isinstance(key, str) and isinstance(v, dict) and isinstance(v.get("position_ref"), str):
            if directory.parse_item(v["position_ref"]):
                out[key] = {"position_ref": v["position_ref"], "position_name": _text(v.get("position_name"), 80)}
    return out


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    receivers, seen = [], set()
    for r in raw.get("receivers") if isinstance(raw.get("receivers"), list) else []:
        if not isinstance(r, dict) or len(receivers) >= MAX_RECEIVERS:
            continue
        rid = r.get("id") if isinstance(r.get("id"), str) else ""
        host = r.get("host").strip() if isinstance(r.get("host"), str) else ""
        if not _ID_RE.fullmatch(rid) or rid in seen or not _HOST_RE.fullmatch(host):
            continue
        seen.add(rid)
        receivers.append({"id": rid, "name": _text(r.get("name"), 40) or host, "host": host,
                          "port": _int(r.get("port"), 1, 65535, DEFAULT_PORT),
                          "channels": _int(r.get("channels"), 1, 4, 4)})
    manual, mseen = [], set()
    for m in raw.get("manual") if isinstance(raw.get("manual"), list) else []:
        if not isinstance(m, dict) or len(manual) >= MAX_MANUAL:
            continue
        mid = m.get("id") if isinstance(m.get("id"), str) else ""
        name = _text(m.get("name"), 40)
        if not _ID_RE.fullmatch(mid) or mid in mseen or not name:
            continue
        mseen.add(mid)
        manual.append({"id": mid, "name": name, "kind": m.get("kind") if m.get("kind") in KINDS else "mic"})
    return {"receivers": receivers, "manual": manual, "assign": _clean_assign(raw.get("assign")),
            "low_battery": _int(raw.get("low_battery"), 1, 99, LOW_BATTERY),
            "weak_rf_dbm": _int(raw.get("weak_rf_dbm"), -120, -20, WEAK_RF_DBM)}


def validate(raw: Any) -> dict:
    """The settings an admin sent, checked strictly (cleaning alone would drop a mistake silently)."""
    if not isinstance(raw, dict):
        raise Invalid("Send the settings as an object.")
    for r in raw.get("receivers") or []:
        if not isinstance(r, dict) or not isinstance(r.get("host"), str) or not _HOST_RE.fullmatch(r["host"].strip()):
            raise Invalid("A receiver needs an address: its IP address or name.")
    for m in raw.get("manual") or []:
        if not isinstance(m, dict) or not _text(m.get("name"), 40):
            raise Invalid("Give each manual mic or pack a name.")
    for key, v in (raw.get("assign") or {}).items():
        ref = v.get("position_ref") if isinstance(v, dict) else None
        if ref and (not isinstance(ref, str) or directory.item(ref) is None):
            raise Invalid("Choose the position from the list.")
        if isinstance(v, dict) and ref:
            v["position_name"] = directory.item(ref)["name"]
    return clean_settings(raw)


def card_level(warnings: list[dict]) -> str:
    return "bad" if any(w["level"] == "bad" for w in warnings) else ("warn" if warnings else "ok")


def warnings_for(c: dict, low: int, weak: int) -> list[dict]:
    """What is wrong with a transmitter that is on and heard: battery, RF and mute."""
    out = []
    if c["battery"] is not None:
        if c["battery"] <= CRITICAL_BATTERY:
            out.append({"id": "battery", "level": "bad", "text": f"Battery {c['battery']}%"})
        elif c["battery"] <= low:
            out.append({"id": "battery", "level": "warn", "text": f"Battery {c['battery']}%"})
    if c["rf_dbm"] is not None and c["rf_dbm"] < weak:
        out.append({"id": "rf", "level": "warn", "text": f"Weak signal {c['rf_dbm']} dBm"})
    if c["muted"]:
        out.append({"id": "mute", "level": "warn", "text": "Muted"})
    return out


class Wireless:
    def __init__(self) -> None:
        self.receivers: dict[str, Receiver] = {}
        self._running = False
        self._people_at = 0.0
        self._people: list[dict] = []

    # ------------------------------------------------------------------ run
    def settings(self) -> dict:
        return clean_settings(config.get().get("wireless_mics"))

    def start(self) -> None:
        self._running = True
        self._open()

    def _open(self) -> None:
        for r in self.settings()["receivers"]:
            rx = Receiver(r["id"], r["name"], r["host"], r["port"], r["channels"])
            self.receivers[r["id"]] = rx
            rx.start()

    async def _close(self) -> None:
        got, self.receivers = list(self.receivers.values()), {}
        await asyncio.gather(*(r.close() for r in got), return_exceptions=True)

    async def stop(self) -> None:
        self._running = False
        await self._close()

    async def restart(self) -> None:
        await self._close()
        if self._running:
            self._open()

    # ------------------------------------------------------------------ who is scheduled
    def _scheduled(self) -> list[dict]:
        """The people on the followed Planning Center plan (read at most every few seconds); [] without it."""
        now = time.monotonic()
        if now - self._people_at < PERSON_CACHE_S:
            return self._people
        self._people_at = now
        fn = modules.using("plan.rundown_now")
        try:
            self._people = list(fn().get("people") or []) if fn is not None else []
        except Exception:  # Planning Center unreachable: no names, not an error on this page
            self._people = []
        return self._people

    def person_for(self, position_name: str) -> dict | None:
        if not position_name:
            return None
        for p in self._scheduled():
            if p.get("name") and p.get("status") != "declined" and text.same_name(p.get("position") or "", position_name):
                return {"name": p["name"], "photo": p.get("photo") or ""}
        return None

    # ------------------------------------------------------------------ what the page shows
    def _where(self, s: dict, key: str) -> dict:
        a = s["assign"].get(key)
        if not a:
            return {"position_ref": "", "position": "", "missing": False, "unmatched": False, "person": None}
        d = directory.describe(a["position_ref"], a["position_name"])
        return {"position_ref": a["position_ref"], "position": d["name"], "missing": d["missing"],
                "unmatched": d["unmatched"], "person": self.person_for(d["name"])}

    def cards(self) -> list[dict]:
        s, out = self.settings(), []
        for r in s["receivers"]:
            rx = self.receivers.get(r["id"])
            for n in range(1, r["channels"] + 1):
                key = f"{r['id']}:{n}"
                c = rx.chans.get(n) if rx is not None else None
                up = bool(rx is not None and rx.connected)
                live = bool(up and c and c["present"])
                warn = warnings_for(c, s["low_battery"], s["weak_rf_dbm"]) if live else []
                if rx is not None and not up:
                    warn = [{"id": "offline", "level": "bad", "text": "Receiver offline"}]
                out.append({"key": key, "source": "shure", "kind": "mic", "receiver": r["name"], "channel": n,
                            "name": (c["name"] if live and c["name"] else f"{r['name']} {n}"),
                            "online": up, "present": live, "tx": c["tx"] if c else "",
                            "battery": c["battery"] if live else None, "minutes": c["minutes"] if live else None,
                            "rf_dbm": c["rf_dbm"] if live else None, "rf": rf_percent(c["rf_dbm"]) if live else None,
                            "audio": audio_percent(c["audio"]) if live else None,
                            "muted": bool(live and c["muted"]), "antenna": c["antenna"] if live else "",
                            "warnings": warn, "level": card_level(warn), **self._where(s, key)})
        for m in s["manual"]:
            key = f"manual:{m['id']}"
            out.append({"key": key, "source": "manual", "kind": m["kind"], "receiver": "", "channel": 0,
                        "name": m["name"], "online": None, "present": False, "tx": "", "battery": None,
                        "minutes": None, "rf_dbm": None, "rf": None, "audio": None, "muted": False, "antenna": "",
                        "warnings": [], "level": "ok", **self._where(s, key)})
        return out

    def snapshot(self) -> dict:
        s = self.settings()
        return {"receivers": [{**r, "connected": bool(self.receivers.get(r["id"]) and self.receivers[r["id"]].connected),
                               "error": self.receivers[r["id"]].error if r["id"] in self.receivers else ""}
                              for r in s["receivers"]],
                "manual": s["manual"], "low_battery": s["low_battery"], "weak_rf_dbm": s["weak_rf_dbm"],
                "cards": self.cards(), "unproven": True}

    def health(self) -> tuple[str, str]:
        s = self.settings()
        if not s["receivers"] and not s["manual"]:
            return "warn", "Not set up: add a receiver or a manual mic"
        if not s["receivers"]:
            return "ok", f"{len(s['manual'])} manual"
        down = [r for r in s["receivers"] if not (self.receivers.get(r["id"]) and self.receivers[r["id"]].connected)]
        if down:
            return "warn", f"{len(down)} of {len(s['receivers'])} receivers not connected"
        return "ok", f"{len(s['receivers'])} receiver{'s' if len(s['receivers']) != 1 else ''} connected (unproven on hardware)"

    def refresh_saved(self) -> None:
        """A position was renamed in Planning Center: bring the saved names up to date."""
        s = self.settings()
        changed = False
        for a in s["assign"].values():
            got = directory.item(a["position_ref"])
            if got and got["name"] != a["position_name"]:
                a["position_name"], changed = got["name"], True
        if changed:
            config.save({"wireless_mics": s}, replace=("wireless_mics",))


service = Wireless()
