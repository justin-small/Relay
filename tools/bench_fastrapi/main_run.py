"""Does Relay's whole app answer the same on fastrapi as on FastAPI?  Serves app/main.py both ways (main_app.py),
hits every parameterless GET route as Owner, and lists what differs.

    python main_run.py --fastapi-python PY --fastrapi-python PY2 [--bench --oha PATH]
"""
import argparse
import hashlib
import json
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from run import free_port, load

HERE = Path(__file__).parent
HDR = {"x-admin-token": "t0ken"}
VOLATILE = re.compile(r'("(?:ts|time|now|uptime|started|since|updated|at|date|timestamp|elapsed|version|v)"\s*:\s*)[^,}\]]+'
                      r'|\b\d{4}-\d\d-\d\dT[\d:.]+\S*|\?v=\w+', re.I)


def start(kind, python, list_only=False):
    port = free_port()
    proc = subprocess.Popen([python, str(HERE / "main_app.py"), kind, str(port)] + (["--list"] if list_only else []),
                            stdout=subprocess.PIPE if list_only else subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
    if list_only:
        out, err = proc.communicate(timeout=120)
        return json.loads(out.strip().splitlines()[-1])
    for _ in range(300):
        if proc.poll() is not None:
            raise SystemExit(f"{kind} exited: {proc.stderr.read()[-1500:]}")
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=1).read()
            return proc, port
        except urllib.error.HTTPError:
            return proc, port
        except OSError:
            time.sleep(0.2)
    raise SystemExit(f"{kind} never came up")


def get(port, path):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", headers=HDR)
    try:
        r = urllib.request.urlopen(req, timeout=4)
    except urllib.error.HTTPError as e:
        r = e
    except Exception as e:  # timeout: a stream or a probe; the same on both is fine
        return ("timeout" if "timed out" in str(e) else type(e).__name__, "", "")
    body, t0 = b"", time.time()
    try:  # a stream never ends: stop reading after 3 s or 200 kB and compare what arrived
        while len(body) < 200000 and time.time() - t0 < 3:
            chunk = r.read1(65536) if hasattr(r, "read1") else r.read(65536)
            if not chunk:
                break
            body += chunk
    except Exception:
        pass
    ct = (r.headers.get("content-type") or "").split(";")[0]
    text = VOLATILE.sub("", body.decode("utf-8", "replace"))
    return r.status, ct, hashlib.sha1(text.encode()).hexdigest()[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fastapi-python", required=True)
    ap.add_argument("--fastrapi-python", required=True)
    ap.add_argument("--oha", default="oha")
    ap.add_argument("--bench", action="store_true")
    ap.add_argument("--secs", type=int, default=8)
    ap.add_argument("--json-out")
    a = ap.parse_args()
    routes = start("fastrapi", a.fastrapi_python, list_only=True)
    paths = sorted({r["path"] for r in routes if "GET" in r["methods"] and r["path"] and "{" not in r["path"]
                    and r["kind"] == "APIRoute"})
    print(f"{len(routes)} routes on fastrapi, {len(paths)} parameterless GETs to compare")
    s0 = start("fastapi", a.fastapi_python)
    s1 = start("fastrapi", a.fastrapi_python)
    try:
        with ThreadPoolExecutor(8) as ex:
            r0 = list(ex.map(lambda p: get(s0[1], p), paths))
            r1 = list(ex.map(lambda p: get(s1[1], p), paths))
        kinds = Counter()
        rows = []
        for p, x, y in zip(paths, r0, r1):
            k = "same" if x == y else ("status differs" if x[0] != y[0] else "body/type differs")
            kinds[k] += 1
            if k != "same":
                rows.append((k, p, x, y))
        print(dict(kinds), "\n")
        for k, p, x, y in sorted(rows):
            print(f"{k:18} {p}\n   FastAPI {x[0]} {x[1]}   fastrapi {y[0]} {y[1]}")
        if a.json_out:
            Path(a.json_out).write_text(json.dumps({"paths": paths, "fastapi": r0, "fastrapi": r1}, default=str))
        if a.bench:
            print("\nload (64 conns):")
            for p in ("/api/admin/crew", "/api/admin/modules", "/static/relay-ui.js", "/"):
                a0, a1 = (load(a.oha, s[1], p, ["-H", "x-admin-token: t0ken"], a.secs, 64) for s in (s0, s1))
                print(f"  {p}: FastAPI {a0['rps']:,.0f} req/s ({a0['ok']:.0%} ok)  fastrapi {a1['rps']:,.0f} req/s ({a1['ok']:.0%} ok)")
    finally:
        for s in (s0, s1):
            s[0].terminate()
            s[0].wait()


if __name__ == "__main__":
    main()
