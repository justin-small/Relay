"""The Planning Center link (#138, stage 6): ProPresenter and Planning Center looking at the same service.

Four things, all behind switches that start off:

* Matching: the current plan's items against what ProPresenter has (plmatch.py has the rules). Relay reads
  ProPresenter's libraries and playlists, caches them for a couple of minutes and shows the result, the unmatched
  items and the operator's own pairings in admin. Reading only.
* Setlist swap: on the operator's click, and only when not view-only, Relay builds a playlist it created and named
  "Relay - <plan date>" in the plan's order. It never writes to any other playlist: the one UUID Relay made is
  kept in the config (`link.owned`), and every write checks that UUID and the name against ProPresenter first.
* Follow the plan (`follow`): ProPresenter's live presentation is matched to a plan item and shown beside
  Planning Center's LIVE item in admin; the Service feed tells panels which item ProPresenter has up (`pp_item`).
  When they disagree both are shown. Lyrics reach panels through the lyrics feed as before: they are not put in
  the Service message, whose firmware parser reads by key name.
* ProPresenter drives Services LIVE (`drive`): once a presentation has been up `dwell_s` seconds (default 10), the
  matching Planning Center item is moved to with the same Next / Previous presses a panel uses
  (pccontrol.Control.drive). Never takes control: it moves LIVE only while Relay's own token already holds it, and
  says so in admin when it does not. One try per activation of a presentation; a quick skip through several
  restarts the wait. Going back to an earlier item needs `allow_back` and stops at the matched item.

Everything written to ProPresenter goes through propresenter.Client (view-only refuses it there).
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Callable

from ... import config
from . import plmatch, propresenter as pp
from .. import modules
from ..planning_center import planningcenter as pc
from .ppcontrol import _seg
from ..planning_center.pclive import follower as pc_follower
from .propresenter import follower as pp_follower

log = logging.getLogger("relay.pclink")


def _pc_on() -> bool:
    """Planning Center is usable: its module is on (it is a separate module, #157) and a token is saved."""
    return modules.enabled("planning_center") and pc.connected(config.get())

TICK_S = 0.5
CACHE_S = 120.0            # ProPresenter's lists are read again after this long while the link is on
RETRY_S = 20.0             # after a read failed
MAX_PLAYLISTS = 40         # playlists read for the match; ProPresenter can stall, so the work is bounded
MAX_LIBRARIES = 8
MAX_CANDIDATES = 6000
LOG_MAX = 30

# What the admin page says. Fixed sentences: nothing from an exception reaches them.
TEXT = {
    "advanced": "Moved Planning Center forward to \"{item}\" ({steps}).",
    "went_back": "Moved Planning Center back to \"{item}\" ({steps}).",
    "already": "Planning Center is already on \"{item}\".",
    "nobody": "Nobody holds LIVE control in Planning Center, so Relay did not move it. Relay never takes control by itself.",
    "someone_else": "{who} holds LIVE control in Planning Center, so Relay did not move it.",
    "behind": "\"{item}\" is behind Planning Center's current item, and going back is switched off.",
    "not_in_plan": "\"{item}\" is not after the current item in the plan.",
    "overshot": "Planning Center stepped past \"{item}\" and Relay stopped ({steps}).",
    "too_far": "\"{item}\" is too many steps away; Relay stopped after {steps}.",
    "lost_control": "Control was lost while moving, so Relay stopped ({steps}).",
    "unknown_item": "Planning Center's current item is not one Relay knows; it did not move.",
    "no_plan": "There is no plan followed for today, so there is nothing to move.",
    "not_connected": "Planning Center is not connected.",
    "unreachable": "Planning Center did not answer.",
    "refused": "Planning Center refused the request.",
    "no_match": "\"{pres}\" matches no item in the plan, so Planning Center was not moved.",
    "unread": "Could not read ProPresenter's lists to match \"{pres}\"; trying again.",
}
READ_ERRORS = {"network": "Could not reach ProPresenter.", "timeout": "ProPresenter did not answer in time.",
               "http": "ProPresenter refused a request.", "bad": "ProPresenter answered in a way Relay did not expect."}


# What the operator is told when the link refuses something: fixed sentences, looked up by key, so no exception text
# ever reaches the browser.
MESSAGES = {
    "pick_both": "Pick a plan item and a ProPresenter presentation.",
    "no_plan": "There is no plan to build a playlist from.",
    "nothing_matched": "No plan item is matched to a presentation yet, so there is nothing to put in a playlist.",
    "name_taken": "A playlist with today's Relay playlist name is already in ProPresenter and Relay did not create "
                  "it, so Relay will not change it. Rename or remove it there first.",
    "not_identified": "ProPresenter made the playlist but Relay could not tell which one it is, so it left it alone.",
    "not_ours": "Relay will only change a playlist it made itself.",
}


class LinkError(Exception):
    """Something the operator should be told: `key` names its fixed sentence in MESSAGES."""

    def __init__(self, key: str):
        super().__init__(MESSAGES[key])
        self.key = key


def _flatten(tree: Any, out: list[dict] | None = None) -> list[dict]:
    """Every playlist (not folders) in ProPresenter's playlist tree, in order."""
    out = [] if out is None else out
    for o in tree if isinstance(tree, list) else []:
        if not isinstance(o, dict):
            continue
        ident = o.get("id") if isinstance(o.get("id"), dict) else {}
        kind = o.get("field_type") or o.get("type") or "playlist"
        kids = o.get("children") if o.get("children") is not None else o.get("playlists")
        if kind == "group":
            _flatten(kids, out)
        else:
            out.append({"uuid": str(ident.get("uuid") or ""), "name": str(ident.get("name") or "").strip()})
    return out


def _entries(body: Any) -> list[dict]:
    """A playlist's items as the matcher wants them."""
    out = []
    for o in (body.get("items") if isinstance(body, dict) else None) or []:
        if not isinstance(o, dict):
            continue
        ident = o.get("id") if isinstance(o.get("id"), dict) else {}
        info = o.get("presentation_info") if isinstance(o.get("presentation_info"), dict) else {}
        kind = str(o.get("type") or "")
        out.append({"name": str(ident.get("name") or "").strip(), "type": kind, "pco": bool(o.get("is_pco")),
                    "uuid": str(info.get("presentation_uuid") or o.get("target_uuid") or "") if kind == "presentation" else ""})
    return out


class Link:
    def __init__(self) -> None:
        self.pp = pp_follower
        self.pc = pc_follower
        self.pc_control: Any = None                  # pccontrol.Control, set by the module (attach)
        self.on_change: Callable[[], None] | None = None   # the Service feed's refresh, set by the module (attach)
        self.clock = time.monotonic                  # a test swaps this
        self.now = time.time
        # what was read from ProPresenter
        self.pool: list[dict] = []                   # presentations {uuid, name, source}, best source first
        self.playlists: list[dict] = []              # [{uuid, name, items: [entries]}] (the church's own, read only)
        self.loaded_at = 0.0
        self.load_error = ""
        self.retry_at = 0.0
        self.loading = False
        self._rows_key: tuple | None = None
        self._rows: list[dict] = []
        self._synced_name = ""
        # follow
        self._pp_item: str | None = None
        # drive
        self.act_uuid: str | None = None
        self.act_name = ""
        self.act_n = 0
        self.act_since = 0.0
        self.fired = 0
        self._drive_on = False
        self._firing = False
        self._unread_at = 0.0
        self.log: deque[dict] = deque(maxlen=LOG_MAX)
        self.last: dict | None = None
        self._task: asyncio.Task | None = None
        self.pp.listeners.append(self._pp_changed)
        self.pc.listeners.append(self._pc_changed)

    # -- lifecycle --------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def _run(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Planning Center link tick failed")
            await asyncio.sleep(TICK_S)

    # -- settings and the plan ---------------------------------------------------------------------------
    @staticmethod
    def settings() -> dict[str, Any]:
        return pp.settings().get("link") or pp.default_link()

    def active(self) -> bool:
        s = self.settings()
        return bool((s["follow"] or s["drive"]) and pp.configured() and _pc_on())

    def track(self) -> tuple[str | None, Any]:
        """The service type and its track the link works on: the operator's pick, else the first with a plan."""
        want = self.settings().get("service_type")
        if want and want in self.pc.tracks:
            return want, self.pc.tracks[want]
        for st, tr in self.pc.tracks.items():
            if tr.plan is not None:
                return st, tr
        return None, None

    # -- reading ProPresenter ------------------------------------------------------------------------------
    async def refresh(self) -> None:
        """Read ProPresenter's libraries and playlists (reads only) and rebuild the pool of presentations. Raises
        ProError when ProPresenter cannot be read; the previous pool stays."""
        if self.loading:
            while self.loading:
                await asyncio.sleep(0.05)
            return
        self.loading = True
        try:
            client = self.pp._need_client()
            call = self.pp.call
            tree = await call(client.read, "/v1/playlists")
            owned = (self.settings().get("owned") or {}).get("uuid")
            lists = [p for p in _flatten(tree) if p["uuid"] and p["uuid"] != owned][:MAX_PLAYLISTS]
            playlists = []
            for p in lists:
                body = await call(client.read, f"/v1/playlist/{_seg(p['uuid'])}")
                playlists.append({**p, "items": _entries(body)})
            libs = await call(client.read, "/v1/libraries")
            pool: list[dict] = []
            for p in playlists:
                pool += [{"uuid": e["uuid"], "name": e["name"], "source": "playlist: " + p["name"]}
                         for e in p["items"] if e["type"] == "presentation" and e["uuid"]]
            for lib in (libs if isinstance(libs, list) else [])[:MAX_LIBRARIES]:
                lid = str((lib or {}).get("uuid") or "") if isinstance(lib, dict) else ""
                if not lid:
                    continue
                body = await call(client.read, f"/v1/library/{_seg(lid)}")
                pool += [{"uuid": str(o.get("uuid") or ""), "name": str(o.get("name") or "").strip(),
                          "source": "library: " + str(lib.get("name") or "")}
                         for o in (body.get("items") if isinstance(body, dict) else None) or []
                         if isinstance(o, dict) and o.get("uuid")]
            seen: set[str] = set()
            self.pool = [c for c in pool if not (c["uuid"] in seen or seen.add(c["uuid"]))][:MAX_CANDIDATES]
            self.playlists = playlists
            self.loaded_at = self.now()
            self.load_error = ""
            self._rows_key = None
        except pp.ProError as exc:
            log.info("Link: could not read ProPresenter (%s)", exc.kind)
            self.load_error = READ_ERRORS.get(exc.kind, "Could not read ProPresenter.")
            self.retry_at = self.clock() + RETRY_S
            raise
        except ValueError:
            self.load_error = "ProPresenter sent an id Relay could not use."
            self.retry_at = self.clock() + RETRY_S
            raise pp.ProError("bad", "bad id") from None
        finally:
            self.loading = False
        self._changed()

    # -- matching ---------------------------------------------------------------------------------------
    def rows(self) -> list[dict]:
        """The plan's items matched to presentations (cached until the plan, the pool or the settings change)."""
        st, tr = self.track()
        s = self.settings()
        items = [{"id": i["id"], "title": i["title"], "type": i["type"]} for i in (tr.items if tr else [])]
        key = (st, tr.plan["id"] if tr and tr.plan else None, tuple((i["id"], i["title"], i["type"]) for i in items),
               self.loaded_at, repr(s["mapping"]), s["threshold"])
        if key == self._rows_key:
            return self._rows
        best_rows, best_n, name = [], -1, ""
        # None is title matching alone; each playlist that came from Planning Center is tried as the synced one,
        # and the one the plan fits best wins (a tie keeps the earlier, so title matching wins a 0-0 tie).
        for p in [None] + [p for p in self.playlists if any(e["pco"] for e in p["items"])]:
            rows = plmatch.match_items(items, self.pool, mapping=s["mapping"], threshold=s["threshold"],
                                       synced=p["items"] if p else None)
            n = sum(1 for r in rows if r["method"] == "synced")
            if n > best_n:
                best_rows, best_n, name = rows, n, p["name"] if p else ""
        self._rows_key, self._rows, self._synced_name = key, best_rows, name if best_n > 0 else ""
        return self._rows

    def set_mapping(self, item: str, uuid: str, name: str = "") -> dict:
        """Pair a plan item title with a presentation (uuid), or remove the pairing (uuid empty). Saved in config."""
        s = dict(self.settings())
        key = plmatch.normalise(item)
        kept = [m for m in s["mapping"] if plmatch.normalise(m["item"]) != key]
        if uuid:
            kept.append({"item": " ".join(str(item).split())[:200], "uuid": uuid, "name": name})
        fields, problem = pp.link_fields({"mapping": kept})
        if problem or not key:
            raise LinkError("pick_both")
        self.save({"mapping": fields["mapping"]})
        return self.settings()

    def save(self, fields: dict) -> None:
        saved = config.get()["propresenter"]
        new = pp.clean_stored({**saved, "link": {**saved["link"], **fields}})
        config.save({"propresenter": new}, replace=("propresenter",))
        self._rows_key = None

    # -- follow the plan -----------------------------------------------------------------------------------
    def follow(self) -> dict:
        """ProPresenter's live presentation and Planning Center's LIVE item side by side."""
        st, tr = self.track()
        pres = self.pp.presentation if self.pp.up else None
        titles = {i["id"]: i["title"] for i in (tr.items if tr else [])}
        live = (tr.live if tr else None) or {}
        cur = (live.get("current") or {}).get("item")
        mine = plmatch.items_for(self.rows(), pres["uuid"], pres["name"]) if pres else []
        if mine and cur in mine:
            pick = cur
        else:   # the matching item nearest after Planning Center's, else the first
            order = [i["id"] for i in (tr.items if tr else [])]
            at = order.index(cur) if cur in order else -1
            after = [m for m in mine if m in order and order.index(m) > at]
            pick = after[0] if after else (mine[0] if mine else None)
        agree = None if not (pres and cur) else bool(pick and pick == cur)
        return {"service_type": st,
                "pp": {"uuid": pres["uuid"], "name": pres["name"], "song": self.pp.is_song(),
                       "slide": self.pp.current["text"]} if pres else None,
                "pp_item": {"id": pick, "title": titles.get(pick, "")} if pick else None,
                "pc_item": {"id": cur, "title": titles.get(cur, "")} if cur else None,
                "controller": (live.get("controller") or {}).get("name"),
                "agree": agree}

    def feed_extra(self, st: str) -> dict:
        """Added to the Service feed message for panels: which plan item ProPresenter has up. Nothing unless the
        plan is being followed and ProPresenter is up; lyrics come by the lyrics feed."""
        s = self.settings()
        mine, _ = self.track()
        if not (s["follow"] or s["drive"]) or st != mine or not self.pp.up:
            return {}
        f = self.follow()
        return {"pp_item": (f["pp_item"] or {}).get("id"), "pp_agree": f["agree"]}

    def _pp_changed(self) -> None:
        self._changed()

    def _pc_changed(self) -> None:
        self._changed()

    def _changed(self) -> None:
        """Tell the Service feed when the item ProPresenter has up changed."""
        if not self.active() or self.on_change is None:
            return
        try:
            item = (self.follow()["pp_item"] or {}).get("id")
        except Exception:
            log.exception("Planning Center link follow failed")
            return
        if item != self._pp_item:
            self._pp_item = item
            self.on_change()

    # -- ProPresenter drives Services LIVE --------------------------------------------------------------------
    def _wait_left(self, now: float) -> float:
        return max(0.0, float(self.settings()["dwell_s"]) - (now - self.act_since))

    async def tick(self, now: float | None = None) -> None:
        """One look at what ProPresenter has up. Cheap when nothing changed; the one place a LIVE move starts."""
        now = self.clock() if now is None else now
        s = self.settings()
        pres = self.pp.presentation if (self.pp.up and pp.configured()) else None
        uuid = pres["uuid"] if pres else None
        if uuid != self.act_uuid:
            self.act_uuid, self.act_name, self.act_since = uuid, (pres["name"] if pres else ""), now
            if uuid:
                self.act_n += 1       # a new activation: one try each, so a re-triggered slide of the same one is not
        on = bool(s["drive"] and pp.configured() and _pc_on())
        if on and not self._drive_on:
            self.act_since = now      # switched on with something already up: the wait starts now
        self._drive_on = on
        if self.active() and not self.loading and now >= self.retry_at and (
                not self.loaded_at or self.now() - self.loaded_at > CACHE_S) and self.pp.up:
            try:
                await self.refresh()
            except pp.ProError:
                pass
        if not on or uuid is None or self.fired == self.act_n or self._firing:
            return
        if now - self.act_since < float(s["dwell_s"]):
            return
        await self._fire(now)

    async def _fire(self, now: float) -> None:
        n, uuid, name = self.act_n, self.act_uuid, self.act_name
        self._firing = True
        try:
            st, tr = self.track()
            if tr is None or tr.plan is None or self.pc_control is None:
                self.fired = n
                return self._note("no_plan", name)
            if not self.loaded_at:
                if now < self.retry_at:
                    return
                try:
                    await self.refresh()
                except pp.ProError:
                    self._note("unread", name, final=False)
                    return
            if self.act_n != n:       # ProPresenter moved on while Relay was reading: this activation is over
                return
            targets = plmatch.items_for(self.rows(), uuid, name)
            if not targets and uuid not in {c["uuid"] for c in self.pool} and self.now() - self.loaded_at > 15:
                try:   # a presentation added since the last read: look again once before saying it matches nothing
                    await self.refresh()
                except pp.ProError:
                    pass
                if self.act_n != n:
                    return
                targets = plmatch.items_for(self.rows(), uuid, name)
            self.fired = n            # one try for this activation, whatever it comes to
            if not targets:
                return self._note("no_match", name)
            titles = {i["id"]: i["title"] for i in tr.items}
            order = [i["id"] for i in tr.items]
            cur = ((tr.live or {}).get("current") or {}).get("item")
            at = order.index(cur) if cur in order else -1
            near = next((t for t in targets if t in order and order.index(t) >= at), targets[0])
            res = await self.pc_control.drive(st, order, targets, bool(self.settings()["allow_back"]))
            self._note(res["result"], name, item=titles.get(near, ""), steps=res["steps"], who=res["who"])
        finally:
            self._firing = False

    def _note(self, code: str, pres: str, item: str = "", steps: int = 0, who: str = "", final: bool = True) -> None:
        text = TEXT.get(code, "Planning Center was not moved.").format(
            item=item or "that item", pres=pres, who=who or "Someone",
            steps=f"{steps} step{'s' if steps != 1 else ''}")
        entry = {"at": self.now(), "presentation": pres, "result": code, "text": text}
        self.last = entry
        if final:
            self.log.appendleft(entry)
        log.info("Link: %s -> %s", pres[:60], code)
        self._changed()

    # -- the setlist swap ---------------------------------------------------------------------------------------
    @staticmethod
    def playlist_name(day: Any) -> str:
        return f"{pp.OWNED_PREFIX}{day.isoformat() if day else 'plan'}"

    async def build_playlist(self) -> dict:
        """Make (or remake) the Relay-owned ProPresenter playlist for the followed plan: matched presentations in
        plan order, headers kept. Refuses any playlist Relay did not create. Writes go through the Client, so
        view-only is refused there before any request."""
        st, tr = self.track()
        if tr is None or tr.plan is None:
            raise LinkError("no_plan")
        client = self.pp._need_client()
        if client.view_only:
            raise pp.ViewOnly("build a playlist")
        await self.refresh()
        rows = self.rows()
        entries: list[dict] = []
        left_out: list[str] = []
        for r in rows:
            if r["type"] == "header":
                entries.append({"id": {"uuid": "", "name": r["title"], "index": len(entries)}, "type": "header",
                                "is_hidden": False, "is_pco": False})
            elif r["status"] == "matched" and r["presentation"]:
                u = r["presentation"]["uuid"]
                entries.append({"id": {"uuid": "", "name": r["presentation"]["name"] or r["title"], "index": len(entries)},
                                "type": "presentation", "is_hidden": False, "is_pco": False,
                                "presentation_info": {"presentation_uuid": u}, "target_uuid": u})
            else:
                left_out.append(r["title"])
        if not any(e["type"] == "presentation" for e in entries):
            raise LinkError("nothing_matched")
        name = self.playlist_name(tr.day)
        owned = self.settings().get("owned") or {}
        tree = _flatten(await self.pp.call(client.read, "/v1/playlists"))
        uuid = ""
        if owned.get("uuid") and any(p["uuid"] == owned["uuid"] and p["name"] == owned["name"] for p in tree) \
                and owned["name"] == name:
            uuid = owned["uuid"]
        elif any(p["name"] == name for p in tree):
            log.warning("Planning Center link: a playlist named %r is already in ProPresenter and is not Relay's", name)
            raise LinkError("name_taken")
        else:
            made = await self.pp.call(client.post, "/v1/playlists", {"name": name, "type": "playlist"})
            uuid = self._uuid_of(made)
            if not uuid:   # the answer did not say: find the playlist that was not there before
                after = _flatten(await self.pp.call(client.read, "/v1/playlists"))
                uuid = next((p["uuid"] for p in after if p["name"] == name and p["uuid"] not in {t["uuid"] for t in tree}), "")
            if not uuid:
                raise LinkError("not_identified")
            self.save({"owned": {"uuid": uuid, "name": name}})
        # The one write to an existing playlist: only ever the one Relay made.
        owned = self.settings().get("owned") or {}
        if not (uuid and owned.get("uuid") == uuid and owned.get("name", "").startswith(pp.OWNED_PREFIX)):
            raise LinkError("not_ours")
        await self.pp.call(client.put, f"/v1/playlist/{_seg(uuid)}", entries)
        back = await self.pp.call(client.read, f"/v1/playlist/{_seg(uuid)}")
        got = [e["uuid"] for e in _entries(back) if e["type"] == "presentation"]
        want = [e["target_uuid"] for e in entries if e["type"] == "presentation"]
        return {"playlist": {"uuid": uuid, "name": name}, "items": len(entries), "presentations": len(want),
                "verified": got == want, "left_out": left_out}

    @staticmethod
    def _uuid_of(body: Any) -> str:
        for o in (body, (body or {}).get("playlist") if isinstance(body, dict) else None):
            ident = o.get("id") if isinstance(o, dict) else None
            if isinstance(ident, dict) and ident.get("uuid"):
                return str(ident["uuid"])
        return ""

    # -- what admin shows -------------------------------------------------------------------------------------------
    def drive_status(self, now: float | None = None) -> dict:
        now = self.clock() if now is None else now
        s = self.settings()
        if not s["drive"]:
            return {"code": "off", "text": "ProPresenter does not move Planning Center. This is off."}
        if not pp.configured() or not _pc_on():
            return {"code": "unavailable", "text": "Needs both ProPresenter and Planning Center to be connected."}
        if not self.pp.up:
            return {"code": "pp_down", "text": "ProPresenter is not connected."}
        if self.act_uuid is None:
            return {"code": "idle", "text": "Nothing is up in ProPresenter."}
        if self.fired == self.act_n and self.last:
            return {"code": self.last["result"], "text": self.last["text"]}
        if self.last and self.last["result"] == "unread" and now < self.retry_at:
            return {"code": "unread", "text": self.last["text"]}
        left = self._wait_left(now)
        return {"code": "waiting", "text": f"\"{self.act_name}\" has been up {now - self.act_since:.0f} s; "
                                           f"Planning Center moves in {left:.0f} s if it stays."}

    def state(self) -> dict:
        st, tr = self.track()
        s = self.settings()
        rows = self.rows() if tr else []
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        cur = ((tr.live or {}).get("current") or {}).get("item") if tr else None
        for r in rows:
            r["current"] = bool(cur and r["item_id"] == cur)
        client = self.pp.client
        return {
            "settings": s,
            "service_types": [{"id": k, "name": self.pc.names.get(k) or f"Service type {k}"} for k in self.pc.tracks],
            "service_type": st,
            "plan": {"title": tr.plan["title"], "dates": tr.plan["dates"], "day": tr.day.isoformat() if tr.day else ""}
            if tr and tr.plan else None,
            "pc_connected": _pc_on(),
            "pp_connected": bool(pp.configured() and self.pp.up),
            "view_only": bool(client.view_only) if client else bool(pp.settings().get("view_only")),
            "rows": rows,
            "counts": counts,
            "synced_playlist": self._synced_name,
            "loaded": {"at": self.loaded_at or None, "presentations": len(self.pool), "playlists": len(self.playlists),
                       "error": self.load_error},
            "follow": self.follow() if self.pp.up and tr else None,
            "drive": {**self.drive_status(), "log": list(self.log)[:10], "dwell_s": s["dwell_s"]},
            "playlist_name": self.playlist_name(tr.day) if tr and tr.plan else "",
        }


link = Link()
