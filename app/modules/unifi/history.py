"""Internet health history (#358): a row a minute for 48 hours, folded into 5-minute rows kept for 30 days.

Each minute holds what the console reports (throughput, latency, drops, availability) and Relay's own probe: a TCP
connection to the gateway and to an outside target, timed, three tries a minute. A failed connection counts as a lost
probe. TCP rather than ICMP because ICMP needs privileges Relay does not run with on every computer.
"""
from __future__ import annotations

import socket
import time
from pathlib import Path

from ... import db, netsock
from .controller import TRAFFIC

MIGRATIONS = Path(__file__).resolve().parent / "migrations"
COLUMNS = ("down_bps", "up_bps", "latency_ms", "drops", "availability", "gw_ms", "gw_loss", "ext_ms", "ext_loss")
KEEP_1M_S = 48 * 3600
KEEP_5M_S = 30 * 86400
PROBE_PORT = 443
PROBE_TRIES = 3
PROBE_TIMEOUT_S = 2.0
_COLS = "down_bps, up_bps, latency_ms, drops, availability, gw_ms, gw_loss, ext_ms, ext_loss"
INSERT = "insert or replace into unifi_wan_1m (ts, down_bps, up_bps, latency_ms, drops, availability, gw_ms, gw_loss, ext_ms, ext_loss) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
FOLD = ("insert or replace into unifi_wan_5m (ts, down_bps, up_bps, latency_ms, drops, availability, gw_ms, gw_loss, ext_ms, ext_loss) select (ts / 300) * 300, avg(down_bps), avg(up_bps), avg(latency_ms), avg(drops), avg(availability), avg(gw_ms), avg(gw_loss), avg(ext_ms), avg(ext_loss)"
        " from unifi_wan_1m where ts < ? group by ts / 300")
SERIES = ("select ts, down_bps, up_bps, latency_ms, drops, availability, gw_ms, gw_loss, ext_ms, ext_loss from unifi_wan_5m where ts >= ? union all"
          " select ts, down_bps, up_bps, latency_ms, drops, availability, gw_ms, gw_loss, ext_ms, ext_loss from unifi_wan_1m where ts >= ? order by ts")
_ready = False


def ready() -> None:
    global _ready
    if not _ready:
        db.migrate("unifi", MIGRATIONS)
        _ready = True


def probe(host: str, port: int = PROBE_PORT, tries: int = PROBE_TRIES) -> tuple[float | None, float]:
    """(average connect time in ms, percent lost) for `tries` TCP connections. Blocking: run it in a thread."""
    times, lost = [], 0
    for _ in range(tries):
        t0 = time.monotonic()
        try:
            sock = netsock.connect_tcp(TRAFFIC, host, port, PROBE_TIMEOUT_S)
        except (OSError, socket.timeout):
            lost += 1
            continue
        times.append((time.monotonic() - t0) * 1000)
        try:
            sock.close()
        except OSError:
            pass
    return (round(sum(times) / len(times), 1) if times else None), round(100.0 * lost / tries, 1)


def record(sample: dict, now: float | None = None) -> None:
    """Keep one minute (a second sample in the same minute replaces the first)."""
    ready()
    ts = int((time.time() if now is None else now) // 60 * 60)
    vals = [sample.get(c) for c in COLUMNS]
    db.execute(INSERT, (ts, *vals))


def fold(now: float | None = None) -> None:
    """Average minutes older than 48 hours into 5-minute rows, and drop 5-minute rows older than 30 days."""
    ready()
    now = time.time() if now is None else now
    cut = int(now - KEEP_1M_S) // 300 * 300
    db.execute(FOLD, (cut,))
    db.execute("delete from unifi_wan_1m where ts < ?", (cut,))
    db.execute("delete from unifi_wan_5m where ts < ?", (int(now - KEEP_5M_S),))


def series(hours: float, now: float | None = None) -> list[dict]:
    """Rows for the last `hours`, oldest first: minutes where they are kept, 5-minute rows before that."""
    ready()
    now = time.time() if now is None else now
    start = int(now - hours * 3600)
    rows = db.query(SERIES, (start, start))
    if hours > 48 and len(rows) > 2000:          # a long view: one row per 15 minutes is plenty to draw
        step, out, bucket = 900, [], None
        for r in rows:
            b = r["ts"] // step * step
            if bucket is None or bucket["ts"] != b:
                bucket = {"ts": b, "_n": {}, **{c: None for c in COLUMNS}}
                out.append(bucket)
            for c in COLUMNS:
                if r[c] is not None:
                    n = bucket["_n"].get(c, 0)
                    bucket[c] = ((bucket[c] or 0) * n + r[c]) / (n + 1)
                    bucket["_n"][c] = n + 1
        for b in out:
            b.pop("_n")
        return out
    return rows


def worst(hours: float, now: float | None = None) -> dict:
    """Peaks over the window, for a service report: busiest upload and download, worst latency and loss."""
    rows = series(hours, now)
    def peak(c: str) -> float | None:
        vals = [r[c] for r in rows if r[c] is not None]
        return max(vals) if vals else None
    low = [r["availability"] for r in rows if r["availability"] is not None]
    return {"down_bps": peak("down_bps"), "up_bps": peak("up_bps"), "latency_ms": peak("latency_ms"),
            "ext_loss": peak("ext_loss"), "gw_loss": peak("gw_loss"), "availability": min(low) if low else None,
            "minutes": len(rows)}
