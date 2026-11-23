#!/usr/bin/env python3
"""Stress-test Relay and show where it slows down.

    .venv/bin/python tools/stress.py http     --seconds 20 --steps 10,50,200     # pages and admin API, stepped concurrency
    .venv/bin/python tools/stress.py sse      --viewers 50,200,500               # caption viewers held open (rehearsal captions)
    .venv/bin/python tools/stress.py alerts   --listeners 100 --rate 20          # stage messages out to many pages
    .venv/bin/python tools/stress.py modules  --cycles 15                        # switch modules on and off under load
    .venv/bin/python tools/stress.py bus      --seconds 600 --scenarios BEG      # the audio bus soak (native/relay-bus/bench)
    .venv/bin/python tools/stress.py all      --quick                            # everything, short

Each scenario but `bus` starts its own Relay (run.py) on ports 18080 / 18443 with a throwaway config and database, in
rehearsal mode (RELAY_DEMO=1: canned captions, no OpenAI), so nothing real is touched. While it runs, a canary asks
`/api/targets` ten times a second: if the canary's latency jumps, the event loop was blocked by whatever the load
reached. The server's memory, CPU and thread count are sampled every two seconds, so a leak shows as a slope.

Results print as tables and are written as JSON to `--out` (default stress-results/). `--pyspy` records a flame graph
of the server while the load runs, if py-spy is installed (`pip install py-spy`; macOS needs sudo for it).

Not covered on purpose: the remote intercom (it needs a Cloudflare tunnel). Not part of the test suite (no `test_` prefix).
"""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import random
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx

REPO = Path(__file__).resolve().parent.parent
PORT, ADMIN_PORT = 18080, 18443
VIEWER = f"http://127.0.0.1:{PORT}"
ADMIN = f"https://127.0.0.1:{ADMIN_PORT}"
PASSWORD = "stress-test-password-1"


# -- numbers ---------------------------------------------------------------------------------------------------
def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p / 100))]


def summarize(ms: list[float]) -> dict:
    return {"n": len(ms), "p50": round(pct(ms, 50), 1), "p95": round(pct(ms, 95), 1), "p99": round(pct(ms, 99), 1),
            "max": round(max(ms), 1) if ms else 0.0}


def table(title: str, header: list[str], rows: list[list]) -> None:
    print(f"\n{title}")
    widths = [max(len(str(x)) for x in col) for col in zip(header, *rows)]
    print("  " + "  ".join(h.ljust(w) for h, w in zip(header, widths)))
    for r in rows:
        print("  " + "  ".join(str(x).ljust(w) for x, w in zip(r, widths)))


# -- the server under test -------------------------------------------------------------------------------------
def _ps(pid: int, *fields: str) -> list[str]:
    out = subprocess.run(["ps", "-o", ",".join(f"{f}=" for f in fields), "-p", str(pid)],
                         capture_output=True, text=True).stdout.split()
    return out


def _cpu_seconds(text: str) -> float:
    """`ps` cputime: [[H:]M:]S[.ff]."""
    total = 0.0
    for part in text.split(":"):
        total = total * 60 + float(part)
    return total


def _threads(pid: int) -> int:
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError):
            m = re.search(r"Threads:\s+(\d+)", Path(f"/proc/{pid}/status").read_text())
            return int(m.group(1)) if m else 0
    if sys.platform == "darwin":
        return max(0, len(subprocess.run(["ps", "-M", "-p", str(pid)], capture_output=True, text=True).stdout.splitlines()) - 1)
    return 0


def _fds(pid: int) -> int:
    if sys.platform.startswith("linux"):
        with contextlib.suppress(OSError):
            return len(os.listdir(f"/proc/{pid}/fd"))
    with contextlib.suppress(OSError, FileNotFoundError):
        return max(0, len(subprocess.run(["lsof", "-p", str(pid)], capture_output=True, text=True).stdout.splitlines()) - 1)
    return 0


class Sampler(threading.Thread):
    """Memory, CPU, threads and open files of one process, every `every` seconds."""

    def __init__(self, pid: int, every: float = 2.0) -> None:
        super().__init__(daemon=True)
        self.pid, self.every, self.rows = pid, every, []
        self._halt = threading.Event()

    def run(self) -> None:
        last_cpu, last_t = None, None
        while not self._halt.is_set():
            got = _ps(self.pid, "rss", "cputime")
            if len(got) == 2:
                now, cpu = time.time(), _cpu_seconds(got[1])
                pct_cpu = 100 * (cpu - last_cpu) / (now - last_t) if last_cpu is not None and now > last_t else 0.0
                last_cpu, last_t = cpu, now
                self.rows.append({"t": now, "rss_mb": int(got[0]) / 1024, "cpu_pct": pct_cpu,
                                  "threads": _threads(self.pid), "fds": _fds(self.pid)})
            self._halt.wait(self.every)

    def finish(self) -> dict:
        self._halt.set()
        self.join(timeout=5)
        if not self.rows:
            return {}
        first, last = self.rows[0], self.rows[-1]
        minutes = max((last["t"] - first["t"]) / 60, 1e-9)
        return {"rss_start_mb": round(first["rss_mb"], 1), "rss_end_mb": round(last["rss_mb"], 1),
                "rss_peak_mb": round(max(r["rss_mb"] for r in self.rows), 1),
                "rss_growth_mb_per_min": round((last["rss_mb"] - first["rss_mb"]) / minutes, 2),
                "cpu_pct_mean": round(statistics.fmean(r["cpu_pct"] for r in self.rows), 1),
                "cpu_pct_peak": round(max(r["cpu_pct"] for r in self.rows), 1),
                "threads_start": first["threads"], "threads_end": last["threads"],
                "fds_start": first["fds"], "fds_end": last["fds"]}


class Server:
    """Relay on its own ports with a throwaway config and database, in rehearsal mode."""

    def __init__(self, pyspy: Path | None = None) -> None:
        self.dir = Path(tempfile.mkdtemp(prefix="relay-stress-"))
        self.pyspy = pyspy
        self.proc: subprocess.Popen | None = None
        self.spy: subprocess.Popen | None = None
        self.sampler: Sampler | None = None
        self.cookies: dict[str, str] = {}
        self.log = self.dir / "relay.log"

    def __enter__(self) -> "Server":
        (self.dir / "config.json").write_text(json.dumps({
            "host": "127.0.0.1", "admin_host": "127.0.0.1", "port": PORT, "admin_port": ADMIN_PORT,
            "targets": {"SPANISH": {"enabled": True}},
            "alerts": {"messages": {"free": {"screens": ["stress"]}}}}))
        env = {**os.environ, "RELAY_CONFIG": str(self.dir / "config.json"), "RELAY_DATABASE": str(self.dir / "relay.db"),
               "RELAY_CERT_DIR": str(self.dir / "certs"), "RELAY_KEY_STORE": "file", "RELAY_DEMO": "1",
               "PYTHONUNBUFFERED": "1"}
        self.proc = subprocess.Popen([sys.executable, str(REPO / "run.py")], cwd=REPO, env=env,
                                     stdout=self.log.open("wb"), stderr=subprocess.STDOUT)
        deadline = time.time() + 60
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise SystemExit(f"Relay exited at start (code {self.proc.returncode}); see {self.log}\n"
                                 + self.log.read_text()[-2000:])
            with contextlib.suppress(httpx.HTTPError):
                if httpx.get(f"{VIEWER}/api/targets", timeout=2).status_code == 200:
                    break
            time.sleep(0.5)
        else:
            raise SystemExit(f"Relay did not answer within 60 s; see {self.log}")
        self._sign_up()
        self.sampler = Sampler(self.proc.pid)
        self.sampler.start()
        if self.pyspy:
            self.spy = subprocess.Popen([str(self.pyspy), "record", "-p", str(self.proc.pid), "-o",
                                         str(self.dir / "profile.svg"), "--duration", "86400", "--nonblocking"])
        return self

    def _sign_up(self) -> None:
        r = httpx.post(f"{ADMIN}/admin/setup", verify=False, follow_redirects=False,
                       data={"username": "stress", "name": "Stress", "password": PASSWORD, "confirm": PASSWORD})
        if r.status_code not in (200, 302, 303) or not r.cookies:
            raise SystemExit(f"first-run setup refused ({r.status_code}): {r.text[:300]}")
        self.cookies = dict(r.cookies)

    def admin(self, **kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=ADMIN, verify=False, cookies=self.cookies, timeout=30, **kw)

    def viewer(self, **kw) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=VIEWER, timeout=30,
                                 limits=httpx.Limits(max_connections=2000, max_keepalive_connections=2000), **kw)

    def resources(self) -> dict:
        return self.sampler.finish() if self.sampler else {}

    def __exit__(self, *exc) -> None:
        if self.spy:
            self.spy.send_signal(2)
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.spy.wait(timeout=30)
        if self.proc:
            self.proc.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.proc.wait(timeout=15)
            if self.proc.poll() is None:
                self.proc.kill()
        if self.spy and (self.dir / "profile.svg").exists():
            keep = REPO / "stress-results" / "profile.svg"
            keep.parent.mkdir(exist_ok=True)
            shutil.copy(self.dir / "profile.svg", keep)
            print(f"\nflame graph: {keep}")
        tail = self.log.read_text(errors="replace")
        bad = [ln for ln in tail.splitlines() if re.search(r"Traceback|ERROR|CRITICAL|Task exception", ln)]
        if bad:
            print(f"\nserver log has {len(bad)} error line(s); first: {bad[0][:200]}\n  full log: {self.dir / 'relay.log'}")
        else:
            shutil.rmtree(self.dir, ignore_errors=True)


class Canary:
    """Asks a cheap route ten times a second. Its latency is the event loop's lag while the load runs."""

    def __init__(self, server: Server) -> None:
        self.server, self.ms, self.errors, self._task = server, [], 0, None

    async def _run(self) -> None:
        async with self.server.viewer() as c:
            while True:
                t = time.perf_counter()
                try:
                    r = await c.get("/api/targets", timeout=10)
                    if r.status_code != 200:
                        self.errors += 1
                except httpx.HTTPError:
                    self.errors += 1
                self.ms.append((time.perf_counter() - t) * 1000)
                await asyncio.sleep(0.1)

    async def __aenter__(self) -> "Canary":
        self._task = asyncio.create_task(self._run())
        return self

    async def __aexit__(self, *exc) -> None:
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task

    def result(self) -> dict:
        return {**summarize(self.ms), "errors": self.errors}

    def mark(self) -> int:
        return len(self.ms)

    def since(self, mark: int) -> dict:
        return summarize(self.ms[mark:])


# -- the loaded state: NDI cameras, a full audio bus and 32 meters ---------------------------------------------------
SENDER = REPO / "tools" / "stress_ndi_sender.py"
SIZES = {"1080": "1920x1080", "720": "1280x720", "480": "854x480"}
LANE_CHANNELS = 6                   # tones each synthetic camera carries; 6 cameras x 6 = 36, the bus takes 32
OUTPUTS, OUT_CHANNELS = 3, 8        # the full matrix feeds three 8-channel NDI sound outputs


class Load:
    """Put Relay into the state of a busy show before a scenario runs on top of it, and say what it did to it.

    * NDI cameras, each its own process (tools/stress_ndi_sender.py): 4 x 1080p and 2 x 720p by default (`--ndi`), 30 fps,
      six channels of tone each. Relay receives all of them: a viewer stream and a level poll on each, and picture
      analysis on two.
    * The audio bus full: all 32 lanes patched from the cameras' sound, three 8-channel NDI outputs, every lane routed to
      every output channel (768 crosspoints at -40 dB).
    * 32 sound meters, one on each lane.
    """

    def __init__(self, server: Server, streams: str, meters: int = 32) -> None:
        self.server, self.meters = server, meters
        self.spec = [(kind, int(n)) for kind, n in (part.split(":") for part in streams.split(",") if part)]
        self.procs: list[subprocess.Popen] = []
        self.names: list[str] = []
        self.tasks: list[asyncio.Task] = []
        self.before: dict = {}

    async def start(self) -> None:
        for kind, n in self.spec:
            for i in range(n):
                name = f"STRESS-{kind}-{i + 1}"
                p = subprocess.Popen([sys.executable, str(SENDER), name, SIZES[kind], "--channels", str(LANE_CHANNELS)],
                                     cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                self.procs.append(p)
        for p in self.procs:
            line = await asyncio.get_running_loop().run_in_executor(None, p.stdout.readline)
            if line.strip() != "ready":
                raise SystemExit("an NDI test camera did not start: " + (p.stderr.read() if p.poll() is not None else line))
        async with self.server.admin() as c:
            r = await c.post("/api/admin/modules/ndi", json={"enabled": True})
            if r.status_code != 200:
                raise SystemExit(f"cannot switch the NDI module on: {r.status_code} {r.text[:200]}")
            want, deadline = len(self.procs), time.time() + 45
            while time.time() < deadline:
                await c.post("/api/admin/ndi/scan")
                snap = (await c.get("/api/admin/ndi")).json()
                if not snap.get("available"):
                    raise SystemExit("the NDI runtime is not available: " + str(snap.get("error")))
                found = sorted(s["name"] for s in snap["sources"] if "STRESS-" in s["name"] and s.get("online", True))
                if len(found) >= want:
                    self.names = found
                    break
                await asyncio.sleep(2)
            else:
                raise SystemExit(f"Relay found {len(found)} of {want} NDI test cameras in 45 s")
            lanes = [{"lane": n + 1, "name": f"cam {n // LANE_CHANNELS + 1} ch {n % LANE_CHANNELS + 1}", "source": "ndi",
                      "device": self.names[n // LANE_CHANNELS], "channel": n % LANE_CHANNELS} for n in range(32)]
            outputs = [{"id": f"o{i + 1}", "name": f"Out {i + 1}", "output": "ndi", "device": f"stress-out-{i + 1}",
                        "channels": OUT_CHANNELS} for i in range(OUTPUTS)]
            matrix = [{"in": lane, "out": o["id"], "ch": ch, "gain_db": -40.0}
                      for o in outputs for ch in range(1, OUT_CHANNELS + 1) for lane in range(1, 33)]
            r = await c.post("/api/admin/audiobus", json={"lanes": lanes, "outputs": outputs, "matrix": matrix}, timeout=120)
            if r.status_code != 200:
                raise SystemExit(f"cannot set up the audio bus: {r.status_code} {r.text[:300]}")
            meters = [{"id": f"m{n + 1}", "name": f"Meter {n + 1}", "lane": n + 1, "kind": "room"} for n in range(self.meters)]
            r = await c.post("/api/admin/metering", json={"meters": meters})
            if r.status_code != 200:
                raise SystemExit(f"cannot set up the meters: {r.status_code} {r.text[:300]}")
            await c.post("/api/admin/ndi/analysis/open", json={"source": self.names[0]})
            await c.post("/api/admin/ndi/analysis/open", json={"source": self.names[-1]})
        self.tasks = [asyncio.create_task(self._watch(n)) for n in self.names]
        await asyncio.sleep(10)                       # settle
        self.before = await self.health()

    async def _watch(self, name: str) -> None:
        """A page's worth of reading one camera: the picture stream held open, and its levels polled."""
        async def level() -> None:
            async with self.server.admin() as c:
                while True:
                    with contextlib.suppress(httpx.HTTPError):
                        await c.get(f"/ndi/level/{name}")
                    await asyncio.sleep(1)
        poll = asyncio.create_task(level())
        try:
            async with self.server.admin() as c:
                async with c.stream("GET", f"/ndi/stream/{name}", timeout=httpx.Timeout(30, read=None)) as r:
                    async for _ in r.aiter_bytes():
                        pass
        except (httpx.HTTPError, asyncio.CancelledError):
            pass
        finally:
            poll.cancel()

    async def health(self) -> dict:
        async with self.server.admin() as c:
            bus = (await c.get("/api/admin/audiobus")).json()["status"]
            mtr = (await c.get("/api/admin/metering")).json()["status"]
            ndi = (await c.get("/api/admin/ndi")).json()
        lanes = bus["lanes"]
        rows = mtr if isinstance(mtr, list) else list(mtr.values()) if isinstance(mtr, dict) else []
        live = [x for x in rows if isinstance(x, dict) and any(isinstance(x.get(k), (int, float)) for k in ("a_fast", "leq_a", "a_slow"))]
        return {"lanes_open": sum(1 for l in lanes if l["state"] in ("open", "ok", "running", "active")),
                "lanes_patched": sum(1 for l in lanes if l["patched"]),
                "lane_states": sorted({l["state"] for l in lanes if l["patched"]}),
                "inputs_dropped": sum(i["dropped"] or 0 for i in bus["inputs"]),
                "output_underruns": sum(o["underruns"] for o in bus["outputs"]),
                "output_states": sorted({o["state"] for o in bus["outputs"]}),
                "meters_reading": len(live), "meters": len(rows),
                "ndi_fps": sorted({round(s.get("fps") or 0) for s in ndi["sources"] if "STRESS-" in s["name"]})}

    async def stop(self) -> dict:
        after = await self.health()
        for t in self.tasks:
            t.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for p in self.procs:
            p.terminate()
        for p in self.procs:
            with contextlib.suppress(subprocess.TimeoutExpired):
                p.wait(timeout=10)
        return after

    def report(self, after: dict) -> dict:
        d = {"cameras": len(self.names), "before": self.before, "after": after}
        keys = ["lanes_open", "lanes_patched", "inputs_dropped", "output_underruns", "meters_reading", "ndi_fps"]
        table("loaded state (audio bus, meters, NDI), before and after the scenario",
              ["", *keys], [["before", *[self.before.get(k) for k in keys]], ["after", *[after.get(k) for k in keys]]])
        print(f"  lane states {after['lane_states']}, output states {after['output_states']}")
        return d


async def with_load(server: Server, streams: str | None, scenario) -> dict:
    """Run `scenario` (a coroutine) with the loaded state around it, when `streams` is given."""
    if not streams:
        return await scenario
    load = Load(server, streams)
    await load.start()
    try:
        res = await scenario
    finally:
        after = await load.stop()
    res["load"] = load.report(after)
    return res


# -- scenario: pages and API at rising concurrency ------------------------------------------------------------
VIEWER_ROUTES = ["/", "/translation", "/transcription", "/status", "/api/targets", "/static/relay-ui.js"]
ADMIN_ROUTES = ["/api/admin/state", "/api/admin/status", "/api/admin/audiobus", "/api/admin/metering",
                "/api/admin/modules", "/api/admin/devices", "/api/admin/urls"]


async def scenario_http(server: Server, seconds: float, steps: list[int]) -> dict:
    out: dict = {"steps": []}
    async with Canary(server) as canary, server.viewer() as viewer, server.admin() as admin:
        for workers in steps:
            lat: dict[str, list[float]] = {}
            errs: dict[str, int] = {}
            stop = time.time() + seconds
            mark = canary.mark()

            async def worker() -> None:
                while time.time() < stop:
                    on_admin = random.random() < 0.4
                    path = random.choice(ADMIN_ROUTES if on_admin else VIEWER_ROUTES)
                    t = time.perf_counter()
                    try:
                        r = await (admin if on_admin else viewer).get(path)
                        ok = r.status_code < 400
                    except httpx.HTTPError:
                        ok = False
                    lat.setdefault(path, []).append((time.perf_counter() - t) * 1000)
                    if not ok:
                        errs[path] = errs.get(path, 0) + 1

            await asyncio.gather(*(worker() for _ in range(workers)))
            total = sum(len(v) for v in lat.values())
            step = {"workers": workers, "rps": round(total / seconds), "errors": sum(errs.values()),
                    "canary": canary.since(mark), "routes": {p: summarize(v) | {"errors": errs.get(p, 0)} for p, v in lat.items()}}
            out["steps"].append(step)
            table(f"http, {workers} workers: {step['rps']} requests/s, {step['errors']} errors, canary p99 {step['canary']['p99']} ms",
                  ["route", "n", "p50", "p95", "p99", "max", "errors"],
                  sorted(([p, s["n"], s["p50"], s["p95"], s["p99"], s["max"], s["errors"]] for p, s in step["routes"].items()),
                         key=lambda r: -r[4])[:8])
        out["canary"] = canary.result()
    return out


# -- scenario: caption viewers held open ------------------------------------------------------------------------
async def _viewer_stream(server: Server, path: str, hold: float, stats: dict, lang: str | None = None) -> None:
    last, gaps, got = None, [], 0
    try:
        async with server.viewer() as c:
            async with c.stream("GET", path, timeout=httpx.Timeout(30, read=None)) as r:
                if r.status_code != 200:
                    stats["refused"] += 1
                    return
                stats["connected"] += 1
                end = time.time() + hold
                async for chunk in r.aiter_bytes():
                    now = time.time()
                    got += chunk.count(b"\n\n")
                    if last is not None:
                        gaps.append(now - last)
                    last = now
                    if now > end:
                        break
    except httpx.HTTPError:
        stats["dropped"] += 1
    stats["events"] += got
    if gaps:
        stats["max_gap_s"] = max(stats["max_gap_s"], max(gaps))


async def scenario_sse(server: Server, seconds: float, ladder: list[int]) -> dict:
    out: dict = {"steps": []}
    async with Canary(server) as canary:
        for viewers in ladder:
            stats = {"connected": 0, "refused": 0, "dropped": 0, "events": 0, "max_gap_s": 0.0}
            mark, t0 = canary.mark(), time.time()
            await asyncio.gather(*(_viewer_stream(server, "/stream?stream=translation&lang=SPANISH", seconds, stats)
                                   for _ in range(viewers)))
            step = {"viewers": viewers, **stats, "events_per_viewer_s": round(stats["events"] / max(stats["connected"], 1) / seconds, 2),
                    "canary": canary.since(mark), "secs": round(time.time() - t0, 1)}
            out["steps"].append(step)
            table(f"sse, {viewers} viewers", ["connected", "refused", "dropped", "events/viewer/s", "longest silence s", "canary p50", "canary p99", "canary max"],
                  [[stats["connected"], stats["refused"], stats["dropped"], step["events_per_viewer_s"],
                    round(stats["max_gap_s"], 1), step["canary"]["p50"], step["canary"]["p99"], step["canary"]["max"]]])
        out["canary"] = canary.result()
    return out


# -- scenario: stage messages to many pages ------------------------------------------------------------------------
async def scenario_alerts(server: Server, seconds: float, listeners: int, rate: float) -> dict:
    latencies: list[float] = []
    seen: set[str] = set()
    sent: dict[str, float] = {}
    stats = {"connected": 0, "dropped": 0}

    async def listen() -> None:
        try:
            async with server.viewer() as c:
                async with c.stream("GET", "/api/alerts/messages/stream", timeout=httpx.Timeout(30, read=None)) as r:
                    stats["connected"] += 1
                    async for chunk in r.aiter_bytes():
                        now = time.time()
                        for m in re.findall(rb"stress-([0-9a-f]{8})", chunk):
                            key = m.decode()
                            if key in sent:
                                latencies.append((now - sent[key]) * 1000)
                                seen.add(key)
        except httpx.HTTPError:
            stats["dropped"] += 1
        except asyncio.CancelledError:
            raise

    async with Canary(server) as canary:
        tasks = [asyncio.create_task(listen()) for _ in range(listeners)]
        await asyncio.sleep(2)
        n, errors, first_error, end = 0, 0, "", time.time() + seconds
        async with server.viewer() as sender:
            while time.time() < end:
                key = f"{n:08x}"
                sent[key] = time.time()
                r = await sender.post("/api/alerts/messages/send", json={"text": f"stress-{key}", "from": "stress"})
                if r.status_code != 200:
                    errors += 1
                    first_error = first_error or f"{r.status_code} {r.text[:120]}"
                n += 1
                await asyncio.sleep(1 / rate)
        await asyncio.sleep(2)
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        res = {"listeners": listeners, "sent": n, "send_errors": errors, "delivered_messages": len(seen),
               "delivery_ms": summarize(latencies), "canary": canary.result(), **stats}
    table(f"alerts: {n} messages at {rate}/s to {listeners} listeners", ["connected", "dropped", "send errors", "reached anyone", "delivery p50", "p95", "p99", "max", "canary p99"],
          [[stats["connected"], stats["dropped"], errors, len(seen), res["delivery_ms"]["p50"], res["delivery_ms"]["p95"],
            res["delivery_ms"]["p99"], res["delivery_ms"]["max"], res["canary"]["p99"]]])
    if first_error:
        print(f"  first send error: {first_error}")
    return res


# -- scenario: modules switched on and off under load --------------------------------------------------------------
async def scenario_modules(server: Server, cycles: int, viewers: int, keep: tuple = ()) -> dict:
    res: dict = {"cycles": cycles, "modules": {}}
    async with Canary(server) as canary, server.admin() as admin:
        listing = (await admin.get("/api/admin/modules")).json()
        items = listing if isinstance(listing, list) else listing.get("modules", [])
        ids = [m["id"] for m in items if m.get("id") not in ("intercom_remote", "remote_access") + keep]
        stats = {"connected": 0, "refused": 0, "dropped": 0, "events": 0, "max_gap_s": 0.0}
        holders = [asyncio.create_task(_viewer_stream(server, "/stream?stream=translation&lang=SPANISH", cycles * 6, stats))
                   for _ in range(viewers)]
        await asyncio.sleep(2)
        before = server.sampler.rows[-1] if server.sampler and server.sampler.rows else {}
        for mid in ids:
            base_threads, base_fds = _threads(server.proc.pid), _fds(server.proc.pid)
            times, failures = [], 0
            for _ in range(cycles):
                for on in (True, False):
                    t = time.perf_counter()
                    r = await admin.post(f"/api/admin/modules/{mid}", json={"enabled": on})
                    times.append((time.perf_counter() - t) * 1000)
                    failures += r.status_code != 200
            await asyncio.sleep(2.5)            # let a stopping thread finish before counting
            res["modules"][mid] = {**summarize(times), "failures": failures,
                                   "threads_added": _threads(server.proc.pid) - base_threads,
                                   "fds_added": _fds(server.proc.pid) - base_fds}
        for h in holders:
            h.cancel()
        await asyncio.gather(*holders, return_exceptions=True)
        res["canary"] = canary.result()
        res["viewers"] = stats
        res["threads_before"] = before.get("threads")
    table(f"modules: {cycles} on/off cycles each, {viewers} viewers held open; canary p99 {res['canary']['p99']} ms, max {res['canary']['max']} ms", ["module", "switches", "p50 ms", "p95 ms", "max ms", "failures", "threads left", "files left"],
          sorted(([m, s["n"], s["p50"], s["p95"], s["max"], s["failures"], s["threads_added"], s["fds_added"]] for m, s in res["modules"].items()), key=lambda r: -r[4]))
    return res


# -- scenario: the audio bus soak ---------------------------------------------------------------------------------
def scenario_bus(seconds: float, scenarios: str, engines: str) -> dict:
    """native/relay-bus/bench/bench.py is the bus benchmark; this runs it long and samples the child's memory, so
    a leak shows as a slope instead of one end-of-run number."""
    bench = REPO / "native" / "relay-bus" / "bench" / "bench.py"
    out: dict = {"runs": []}
    rows = []
    for eng in engines.split(","):
        for scn in scenarios:
            if eng != "rust" and scn == "F":
                continue
            if eng == "rust" and scn not in "ABCDEFG":
                continue
            result = REPO / "stress-results" / f"bus-{eng}-{scn}.json"
            result.parent.mkdir(exist_ok=True)
            proc = subprocess.Popen([sys.executable, str(bench), "--engine", eng, "--scenario", scn,
                                     "--seconds", str(seconds), "--json", str(result)], cwd=REPO,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            sampler = Sampler(proc.pid, every=5.0)
            sampler.start()
            log, _ = proc.communicate()
            res = sampler.finish()
            data = json.loads(result.read_text()) if result.exists() else {}
            run = {"engine": eng, "scenario": scn, "exit": proc.returncode, "memory": res,
                   "cpu_pct": data.get("cpu_pct"), "speaker": data.get("speaker"), "input_dropped": data.get("input_dropped")}
            out["runs"].append(run)
            rows.append([eng, scn, proc.returncode, run["cpu_pct"], res.get("rss_start_mb"), res.get("rss_end_mb"),
                         res.get("rss_growth_mb_per_min"), run["input_dropped"]])
            if proc.returncode:
                print(log[-1500:])
    table(f"bus soak, {seconds:.0f} s per run (growth is MB/min of the whole bench process, harness included)",
          ["engine", "scenario", "exit", "cpu %", "rss start", "rss end", "growth MB/min", "inputs dropped"], rows)
    return out


# -- the command line -------------------------------------------------------------------------------------------
def ints(text: str) -> list[int]:
    return [int(x) for x in text.split(",") if x.strip()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("scenario", choices=["http", "sse", "alerts", "modules", "bus", "all"])
    ap.add_argument("--seconds", type=float, help="per step (http, sse, alerts) or per run (bus)")
    ap.add_argument("--steps", default="10,50,200", help="http: concurrent workers, one step each")
    ap.add_argument("--viewers", default="50,200,500", help="sse: viewers held open, one step each; modules: how many")
    ap.add_argument("--listeners", type=int, default=100, help="alerts: pages listening")
    ap.add_argument("--rate", type=float, default=20, help="alerts: messages per second")
    ap.add_argument("--cycles", type=int, default=10, help="modules: on/off cycles per module")
    ap.add_argument("--scenarios", default="BEG", help="bus: bench.py scenarios (B heavy, E busy Python, G bursty Python, ...)")
    ap.add_argument("--engines", default="rust", help="bus: bench.py engines")
    ap.add_argument("--loaded", action="store_true",
                    help="first load Relay like a busy show: NDI cameras, the audio bus full, 32 meters (see Load)")
    ap.add_argument("--ndi", default="1080:4,720:2", help="with --loaded: cameras as size:count, sizes 1080, 720, 480")
    ap.add_argument("--quick", action="store_true", help="short runs, to check the harness works")
    ap.add_argument("--pyspy", action="store_true", help="record a flame graph of the server (needs py-spy)")
    ap.add_argument("--out", default=str(REPO / "stress-results"))
    args = ap.parse_args()

    quick = args.quick
    seconds = args.seconds or (5 if quick else 30)
    steps = [5, 20] if quick else ints(args.steps)
    ladder = [20] if quick else ints(args.viewers)
    which = ["http", "sse", "alerts", "modules", "bus"] if args.scenario == "all" else [args.scenario]
    spy = None
    if args.pyspy:
        spy = shutil.which("py-spy")
        if not spy:
            print("py-spy is not installed (pip install py-spy); running without a flame graph")
    results: dict = {"started": time.strftime("%Y-%m-%d %H:%M:%S"), "scenarios": {}}
    for name in which:
        print(f"\n=== {name} ===")
        t0 = time.time()
        if name == "bus":
            results["scenarios"][name] = scenario_bus(args.seconds or (10 if quick else 120), "B" if quick else args.scenarios,
                                                      args.engines)
        else:
            with Server(Path(spy) if spy else None) as server:
                ndi = args.ndi if args.loaded else None
                if name == "http":
                    res = asyncio.run(with_load(server, ndi, scenario_http(server, seconds, steps)))
                elif name == "sse":
                    res = asyncio.run(with_load(server, ndi, scenario_sse(server, seconds, ladder)))
                elif name == "alerts":
                    res = asyncio.run(with_load(server, ndi, scenario_alerts(server, seconds, 10 if quick else args.listeners, args.rate)))
                else:
                    res = asyncio.run(with_load(server, ndi, scenario_modules(server, 2 if quick else args.cycles,
                                                                              5 if quick else ladder[0], keep=("ndi",))))
                res["server"] = server.resources()
            results["scenarios"][name] = res
            s = res["server"]
            if s:
                print(f"\nserver: rss {s['rss_start_mb']} -> {s['rss_end_mb']} MB (peak {s['rss_peak_mb']}, "
                      f"{s['rss_growth_mb_per_min']} MB/min), cpu mean {s['cpu_pct_mean']}% peak {s['cpu_pct_peak']}%, "
                      f"threads {s['threads_start']} -> {s['threads_end']}, open files {s['fds_start']} -> {s['fds_end']}")
        results["scenarios"][name]["seconds"] = round(time.time() - t0, 1)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    path = out / f"stress-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(results, indent=1, default=float))
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
