"""Plan files and announcements as signage items (#209): the files attached to the plan Relay follows, and optionally
a text slide for each plan item, kept in the signage media library and in one signage playlist.

Relay follows one chosen service type's current plan (today's, or the next one coming, as the follower picks it). Each
pass lists the files attached to the plan and to its items, copies the pictures and videos signage can play into the
media library under a name that starts with `PREFIX`, and (if the operator asked for it) rewrites the signage playlist
`PLAYLIST_NAME` from them. A plan item's title, with one of its notes, can be a text slide too: a signage source of
kind `KIND` that draws the words as a page. The operator can limit all of it to plan items whose title holds a word,
such as "Announcements".

It looks again every `CHECK_S` seconds and when the operator presses Sync now; a changed plan changes the next look.
A file whose Planning Center `updated_at` has not changed is not downloaded again. Pictures and texts nothing wants
any more are deleted, but only a library row whose name starts with `PREFIX`: a file an operator uploaded or renamed
is never touched. Nothing is fetched while Planning Center is unreachable or signage is off; what is stored keeps
playing. Off until the operator switches it on.
"""
from __future__ import annotations

import asyncio
import hashlib
import html
import json
import logging
import os
import re
import time
import urllib.parse
from typing import Any

from ... import config, paths
from . import planningcenter as pc
from .pclive import Follower, follower as pc_follower

log = logging.getLogger("relay.pcsignage")

PREFIX = "Planning Center: "
PLAYLIST_NAME = "Planning Center plan"
KIND = "pc_text"
CHECK_S = 300.0
ITEMS_MAX = 60              # plan items whose attachments Relay lists in one pass (one request each)
SLIDES_MAX = 150
DURATION_DEFAULT_S = 10
TEXT_DURATION_S = 12
MESSAGES = {
    "no_type": "Pick the service type first.",
    "not_followed": "That service type is not one Relay follows. Follow it above first.",
    "not_connected": "Planning Center is not connected.",
    "off": "Signage is switched off.",
    "no_plan": "That service type has no current or upcoming plan.",
    "too_many": f"That plan has more than {SLIDES_MAX} files and slides, more than Relay will copy.",
    "failed": "Planning Center did not give Relay the plan's files; it will try again.",
    "store": "Relay could not save the files into signage.",
}
PLAYABLE = {"image/jpeg": "jpg", "image/jpg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif",
            "video/mp4": "mp4", "video/webm": "webm"}


def default_signage() -> dict[str, Any]:
    return {"enabled": False, "service_type": "", "plan_files": True, "item_files": True, "text_slides": False,
            "note_category": "", "item_match": "", "playlist": False}


def signage_fields(raw: object) -> tuple[dict[str, Any], str]:
    """The settings `raw` sets, each checked, with a fixed sentence for the first thing wrong. A key left out stays out."""
    raw = raw if isinstance(raw, dict) else {}
    out: dict[str, Any] = {}
    problem = ""
    for key in ("enabled", "plan_files", "item_files", "text_slides", "playlist"):
        if raw.get(key) is not None:
            out[key] = raw[key] is True
    if raw.get("service_type") is not None:
        v = str(raw["service_type"]).strip()
        if v and not re.fullmatch(r"\d{1,20}", v):
            problem = "That is not a Planning Center service type."
        else:
            out["service_type"] = v
    for key, what in (("note_category", "note category"), ("item_match", "plan item word")):
        if raw.get(key) is not None:
            v = " ".join(str(raw[key]).split())
            if len(v) > 80:
                problem = problem or f"The {what} is too long."
            else:
                out[key] = v
    return out, problem


def clean_signage_settings(raw: object, base: dict[str, Any]) -> dict[str, Any]:
    return {**default_signage(), **base, **signage_fields(raw)[0]}


# -- the text slides ---------------------------------------------------------------------------------------
def _text_file():
    return paths.state_dir() / "planning_center" / "signage.json"


class Memory:
    """What survives a restart: which Planning Center file became which library picture, and the words of each
    text slide (a signage item names a text only by its short id)."""

    def __init__(self) -> None:
        self.data: dict[str, dict] | None = None

    def load(self) -> dict[str, dict]:
        if self.data is None:
            try:
                raw = json.loads(_text_file().read_text("utf-8"))
            except (OSError, ValueError):
                raw = {}
            raw = raw if isinstance(raw, dict) else {}
            self.data = {"files": raw.get("files") if isinstance(raw.get("files"), dict) else {},
                         "texts": raw.get("texts") if isinstance(raw.get("texts"), dict) else {}}
        return self.data

    def save(self) -> None:
        path = _text_file()
        paths.make_private_dir(path.parent)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.load()), "utf-8")
        os.replace(tmp, path)

    def forget(self) -> None:
        self.data = None


memory = Memory()


def text_page(title: str, body: str) -> str:
    """The words of one text slide as a full-screen page. Everything typed in Planning Center is escaped."""
    t = html.escape(title)
    b = "".join(f"<p>{html.escape(line)}</p>" for line in body.splitlines() if line.strip())
    return ("<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            "<style>html,body{margin:0;height:100%;background:#0b1220;color:#f8fafc;font-family:system-ui,sans-serif}"
            "main{box-sizing:border-box;height:100%;display:flex;flex-direction:column;justify-content:center;"
            "align-items:center;text-align:center;padding:6vh 8vw;gap:3vh}"
            "h1{font-size:7vw;margin:0;line-height:1.1}p{font-size:3.6vw;margin:0;color:#cbd5e1;line-height:1.3}"
            f"</style><main><h1>{t}</h1>{b}</main>")


def _text_for(ref: str) -> dict | None:
    row = memory.load()["texts"].get(ref)
    return row if isinstance(row, dict) else None


def source_resolve(ref: str) -> dict | None:
    row = _text_for(ref)
    if row is None:
        return None
    page = text_page(str(row.get("title") or ""), str(row.get("body") or ""))
    return {"type": "page", "url": "data:text/html;charset=utf-8," + urllib.parse.quote(page)}


def source_describe(ref: str) -> dict:
    row = _text_for(ref)
    if row is None:
        return {"name": "Deleted slide", "thumb": "", "missing": True}
    return {"name": PREFIX + str(row.get("title") or ref), "thumb": ""}


def source_choices() -> list[dict]:
    return [{"ref": k, "name": PREFIX + str(v.get("title") or k), "thumb": ""}
            for k, v in memory.load()["texts"].items() if isinstance(v, dict)]


class SyncError(Exception):
    def __init__(self, key: str):
        super().__init__(key)
        self.key = key


class Sync:
    def __init__(self, follower: Follower | None = None) -> None:
        self.f = follower or pc_follower
        self.make_client = lambda: pc.client_from(config.get())   # a test swaps this
        self.rev = ""
        self.at = 0.0
        self.files = 0
        self.texts = 0
        self.plan_title = ""
        self.error = ""
        self.checked = 0.0
        self._lock: asyncio.Lock | None = None
        self._lock_loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task | None = None

    # -- lifecycle ---------------------------------------------------------------------------------------
    def start(self) -> None:
        self._task = asyncio.create_task(self._run())

    def cancel(self) -> None:
        if self._task is not None:
            self._task.cancel()
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.sync()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Plan files sync failed")
            await asyncio.sleep(CHECK_S)

    def _guard(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._lock_loop is not loop:
            self._lock, self._lock_loop = asyncio.Lock(), loop
        return self._lock

    @staticmethod
    def settings() -> dict[str, Any]:
        return config.get()["planning_center"].get("signage") or default_signage()

    @staticmethod
    def _signage_on() -> bool:
        from ... import modules

        return modules.modules.enabled("signage")

    # -- one pass ----------------------------------------------------------------------------------------
    async def sync(self, force: bool = False) -> dict:
        s = self.settings()
        if not s["enabled"]:
            self.error = ""
            return self.state()
        async with self._guard():
            self.checked = time.time()
            try:
                await self._pass(s, force)
                self.error = ""
            except SyncError as exc:
                self.error = exc.key
            except pc.PlanningCenterError as exc:
                log.info("Plan files sync: %s", exc)
                self.error = "failed"
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Plan files sync failed")
                self.error = "failed"
        return self.state()

    def _plan(self, st: str) -> tuple[dict, list[dict]]:
        if not st:
            raise SyncError("no_type")
        tr = self.f.tracks.get(st)
        if tr is None:
            raise SyncError("not_followed")
        plan = tr.plan or (tr.plans[0] if tr.plans else None)
        if plan is None:
            raise SyncError("no_plan")
        return plan, list(tr.items)

    @staticmethod
    def _wanted(items: list[dict], s: dict) -> list[dict]:
        word = s["item_match"].lower()
        return [i for i in items if i["type"] != "header" and (not word or word in i["title"].lower())]

    def _collect(self, client, st: str, plan: dict, items: list[dict], s: dict) -> list[dict]:
        """Everything the plan would put on screen, in order, as {"kind": "file" | "text", ...}. Runs in a thread."""
        wanted = self._wanted(items, s)
        out: list[dict] = []
        if s["item_files"]:
            for it in wanted[:ITEMS_MAX]:
                for a in client.item_attachments(st, plan["id"], it["id"]):
                    out.append({"kind": "file", "label": it["title"], **a})
        if s["text_slides"]:
            for it in wanted:
                if not it["title"].strip():
                    continue
                cat = s["note_category"].lower()
                body = next((n["content"] for n in it["notes"] if cat and n["category"].lower() == cat), "")
                out.append({"kind": "text", "key": hashlib.sha1(f"{st}:{it['id']}".encode()).hexdigest()[:16],
                            "title": it["title"].strip()[:120], "body": body.strip()[:600]})
        if s["plan_files"] and not s["item_match"]:
            for a in client.plan_attachments(st, plan["id"]):
                out.append({"kind": "file", "label": plan["title"] or "Plan", **a})
        # A file shown on two items is one file.
        seen: set[tuple] = set()
        keep = []
        for x in out:
            k = (x["kind"], x["id"] if x["kind"] == "file" else x["key"])
            if k not in seen:
                seen.add(k)
                keep.append(x)
        return keep

    async def _pass(self, s: dict, force: bool) -> None:
        plan, items = self._plan(s["service_type"])
        if not self._signage_on():
            raise SyncError("off")
        client = self.make_client()
        if client is None:
            raise SyncError("not_connected")
        loop = asyncio.get_running_loop()
        found = await loop.run_in_executor(None, self._collect, client, s["service_type"], plan, items, s)
        playable = [x for x in found if x["kind"] == "text" or (
            x["content_type"] in PLAYABLE and not x["linked"] and x["url"])]
        if len(playable) > SLIDES_MAX:
            raise SyncError("too_many")
        rev = hashlib.sha1(json.dumps([s, plan["id"], [(x["kind"], x.get("id") or x["key"], x.get("updated_at"),
                                                        x.get("title"), x.get("body")) for x in playable]]
                                      ).encode()).hexdigest()[:16]
        if rev == self.rev and not force:
            return
        try:
            refs = await self._store(client, playable, s)
        except SyncError:
            raise
        except pc.PlanningCenterError:
            raise
        except Exception:
            log.exception("Plan files sync could not store the files")
            raise SyncError("store") from None
        self.rev, self.at, self.plan_title = rev, time.time(), plan["title"] or plan["dates"] or "Plan"
        self.files = sum(1 for r in refs if r[0] == "media")
        self.texts = sum(1 for r in refs if r[0] == KIND)
        log.info("Plan files sync: %d file(s), %d text slide(s) from plan %s", self.files, self.texts, plan["id"])

    async def _download(self, client, x: dict):
        from ..signage import media

        limit = media.IMAGE_LIMIT if x["content_type"].startswith("image/") else media.VIDEO_LIMIT
        gen = client.stream_file(x["url"], limit)

        async def chunks():
            while True:
                chunk = await asyncio.to_thread(next, gen, None)
                if chunk is None:
                    return
                yield chunk
        try:
            return await media.receive(chunks())
        except media.Refused:
            return None

    async def _store(self, client, playable: list[dict], s: dict) -> list[tuple[str, str]]:
        from ..signage import store

        mem = memory.load()
        rows = {m["id"]: m for m in await asyncio.to_thread(store.media_list) if m["name"].startswith(PREFIX)}
        by_sha = {m["sha256"]: m for m in rows.values()}
        refs: list[tuple[str, str]] = []
        files: dict[str, dict] = {}
        texts: dict[str, dict] = {}
        for x in playable:
            if x["kind"] == "text":
                texts[x["key"]] = {"title": x["title"], "body": x["body"]}
                refs.append((KIND, x["key"]))
                continue
            known = mem["files"].get(x["id"])
            if known and known.get("updated") == x["updated_at"] and known.get("media") in rows:
                files[x["id"]] = known
                refs.append(("media", known["media"]))
                continue
            got = await self._download(client, x)
            if got is None:
                continue                                  # a type signage does not play, or too big
            sha, ext, size = got
            row = by_sha.get(sha)
            if row is None:
                name = f"{PREFIX}{x['label']} {x['name']}".strip()
                row = await asyncio.to_thread(store.media_add, name, sha, ext, size)
                by_sha[sha] = row
                rows[row["id"]] = row
            files[x["id"]] = {"updated": x["updated_at"], "media": row["id"]}
            refs.append(("media", row["id"]))
        mem["files"], mem["texts"] = files, texts
        await asyncio.to_thread(memory.save)
        await asyncio.to_thread(self._finish, refs, s["playlist"])
        return refs

    @staticmethod
    def _finish(refs: list[tuple[str, str]], playlist: bool) -> None:
        from ..signage import store

        if playlist:
            lists = [p for p in store.playlists() if p["name"] == PLAYLIST_NAME]
            pl = lists[0] if lists else store.playlist_create(PLAYLIST_NAME)
            keep = {}
            for it in (lists[0]["items"] if lists else []):
                keep.setdefault((it["source"], it["ref"]), it)
            items = []
            for source, ref in refs:
                old = keep.get((source, ref))
                items.append({"source": source, "ref": ref,
                              "duration_s": old["duration_s"] if old else (
                                  TEXT_DURATION_S if source == KIND else DURATION_DEFAULT_S),
                              "start_date": old["start_date"] if old else "",
                              "end_date": old["end_date"] if old else "",
                              "transition": old["transition"] if old else "fade",
                              "fit": old["fit"] if old else "fit"})
            store.playlist_set_items(pl["id"], items)
        # Pictures nothing wants any more and no playlist uses. media_delete refuses one a playlist still uses.
        wanted = {r for k, r in refs if k == "media"}
        for m in store.media_list():
            if m["name"].startswith(PREFIX) and m["id"] not in wanted:
                try:
                    store.media_delete(m["id"])
                except store.Invalid:
                    pass

    # -- state -------------------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        s = self.settings()
        return {"enabled": s["enabled"], "settings": s, "playlist_name": PLAYLIST_NAME, "plan": self.plan_title,
                "files": self.files, "texts": self.texts, "synced_at": self.at, "checked_at": self.checked,
                "error": MESSAGES.get(self.error, "") if self.error else "", "signage_on": self._signage_on()}


sync = Sync()
