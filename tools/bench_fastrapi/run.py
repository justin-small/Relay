"""FastAPI (uvicorn) vs fastrapi on the same routes, driven by oha.

    python run.py --fastapi-python PY --fastrapi-python PY2 [--oha PATH] [--secs 8] [--conns 64]

Each framework runs in its own interpreter (fastrapi needs its own wheel; FastAPI needs the app's). Servers run
one process each, plus FastAPI with --uvicorn-workers N. Prints a table of requests/s and latency per route.
"""
import argparse
import json
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).parent
BODY = json.dumps({"name": "mic", "qty": 3, "tags": ["a", "b"]})
ROUTES = [  # name, path, extra oha args
    ("json", "/json", []),
    ("path+query", "/item/42?q=hello", []),
    ("depends", "/guarded", ["-H", "x-token: secret"]),
    ("pydantic POST", "/echo", ["-m", "POST", "-H", "content-type: application/json", "-d", BODY]),
    ("template (jinja)", "/page", []),
    ("static 27 KB", "/static/app.js", []),
    ("blocking 10 ms", "/slow", []),
]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_up(port, proc):
    for _ in range(100):
        if proc.poll() is not None:
            raise SystemExit(f"server exited early ({proc.returncode})")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=1).read()
            return
        except OSError:
            time.sleep(0.1)
    raise SystemExit("server never came up")


def load(oha, port, path, extra, secs, conns):
    url = f"http://127.0.0.1:{port}{path}"
    out = subprocess.run([oha, "--no-tui", "--output-format", "json", "-z", f"{secs}s", "-c", str(conns), *extra, url],
                         capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    ok = j["statusCodeDistribution"].get("200", 0)
    total = sum(j["statusCodeDistribution"].values()) or 1
    lat = j["latencyPercentiles"]
    return {"rps": j["summary"]["requestsPerSec"], "p50": lat["p50"] * 1000, "p99": lat["p99"] * 1000,
            "ok": ok / total}


def bench(label, cmd, oha, secs, conns, routes):
    port = free_port()
    proc = subprocess.Popen([*cmd, str(port)] if "uvicorn" not in label else [*cmd[:2], str(port), *cmd[2:]],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        wait_up(port, proc)
        res = {}
        for name, path, extra in routes:
            load(oha, port, path, extra, 1, conns)  # warm up
            res[name] = load(oha, port, path, extra, secs, conns)
        return res
    finally:
        proc.terminate()
        proc.wait()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fastapi-python", required=True)
    ap.add_argument("--fastrapi-python", required=True)
    ap.add_argument("--oha", default="oha")
    ap.add_argument("--secs", type=int, default=8)
    ap.add_argument("--conns", type=int, default=64)
    ap.add_argument("--uvicorn-workers", type=int, default=0)
    ap.add_argument("--json-out")
    a = ap.parse_args()
    runs = {"fastapi (uvicorn)": [a.fastapi_python, str(HERE / "app_fastapi.py")]}
    if a.uvicorn_workers > 1:
        runs[f"fastapi x{a.uvicorn_workers} workers (uvicorn)"] = [a.fastapi_python, str(HERE / "app_fastapi.py"),
                                                                  str(a.uvicorn_workers)]
    runs["fastrapi"] = [a.fastrapi_python, str(HERE / "app_fastrapi.py")]
    results = {}
    for label, cmd in runs.items():
        # fastrapi serves only the routes it implements; a route that does not answer 200 is reported "n/a".
        port_routes = ROUTES
        print(f"running {label} ...", file=sys.stderr, flush=True)
        results[label] = bench(label, cmd, a.oha, a.secs, a.conns, port_routes)
    names = [r[0] for r in ROUTES]
    print(f"\n{a.conns} connections, {a.secs}s per cell, keep-alive, loopback\n")
    print("| route | " + " | ".join(results) + " |")
    print("|---|" + "---|" * len(results))
    for n in names:
        cells = []
        for label in results:
            r = results[label][n]
            cells.append("n/a (HTTP errors)" if r["ok"] < 0.99 else
                         f"{r['rps']:,.0f} rps, p50 {r['p50']:.1f} ms, p99 {r['p99']:.1f} ms")
        print(f"| {n} | " + " | ".join(cells) + " |")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(results, indent=1))


if __name__ == "__main__":
    main()
