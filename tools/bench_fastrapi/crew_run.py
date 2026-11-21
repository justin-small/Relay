"""Before/after for the crew router: FastAPI+uvicorn (before) vs fastrapi (after), same handlers.

    python crew_run.py --fastapi-python PY --fastrapi-python PY2 --oha PATH [--secs 8] [--conns 64]

First checks that both answer the same status, body and key headers on a set of requests (parity), then loads
each route. Needs the repo's requirements in both interpreters.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from run import free_port, load  # noqa: E402

HERE = Path(__file__).parent
ROUTES = [  # name, method, path, body
    ("manifest (JSONResponse)", "GET", "/crew/_/manifest.webmanifest", None),
    ("service worker (Response)", "GET", "/crew/_/sw.js", None),
    ("offline page (HTMLResponse)", "GET", "/crew/_/offline", None),
    ("plan (Depends)", "GET", "/crew/api/plan", None),
    ("settings (Depends)", "GET", "/api/admin/crew", None),
    ("today (db + state.user)", "GET", "/crew/api/today", None),
    ("activity (db query)", "GET", "/crew/api/activity", None),
    ("push key", "GET", "/crew/api/push/key", None),
]
PARITY = [r for r in ROUTES if r[0] != "push key"] + [  # push key: a fresh random VAPID key per process
    ("unknown path", "GET", "/crew/nope", None),
    ("wrong method", "DELETE", "/crew/api/plan", None),
    ("people list", "GET", "/api/admin/crew/people", None),
    ("path param, bad body", "PUT", "/api/admin/crew/people/1/role", {"role": "nonsense"}),
    ("path param, missing user", "POST", "/api/admin/crew/people/999/name", {"name": "x"}),
    ("post dict body", "POST", "/api/admin/crew", {"name": "Church", "show_plan": False}),
    ("settings after save", "GET", "/api/admin/crew", None),
    ("post wrong type", "POST", "/api/admin/crew", [1, 2]),
]
HEADERS = ("content-type", "cache-control", "service-worker-allowed")


def call(port, method, path, body):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method,
                                 headers={"content-type": "application/json"} if data else {})
    try:
        r = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        r = e
    raw = r.read().decode("utf-8", "replace")
    try:
        raw = json.loads(raw)
    except ValueError:
        pass
    return r.status, {h: (r.headers.get(h) or "").split(";")[0] for h in HEADERS}, raw


def start(kind, python):
    port = free_port()
    proc = subprocess.Popen([python, str(HERE / "crew_app.py"), kind, str(port)],
                            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    for _ in range(150):
        if proc.poll() is not None:
            raise SystemExit(proc.stderr.read().decode()[-800:])
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/crew/_/offline", timeout=1).read()
            return proc, port
        except OSError:
            time.sleep(0.1)
    raise SystemExit("never came up")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fastapi-python", required=True)
    ap.add_argument("--fastrapi-python", required=True)
    ap.add_argument("--oha", default="oha")
    ap.add_argument("--secs", type=int, default=8)
    ap.add_argument("--conns", type=int, default=64)
    ap.add_argument("--json-out")
    a = ap.parse_args()
    runs = {"before: FastAPI + uvicorn": ("fastapi", a.fastapi_python), "after: fastrapi": ("fastrapi", a.fastrapi_python)}
    servers = {label: start(*spec) for label, spec in runs.items()}
    try:
        labels = list(servers)
        print("parity (status, headers, body):")
        diffs = 0
        for name, m, p, b in PARITY:
            got = [call(servers[l][1], m, p, b) for l in labels]
            same = got[0] == got[1]
            diffs += not same
            print(f"  {'same' if same else 'DIFF'}  {m} {p}  [{name}]")
            if not same:
                for l, g in zip(labels, got):
                    print(f"        {l}: {g[0]} {json.dumps(g[2])[:140]}")
        print(f"{diffs} of {len(PARITY)} differ\n")
        res = {l: {} for l in labels}
        for name, m, p, b in ROUTES:
            for l in labels:
                port = servers[l][1]
                load(a.oha, port, p, [], 1, a.conns)
                res[l][name] = load(a.oha, port, p, [], a.secs, a.conns)
    finally:
        for proc, _ in servers.values():
            proc.terminate()
            proc.wait()
    print(f"{a.conns} connections, {a.secs}s per cell, keep-alive, loopback\n")
    print("| route | " + " | ".join(labels) + " | speedup |")
    print("|---|---|---|---|")
    for name, *_ in ROUTES:
        r0, r1 = (res[l][name] for l in labels)
        print(f"| {name} | " + " | ".join(f"{r['rps']:,.0f} req/s, p50 {r['p50']:.1f} ms, p99 {r['p99']:.1f} ms"
                                           for r in (r0, r1)) + f" | {r1['rps'] / r0['rps']:.1f}x |")
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(res, indent=1))


main()
