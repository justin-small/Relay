"""Middleware, audit, headers and WebSocket checks on the whole app, FastAPI vs fastrapi."""
import asyncio
import json
import sys
import urllib.error
import urllib.request

import main_run as m

a = m.start("fastapi", sys.argv[1])
b = m.start("fastrapi", sys.argv[2])


def call(port, method, path, body=None, auth=True):
    h = dict(m.HDR) if auth else {}
    data = None
    if body is not None:
        data, h["content-type"] = json.dumps(body).encode(), "application/json"
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=h)
    try:
        r = urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        r = e
    raw = r.read().decode("utf-8", "replace")
    return r, raw


def hdrs(r):
    skip = {"date", "content-length", "etag", "last-modified", "server", "connection", "keep-alive"}
    return {k.lower(): v for k, v in r.headers.items() if k.lower() not in skip}


async def ws(port, path):
    import websockets
    try:
        async with websockets.connect(f"ws://127.0.0.1:{port}{path}", open_timeout=4) as c:
            try:
                return "open, first frame: " + str(await asyncio.wait_for(c.recv(), 1.5))[:60]
            except asyncio.TimeoutError:
                return "open, no frame"
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:70]}"


try:
    print("response headers (middleware):")
    for p, auth in (("/", False), ("/api/admin/state", True), ("/static/relay-ui.js", False), ("/nope", False)):
        ra, rb = call(a[1], "GET", p, auth=auth)[0], call(b[1], "GET", p, auth=auth)[0]
        ha, hb = hdrs(ra), hdrs(rb)
        print(f"  {p}: status {ra.status}/{rb.status}", "headers same" if ha == hb else "headers DIFFER")
        for k in sorted(set(ha) | set(hb)):
            if ha.get(k) != hb.get(k):
                print(f"      {k}: FastAPI={ha.get(k)!r} fastrapi={hb.get(k)!r}")
    print("audit middleware (POST then read the log):")
    for label, s in (("FastAPI", a), ("fastrapi", b)):
        r, raw = call(s[1], "POST", "/api/admin/crew", {"name": "Audit Test", "show_plan": False})
        r2, raw2 = call(s[1], "GET", "/api/admin/audit?kind=crew")
        try:
            n = len(json.loads(raw2).get("entries", []))
        except Exception:
            n = f"unreadable ({r2.status}: {raw2[:80]})"
        print(f"  {label}: POST {r.status}, audit entries {n}")
    print("bad input:")
    for method, p, body in (("POST", "/api/admin/crew", [1]), ("PUT", "/api/admin/crew", {}), ("GET", "/api/admin/crew?x=%ff", None)):
        print(f"  {method} {p}: FastAPI {call(a[1], method, p, body)[0].status}  fastrapi {call(b[1], method, p, body)[0].status}")
    print("websockets:")
    for p in ("/intercom/ws", "/intercom/rtc", "/intercom/remote/ws"):
        print(f"  {p}: FastAPI [{asyncio.run(ws(a[1], p))}]  fastrapi [{asyncio.run(ws(b[1], p))}]")
finally:
    a[0].terminate()
    b[0].terminate()
