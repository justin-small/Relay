#!/usr/bin/env python3
"""Check that the axum front door (native/relay-front, #651) passes streams and websockets through without buffering.

    cargo build --release -p relay-front --manifest-path native/Cargo.toml
    .venv/bin/python tools/front_check.py

Starts a small upstream of its own (one server-sent-event stream that sends an event every 200 ms and never ends, a
websocket echo, a route that answers 204) and the front door in front of it, then measures through the front door:

* the first event arrives at once, and the events keep the upstream's 200 ms rhythm (a proxy that buffers delivers them
  in bursts);
* a websocket message comes back echoed, ten times, with its round trip;
* a client that drops mid-stream makes the upstream's stream end, so nothing is held open for a viewer that left;
* a down upstream is a 502, not a hung or crashed front door.

Exit 0 when every check passes. Not part of the test suite (no `test_` prefix): it needs the front door built.
"""
from __future__ import annotations

import asyncio
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx
import websockets

REPO = Path(__file__).resolve().parent.parent
FRONT_BIN = REPO / "native" / "target" / "release" / "relay-front"
UP, FRONT = 18900, 18901
results: list[tuple[str, bool, str]] = []
upstream_stream_closed = asyncio.Event()


def report(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"{'ok  ' if ok else 'FAIL'} {name}: {detail}")


async def upstream_http(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    head = await reader.readuntil(b"\r\n\r\n")
    line = head.split(b"\r\n", 1)[0].decode()
    path = line.split(" ")[1]
    if path == "/stream":
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nCache-Control: no-cache\r\n"
                     b"Transfer-Encoding: chunked\r\n\r\n")
        try:
            n = 0
            while True:
                body = f"data: {n} {time.monotonic():.4f}\n\n".encode()
                writer.write(f"{len(body):x}\r\n".encode() + body + b"\r\n")
                await writer.drain()
                n += 1
                await asyncio.sleep(0.2)
        except (ConnectionError, asyncio.CancelledError):
            upstream_stream_closed.set()
    else:
        writer.write(b"HTTP/1.1 204 No Content\r\nContent-Length: 0\r\n\r\n")
        await writer.drain()
    writer.close()


async def echo(ws) -> None:
    async for message in ws:
        await ws.send(message)


async def main() -> int:
    if not FRONT_BIN.exists():
        print(f"build the front door first: cargo build --release -p relay-front --manifest-path {FRONT_BIN.parents[2]}/Cargo.toml")
        return 2
    ws_server = await websockets.serve(echo, "127.0.0.1", UP + 2)
    http_server = await asyncio.start_server(upstream_http, "127.0.0.1", UP)
    ws_front_target = UP + 2
    # The front door has one upstream, so the websocket check gets a second front door on the websocket upstream.
    fronts = [subprocess.Popen([str(FRONT_BIN), "--listen", f"127.0.0.1:{FRONT}", "--upstream", f"http://127.0.0.1:{UP}"],
                               stderr=subprocess.DEVNULL),
              subprocess.Popen([str(FRONT_BIN), "--listen", f"127.0.0.1:{FRONT + 2}",
                                "--upstream", f"http://127.0.0.1:{ws_front_target}"], stderr=subprocess.DEVNULL),
              subprocess.Popen([str(FRONT_BIN), "--listen", f"127.0.0.1:{FRONT + 3}",
                                "--upstream", "http://127.0.0.1:1"], stderr=subprocess.DEVNULL)]
    await asyncio.sleep(0.5)
    try:
        # 1. server-sent events keep their rhythm
        async with httpx.AsyncClient(timeout=10) as c:
            t0 = time.monotonic()
            arrivals: list[float] = []
            async with c.stream("GET", f"http://127.0.0.1:{FRONT}/stream") as r:
                async for line in r.aiter_lines():
                    if line.startswith("data:"):
                        arrivals.append(time.monotonic())
                        if len(arrivals) == 15:
                            break
            first = (arrivals[0] - t0) * 1000
            gaps = [(b - a) * 1000 for a, b in zip(arrivals, arrivals[1:])]
            report("sse first event", first < 100, f"{first:.1f} ms after the request")
            report("sse rhythm", 150 < statistics.median(gaps) < 250 and max(gaps) < 320,
                   f"gaps median {statistics.median(gaps):.0f} ms, min {min(gaps):.0f}, max {max(gaps):.0f} (upstream sends every 200)")
        # 2. a viewer that leaves ends the upstream's stream
        try:
            await asyncio.wait_for(upstream_stream_closed.wait(), 3)
            report("sse client drop reaches upstream", True, "upstream saw its stream close")
        except asyncio.TimeoutError:
            report("sse client drop reaches upstream", False, "upstream stream still open 3 s after the client left")
        # 3. websocket echo through the front door
        rtts = []
        async with websockets.connect(f"ws://127.0.0.1:{FRONT + 2}/") as ws:
            for i in range(10):
                t = time.monotonic()
                await ws.send(f"hello {i}")
                got = await asyncio.wait_for(ws.recv(), 3)
                assert got == f"hello {i}", got
                rtts.append((time.monotonic() - t) * 1000)
            big = "x" * 1_000_000
            await ws.send(big)
            report("websocket 1 MB message", await asyncio.wait_for(ws.recv(), 5) == big, "echoed intact")
        report("websocket echo", True, f"10 round trips, median {statistics.median(rtts):.2f} ms, max {max(rtts):.2f} ms")
        # 4. a down upstream is a 502
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.get(f"http://127.0.0.1:{FRONT + 3}/")
            report("upstream down", r.status_code == 502, f"HTTP {r.status_code}")
            r = await c.get(f"http://127.0.0.1:{FRONT}/anything")
            report("front door still serving after a 502", r.status_code == 204, f"HTTP {r.status_code}")
    finally:
        for p in fronts:
            p.terminate()
        ws_server.close()
        http_server.close()
    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)} of {len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
