"""The Audio Mixers service (#150, #486): every desk the operator set up, its mirror and driver, and what the rest of Relay
asks of them.

* **Gear, channels and names.** Each desk is one core gear record (`mixer:<desk id>` plus its address). Its
  inputs are audio source channels and its mixes audio destination channels, offered through `gear.audio_mixers`, so
  the Planning Center board and the Network page see them without anyone typing them in. A rename on the Network page
  goes to the desk (`can_rename`, `rename`) when its protocol takes names.
* **Routes.** A mix is a destination that mixes several inputs, each at its own send level: `routes.audio_mixers`
  sets the sends on the desk and reads them back where the desk says them.
* **Watchdog.** The operator saves a baseline (names, the mutes they tick, the scene). When the desk drifts from it
  without Relay doing it, one chosen person gets a crew notification that buzzes until read (`crew.notify`), once per
  difference.
* **Mic names from Planning Center.** With Planning Center on, the next service's mics (`plan.rundown_now`) give each
  board channel its person; the operator previews the names and confirms before any goes to the desk.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import ipaddress
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from ... import config, modules
from . import drivers, showfile
from .mirror import Mirror
from .models import COLOURS, KINDS, MODELS, OFF_DB, TESTED, clamp_db, clean_name, db_text, default_name, has_strip, \
    parse_strip

log = logging.getLogger("relay.audio_mixers")

KEY = "audio_mixers"
MAX_DESKS = 8
NAME_MAX = 40
WATCH_SETTLE_S = 8.0              # changes come in bursts (a scene recall): wait this long before comparing
RELAY_GRACE_S = 15.0              # after Relay recalls a scene, its changes are expected for this long
MAX_WATCHED = 64
_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,23}")

DEFAULTS: dict = {"desks": [], "watchdog": {"on": False, "user_id": 0, "names": True, "scene": True, "mutes": []},
                  "baselines": {}, "names": {"style": "first"}}


# -- settings ----------------------------------------------------------------------------------------------------------
def _host(v: Any) -> str:
    h = v.strip() if isinstance(v, str) else ""
    return h if re.fullmatch(r"[A-Za-z0-9.\-:]{1,253}", h) else ""


def _int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return n if lo <= n <= hi else default


def clean_desk(raw: Any, n: int) -> dict | None:
    if not isinstance(raw, dict):
        return None
    model = raw.get("model") if raw.get("model") in MODELS else None
    if model is None:
        return None
    did = raw.get("id") if isinstance(raw.get("id"), str) and _ID.fullmatch(raw["id"]) else f"desk{n}"
    name = raw.get("name").strip()[:NAME_MAX] if isinstance(raw.get("name"), str) else ""
    return {"id": did, "name": name or MODELS[model].label, "model": model, "host": _host(raw.get("host")),
            "port": _int(raw.get("port"), 0, 65535, 0), "midi_channel": _int(raw.get("midi_channel"), 1, 12, 1),
            "ahnet": bool(raw.get("ahnet")) and model == "avantis",
            "scene_names": _int(raw.get("scene_names"), 0, 500, 0)}


def _strip_ref(v: Any, desks: dict[str, dict]) -> str | None:
    if not isinstance(v, str) or ":" not in v:
        return None
    did, key = v.split(":", 1)
    d = desks.get(did)
    return v if d and has_strip(MODELS[d["model"]], key) else None


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    desks: list[dict] = []
    seen: set[str] = set()
    for i, d in enumerate(raw.get("desks") or []):
        c = clean_desk(d, i + 1)
        if c and c["id"] not in seen and len(desks) < MAX_DESKS:
            seen.add(c["id"])
            desks.append(c)
    by_id = {d["id"]: d for d in desks}
    w = raw.get("watchdog") if isinstance(raw.get("watchdog"), dict) else {}
    mutes = []
    for m in w.get("mutes") or []:
        r = _strip_ref(m, by_id)
        if r and r not in mutes and len(mutes) < MAX_WATCHED:
            mutes.append(r)
    watchdog = {"on": bool(w.get("on")), "user_id": _int(w.get("user_id"), 0, 10**9, 0),
                "names": w.get("names") is not False, "scene": w.get("scene") is not False, "mutes": mutes}
    baselines: dict[str, dict] = {}
    for did, b in (raw.get("baselines") or {}).items() if isinstance(raw.get("baselines"), dict) else []:
        d = by_id.get(did)
        if d is None or not isinstance(b, dict):
            continue
        model = MODELS[d["model"]]
        names = {k: v[:64] for k, v in (b.get("names") or {}).items()
                 if has_strip(model, k) and isinstance(v, str)} if isinstance(b.get("names"), dict) else {}
        bm = {k: bool(v) for k, v in (b.get("mutes") or {}).items()
              if has_strip(model, k) and isinstance(v, bool)} if isinstance(b.get("mutes"), dict) else {}
        scene = b.get("scene") if isinstance(b.get("scene"), int) and not isinstance(b.get("scene"), bool) else None
        saved = b.get("saved_at") if isinstance(b.get("saved_at"), (int, float)) else 0
        baselines[did] = {"names": names, "mutes": bm, "scene": scene, "saved_at": saved}
    sent = raw.get("patches") if isinstance(raw.get("patches"), dict) else {}
    patches = {did: c for did, v in sent.items()
               if did in by_id and by_id[did]["model"] in showfile.MODELS and (c := showfile.clean_patch(v))}
    style = (raw.get("names") or {}).get("style") if isinstance(raw.get("names"), dict) else None
    return {"desks": desks, "watchdog": watchdog, "baselines": baselines, "patches": patches,
            "names": {"style": style if style in ("first", "full", "last") else "first"}}


def settings() -> dict:
    return config.get().get(KEY) or clean_settings({})


def save(changes: dict) -> dict:
    new = clean_settings({**settings(), **changes})
    config.save({KEY: new}, replace=(KEY,))
    return new


# -- desks --------------------------------------------------------------------------------------------------------
@dataclass
class Desk:
    cfg: dict
    mirror: Mirror
    driver: drivers.Driver
    gear_id: int | None = None
    grace_until: float = 0.0
    rebase: bool = False          # Relay recalled a scene: save what the desk shows as the baseline once it settles
    told: set[str] = field(default_factory=set)        # watchdog differences already sent

    @property
    def id(self) -> str:
        return self.cfg["id"]

    def strip(self, key: str):
        return self.mirror.strips.get(key)


class Service:
    def __init__(self) -> None:
        self.desks: dict[str, Desk] = {}
        self.version = 0
        self._gear_task: asyncio.Task | None = None
        self._watch_task: asyncio.Task | None = None
        self.running = False
        self.loop: asyncio.AbstractEventLoop | None = None

    def _on_loop(self, fn, *args):
        """Run `fn` on the event loop the drivers write on: core calls rename and routes from worker threads."""
        try:
            here = asyncio.get_running_loop()
        except RuntimeError:
            here = None
        if self.loop is None or here is self.loop or self.loop.is_closed():
            return fn(*args)
        fut: concurrent.futures.Future = concurrent.futures.Future()

        def run() -> None:
            try:
                fut.set_result(fn(*args))
            except Exception as e:  # handed back to the caller's thread
                fut.set_exception(e)
        self.loop.call_soon_threadsafe(run)
        return fut.result(timeout=5)

    # -- life ----------------------------------------------------------------------------------------------------
    async def start(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.running = True
        await self.reload()

    async def stop(self) -> None:
        self.running = False
        for d in list(self.desks.values()):
            await d.driver.stop()
        self.desks.clear()
        for t in (self._gear_task, self._watch_task):
            if t is not None:
                t.cancel()
        self._bump()

    async def reload(self) -> None:
        """Bring the running desks in line with the settings: a changed desk reconnects, a removed one stops."""
        if not self.running:
            return
        want = {d["id"]: d for d in settings()["desks"]}
        for did, desk in list(self.desks.items()):
            if want.get(did) != desk.cfg:
                await desk.driver.stop()
                del self.desks[did]
        for did, cfg in want.items():
            if did in self.desks:
                continue
            mirror = Mirror(MODELS[cfg["model"]])
            desk = Desk(cfg, mirror, drivers.make(cfg, mirror))
            mirror.on_change = self._changed_cb(desk)
            self.desks[did] = desk
            if cfg["host"]:
                desk.driver.start()
        self._bump()
        self._gear_soon()

    def _changed_cb(self, desk: Desk):
        def changed(what: str, key: str) -> None:
            self._bump()
            if what in ("name", "link"):
                self._gear_soon()
            if what in ("name", "mute", "scene", "link"):
                self._watch_soon()
        return changed

    def _bump(self) -> None:
        self.version += 1

    def desk(self, did: Any) -> Desk | None:
        return self.desks.get(did) if isinstance(did, str) else None

    def health(self) -> tuple[str, str]:
        if not self.desks:
            return "idle", "No desk set up"
        down = [d.cfg["name"] for d in self.desks.values() if d.cfg["host"] and not d.driver.connected()]
        if not down:
            n = len(self.desks)
            return "ok", f"{n} desk{'s' if n != 1 else ''} connected"
        return ("error" if len(down) == len(self.desks) else "warn"), "Not connected: " + ", ".join(down)

    # -- gear, channels and names (app/gearfeed.py) ---------------------------------------------------------------------
    def _gear_soon(self) -> None:
        if self._gear_task is None or self._gear_task.done():
            try:
                self._gear_task = asyncio.get_running_loop().create_task(self._gear_later())
            except RuntimeError:
                pass

    async def _gear_later(self) -> None:
        await asyncio.sleep(1.0)            # names arrive in a burst on connect: report once
        await asyncio.to_thread(self.report)

    def report(self) -> None:
        """Adopt each desk as gear and tell core what channels it has now. Blocks (database)."""
        from ... import gear, gearfeed

        hosts = [d.cfg["host"] for d in self.desks.values()]
        for d in self.desks.values():
            if not d.cfg["host"]:
                continue
            keys = [f"mixer:{d.id}"]                 # gear keys are letters, a colon, then the id
            if hosts.count(d.cfg["host"]) == 1:      # two desks at one address are two boxes, not one
                try:
                    keys += gear.net_keys(ips=[str(ipaddress.ip_address(d.cfg["host"]))])
                except ValueError:
                    keys += gear.net_keys(host=d.cfg["host"])
            try:
                g = gear.adopt(keys, name=d.cfg["name"], kind="mixer", model=MODELS[d.cfg["model"]].label,
                               heard=d.driver.connected())
                d.gear_id = g["id"]
            except Exception:  # the database may be down; channels go out on the next change
                log.debug("audio mixers: gear not adopted", exc_info=True)
        try:
            gearfeed.changed(KEY)
        except Exception:
            log.debug("audio mixers: gearfeed not told", exc_info=True)

    def gear_list(self) -> dict:
        devices, chans = [], []
        for d in self.desks.values():
            if not d.cfg["host"]:
                continue
            try:
                addr = {"ip": str(ipaddress.ip_address(d.cfg["host"]))}
            except ValueError:
                addr = {"host": d.cfg["host"]}
            devices.append({**addr, "name": d.cfg["name"], "kind": "mixer", "tags": ["Audio mixer"]})
            if not d.driver.connected() or d.gear_id is None:
                continue        # what a desk Relay cannot hear has does not turn missing
            items = []
            for s in d.mirror.strips.values():
                kind, n = parse_strip(s.key)  # type: ignore[misc]
                if kind in ("in", "mix"):
                    items.append({"key": f"{d.id}:{s.key}", "kind": "audio",
                                  "direction": "source" if kind == "in" else "destination", "number": n,
                                  "gear_name": s.name or default_name(s.key)})
            chans.append({"gear": d.gear_id, "within": f"{d.id}:", "items": items})
        return {"devices": devices, "channels": chans}

    def _end(self, key: str) -> tuple[Desk, str] | None:
        did, _, strip = key.partition(":")
        d = self.desk(did)
        return (d, strip) if d and strip in d.mirror.strips else None

    def can_rename(self, key: str) -> bool:
        got = self._end(key)
        return bool(got and got[0].driver.connected() and got[0].driver.can_name())

    def rename(self, key: str, name: str) -> None:
        """Send a channel name to the desk (Network page, #543). Raises ValueError with a fixed sentence."""
        got = self._end(key)
        if got is None or not got[0].driver.connected():
            raise ValueError("The desk is not connected, so its channel name was not changed.")
        d, strip = got
        if not d.driver.can_name():
            raise ValueError(drivers.NO_NAMES)
        text = clean_name(name, d.mirror.model.name_len)
        if not text:
            raise ValueError("Write a name the desk can show (plain letters and numbers).")
        said = self._on_loop(d.driver.name, strip, text)
        if said:
            raise ValueError(said)
        self.expect(d, strip, name=text)

    # -- I/O patch read from an exported show (#261), for the signal map (#262) ---------------------------------------
    def patch_list(self) -> list[dict]:
        """Every desk with a patch read from a show file: {desk, name, model, family, firmware, file, read_at,
        verified, inputs, outputs}. Inputs: channel, name, named, source {kind, port, socket, label}."""
        s = settings()
        got = s.get("patches", {})
        return [{"desk": d["id"], "name": d["name"], "model": d["model"], **got[d["id"]]}
                for d in s["desks"] if d["id"] in got]

    # -- routes (app/routing.py): an input's send to a mix ----------------------------------------------------------------
    def routes_api(self) -> dict:
        return {"exclusive": False, "gain": True, "permission": f"{KEY}.control", "apply": self.apply_route,
                "read": self.read_route}

    def apply_route(self, end: dict, wanted: list[dict]) -> str:
        return self._on_loop(self._apply_route, end, wanted)

    def _apply_route(self, end: dict, wanted: list[dict]) -> str:
        got = self._end(end.get("key") or "")
        if got is None:
            return "That mix is not on a desk Relay knows."
        d, strip = got
        kind, mix = parse_strip(strip)  # type: ignore[misc]
        if kind != "mix":
            return "Only a mix takes sends."
        if not d.driver.connected():
            return drivers.NOT_CONNECTED
        if not d.driver.can_send:
            return drivers.NO_SENDS
        want: dict[int, float] = {}
        for w in wanted:
            for e in w.get("ends") or []:
                if e.get("provider") == KEY and str(e.get("key", "")).startswith(f"{d.id}:in"):
                    got_in = parse_strip(e["key"].split(":", 1)[1])
                    if got_in:
                        want[got_in[1]] = 0.0 if w.get("gain_db") is None else float(w["gain_db"])
                        break
        problems = []
        for n in range(1, d.mirror.model.count("in") + 1):
            on, _ = d.mirror.strips[f"in{n}"].sends.get(mix, (None, None))
            if n in want:
                said = d.driver.send(n, mix, want[n])
            elif on is not False:
                said = d.driver.send(n, mix, None)
            else:
                continue
            if said and said not in problems:
                problems.append(said)
        return " ".join(problems)

    def read_route(self, end: dict) -> list[dict] | None:
        got = self._end(end.get("key") or "")
        if got is None:
            return None
        d, strip = got
        kind, mix = parse_strip(strip)  # type: ignore[misc]
        if kind != "mix" or not d.driver.reads_sends or not d.driver.connected():
            return None
        out = []
        for n in range(1, d.mirror.model.count("in") + 1):
            on, db = d.mirror.strips[f"in{n}"].sends.get(mix, (None, None))
            if on is None:
                return None
            if on and (db is None or db > OFF_DB):
                out.append({"provider": KEY, "key": f"{d.id}:in{n}", "gain_db": db})
        return out

    # -- control (each returns "" or a fixed sentence) --------------------------------------------------------------------
    def control(self, did: Any, action: str, strip: Any = None, value: Any = None) -> str:
        d = self.desk(did)
        if d is None:
            return "No such desk."
        if not d.driver.connected():
            return drivers.NOT_CONNECTED
        if action == "scene":
            n = value if isinstance(value, int) and not isinstance(value, bool) else 0
            if not 1 <= n <= d.mirror.model.scenes:
                return "No such scene on this desk."
            said = d.driver.recall(n)
            if not said:
                d.grace_until, d.rebase = time.time() + RELAY_GRACE_S, True
                self.expect(d, "", scene=n)
                self._watch_soon()
            return said
        if not isinstance(strip, str) or strip not in d.mirror.strips:
            return drivers.NO_SUCH_STRIP
        if action == "mute":
            if not isinstance(value, bool):
                return "Say whether to mute."
            said = d.driver.mute(strip, value)
            if not said:
                self.expect(d, strip, muted=value)
            return said
        if action == "level":
            db = clamp_db(value)
            return "Give a level in dB." if db is None else d.driver.level(strip, db)
        if action == "name":
            if not d.driver.can_name():
                return drivers.NO_NAMES
            text = clean_name(value, d.mirror.model.name_len)
            if not text:
                return "Write a name the desk can show (plain letters and numbers)."
            said = d.driver.name(strip, text)
            if not said:
                self.expect(d, strip, name=text)
            return said
        if action == "colour":
            if not d.mirror.model.colours or value not in COLOURS:
                return "Pick one of the desk's colours."
            return d.driver.colour(strip, value)
        return "That does nothing."

    # -- watchdog --------------------------------------------------------------------------------------------------------
    def baseline(self, did: str) -> dict | None:
        return settings()["baselines"].get(did)

    def save_baseline(self, did: str) -> dict | None:
        d = self.desk(did)
        if d is None or not d.driver.connected():
            return None
        names = {k: s.name for k, s in d.mirror.strips.items() if s.name is not None}
        mutes = {k: s.muted for k, s in d.mirror.strips.items() if s.muted is not None}
        b = {"names": names, "mutes": mutes, "scene": d.mirror.scene, "saved_at": time.time()}
        save({"baselines": {**settings()["baselines"], did: b}})
        d.told.clear()
        self._bump()
        return b

    def expect(self, d: Desk, strip: str, **what: Any) -> None:
        """Something Relay itself did: the baseline follows it, so the watchdog does not page about it."""
        b = self.baseline(d.id)
        if b is None:
            return
        b = {**b, "names": dict(b["names"]), "mutes": dict(b["mutes"])}
        if "name" in what:
            b["names"][strip] = what["name"]
        if "muted" in what:
            b["mutes"][strip] = what["muted"]
        if "scene" in what:
            b["scene"] = what["scene"]
        save({"baselines": {**settings()["baselines"], d.id: b}})

    def differences(self, d: Desk) -> list[dict]:
        """What differs from the saved baseline now: [{"id", "text"}]. Unknown values are not differences."""
        b = self.baseline(d.id)
        w = settings()["watchdog"]
        if b is None or not d.driver.connected():
            return []
        out = []
        if w["scene"] and b["scene"] is not None and d.mirror.scene is not None and d.mirror.scene != b["scene"]:
            name = d.mirror.scene_name()
            out.append({"id": f"scene:{d.mirror.scene}",
                        "text": f"scene {d.mirror.scene}{' ' + name if name else ''} is recalled (saved: {b['scene']})"})
        if w["names"]:
            for k, was in b["names"].items():
                now = d.mirror.strips[k].name if k in d.mirror.strips else None
                if now is not None and now != was:
                    out.append({"id": f"name:{k}:{now}", "text": f"{default_name(k)} is named “{now}” "
                                f"(saved: “{was}”)"})
        for ref in w["mutes"]:
            did, _, k = ref.partition(":")
            if did != d.id or k not in b["mutes"]:
                continue
            now = d.mirror.strips[k].muted
            if now is not None and now != b["mutes"][k]:
                out.append({"id": f"mute:{k}:{now}", "text": f"{d.mirror.strips[k].shown()} is "
                            f"{'muted' if now else 'unmuted'} (saved: {'muted' if b['mutes'][k] else 'unmuted'})"})
        return out

    def _watch_soon(self) -> None:
        if self._watch_task is None or self._watch_task.done():
            try:
                self._watch_task = asyncio.get_running_loop().create_task(self._watch_later())
            except RuntimeError:
                pass

    async def _watch_later(self) -> None:
        await asyncio.sleep(WATCH_SETTLE_S)
        while True:
            await self.watch()
            wait = max((d.grace_until for d in self.desks.values()), default=0.0) - time.time()
            if wait <= 0:
                return
            await asyncio.sleep(wait + 0.5)

    async def watch(self) -> list[str]:
        """Compare every desk with its baseline and notify the chosen person of new differences. The messages sent."""
        w = settings()["watchdog"]
        sent = []
        for d in list(self.desks.values()):
            if time.time() < d.grace_until:
                continue
            if d.rebase:
                d.rebase = False
                if self.baseline(d.id) is not None:
                    await asyncio.to_thread(self.save_baseline, d.id)
                continue
            diffs = self.differences(d)
            new = [x for x in diffs if x["id"] not in d.told]
            d.told = {x["id"] for x in diffs}        # one that clears and comes back is sent again
            if not new or not w["on"] or not w["user_id"]:
                continue
            notify = modules.using("crew.notify")
            text = f"{d.cfg['name']}: " + "; ".join(x["text"] for x in new[:3])
            if len(new) > 3:
                text += f"; and {len(new) - 3} more"
            text = text[:140]
            if notify is None:
                log.warning("audio mixers watchdog: %s (crew is off, nobody notified)", text)
                continue
            try:
                await asyncio.to_thread(notify, text, [w["user_id"]])
                sent.append(text)
            except Exception:
                log.exception("audio mixers watchdog could not notify")
        return sent

    # -- mic names from Planning Center -------------------------------------------------------------------------------
    def name_preview(self) -> dict:
        """What the next service's mics would name each desk channel: {"rows": [...], "problem": sentence or ""}."""
        from ... import channels

        rundown = modules.using("plan.rundown_now")
        if rundown is None:
            return {"rows": [], "problem": "Turn on Planning Center to name channels from the plan."}
        try:
            msg = rundown()
        except Exception:
            log.exception("audio mixers: rundown failed")
            return {"rows": [], "problem": "Planning Center did not answer."}
        style = settings()["names"]["style"]
        rows, seen = [], set()
        for m in msg.get("mics") or []:
            person = (m.get("person") or {}).get("name") or ""
            refs = [m.get("channel_ref")] + list(m.get("channel_refs") or [])
            for ref in [r for r in refs if isinstance(r, str) and r]:
                try:
                    ch = channels.by_ref(ref)
                except Exception:
                    ch = None
                for e in (ch or {}).get("ends") or []:
                    if e.get("provider") != KEY or e.get("missing"):
                        continue
                    got = self._end(e["key"])
                    if got is None or e["key"] in seen:
                        continue
                    seen.add(e["key"])
                    d, strip = got
                    proposed = clean_name(_style(person, style), d.mirror.model.name_len) if person else ""
                    rows.append({"key": e["key"], "desk": d.cfg["name"], "strip": strip, "mic": m.get("mic") or "",
                                 "position": m.get("position") or "", "person": person,
                                 "now": d.mirror.strips[strip].name, "proposed": proposed,
                                 "can": d.driver.connected() and d.driver.can_name()})
        plan = msg.get("plan") or {}
        problem = "" if rows else ("No mics on the next plan are on a desk's channels yet. Pick desk channels on the "
                                   "Planning Center page's Team and mics tab.")
        return {"rows": rows, "problem": problem, "plan": plan.get("title") or plan.get("dates") or ""}

    def push_names(self, rows: Any) -> dict:
        """Send the names the operator confirmed: [{"key", "name"}]. {"sent": n, "problems": [sentences]}."""
        sent, problems = 0, []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            try:
                self.rename(str(r.get("key") or ""), str(r.get("name") or ""))
                sent += 1
            except ValueError as e:
                if str(e) not in problems:
                    problems.append(str(e))
        return {"sent": sent, "problems": problems}

    # -- what pages and boards read -------------------------------------------------------------------------------------
    def snapshot(self, meters: bool = True) -> dict:
        out = []
        for d in self.desks.values():
            model = d.mirror.model
            pub = d.mirror.public()
            if not meters:
                for s in pub["strips"]:
                    s.pop("meter", None)
            out.append({"id": d.id, "name": d.cfg["name"], "model": model.id, "label": model.label,
                        "host": d.cfg["host"], "connected": d.driver.connected(), "error": d.driver.error,
                        "tested": model.id in TESTED, "can_name": d.driver.can_name(), "can_send": d.driver.can_send,
                        "name_len": model.name_len, "scenes": model.scenes, "colours": model.colours,
                        "meters": model.meters, "ahnet": getattr(d.driver, "ahnet_state", "off"),
                        "gear_id": d.gear_id, "baseline_at": (self.baseline(d.id) or {}).get("saved_at") or 0,
                        "differences": self.differences(d), **pub})
        return {"version": self.version, "desks": out}

    def mic_lines(self) -> list[str]:
        """Mic states for screens and panels: each named input, live or muted."""
        lines = []
        for d in self.desks.values():
            for k, s in d.mirror.strips.items():
                if k.startswith("in") and s.name:
                    state = "?" if s.muted is None else ("MUTED" if s.muted else "live")
                    lines.append(f"{s.name[:12]:<12} {state}" + (f"  {d.cfg['name']}" if len(self.desks) > 1 else ""))
        return lines

    def wall_lines(self, did: str, kinds: tuple[str, ...] = KINDS) -> list[str]:
        d = self.desk(did) or next(iter(self.desks.values()), None)
        if d is None:
            return ["No desk set up"]
        if not d.driver.connected():
            return [f"{d.cfg['name']}: not connected"]
        out = []
        for k, s in d.mirror.strips.items():
            if parse_strip(k)[0] in kinds:  # type: ignore[index]
                mute = "?" if s.muted is None else ("M" if s.muted else " ")
                out.append(f"{mute} {s.shown()[:12]:<12} {db_text(s.db):>6}")
        return out


def _style(person: str, style: str) -> str:
    parts = person.split()
    if not parts:
        return ""
    if style == "full":
        return person
    if style == "last":
        return parts[-1]
    return parts[0]


service = Service()
