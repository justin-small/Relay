"""Try Relay's UniFi reading against the church's real controller (#358). Read-only.

  1. Fill in unifi-test.env (git-ignored) at the repo root: the console's address, a read-only API key and a local
     read-only user.
  2. .venv/bin/python tools/check_unifi.py            every endpoint: status and the shape of the answer
     .venv/bin/python tools/check_unifi.py --module   also run the module's own client the way the poller does
     .venv/bin/python tools/check_unifi.py --events 20   also listen to the event stream for 20 seconds

It prints the certificate's fingerprint (compare it with the console's), each endpoint's HTTP status and the shape
of what came back: field names and types, counts, and values only for fields that say nothing about a person
(model, firmware, speed, PoE). It never prints the key, the password, a MAC address, a client name or an address.
With --raw DIR it also saves every answer as JSON in DIR, for working out the parser; keep DIR off the repo.

Some reads on UniFi are POSTs (reports, traffic flows). They change nothing on the controller.
"""
from __future__ import annotations

import argparse
import asyncio
import http.client
import json
import sys
import time
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import netsock  # noqa: E402
from app.modules.unifi import controller as uc  # noqa: E402

ENV = ROOT / "unifi-test.env"
SAFE = {"type", "model", "model_name", "shortname", "version", "firmware", "firmwareVersion", "state", "is_wired",
        "speed", "full_duplex", "media", "poe_mode", "poe_enable", "poe_power", "poe_class", "is_uplink", "up",
        "stp_state", "radio", "channel", "subsystem", "status", "role", "purpose", "vlan_enabled", "enabled",
        "apiVersion", "applicationVersion", "key", "subsystem", "uplink_type", "satisfaction", "num_sta",
        "unifi_device", "adopted", "features", "supported", "kind", "port_idx", "op_mode", "speed_caps",
        "connectionType", "interfaces", "meta", "rc", "count", "totalCount", "limit", "offset", "wan_type"}


def read_env() -> dict:
    out = {}
    for line in ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []:
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip("'\"")
    return out


def shape(v, depth=0, key=""):
    """What a value looks like, without the personal parts."""
    if isinstance(v, dict):
        if depth > 3:
            return "{…}"
        return {k: shape(x, depth + 1, k) for k, x in list(v.items())[:60]}
    if isinstance(v, list):
        if not v:
            return []
        return [f"{len(v)} items", shape(v[0], depth + 1, key)]
    if key in SAFE and isinstance(v, (str, int, float, bool)) or isinstance(v, (bool, type(None))):
        return v
    if isinstance(v, (int, float)):
        return type(v).__name__
    if isinstance(v, str):
        return f"str({len(v)})"
    return type(v).__name__


class Session:
    def __init__(self, env: dict, fp: str):
        self.host, self.port = env["UNIFI_HOST"], int(env.get("UNIFI_PORT") or 443)
        self.fp, self.key = fp, env.get("UNIFI_API_KEY", "")
        self.cookie, self.csrf = "", ""

    def call(self, method: str, path: str, body=None, auth: str = "key") -> tuple[int, object, dict]:
        conn = netsock.http_connection(uc.TRAFFIC, self.host, self.port, tls=True, timeout=15,
                                       context=uc._unverified())
        conn.connect()
        der = conn.sock.getpeercert(binary_form=True)
        if uc.fingerprint_of(der) != self.fp:
            conn.close()
            raise SystemExit("The certificate changed during the run. Stopping.")
        headers = {"Accept": "application/json", "User-Agent": "Relay spike"}
        if auth == "key":
            headers["X-API-KEY"] = self.key
        elif auth == "session" and self.cookie:
            headers["Cookie"] = self.cookie
            if self.csrf:
                headers["X-CSRF-Token"] = self.csrf
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            r = conn.getresponse()
            raw = r.read(uc.LIMIT)
            hdrs = {k.lower(): v for k, v in r.getheaders()}
            status = r.status
        except (OSError, http.client.HTTPException) as e:
            return 0, f"{type(e).__name__}", {}
        finally:
            conn.close()
        try:
            return status, json.loads(raw) if raw else None, hdrs
        except ValueError:
            return status, f"<{len(raw)} bytes, not JSON>", hdrs


def out(label, status, d, raw_dir, name):
    print(f"\n== {label}: HTTP {status}")
    if raw_dir and status == 200:
        (raw_dir / f"{name}.json").write_text(json.dumps(d, indent=1))
    if status == 200:
        print(json.dumps(shape(d), indent=1)[:2500])
    elif isinstance(d, (dict, list)):
        print("   ", json.dumps(shape(d))[:300])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", type=Path)
    ap.add_argument("--module", action="store_true")
    ap.add_argument("--events", type=int, default=0)
    a = ap.parse_args()
    env = read_env()
    if not env.get("UNIFI_HOST"):
        print(f"Fill in {ENV.name} first.")
        return 2
    if a.raw:
        a.raw.mkdir(parents=True, exist_ok=True)
    site = env.get("UNIFI_SITE") or "default"
    probe = uc.Controller(env["UNIFI_HOST"], int(env.get("UNIFI_PORT") or 443), "", "", site)
    try:
        fp = probe.peer_fingerprint()
    except uc.Problem as e:
        print("Could not reach the console:", e)
        return 1
    print("Certificate SHA-256:", uc.show_fingerprint(fp))
    try:                                   # is it trusted by this computer as it is?
        uc.Controller(probe.host, probe.port, "", "", site)._connect().close()
        print("Trusted by this computer's certificate store: yes")
    except uc.Untrusted:
        print("Trusted by this computer's certificate store: no (self-signed); the spike pins the fingerprint above")
    except uc.Problem as e:
        print("Connect without a pin:", e)

    s = Session(env, fp)
    q = urllib.parse.quote(site, safe="")
    t0 = time.time()

    # ---------------------------------------------------------------- the API key
    print("\n######## with the API key")
    st, info, _ = s.call("GET", "/proxy/network/integration/v1/info")
    out("integration info", st, info, a.raw, "int-info")
    st, sites, _ = s.call("GET", "/proxy/network/integration/v1/sites?limit=50")
    out("integration sites", st, sites, a.raw, "int-sites")
    sid = ""
    if st == 200:
        for x in sites.get("data", []):
            if site in (x.get("internalReference"), x.get("id"), x.get("name")):
                sid = x["id"]
        print("site found:", bool(sid), "| sites:", len(sites.get("data", [])))
    devs = []
    if sid:
        for name, path in [("devices", f"sites/{sid}/devices?limit=200"), ("clients", f"sites/{sid}/clients?limit=200"),
                           ("networks", f"sites/{sid}/networks?limit=200"), ("wifi", f"sites/{sid}/wifi/broadcasts?limit=50"),
                           ("wans", f"sites/{sid}/wans"), ("vpn", f"sites/{sid}/vpn/servers"),
                           ("fw-zones", f"sites/{sid}/firewall/zones"), ("acl", f"sites/{sid}/acl-rules"),
                           ("pending", "pending-devices"), ("dpi-apps", "dpi/applications?limit=5"),
                           ("countries", "countries?limit=1")]:
            st, d, _ = s.call("GET", f"/proxy/network/integration/v1/{path}")
            out(f"integration {name}", st, d, a.raw, f"int-{name}")
            if name == "devices" and st == 200:
                devs = d.get("data", [])
        for dv in devs[:3]:
            st, d, _ = s.call("GET", f"/proxy/network/integration/v1/sites/{sid}/devices/{dv['id']}")
            out(f"integration device detail ({dv.get('model')})", st, d, a.raw, f"int-device-{dv.get('model')}")
            st, d, _ = s.call("GET", f"/proxy/network/integration/v1/sites/{sid}/devices/{dv['id']}/statistics/latest")
            out(f"integration device stats ({dv.get('model')})", st, d, a.raw, f"int-devstats-{dv.get('model')}")

    legacy = ["stat/sta", "stat/device-basic", "stat/device", "stat/health", "stat/sysinfo", "rest/networkconf",
              "rest/wlanconf", "rest/portconf", "rest/user", "stat/event?_limit=5", "rest/alarm?_limit=5",
              "list/alarm", "stat/sitedpi", "stat/stadpi", "stat/routing", "stat/dynamicdns", "stat/rogueap",
              "stat/current-channel", "stat/spectrumscan", "stat/portforward", "rest/setting"]
    for auth in ("key",):
        for p in legacy:
            st, d, _ = s.call("GET", f"/proxy/network/api/s/{q}/{p}", auth=auth)
            out(f"legacy {p} [{auth}]", st, d, a.raw, f"legacy-{auth}-{p.split('?')[0].replace('/', '-')}")
    v2 = [("GET", f"/proxy/network/v2/api/site/{q}/clients/active?includeTrafficUsage=true", None),
          ("GET", f"/proxy/network/v2/api/site/{q}/clients/history?onlyNonBlocked=true&withinHours=24", None),
          ("GET", f"/proxy/network/v2/api/site/{q}/device", None),
          ("GET", f"/proxy/network/v2/api/site/{q}/wan/enriched-configuration", None),
          ("GET", f"/proxy/network/v2/api/site/{q}/system-log/setting", None),
          ("POST", f"/proxy/network/v2/api/site/{q}/traffic-flows", {"timestampFrom": int(t0 - 3600) * 1000,
                                                                       "timestampTo": int(t0) * 1000, "pageSize": 5,
                                                                       "pageNumber": 0}),
          ("POST", f"/proxy/network/api/s/{q}/stat/report/5minutes.site",
           {"attrs": ["wan-tx_bytes", "wan-rx_bytes", "num_sta", "latency_avg"], "start": int(t0 - 3600) * 1000,
            "end": int(t0) * 1000}),
          ("POST", f"/proxy/network/api/s/{q}/stat/report/archive.speedtest",
           {"attrs": ["xput_download", "xput_upload", "latency", "time"], "start": int(t0 - 30 * 86400) * 1000,
            "end": int(t0) * 1000})]
    for auth in ("key",):
        for m, p, b in v2:
            st, d, _ = s.call(m, p, b, auth=auth)
            out(f"{m} {p.split('/proxy/network/')[1].split('?')[0]} [{auth}]", st, d, a.raw,
                f"x-{auth}-" + p.split("/")[-1].split("?")[0])

    # ---------------------------------------------------------------- the local user
    print("\n######## with the local user")
    if env.get("UNIFI_USERNAME") and env.get("UNIFI_PASSWORD"):
        st, d, h = s.call("POST", "/api/auth/login", {"username": env["UNIFI_USERNAME"],
                                                       "password": env["UNIFI_PASSWORD"], "rememberMe": False},
                          auth="none")
        cookie = h.get("set-cookie", "")
        s.cookie = cookie.split(";")[0] if cookie else ""
        s.csrf = h.get("x-csrf-token") or h.get("x-updated-csrf-token") or ""
        print(f"\n== login: HTTP {st} | session cookie: {bool(s.cookie)} | csrf header: {bool(s.csrf)}")
        if isinstance(d, dict) and st == 200:
            print("   user fields:", sorted(d.keys())[:40])
            print("   roles/permissions keys present:", [k for k in d if "perm" in k.lower() or "role" in k.lower()])
        if s.cookie:
            for p in ["stat/sta", "stat/device", "stat/health", "stat/event?_limit=5", "rest/alarm?_limit=5",
                      "stat/sitedpi", "stat/stadpi", "rest/user", "stat/rogueap"]:
                st, d, _ = s.call("GET", f"/proxy/network/api/s/{q}/{p}", auth="session")
                out(f"legacy {p} [session]", st, d, a.raw, f"legacy-session-{p.split('?')[0].replace('/', '-')}")
            for m, p, b in v2:
                st, d, _ = s.call(m, p, b, auth="session")
                out(f"{m} {p.split('/proxy/network/')[1].split('?')[0]} [session]", st, d, a.raw,
                    "x-session-" + p.split("/")[-1].split("?")[0])
            if a.events:
                asyncio.run(events(s, q, a.events))
            s.call("POST", "/api/auth/logout", {}, auth="session")
    else:
        print("no local user in the file")

    if a.module:
        print("\n######## the module's own client")
        ctl = uc.Controller(s.host, s.port, s.key, fp, site)
        try:
            print("site:", shape(ctl.find_site()))
            cl = ctl.clients()
            print(f"clients: {len(cl)} via {ctl.api}")
            for k in ("hostname", "ip", "uplink", "port", "ssid", "network", "vlan", "name"):
                print(f"   with {k}: {sum(1 for c in cl if c.get(k) not in ('', None))}")
            print(f"   wired: {sum(c['wired'] for c in cl)}  guest: {sum(c['guest'] for c in cl)}  "
                  f"private MAC: {sum(c['private'] for c in cl)}")
            names = ctl.devices()
            print(f"devices named: {len(names)}; clients whose uplink has a name: "
                  f"{sum(1 for c in cl if c['uplink'] in names)}")
        except uc.Problem as e:
            print("module client failed:", e)
    print(f"\ndone in {time.time() - t0:.1f}s")
    return 0


async def events(s: Session, q: str, seconds: int) -> None:
    ctx = uc._unverified()
    url = f"wss://{s.host}:{s.port}/proxy/network/wss/s/{q}/events?clients=v2"
    kinds: dict[str, int] = {}
    print(f"\n== event stream for {seconds}s")
    try:          # websockets 13's legacy client takes extra_headers; the stream needs the session, not the key
        ws = await netsock.websocket(uc.TRAFFIC, url, ssl=ctx, extra_headers={"Cookie": s.cookie})
    except Exception as e:
        print("   could not open:", type(e).__name__, str(e)[:120])
        return
    der = ws.transport.get_extra_info("ssl_object").getpeercert(binary_form=True)
    if uc.fingerprint_of(der) != s.fp:
        await ws.close()
        print("   certificate changed; stopped")
        return
    end = time.time() + seconds
    sample: dict[str, object] = {}
    try:
        while time.time() < end:
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=end - time.time())
            except asyncio.TimeoutError:
                break
            try:
                d = json.loads(msg)
            except ValueError:
                continue
            k = str((d.get("meta") or {}).get("message"))
            kinds[k] = kinds.get(k, 0) + 1
            sample.setdefault(k, shape(d))
    finally:
        await ws.close()
    print("   messages by kind:", kinds)
    for k, v in sample.items():
        print(f"   -- {k}:", json.dumps(v)[:600])


if __name__ == "__main__":
    sys.exit(main())
