"""The intercom's watchdog (#276): checks what the intercom depends on every 5 s and keeps one card per check.

Checks: LiveKit, the Relay app (event-loop lag, memory, open files), the database, the Cloudflare bridge (only when
another module registers it, see `sources`), audio quality per phone, local round-trip latency and talk grant time.
LiveKit is the only thing it restarts, and that through the supervisor's own back-off (livekit.py): Relay never
restarts itself. The cards feed the module's Health, the admin top bar (at `error`) and the Health section of the
Intercom page. Each evaluator is a plain function of its measurements, so tests can force every state.

Audio is never recorded or logged here: a phone reports packet counts, jitter and its socket round trip, nothing else.
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import sys
import time
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any, Awaitable, Callable

from ... import db

log = logging.getLogger("relay.intercom")

EVERY_S = 5.0
LAG_TICK_S = 0.25
LAG_WINDOW_S = 5.0
LOSS_WINDOW_S = 10.0
GRANT_WINDOW_S = 300.0
AUDIO_WARN_EVERY_S = 300.0
LEVELS = {"idle": 0, "ok": 1, "warn": 2, "error": 3}

# key -> (default, low, high, label). Every threshold is a module setting with the issue's default.
THRESHOLDS: dict[str, tuple[float, float, float, str]] = {
    "lag_warn_ms": (50, 5, 5000, "Event-loop lag, warn above (ms)"),
    "lag_error_ms": (250, 10, 10000, "Event-loop lag, error above (ms)"),
    "lag_error_s": (15, 1, 300, "… held for (s)"),
    "rss_warn_mb": (1024, 64, 65536, "Relay memory, warn above (MB)"),
    "db_write_warn_ms": (50, 1, 5000, "Database write, warn above (ms)"),
    "wal_warn_mb": (64, 1, 4096, "Database WAL file, warn above (MB)"),
    "backup_warn_h": (26, 1, 720, "Last backup, warn when older than (h)"),
    "bridge_error_s": (120, 10, 3600, "Cloudflare bridge down, error after (s)"),
    "loss_warn_pct": (2, 0.1, 100, "Packet loss, warn above (%)"),
    "loss_error_pct": (5, 0.1, 100, "Packet loss, error above (%)"),
    "jitter_warn_ms": (30, 1, 1000, "Jitter, warn above (ms)"),
    "jitter_error_ms": (60, 1, 1000, "Jitter, error above (ms)"),
    "latency_warn_ms": (150, 10, 5000, "Round trip p95, warn above (ms)"),
    "grant_warn_ms": (100, 5, 5000, "Talk grant p95, warn above (ms)"),
}
DEFAULTS = {k: v[0] for k, v in THRESHOLDS.items()}


def clean_thresholds(raw: Any) -> dict:
    """The saved thresholds with anything missing, mistyped or out of range put back to its default."""
    raw = raw if isinstance(raw, dict) else {}
    out = {}
    for key, (default, low, high, _) in THRESHOLDS.items():
        v = raw.get(key)
        ok = isinstance(v, (int, float)) and not isinstance(v, bool) and low <= v <= high
        out[key] = (int(v) if float(v).is_integer() else float(v)) if ok else default
    return out


def thresholds() -> dict:
    from ... import config

    return clean_thresholds((config.get().get("intercom") or {}).get("thresholds"))


@dataclass
class Card:
    id: str
    title: str
    state: str = "idle"           # idle | ok | warn | error
    detail: str = ""
    facts: dict = field(default_factory=dict)


def worst(states) -> str:
    return max(states, key=lambda s: LEVELS.get(s, 0), default="idle")


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def percentile(values: list[float], p: float) -> float | None:
    """Nearest-rank percentile; None for no values."""
    if not values:
        return None
    s = sorted(values)
    return s[max(0, min(len(s) - 1, math.ceil(p / 100 * len(s)) - 1))]


# ------------------------------------------------------------------ evaluators (pure)

def eval_livekit(state: str, detail: str, fails: int, rooms: int | None = None,
                 participants: int | None = None) -> Card:
    """The supervisor's state. One failed check is a warning; the supervisor itself restarts after three and gives
    up (state `error`) after five restarts in ten minutes."""
    c = Card("livekit", "LiveKit", facts={"rooms": rooms, "participants": participants, "failed_checks": fails})
    if state == "running" and not fails:
        c.state = "ok"
        c.detail = "Running" + (f": {rooms} rooms, {participants} on" if rooms is not None else "")
    elif state == "running":
        c.state, c.detail = "warn", f"{fails} health check{'s' if fails != 1 else ''} failed"
    elif state in ("downloading", "starting", "restarting"):
        c.state, c.detail = "warn", detail or "Starting"
    elif state in ("missing", "error"):
        c.state, c.detail = "error", detail or "Not running"
    else:
        c.state, c.detail = "idle", "Not running"
    return c


def eval_app(t: dict, lag_ms: float | None, over_error_s: float, rss_mb: float | None,
             open_files: int | None = None) -> Card:
    """The Relay process. It only measures and warns: Relay never restarts itself."""
    c = Card("app", "Relay app", facts={"lag_ms": None if lag_ms is None else round(lag_ms, 1),
                                        "rss_mb": None if rss_mb is None else round(rss_mb),
                                        "open_files": open_files})
    if lag_ms is None:
        c.detail = "Measuring"
        return c
    if over_error_s >= t["lag_error_s"]:
        c.state = "error"
        c.detail = f"The server has been stalled over {t['lag_error_ms']:g} ms for {int(over_error_s)} s"
    elif lag_ms > t["lag_warn_ms"]:
        c.state, c.detail = "warn", f"Event loop {lag_ms:.0f} ms behind"
    elif rss_mb is not None and rss_mb > t["rss_warn_mb"]:
        c.state, c.detail = "warn", f"Using {rss_mb:.0f} MB of memory"
    else:
        c.state = "ok"
        c.detail = f"Loop {lag_ms:.0f} ms" + (f", {rss_mb:.0f} MB" if rss_mb is not None else "")
    return c


def eval_db(t: dict, ok: bool, write_ms: float | None, wal_mb: float | None, backup_age_h: float | None) -> Card:
    """The database. A failed write is an error, but local audio carries on from memory."""
    c = Card("database", "Database", facts={"write_ms": None if write_ms is None else round(write_ms, 1),
                                            "wal_mb": None if wal_mb is None else round(wal_mb, 1),
                                            "backup_age_h": None if backup_age_h is None else round(backup_age_h, 1)})
    if not ok:
        c.state = "error"
        c.detail = "Can't write to the database. Local audio carries on; changes and the log wait for it."
        return c
    problems = []
    if write_ms is not None and write_ms > t["db_write_warn_ms"]:
        problems.append(f"a write took {write_ms:.0f} ms")
    if wal_mb is not None and wal_mb > t["wal_warn_mb"]:
        problems.append(f"the WAL file is {wal_mb:.0f} MB")
    if backup_age_h is None:
        problems.append("no backup yet")
    elif backup_age_h > t["backup_warn_h"]:
        problems.append(f"the last backup is {backup_age_h:.0f} h old")
    if problems:
        text = "; ".join(problems)
        c.state, c.detail = "warn", text[0].upper() + text[1:]
    else:
        c.state, c.detail = "ok", f"Write {write_ms:.0f} ms" if write_ms is not None else "OK"
    return c


def eval_bridge(t: dict, state: str, detail: str, down_s: float, reachable: bool | None) -> Card:
    """The Cloudflare bridge (intercom_remote). It never touches local audio, whatever its state."""
    c = Card("bridge", "Cloudflare bridge", facts={"state": state, "down_s": int(down_s),
                                                   "cloud_reachable": reachable})
    if state == "connected":
        c.state, c.detail = "ok", "Connected"
        if reachable is False:
            c.state, c.detail = "warn", "Connected, but Relay's cloud did not answer the last check"
    elif state == "off":
        c.state, c.detail = "idle", "Not running"
    elif state == "missing":
        c.state, c.detail = "error", detail or "The bridge can't run on this computer"
    elif down_s >= t["bridge_error_s"]:
        c.state = "error"
        c.detail = f"Down for {int(down_s // 60)} min. Local audio is not affected."
    else:
        c.state, c.detail = "warn", (detail or "Reconnecting") + ". Local audio is not affected."
    return c


def phone_level(t: dict, loss_pct: float | None, jitter_ms: float | None) -> str:
    if (_num(loss_pct) and loss_pct > t["loss_error_pct"]) or (_num(jitter_ms) and jitter_ms > t["jitter_error_ms"]):
        return "error"
    if (_num(loss_pct) and loss_pct > t["loss_warn_pct"]) or (_num(jitter_ms) and jitter_ms > t["jitter_warn_ms"]):
        return "warn"
    return "ok" if _num(loss_pct) or _num(jitter_ms) else ""


def eval_audio(t: dict, phones: list[dict]) -> Card:
    """`phones`: [{"name", "loss_pct", "jitter_ms"}] over the last 10 s. The card is the worst phone."""
    c = Card("audio", "Audio quality", facts={"phones": len(phones)})
    rated = [(phone_level(t, p.get("loss_pct"), p.get("jitter_ms")), p) for p in phones]
    rated = [(lv, p) for lv, p in rated if lv]
    if not rated:
        c.detail = "No phone has reported yet"
        return c
    lv, p = max(rated, key=lambda r: (LEVELS[r[0]], r[1].get("loss_pct") or 0, r[1].get("jitter_ms") or 0))
    c.state = lv
    bits = []
    if _num(p.get("loss_pct")):
        bits.append(f"{p['loss_pct']:.1f} % loss")
    if _num(p.get("jitter_ms")):
        bits.append(f"{p['jitter_ms']:.0f} ms jitter")
    bad = sum(1 for r in rated if r[0] != "ok")
    c.detail = (f"{p['name']}: " + ", ".join(bits) + (f" ({bad} phones)" if bad > 1 else "")) if lv != "ok" else \
        f"{len(rated)} phone{'s' if len(rated) != 1 else ''} fine; worst " + ", ".join(bits)
    return c


def eval_latency(t: dict, rtts: list[float]) -> Card:
    p50, p95 = percentile(rtts, 50), percentile(rtts, 95)
    c = Card("latency", "Local latency", facts={"p50_ms": p50, "p95_ms": p95, "samples": len(rtts)})
    if p95 is None:
        c.detail = "No phone connected"
    elif p95 > t["latency_warn_ms"]:
        c.state, c.detail = "warn", f"Round trip p95 {p95:.0f} ms (p50 {p50:.0f} ms)"
    else:
        c.state, c.detail = "ok", f"Round trip p50 {p50:.0f} ms, p95 {p95:.0f} ms"
    return c


def eval_grant(t: dict, grants_ms: list[float]) -> Card:
    p50, p95 = percentile(grants_ms, 50), percentile(grants_ms, 95)
    c = Card("grant", "Talk grant time", facts={"p50_ms": p50, "p95_ms": p95, "samples": len(grants_ms)})
    if p95 is None:
        c.detail = "Nobody has pressed talk in the last 5 min"
    elif p95 > t["grant_warn_ms"]:
        c.state, c.detail = "warn", f"Press to grant p95 {p95:.0f} ms"
    else:
        c.state, c.detail = "ok", f"Press to grant p50 {p50:.0f} ms, p95 {p95:.0f} ms"
    return c


# ------------------------------------------------------------------ measurements

class LagMeter:
    """Event-loop lag: how late a short scheduled sleep wakes up. A blocked loop shows as one large sample."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.samples: deque[tuple[float, float]] = deque()     # (when, lag ms)
        self.over_since: float | None = None                  # first sample of the current run over the error bar
        self.error_ms = DEFAULTS["lag_error_ms"]

    def add(self, now: float, lag_ms: float) -> None:
        self.samples.append((now, lag_ms))
        while self.samples and now - self.samples[0][0] > 60:
            self.samples.popleft()
        if lag_ms > self.error_ms:
            if self.over_since is None:
                # the stall began when this sleep should have ended, not when it finally did
                self.over_since = now - lag_ms / 1000
        else:
            self.over_since = None

    def lag_ms(self, now: float) -> float | None:
        recent = [v for at, v in self.samples if now - at <= LAG_WINDOW_S]
        return max(recent) if recent else None

    def over_error_s(self, now: float) -> float:
        return 0.0 if self.over_since is None else now - self.over_since

    async def run(self) -> None:
        while True:
            start = self.clock()
            await asyncio.sleep(LAG_TICK_S)
            now = self.clock()
            self.add(now, max(0.0, (now - start - LAG_TICK_S) * 1000))


class LossTracker:
    """Packet loss as a percentage over the last 10 s, from the cumulative counts each phone reports every 5 s."""

    def __init__(self) -> None:
        self.hist: dict[int, deque[tuple[float, float, float, float | None]]] = {}   # device -> (t, lost, recv, jit)

    def add(self, device_id: int, stats: dict, now: float) -> None:
        lost, recv = stats.get("packets_lost"), stats.get("packets_received")
        if not _num(lost):
            return
        q = self.hist.setdefault(device_id, deque())
        if q and (lost < q[-1][1] or (_num(recv) and recv < q[-1][2])):
            q.clear()                    # the phone rejoined its rooms: counts start again
        q.append((now, float(lost), float(recv) if _num(recv) else 0.0, stats.get("jitter_ms")))
        while len(q) > 2 and now - q[1][0] >= LOSS_WINDOW_S:
            q.popleft()

    def view(self, device_id: int, now: float) -> tuple[float | None, float | None]:
        """(loss %, mean jitter ms) over the window; None where there is not enough to say."""
        q = [s for s in self.hist.get(device_id, ()) if now - s[0] <= LOSS_WINDOW_S + EVERY_S]
        jit = [s[3] for s in q if _num(s[3])]
        jitter = sum(jit) / len(jit) if jit else None
        if len(q) < 2:
            return None, jitter
        d_lost, d_recv = q[-1][1] - q[0][1], q[-1][2] - q[0][2]
        total = d_lost + d_recv
        return (round(100 * d_lost / total, 2) if total > 0 else None), jitter

    def forget(self, keep: set[int]) -> None:
        for k in [k for k in self.hist if k not in keep]:
            del self.hist[k]


def rss_mb() -> float | None:
    """Resident memory of this process, without psutil. macOS reports the peak (the closest it offers cheaply)."""
    try:
        if sys.platform.startswith("linux"):
            with open("/proc/self/statm", encoding="ascii") as f:
                return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2 ** 20
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                            ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                            ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t),
                            ("PeakPagefileUsage", ctypes.c_size_t)]

            c = Counters()
            c.cb = ctypes.sizeof(c)
            proc = ctypes.windll.kernel32.GetCurrentProcess()
            if ctypes.windll.psapi.GetProcessMemoryInfo(proc, ctypes.byref(c), c.cb):
                return c.WorkingSetSize / 2 ** 20
            return None
        import resource

        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 2 ** 20     # bytes on macOS
    except Exception:
        return None


def open_files() -> int | None:
    for d in ("/proc/self/fd", "/dev/fd"):
        try:
            return len(os.listdir(d))
        except OSError:
            continue
    return None


def _db_probe() -> tuple[bool, float | None, float | None, float | None]:
    """(ok, write ms, WAL MB, hours since the last backup). Runs in a worker thread."""
    from datetime import datetime, timezone

    from . import store

    wal = backup = None
    try:
        p = db.path()
        w = p.with_name(p.name + "-wal")
        wal = w.stat().st_size / 2 ** 20 if w.exists() else 0.0
    except OSError:
        pass
    try:
        taken = db.last_backup_at()
        backup = None if taken is None else (datetime.now(timezone.utc) - taken).total_seconds() / 3600
    except OSError:
        pass
    try:
        store.ready()
        db.one("select 1")
        t0 = time.perf_counter()
        db.execute("insert into intercom_watchdog (id, at) values (1, ?) on conflict (id) do update set at = "
                   "excluded.at", (db.now(),))
        return True, (time.perf_counter() - t0) * 1000, wal, backup
    except Exception:
        return False, None, wal, backup


# ------------------------------------------------------------------ the watchdog

# A check another module adds (intercom_remote adds the bridge): an async function of the thresholds that returns
# a Card, or None to hide it. Keyed by card id; the module removes its own when it stops.
Source = Callable[[dict], Awaitable["Card | None"]]
sources: dict[str, Source] = {}

ORDER = ("livekit", "app", "database", "bridge", "audio", "latency", "grant")


class Watchdog:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.cards: dict[str, Card] = {}
        self.lag = LagMeter(clock)
        self.loss = LossTracker()
        self.phone_levels: dict[int, tuple[float | None, float | None, str]] = {}   # device -> (loss %, jitter, lv)
        self._warned: dict[int, float] = {}
        self._tasks: list[asyncio.Task] = []
        self.probe: Callable[[], tuple] = _db_probe
        self.on_change: Callable[[], None] | None = None

    # -------------------------------------------------------------- life
    def begin(self) -> None:
        if self._tasks:
            return
        self._tasks = [asyncio.create_task(self.lag.run(), name="intercom-lag"),
                       asyncio.create_task(self._loop(), name="intercom-watchdog")]

    async def end(self) -> None:
        tasks, self._tasks = self._tasks, []
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self.cards.clear()
        self.phone_levels.clear()
        self.loss.hist.clear()
        self._warned.clear()

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except Exception:
                log.exception("intercom: watchdog pass failed")
            await asyncio.sleep(EVERY_S)

    # -------------------------------------------------------------- one pass
    async def tick(self) -> None:
        from .arbiter import arbiter
        from .livekit import supervisor
        from .presence import presence

        t = thresholds()
        now = self.clock()
        self.lag.error_ms = t["lag_error_ms"]
        cards: list[Card] = []

        rooms_n = people = None
        if supervisor.state == "running":
            rooms_n, people = await self._room_counts()
        cards.append(eval_livekit(supervisor.state, supervisor.detail, supervisor.fails, rooms_n, people))
        cards.append(eval_app(t, self.lag.lag_ms(now), self.lag.over_error_s(now), rss_mb(), open_files()))
        ok, write_ms, wal, backup = await asyncio.to_thread(self.probe)
        cards.append(eval_db(t, ok, write_ms, wal, backup))
        for key, src in list(sources.items()):
            try:
                got = await src(t)
            except Exception:
                log.exception("intercom: watchdog check %s failed", key)
                got = Card(key, key, "warn", "The check itself failed. See Relay's log.")
            if got is not None:
                cards.append(got)

        conns = list(presence.conns.values())
        phones, rtts = [], []
        self.loss.forget({c.device_id for c in conns})
        levels = {}
        for c in conns:
            s = c.stats or {}
            if s:
                self.loss.add(c.device_id, s, now)
            loss_pct, jitter = self.loss.view(c.device_id, now)
            lv = phone_level(t, loss_pct, jitter)
            levels[c.device_id] = (loss_pct, jitter, lv)
            if lv:
                phones.append({"name": c.name, "loss_pct": loss_pct, "jitter_ms": jitter})
            if lv in ("warn", "error"):
                await self._audio_warning(c, loss_pct, jitter, lv, now)
            if c.remote is None and _num(s.get("rtt_ms")):
                rtts.append(float(s["rtt_ms"]))
        self.phone_levels = levels
        cards.append(eval_audio(t, phones))
        cards.append(eval_latency(t, rtts))
        cards.append(eval_grant(t, arbiter.grant_times(now - GRANT_WINDOW_S)))

        before = {k: c.state for k, c in self.cards.items()}
        self.cards = {c.id: c for c in sorted(cards, key=lambda c: ORDER.index(c.id) if c.id in ORDER else 99)}
        if before != {k: c.state for k, c in self.cards.items()} and self.on_change is not None:
            self.on_change()

    async def _room_counts(self) -> tuple[int | None, int | None]:
        from . import rooms

        try:
            got = await rooms.call("ListRooms", {})
        except Exception:
            return None, None
        rs = got.get("rooms") or []
        return len(rs), sum(int(r.get("num_participants") or r.get("numParticipants") or 0) for r in rs)

    async def _audio_warning(self, conn, loss_pct, jitter, lv: str, now: float) -> None:
        """At most one `audio_warning` row per phone per 5 minutes."""
        if now - self._warned.get(conn.device_id, -AUDIO_WARN_EVERY_S) < AUDIO_WARN_EVERY_S:
            return
        self._warned[conn.device_id] = now
        detail = {"level": lv, "loss_pct": loss_pct, "jitter_ms": None if jitter is None else round(jitter, 1)}
        await record("audio_warning", user_id=conn.user_id, device_id=conn.device_id, detail=detail)

    # -------------------------------------------------------------- what others read
    def state(self) -> str:
        return worst(c.state for c in self.cards.values())

    def problems(self) -> list[Card]:
        return [c for c in self.cards.values() if c.state == "error"]

    def view(self) -> dict:
        return {"state": self.state(), "cards": [asdict(c) for c in self.cards.values()],
                "thresholds": thresholds(), "defaults": DEFAULTS,
                "labels": {k: v[3] for k, v in THRESHOLDS.items()}}


async def record(kind: str, **kw: Any) -> None:
    """One `intercom_events` row from the loop; a database that is down just loses it (and says so in the log)."""
    from . import store

    def write() -> None:
        with db.transaction() as tx:
            store.log_event(tx, kind, **kw)

    try:
        await store.run(write)
    except Exception:
        log.warning("intercom: could not log %s", kind)


watchdog = Watchdog()
