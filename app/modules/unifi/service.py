"""The UniFi module's background work (#358). While the module is on, one loop reads the console on a schedule:

- the client list every `poll_s` seconds (presence: joins, roams, leaves), and at once when the live event stream
  says a client changed (the stream needs the local user; without one, polling alone);
- internet health and Relay's own probes every minute, kept as history (history.py);
- the gear (ports, PoE, firmware, radios) every minute; Wi-Fi neighbours every 10 minutes;
- NDI and Dante from traffic flows every 2 minutes; the console's change log every 5; speed tests every 30.

A reading that fails says so on its own part of the page and does not stop the others.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from ... import config, netsock
from . import history, readings
from .controller import (TRAFFIC, Controller, Problem, Untrusted, clean_fingerprint, clean_host, fingerprint_of,
                         show_fingerprint)
from .presence import Presence

log = logging.getLogger("relay.unifi")

KEY = "unifi"
POLL_S = (10, 300, 30)          # (least, most, default) seconds between client polls
AWAY_S = (60, 3600, 300)        # how long a client may be missing before it counts as gone
EVERY = {"internet": 60, "gear": 60, "neighbours": 600, "av": 120, "changes": 300, "speedtests": 1800, "fold": 3600}
AV_WINDOW_S = 300
UNREADABLE = "Relay could not read this from the console."
UNTRUSTED = ("The controller's certificate is not one this computer trusts. Check the fingerprint matches the "
             "console's, then trust it.")
EVENT_KINDS = ("sta:sync", "client:sync", "events")      # stream messages that mean a client came or went
DEFAULTS = {"host": "", "port": 443, "site": "default", "api_key": "", "fingerprint": "", "username": "",
            "password": "", "probe_target": "1.1.1.1", "poll_s": POLL_S[2], "away_s": AWAY_S[2]}


def _clamp(v: Any, bounds: tuple[int, int, int]) -> int:
    lo, hi, default = bounds
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def _str(v: Any, n: int) -> str:
    return v.strip()[:n] if isinstance(v, str) else ""


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    site = raw.get("site")
    site = "".join(ch for ch in site.strip() if ch.isalnum() or ch in "-_")[:64] if isinstance(site, str) else ""
    try:
        port = int(raw.get("port") or 443)
    except (TypeError, ValueError):
        port = 443
    target = clean_host(raw.get("probe_target")) if raw.get("probe_target") not in (None, "") else "1.1.1.1"
    return {"host": clean_host(raw.get("host")), "port": port if 1 <= port <= 65535 else 443,
            "site": site or "default", "api_key": _str(raw.get("api_key"), 200),
            "fingerprint": clean_fingerprint(raw.get("fingerprint")),
            "username": _str(raw.get("username"), 120), "password": _str(raw.get("password"), 200),
            "probe_target": target or "1.1.1.1",
            "poll_s": _clamp(raw.get("poll_s"), POLL_S), "away_s": _clamp(raw.get("away_s"), AWAY_S)}


def public(s: dict) -> dict:
    out = {k: v for k, v in s.items() if k not in ("api_key", "password")}
    out["api_key_set"] = bool(s.get("api_key"))
    out["password_set"] = bool(s.get("password"))
    out["fingerprint"] = show_fingerprint(s["fingerprint"]) if s.get("fingerprint") else ""
    return out


def settings() -> dict:
    return clean_settings(config.get().get(KEY))


class Service:
    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.stream_task: asyncio.Task | None = None
        self.wake: asyncio.Event | None = None
        self.presence = Presence()
        self.clients: list[dict] = []
        self.problem = ""
        self.untrusted = ""             # fingerprint of a certificate this computer does not trust, for the page
        self.site: dict = {}
        self.api = ""
        self.checked_at = 0.0
        self.due: dict[str, float] = {}
        self.parts: dict[str, Any] = {}           # internet, speedtests, gear, wifi, av, changes
        self.part_problems: dict[str, str] = {}
        self.part_at: dict[str, float] = {}
        self._counters: dict[str, dict] = {}
        self._heard: list[dict] = []
        self.stream = {"state": "off", "detail": "", "events": 0, "since": None}
        self._sig: tuple = ()

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self._run(), name="unifi-poll")
        self.stream_task = asyncio.create_task(self._events(), name="unifi-events")

    async def stop(self) -> None:
        for name in ("stream_task", "task"):
            task = getattr(self, name)
            setattr(self, name, None)
            if task:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        self.presence.reset()
        self.clients, self.problem, self.untrusted, self.api = [], "", "", ""
        self.site, self.due, self.parts, self.part_problems, self.part_at = {}, {}, {}, {}, {}
        self._counters, self._heard, self._sig = {}, [], ()
        self.stream = {"state": "off", "detail": "", "events": 0, "since": None}

    def poke(self) -> None:
        if self.wake:
            self.wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self.check()
                await self.extras()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("unifi: poll failed")
                self.problem = "Relay could not read the controller. It will try again."
            if self.wake is None:
                return
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=settings()["poll_s"])
            except asyncio.TimeoutError:
                pass

    # ---------------------------------------------------------------- the client list
    def _controller(self, s: dict) -> Controller:
        ctl = Controller(s["host"], s["port"], s["api_key"], s["fingerprint"], s["site"])
        if self.site.get("id"):
            ctl.site_id = self.site["id"]
        return ctl

    async def check(self) -> None:
        s = settings()
        if not s["host"] or not s["api_key"]:
            self.problem, self.untrusted, self.site = "", "", {}
            return
        sig = (s["host"], s["port"], s["site"], s["api_key"], s["fingerprint"])
        if sig != self._sig:              # another controller or site: start afresh
            self.presence.reset()
            self.site, self.due, self.parts, self.part_problems, self._counters = {}, {}, {}, {}, {}
            self._sig = sig
        ctl = self._controller(s)
        now = time.time()
        try:
            if not self.site.get("id"):
                self.site = await asyncio.to_thread(ctl.find_site)
            found = await asyncio.to_thread(ctl.clients)
        except Untrusted:
            self.untrusted, self.problem = ctl.untrusted_fp, UNTRUSTED
            return
        except Problem:
            self.problem, self.untrusted = ctl.error or UNREADABLE, ""
            return
        self.problem, self.untrusted, self.api, self.checked_at = "", "", ctl.api, now
        self.clients = found
        self.presence.update(found, now, s["away_s"])

    # ---------------------------------------------------------------- everything else, each on its own schedule
    async def extras(self, force: bool = False) -> None:
        if not self.checked_at or self.problem or self.untrusted:
            return
        s = settings()
        ctl = self._controller(s)
        now = time.time()
        for part in EVERY:
            if not force and self.due.get(part, 0) > now:
                continue
            self.due[part] = now + EVERY[part]
            try:
                await getattr(self, "_" + part)(ctl, s, now)
                self.part_problems.pop(part, None)
                self.part_at[part] = now
            except Problem:
                self.part_problems[part] = ctl.error or UNREADABLE
            except Exception:
                log.warning("unifi: reading %s failed", part, exc_info=True)
                self.part_problems[part] = UNREADABLE

    async def _internet(self, ctl: Controller, s: dict, now: float) -> None:
        net = readings.internet(await asyncio.to_thread(ctl.health))
        gw_ms, gw_loss = await asyncio.to_thread(history.probe, s["host"])
        ext_ms, ext_loss = await asyncio.to_thread(history.probe, s["probe_target"])
        net["probe"] = {"gateway": {"ms": gw_ms, "loss": gw_loss}, "outside": {"target": s["probe_target"],
                                                                               "ms": ext_ms, "loss": ext_loss}}
        self.parts["internet"] = net
        try:
            await asyncio.to_thread(history.record, {
                "down_bps": net["down_bps"], "up_bps": net["up_bps"], "latency_ms": net["latency_ms"],
                "drops": net["drops"], "availability": net["availability"], "gw_ms": gw_ms, "gw_loss": gw_loss,
                "ext_ms": ext_ms, "ext_loss": ext_loss}, now)
        except Exception:
            log.warning("unifi: could not keep internet history", exc_info=True)

    async def _fold(self, ctl: Controller, s: dict, now: float) -> None:
        await asyncio.to_thread(history.fold, now)

    async def _speedtests(self, ctl: Controller, s: dict, now: float) -> None:
        ms = int(now * 1000)
        self.parts["speedtests"] = readings.speedtests(await asyncio.to_thread(ctl.speedtests, ms - 30 * 86400000, ms))

    async def _gear(self, ctl: Controller, s: dict, now: float) -> None:
        g = readings.gear(await asyncio.to_thread(ctl.gear), self.clients, self._counters)
        self._counters = readings.counters(g)
        self.parts["gear"] = g
        self.parts["wifi"] = readings.wifi(g, self.clients, self._heard)

    async def _neighbours(self, ctl: Controller, s: dict, now: float) -> None:
        self._heard = await asyncio.to_thread(ctl.neighbours)
        if "gear" in self.parts:
            self.parts["wifi"] = readings.wifi(self.parts["gear"], self.clients, self._heard)

    async def _av(self, ctl: Controller, s: dict, now: float) -> None:
        ms = int(now * 1000)
        flows = await asyncio.to_thread(ctl.flows, ms - AV_WINDOW_S * 1000, ms)
        self.parts["av"] = {"devices": readings.av(flows, self.clients, AV_WINDOW_S), "flows": len(flows),
                            "window_s": AV_WINDOW_S}

    async def _changes(self, ctl: Controller, s: dict, now: float) -> None:
        ms = int(now * 1000)
        self.parts["changes"] = readings.changes(await asyncio.to_thread(ctl.changes, ms - 30 * 86400000, ms))

    # ---------------------------------------------------------------- the live event stream (local user only)
    async def _events(self) -> None:
        """Sign in as the local user and hold the console's event stream open; a client event polls at once."""
        backoff = 5.0
        while True:
            s = settings()
            if not (s["host"] and s["username"] and s["password"]) or self.untrusted or not self.checked_at:
                self.stream.update(state="off" if not s["username"] else "waiting", detail="")
                await asyncio.sleep(5)
                continue
            ctl = self._controller(s)
            cookie = ""
            try:
                cookie = await asyncio.to_thread(ctl.login, s["username"], s["password"])
                ws = await netsock.websocket(TRAFFIC, ctl.events_url(), ssl=ctl.ssl_context(),
                                             extra_headers={"Cookie": cookie})
                try:
                    if s["fingerprint"]:
                        sslobj = ws.transport.get_extra_info("ssl_object")
                        der = sslobj.getpeercert(binary_form=True) if sslobj else None
                        if not der or fingerprint_of(der) != s["fingerprint"]:
                            ctl.error = "The event stream showed a different certificate."
                            raise Problem(ctl.error)
                    self.stream.update(state="live", detail="", since=time.time())
                    backoff = 5.0
                    last_poke = 0.0
                    while True:
                        msg = await ws.recv()
                        try:
                            kind = str((json.loads(msg).get("meta") or {}).get("message") or "")
                        except (ValueError, AttributeError):
                            continue
                        if kind in EVENT_KINDS:
                            self.stream["events"] += 1
                            if time.monotonic() - last_poke >= 3:
                                last_poke = time.monotonic()
                                self.poke()
                        if settings()["username"] != s["username"]:
                            break
                finally:
                    await ws.close()
            except asyncio.CancelledError:
                if cookie:
                    await asyncio.to_thread(ctl.logout, cookie)
                raise
            except Problem:
                self.stream.update(state="error", detail=ctl.error or "The console refused the event stream.")
            except Exception:
                log.info("unifi: event stream closed; reconnecting", exc_info=True)
                self.stream.update(state="error", detail="The event stream closed. Relay reconnects by itself.")
            if cookie:
                await asyncio.to_thread(ctl.logout, cookie)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 120.0)

    # ---------------------------------------------------------------- what the page, health and pills read
    def health(self) -> tuple[str, str]:
        s = settings()
        if not s["host"] or not s["api_key"]:
            return "idle", "Not set up: add the controller's address and a read-only API key."
        if self.untrusted:
            return "warn", "Waiting for an admin to trust the controller's certificate."
        if self.problem:
            return "error", self.problem
        if not self.checked_at:
            return "idle", "Connecting to the controller…"
        n = len(self.presence.online())
        warns = self.warnings()
        if warns:
            return "warn", f"{len(warns)} thing{'' if len(warns) == 1 else 's'} to check: {warns[0]}"
        return "ok", f"Reading {self.site.get('name') or 'the site'}: {n} client{'' if n == 1 else 's'} online"

    def warnings(self) -> list[str]:
        out = []
        net = self.parts.get("internet") or {}
        if net:
            if net.get("status") not in ("ok", "unknown"):
                out.append("The internet connection is down.")
            ext = ((net.get("probe") or {}).get("outside") or {})
            if (ext.get("loss") or 0) >= 34:
                out.append(f"{int(ext['loss'])}% of Relay's checks to {ext.get('target')} failed.")
            if (net.get("latency_ms") or 0) >= 150:
                out.append(f"Internet latency is {int(net['latency_ms'])} ms.")
        for g in self.parts.get("gear") or []:
            if not g["online"]:
                out.append(f"{g['name']} is offline.")
            out += g["warnings"]
        out += [r["warn"] for r in (self.parts.get("wifi") or {}).get("radios", []) if r["warn"]]
        return out

    def pill(self) -> list[dict]:
        net = self.parts.get("internet")
        if not net or self.problem or self.untrusted:
            return []
        ext = ((net.get("probe") or {}).get("outside") or {})
        bad = net.get("status") not in ("ok", "unknown") or (ext.get("loss") or 0) >= 34 or (net.get("latency_ms") or 0) >= 150
        lat = net.get("latency_ms")
        text = "INTERNET DOWN" if net.get("status") not in ("ok", "unknown") else (
            f"INTERNET {int(lat)} MS" if lat is not None else "INTERNET")
        if (ext.get("loss") or 0) >= 34 and net.get("status") in ("ok", "unknown"):
            text += f" · {int(ext['loss'])}% LOST"
        pill = {"id": "unifi-internet", "text": text, "href": "#network/unifi"}
        if bad:
            pill["level"] = "error"
        return [pill]

    def where(self, c: dict) -> str:
        up = c.get("uplink") or ""
        name = next((g["name"] for g in self.parts.get("gear") or [] if g["mac"] == up), "") or c.get("uplink_name") or ""
        if c.get("wired"):
            return f"{name or 'switch'} port {c['port']}" if c.get("port") is not None else name
        return " · ".join(x for x in (name, c.get("ssid") or "") if x)

    def map_devices(self) -> list[dict]:
        """What this console knows, for the core network map (#437): the gear with its ports, the clients with the
        switch and port each is on, and which clients carry NDI or Dante. Read from what is already in memory."""
        by_mac = {g["mac"]: g for g in self.parts.get("gear") or []}
        av = {a["mac"]: a for a in (self.parts.get("av") or {}).get("devices") or []}
        out = []
        for g in by_mac.values():
            up = g.get("uplink") or {}
            parent = next((m for m, o in by_mac.items() if o["name"] == up.get("to")), "") if up.get("to") else ""
            out.append({"mac": g["mac"], "ip": g.get("ip"), "name": g["name"],
                        "kind": g.get("kind") if g.get("kind") in ("gateway", "switch", "ap") else "switch",
                        "uplink_mac": parent, "speed": up.get("speed"), "poe_w": g.get("poe_w"),
                        "tags": [t for t in (g.get("model"),) if t], "ports": g.get("ports") or [],
                        "model": g.get("model") or "", "poe_budget_w": g.get("poe_budget_w")})
        ports = {(g["mac"], p["port"]): p for g in by_mac.values() for p in g.get("ports") or []}
        for c in self.presence.online():
            flows = av.get(c["mac"])
            uplink = c.get("uplink")
            port = ports.get((uplink, c.get("port"))) if c.get("wired") else None
            out.append({"mac": c["mac"], "ip": c.get("ip"), "host": c.get("hostname"), "name": c.get("name") or c.get("hostname") or "",
                        "uplink_mac": uplink if uplink in by_mac else "", "uplink_port": c.get("port") if c.get("wired") else None,
                        "port_name": (port or {}).get("name") or "", "speed": (port or {}).get("speed"),
                        "poe_w": (port or {}).get("poe_w"), "errors": (port or {}).get("errors"),
                        "ndi": ("ndi" in flows["kinds"]) if flows else None,
                        "dante": ("dante" in flows["kinds"]) if flows else None,
                        "identified_by": ["traffic flows"] if flows else []})
        return out

    def snapshot(self) -> dict:
        s = settings()
        level, detail = self.health()
        online = sorted(self.presence.online(), key=lambda c: (c.get("name") or c.get("hostname") or c["mac"]).lower())
        return {
            "settings": public(s), "configured": bool(s["host"] and s["api_key"]), "level": level, "detail": detail,
            "problem": self.problem, "untrusted": show_fingerprint(self.untrusted) if self.untrusted else "",
            "site": self.site, "api": self.api, "checked_at": self.checked_at or None, "stream": dict(self.stream),
            "warnings": self.warnings(),
            "clients": [{"mac": c["mac"], "name": c.get("name") or "", "hostname": c.get("hostname") or "",
                         "ip": c.get("ip") or "", "wired": bool(c.get("wired")), "where": self.where(c),
                         "network": c.get("network") or "", "model": c.get("model") or "", "guest": bool(c.get("guest")),
                         "private": bool(c.get("private")), "signal": c.get("signal"), "since": c.get("since"),
                         "missing": c.get("missing")} for c in online],
            "events": [{**e, "where": self.where(e)} for e in list(self.presence.events)[:100]],
            "internet": self.parts.get("internet"), "speedtests": (self.parts.get("speedtests") or [])[:30],
            "gear": self.parts.get("gear") or [], "wifi": self.parts.get("wifi"), "av": self.parts.get("av"),
            "changes": [c for c in self.parts.get("changes") or [] if c["change"]][:50],
            "signins": sum(1 for c in self.parts.get("changes") or [] if not c["change"]),
            "part_problems": dict(self.part_problems), "part_at": dict(self.part_at),
        }


service = Service()
