"""Dante routing (#259) and routing presets (#260): change a receive channel's subscription from Relay, lock channels
Relay must never change, undo the last change, and save, compare and apply whole routings.

Every write goes through `Routing.apply`, one at a time: locked channels are skipped and listed, each subscription is
written, read back and reported by itself, every result goes to the module's history with who made it, and what was
there before is kept so the whole change can be undone. The write packet itself is arc.set_subscription, which has not
been checked against real Dante gear: the page says "untested" and a result Relay cannot confirm is reported as such.

A route is {"device": Dante name, "channel": receive channel number, "source": "<transmit channel>@<device>" or ""
for no subscription}.
"""
from __future__ import annotations

import json
import logging
import threading
from typing import Callable

from ... import db, text
from . import arc
from .monitor import monitor
from .service import DEFAULT_ARC, service

log = logging.getLogger("relay.dante")

MAX_PRESETS = 200
MAX_NAME = 80


class Refused(Exception):
    """A request Relay will not carry out; `str(e)` says why, in words for the operator."""


def routes_of(devices: list[dict], scope: list[str] | None = None) -> list[dict]:
    """Every receive channel of every device whose subscriptions Relay could read (all of `scope` when given)."""
    wanted = {s.lower() for s in scope} if scope else None
    out: list[dict] = []
    for d in devices:
        if wanted is not None and d["name"].lower() not in wanted:
            continue
        for s in d.get("subscriptions") or []:
            out.append({"device": d["name"], "channel": s["channel"], "source": s["source"] if s["status"] != "none" else ""})
    return out


def same_source(a: str, b: str) -> bool:
    """Two subscription sources are the same: both empty (no subscription), or the same name ignoring case."""
    return (not a and not b) or text.same_name(a, b)


def sources(devices: list[dict]) -> set[str]:
    return {f"{c}@{d['name']}" for d in devices for c in d["transmit"]}


def diff(routes: list[dict], devices: list[dict], locked: set[tuple[str, int]]) -> list[dict]:
    """What applying `routes` would do now, one entry per route that differs or cannot be applied:
    {"device", "channel", "from", "to", "action": "change"|"locked"|"missing", "why"}."""
    by_name = {d["name"].lower(): d for d in devices}
    known = {s.lower() for s in sources(devices)}
    out: list[dict] = []
    for r in routes:
        d = by_name.get(r["device"].lower())
        entry = {"device": r["device"], "channel": r["channel"], "from": "", "to": r["source"], "action": "change", "why": ""}
        if d is None:
            entry.update(action="missing", why="that device is not on the network")
        elif d.get("subscriptions") is None:
            entry.update(action="missing", why="Relay cannot read that device's subscriptions")
        else:
            now = next((s for s in d["subscriptions"] if s["channel"] == r["channel"]), None)
            if now is None:
                entry.update(action="missing", why=f"the device has no receive channel {r['channel']}")
            else:
                entry["from"] = now["source"] if now["status"] != "none" else ""
                if same_source(entry["from"], r["source"]):
                    continue
                if (d["name"], r["channel"]) in locked:
                    entry.update(action="locked", why="the channel is locked")
                elif r["source"] and r["source"].lower() not in known:
                    entry.update(action="missing", why="the source is not on the network")
        out.append(entry)
    return out


class Routing:
    def __init__(self) -> None:
        self._write = threading.Lock()
        self.last: dict | None = None        # what the last change replaced, for Undo (in memory: gone on a restart)

    # ------------------------------------------------------------------------------------------------ locks
    def locks(self) -> set[tuple[str, int]]:
        try:
            return {(r["device"], r["channel"]) for r in db.query("select device, channel from dante_locks")}
        except Exception:  # noqa: BLE001
            return set()

    def set_lock(self, device: str, channel: int, on: bool) -> None:
        if on:
            db.execute("insert or ignore into dante_locks (device, channel) values (?, ?)", (device, channel))
        else:
            db.execute("delete from dante_locks where device = ? and channel = ?", (device, channel))

    # ------------------------------------------------------------------------------------------------ writing
    def _write_one(self, d: dict, channel: int, source: str) -> tuple[str, str]:
        """One subscription written and read back. ("ok"|"refused"|"silent"|"unconfirmed"|"error", message)."""
        ip, port = d["ips"][0], d["ports"].get("arc", DEFAULT_ARC)
        try:
            state, why = arc.set_subscription(ip, port, channel, source)
        except OSError as e:
            return "error", f"Relay could not reach the Dante network: {e}"
        if state != "ok":
            return state, why
        service.read_state(d, ip, port)
        now = next((s for s in d.get("subscriptions") or [] if s["channel"] == channel), None)
        if now is None or (now["source"] if now["status"] != "none" else "").lower() != source.lower():
            return "unconfirmed", "The device accepted the change but did not show it afterwards."
        service.keep_subscriptions(d["key"], d["subscriptions"])
        return "ok", ""

    def apply(self, changes: list[dict], who: str, label: str, *, undoing: bool = False) -> list[dict]:
        """Write each change ({"device", "channel", "to"}). Blocks: call through to_thread. Returns one result per
        change: {"device", "channel", "from", "to", "result", "message"}; result is "ok" or what went wrong."""
        if not self._write.acquire(timeout=10):
            raise Refused("Another routing change is still being written. Try again in a moment.")
        try:
            devices = {d["name"].lower(): d for d in service.devices_copy()}
            locked = self.locks()
            results: list[dict] = []
            done: list[dict] = []
            for c in changes:
                d = devices.get(c["device"].lower())
                row = {"device": c["device"], "channel": c["channel"], "from": "", "to": c["to"], "result": "", "message": ""}
                results.append(row)
                if d is None or not d.get("ips") or d.get("subscriptions") is None:
                    row.update(result="missing", message="Relay cannot read that device right now.")
                    continue
                now = next((s for s in d["subscriptions"] if s["channel"] == c["channel"]), None)
                if now is None:
                    row.update(result="missing", message=f"The device has no receive channel {c['channel']}.")
                    continue
                row["from"] = now["source"] if now["status"] != "none" else ""
                if (d["name"], c["channel"]) in locked:
                    row.update(result="locked", message="The channel is locked, so Relay left it alone.")
                    continue
                if same_source(row["from"], c["to"]):
                    row.update(result="ok", message="Already set.")
                    continue
                monitor.relay_wrote(d["key"])
                row["result"], row["message"] = self._write_one(d, c["channel"], c["to"])
                monitor.event(d["name"], "routing", (
                    f"by {who}: receive channel {c['channel']} {c['to'] or 'unsubscribed'} (was "
                    f"{row['from'] or 'unsubscribed'}), {label}" if row["result"] == "ok" else
                    f"by {who}: receive channel {c['channel']} -> {c['to'] or 'unsubscribed'} failed, {label}: "
                    f"{row['message']}"))
                if row["result"] == "ok":
                    done.append({"device": d["name"], "channel": c["channel"], "to": row["from"]})
            if done and not undoing:
                self.last = {"label": label, "by": who, "at": db.now(), "changes": done}
            elif undoing:
                self.last = None
            return results
        finally:
            self._write.release()

    def subscribe(self, device: str, channel: int, source: str, who: str) -> dict:
        """One subscription changed (blocks). Raises Refused when it cannot be tried at all; otherwise the result row."""
        devices = service.devices_copy()
        d = next((x for x in devices if text.same_name(x["name"], device)), None)
        if d is None:
            raise Refused("Relay has not seen that device.")
        if source and source.lower() not in {s.lower() for s in sources(devices)}:
            raise Refused("Relay has not seen that transmit channel.")
        if (d["name"], channel) in self.locks():
            raise Refused("That receive channel is locked, so Relay will not change it.")
        label = f"{source or 'no source'} to {d['name']} channel {channel}"
        return self.apply([{"device": d["name"], "channel": channel, "to": source}], who, label)[0]

    def undo(self, who: str) -> list[dict]:
        last = self.last
        if not last:
            raise Refused("There is nothing to undo.")
        return self.apply(last["changes"], who, "undo of " + last["label"], undoing=True)

    def undo_info(self) -> dict | None:
        last = self.last
        return {"label": last["label"], "by": last["by"], "at": last["at"], "count": len(last["changes"])} if last else None

    # ------------------------------------------------------------------------------------------------ presets
    def presets(self) -> list[dict]:
        try:
            rows = db.query("select id, name, saved_at, saved_by, devices, routes from dante_presets order by name collate nocase")
        except Exception:  # noqa: BLE001
            return []
        return [{"id": r["id"], "name": r["name"], "saved_at": r["saved_at"], "saved_by": r["saved_by"],
                 "devices": json.loads(r["devices"]), "count": len(json.loads(r["routes"]))} for r in rows]

    def preset(self, preset_id: int) -> dict:
        row = db.one("select id, name, saved_at, saved_by, devices, routes from dante_presets where id = ?", (preset_id,))
        if not row:
            raise Refused("There is no such preset.")
        return {"id": row["id"], "name": row["name"], "saved_at": row["saved_at"], "saved_by": row["saved_by"],
                "devices": json.loads(row["devices"]), "routes": json.loads(row["routes"])}

    def save_preset(self, name: str, scope: list[str], who: str) -> dict:
        name = name.strip()
        if not name or len(name) > MAX_NAME:
            raise Refused(f"Give the preset a name of up to {MAX_NAME} characters.")
        devices = service.devices_copy()
        known = {d["name"].lower(): d["name"] for d in devices}
        unknown = [s for s in scope if s.lower() not in known]
        if unknown:
            raise Refused(f"Relay has not seen {unknown[0]}.")
        routes = routes_of(devices, scope or None)
        if not routes:
            raise Refused("Relay can read no subscriptions to save yet.")
        if len(self.presets()) >= MAX_PRESETS and not db.one("select 1 as x from dante_presets where name = ?", (name,)):
            raise Refused("There are too many presets: delete one first.")
        names = [known[s.lower()] for s in scope]
        db.execute("insert into dante_presets (name, saved_at, saved_by, devices, routes) values (?, ?, ?, ?, ?) "
                   "on conflict(name) do update set saved_at = excluded.saved_at, saved_by = excluded.saved_by, "
                   "devices = excluded.devices, routes = excluded.routes",
                   (name, db.now(), who, json.dumps(names), json.dumps(routes)))
        row = db.one("select id from dante_presets where name = ?", (name,))
        return {"id": row["id"], "name": name, "count": len(routes)}

    def delete_preset(self, preset_id: int) -> str:
        p = self.preset(preset_id)
        db.execute("delete from dante_presets where id = ?", (preset_id,))
        return p["name"]

    def compare(self, preset_id: int) -> dict:
        """The preset against the live network: what would change, what is locked, what is missing, how many match."""
        p = self.preset(preset_id)
        devices = service.devices_copy()
        entries = diff(p["routes"], devices, self.locks())
        return {"preset": {"id": p["id"], "name": p["name"], "saved_at": p["saved_at"], "saved_by": p["saved_by"]},
                "changes": [e for e in entries if e["action"] == "change"],
                "locked": [e for e in entries if e["action"] == "locked"],
                "missing": [e for e in entries if e["action"] == "missing"],
                "same": len(p["routes"]) - len(entries)}

    def apply_preset(self, preset_id: int, who: str) -> dict:
        """Apply a preset (blocks): shows what it did per subscription. Locked channels and what is missing are listed
        and skipped. Undo restores the routing from before the apply."""
        cmp = self.compare(preset_id)
        name = cmp["preset"]["name"]
        results = self.apply([{"device": e["device"], "channel": e["channel"], "to": e["to"]} for e in cmp["changes"]],
                             who, f"preset {name}")
        return {"preset": cmp["preset"], "results": results, "locked": cmp["locked"], "missing": cmp["missing"],
                "failed": sum(1 for r in results if r["result"] != "ok")}


routing = Routing()
