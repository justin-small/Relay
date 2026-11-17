"""YouTube live status (#252): whether the church's channel is live, since when, and how many are watching.

Relay signs in to the channel with core's sign-in (`app/oauth.py`, #304: through relay-cloud with Relay's Google app,
or a device code with the church's own) and reads it with the read-only scope.
Every check costs quota from one daily allowance (10,000 units) that every Relay install shares through Relay's Google
app, so it asks rarely: every `live_s` seconds while YouTube or another stream Relay follows (OBS, say) is on air,
and every `idle_s` seconds otherwise. A check is one `liveBroadcasts.list` (1 unit), plus one `videos.list` (1 unit)
while something is live, for the viewer count.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from typing import Any

from ... import config, oauth
from ...streamstatus import streams

log = logging.getLogger("relay.youtube")

KEY = "youtube"
UNREACHABLE = "Relay could not reach YouTube or got no answer. It will try again."
SOURCE = "youtube"
API = "https://www.googleapis.com/youtube/v3"
SCOPE = "https://www.googleapis.com/auth/youtube.readonly"

LIVE_S = (30, 600, 60)          # (least, most, default) seconds between checks while something is on air
IDLE_S = (120, 3600, 600)       # and otherwise
DEFAULTS = {"account": "", "client_id": "", "client_secret": "", "live_s": LIVE_S[2], "idle_s": IDLE_S[2]}


def _clamp(v: Any, bounds: tuple[int, int, int]) -> int:
    lo, hi, default = bounds
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def clean_settings(raw: Any) -> dict:
    raw = raw if isinstance(raw, dict) else {}
    cid = raw.get("client_id")
    secret = raw.get("client_secret")
    account = raw.get("account")
    return {
        "account": account if isinstance(account, str) else "",
        "client_id": cid.strip()[:200] if isinstance(cid, str) else "",
        "client_secret": secret.strip()[:200] if isinstance(secret, str) else "",
        "live_s": _clamp(raw.get("live_s"), LIVE_S),
        "idle_s": _clamp(raw.get("idle_s"), IDLE_S),
    }


def public(settings: dict) -> dict:
    s = dict(settings)
    s.pop("account", None)
    s["client_secret_set"] = bool(s.pop("client_secret", ""))
    return s


def settings() -> dict:
    return clean_settings(config.get().get(KEY))


def client() -> oauth.Client:
    """The church's own Google app, when it set one ("TVs and Limited Input devices" client, device sign-in).
    Otherwise empty: Relay's own app, through relay-cloud (docs/reference/oauth-apps.md)."""
    s = settings()
    return oauth.Client(s["client_id"], s["client_secret"])


class Google(oauth.Provider):
    id, name = "google", "YouTube"
    cloud = "google"
    device_url = "https://oauth2.googleapis.com/device/code"
    token_url = "https://oauth2.googleapis.com/token"
    revoke_url = "https://oauth2.googleapis.com/revoke"

    async def identity(self, tokens: oauth.Tokens) -> dict:
        status, d = await self.call(f"{API}/channels?part=snippet&mine=true", token=tokens.access_token)
        items = d.get("items") if status == 200 else None
        if not isinstance(items, list):
            log.warning("youtube: reading the channel failed (HTTP %s)", status)
            raise oauth.Problem("YouTube did not say which channel this is. Try again.")
        if not items:
            raise oauth.Problem("That Google account has no YouTube channel. Sign in with the church's channel.")
        ch = items[0] if isinstance(items[0], dict) else {}
        return {"id": str(ch.get("id") or ""), "name": str((ch.get("snippet") or {}).get("title") or "")[:120]}


PROVIDER = Google("youtube")
ACCOUNT = oauth.Account("youtube", PROVIDER, settings_key=KEY, client=client, scopes=(SCOPE,),
                        on_change=lambda: service.poke())


def _when(text: Any) -> float | None:
    if not isinstance(text, str) or not text:
        return None
    try:
        return dt.datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _quota_reset(now: float) -> float:
    """YouTube's daily quota resets at midnight Pacific time; a little after it, to be safe."""
    utc = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    pacific = utc - dt.timedelta(hours=8)          # PST; during PDT this waits one hour longer, which is harmless
    midnight = (pacific + dt.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    return (midnight + dt.timedelta(hours=8)).timestamp()


def _reasons(d: dict) -> set[str]:
    err = d.get("error")
    errs = err.get("errors") if isinstance(err, dict) else None
    return {str(e.get("reason")) for e in errs if isinstance(e, dict)} if isinstance(errs, list) else set()


class Service:
    """Checks the channel in the background while the module is on, and publishes one stream status entry."""

    def __init__(self) -> None:
        self.task: asyncio.Task | None = None
        self.wake: asyncio.Event | None = None
        self.broadcasts: list[dict] = []
        self.problem = ""               # a fixed sentence, or ""
        self.checked_at = 0.0
        self.quota_until = 0.0

    # ---------------------------------------------------------------- lifecycle
    async def start(self) -> None:
        if self.task and not self.task.done():
            return
        self.wake = asyncio.Event()
        self.task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        task, self.task = self.task, None
        if task:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass
        await ACCOUNT.close()
        self.broadcasts, self.problem = [], ""
        streams.clear(SOURCE)

    def poke(self) -> None:
        """Check soon (connected, settings changed)."""
        if self.wake:
            self.wake.set()

    async def _run(self) -> None:
        while True:
            try:
                await self.check()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("youtube: check failed")
                self.problem = "Relay could not check YouTube. It will try again."
            assert self.wake is not None
            self.wake.clear()
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=self.next_wait())
            except asyncio.TimeoutError:
                pass

    def live(self) -> bool:
        return any(b["live"] for b in self.broadcasts)

    def next_wait(self, now: float | None = None) -> float:
        now = now or time.time()
        if self.quota_until > now:
            return self.quota_until - now
        s = settings()
        others = any(e.get("on_air") for e in streams.entries() if e["source"] != SOURCE)
        return float(s["live_s"] if self.live() or others else s["idle_s"])

    # ---------------------------------------------------------------- one check
    async def check(self) -> None:
        now = time.time()
        if not ACCOUNT.connected():
            self.broadcasts, self.problem = [], ""
            streams.clear(SOURCE)
            return
        if self.quota_until > now:
            return
        try:
            token = await ACCOUNT.token()
            status, d = await PROVIDER.call(f"{API}/liveBroadcasts?part=id,snippet,status&broadcastStatus=active"
                                            "&broadcastType=all&maxResults=5", token=token)
            if status == 200:
                found = self._broadcasts(d)
                if any(b["live"] for b in found):
                    await self._viewers(found, token)
                self.broadcasts, self.problem, self.checked_at = found, "", now
            else:
                self._refused(status, d, now)
        except oauth.NeedsSignIn:
            self.broadcasts, self.problem = [], ""
        except oauth.Problem:
            # A fixed sentence of our own: the exception's text never reaches a page (CodeQL stack-trace exposure).
            self.problem = UNREACHABLE
        self.publish()

    def _broadcasts(self, d: dict) -> list[dict]:
        out = []
        for it in d.get("items") or []:
            if not isinstance(it, dict) or not isinstance(it.get("id"), str):
                continue
            sn, st = it.get("snippet") or {}, it.get("status") or {}
            life = str(st.get("lifeCycleStatus") or "")
            vid = it["id"][:40]
            out.append({"id": vid, "title": str(sn.get("title") or "")[:200], "state": life[:20],
                        "live": life == "live", "started": _when(sn.get("actualStartTime")), "viewers": None,
                        "privacy": str(st.get("privacyStatus") or "")[:20],
                        "watch": f"https://www.youtube.com/watch?v={vid}",
                        "studio": f"https://studio.youtube.com/video/{vid}/livestreaming"})
        return out

    async def _viewers(self, found: list[dict], token: str) -> None:
        ids = ",".join(b["id"] for b in found if b["live"])
        status, d = await PROVIDER.call(f"{API}/videos?part=liveStreamingDetails&id={ids}", token=token)
        if status != 200:
            return
        by_id = {it.get("id"): it.get("liveStreamingDetails") or {} for it in d.get("items") or [] if isinstance(it, dict)}
        for b in found:
            det = by_id.get(b["id"]) or {}
            try:
                b["viewers"] = int(det["concurrentViewers"])
            except (KeyError, TypeError, ValueError):
                pass
            b["started"] = b["started"] or _when(det.get("actualStartTime"))

    def _refused(self, status: int, d: dict, now: float) -> None:
        reasons = _reasons(d)
        if status == 401:
            ACCOUNT.refused()
            self.problem = "YouTube refused Relay's sign-in. Relay will try again."
        elif status == 403 and reasons & {"quotaExceeded", "dailyLimitExceeded", "rateLimitExceeded"}:
            self.quota_until = _quota_reset(now)
            self.problem = "YouTube's daily allowance for Relay is used up. Live status comes back after midnight Pacific time."
        elif status == 403 and "insufficientPermissions" in reasons:
            self.problem = "YouTube did not give Relay permission to read the channel. Press Connect again."
        elif status == 403 and reasons & {"liveStreamingNotEnabled", "forbidden"}:
            self.broadcasts = []
            self.problem = "This channel cannot go live yet (live streaming is not switched on in YouTube Studio)."
        else:
            log.warning("youtube: liveBroadcasts answered HTTP %s %s", status, sorted(reasons)[:3])
            self.problem = "YouTube did not answer as expected. Relay will try again."

    # ---------------------------------------------------------------- what others read
    def publish(self) -> None:
        if not ACCOUNT.connected():
            streams.clear(SOURCE)
            return
        live = [b for b in self.broadcasts if b["live"]]
        level, _ = self.health()
        viewers = [b["viewers"] for b in live if isinstance(b["viewers"], int)]
        starts = [b["started"] for b in live if b["started"]]
        detail = "; ".join(f"{b['title']} ({b['state']})" for b in self.broadcasts) or "No live broadcast"
        streams.publish(SOURCE, label="YouTube", on_air=bool(live), health=level,
                        live_since=min(starts) if starts else None, viewers=sum(viewers) if viewers else None,
                        detail=detail)

    def health(self) -> tuple[str, str]:
        warn = ACCOUNT.health()
        if warn:
            return warn
        if not ACCOUNT.connected():
            return "idle", "Not connected. Press Connect on the YouTube page."
        if self.problem:
            return "warn", self.problem
        live = [b for b in self.broadcasts if b["live"]]
        if live:
            return "ok", "Live: " + live[0]["title"]
        return "idle", "Connected; not live."

    def snapshot(self) -> dict:
        level, detail = self.health()
        return {"health": {"level": level, "detail": detail}, "broadcasts": self.broadcasts, "live": self.live(),
                "checked_at": self.checked_at or None, "settings": public(settings()),
                "account": ACCOUNT.status()}


service = Service()
