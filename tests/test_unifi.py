"""UniFi module (#358) against a fake UniFi OS console over real HTTPS with a self-signed certificate. The answers are
shaped like a real UCG Max on Network 10.6.109 (recorded with tools/check_unifi.py, values made up): the
certificate pin, the API key, the client list (v2, then stat/sta, then the integration API), presence, internet
health and its history, gear and ports, Wi-Fi, the change log, NDI and Dante from flows, the admin routes, and the
crew page's Wi-Fi check-in (#356). No real network.

Run: .venv/bin/python tests/test_unifi.py
"""
import asyncio
import datetime as dt
import json
import os
import ssl
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["RELAY_CONFIG"] = os.path.join(tempfile.mkdtemp(), "config.json")
os.environ["RELAY_ANNOUNCE"] = "0"
os.environ.pop("RELAY_DB_BACKUPS", None)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _db  # noqa: E402

_db.start()
_db.reset()

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec  # noqa: E402
from cryptography.x509.oid import NameOID  # noqa: E402

import app.main as main  # noqa: E402,F401
from app import config, users  # noqa: E402
from app.main import app  # noqa: E402
from app.modules import modules as registry  # noqa: E402
from app.modules.unifi import controller as uc, history, readings  # noqa: E402
from app.modules.unifi.presence import Presence  # noqa: E402
from app.modules.unifi.service import clean_settings, public, service  # noqa: E402
from _harness import check, done, eq, section, tmpdir  # noqa: E402

KEY = "test-read-only-key"
SITE_ID = "8f1c2d3e-0000-4000-8000-000000000001"
PHONE, TV, CAM, SW, AP, GW = ("aa:bb:cc:00:00:01", "aa:bb:cc:00:00:02", "aa:bb:cc:00:00:03", "d8:b3:70:00:00:10",
                              "9c:05:d6:00:00:20", "70:a7:41:00:00:30")


def cert(dirpath: str) -> tuple[str, str]:
    k = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "unifi.local")])
    now = dt.datetime.now(dt.timezone.utc)
    c = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(k.public_key())
         .serial_number(x509.random_serial_number()).not_valid_before(now - dt.timedelta(days=1))
         .not_valid_after(now + dt.timedelta(days=30)).sign(k, hashes.SHA256()))
    cp, kp = os.path.join(dirpath, "c.pem"), os.path.join(dirpath, "k.pem")
    Path(cp).write_bytes(c.public_bytes(serialization.Encoding.PEM))
    Path(kp).write_bytes(k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()))
    return cp, kp


class Fake:
    """What the console answers. Tests change these between reads."""
    v2 = True
    legacy = True
    clients: list = []
    seen: list = []
    login_ok = True


def v2_client(mac, name, ip, wired=False, port=None, host="", up=AP):
    c = {"mac": mac, "display_name": name, "hostname": host, "ip": ip, "is_wired": wired, "is_guest": False,
         "last_uplink_mac": up, "last_uplink_name": "U7 Pro XG" if not wired else "USW Flex Mini",
         "network_name": "Default", "essid": "" if wired else "Church", "model_name": "Apple iPhone 13",
         "signal": -58, "rx_bytes-r": 1000, "tx_bytes-r": 500, "status": "online", "type": "WIRED" if wired else "WIRELESS"}
    if wired:
        c.update(uplink_mac=up, sw_port=port)
    return c


DEVICES = [
    {"mac": GW, "name": "Office gateway", "model": "UCGMAX", "type": "udm", "state": 1, "version": "6.0.17",
     "upgradable": False, "ip": "203.0.113.5", "port_table": [
         {"port_idx": 1, "up": True, "speed": 2500, "speed_caps": 1 << 10 | 1 << 5, "full_duplex": True,
          "rx_bytes-r": 10, "tx_bytes-r": 10, "rx_errors": 0, "tx_errors": 0, "name": "Port 1"}]},
    {"mac": SW, "name": "Booth switch", "model": "USMINI", "type": "usw", "state": 1, "version": "2.1.6",
     "upgradable": True, "upgrade_to_firmware": "2.1.7", "ip": "192.168.1.56",
     "uplink": {"uplink_device_name": "Office gateway", "uplink_remote_port": 4, "speed": 1000, "type": "wire"},
     "port_table": [
         {"port_idx": 1, "up": True, "is_uplink": True, "speed": 1000, "speed_caps": 1 << 5, "full_duplex": True,
          "rx_bytes-r": 100_000_000, "tx_bytes-r": 1_000_000, "rx_errors": 0, "tx_errors": 0},
         {"port_idx": 2, "up": True, "speed": 100, "speed_caps": 1 << 5 | 1 << 3, "full_duplex": True,
          "rx_bytes-r": 1000, "tx_bytes-r": 1000, "rx_errors": 0, "tx_errors": 0, "poe_enable": True,
          "poe_power": "4.20", "poe_good": True},
         {"port_idx": 3, "up": True, "speed": 1000, "speed_caps": 1 << 5, "full_duplex": True,
          "rx_bytes-r": 0, "tx_bytes-r": 0, "rx_errors": 5, "tx_errors": 0}]},
    {"mac": AP, "name": "U7 Pro XG", "model": "UAPA6AE", "type": "uap", "state": 1, "version": "8.7.11",
     "upgradable": False, "radio_table_stats": [
         {"radio": "ng", "channel": 11, "num_sta": 4, "cu_total": 81, "tx_retries_pct": 2, "satisfaction": -1},
         {"radio": "na", "channel": 161, "num_sta": 9, "cu_total": 9, "tx_retries_pct": 12.1, "satisfaction": 96}]},
]
HEALTH = [{"subsystem": "wan", "status": "ok", "isp_name": "Example ISP", "rx_bytes-r": 1000, "tx_bytes-r": 500,
           "uptime_stats": {"WAN": {"availability": 99.5, "latency_average": 19, "monitors": [
               {"target": "1.1.1.1", "availability": 100.0, "latency_average": 19, "type": "icmp"}]}}},
          {"subsystem": "www", "status": "ok", "rx_bytes-r": 250_000, "tx_bytes-r": 20_000, "latency": 21, "drops": 2,
           "uptime": 3600, "xput_down": 927.0, "xput_up": 42.0, "speedtest_ping": 29, "speedtest_lastrun": 1_700_000_000}]
CHANGES = {"data": [
    {"key": "ADMIN_ACCESS", "admin": {"name": "Pat Admin"}, "ip": "192.168.1.9", "platform": "web",
     "message": "{ADMIN} accessed UniFi Network using the {PLATFORM}. Source IP: {IP}", "timestamp": 1_700_000_100_000},
    {"key": "MADE_CHANGES_ONE_SETTING_OBJECT", "meta": {"actor": "Pat Admin", "section": "WIFI",
     "display_property_name": "name", "display_property_value": "Church"},
     "updates": [{"property": "x_passphrase", "type": "UPDATE"}],
     "message": "{ADMIN} made a change to {SETTING_NAME} in {OBJECT} {SECTION} settings. Source IP: {IP}",
     "timestamp": 1_700_000_200_000}]}
FLOWS = {"data": [
    {"protocol": "TCP", "source": {"mac": CAM, "ip": "192.168.1.30", "port": 5961},
     "destination": {"mac": PHONE, "ip": "192.168.1.20", "port": 50122},
     "traffic_data": {"bytes_total": 3_750_000_000}},
    {"protocol": "UDP", "source": {"mac": TV, "ip": "192.168.1.21", "port": 319},
     "destination": {"ip": "224.0.1.129", "port": 319}, "traffic_data": {"bytes_total": 1000}},
    {"protocol": "TCP", "source": {"mac": PHONE, "port": 51000}, "destination": {"ip": "8.8.8.8", "port": 443},
     "traffic_data": {"bytes_total": 99}}], "has_next": False}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, status, body, headers=None):
        raw = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _route(self, method):
        Fake.seen.append((method, self.path))
        length = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(length) or b"null") if length else None
        if self.path == "/api/auth/login":
            ok = Fake.login_ok and body and body.get("password") == "local-pass"
            return self._send(200 if ok else 401, {}, {"Set-Cookie": "TOKEN=abc; Path=/; Secure"} if ok else None)
        if self.path == "/api/auth/logout":
            return self._send(200, {})
        if self.headers.get("X-API-KEY") != KEY:
            return self._send(401, {"error": "unauthorized"})
        p = self.path
        if p.startswith("/proxy/network/integration/v1/sites?"):
            return self._send(200, {"data": [{"id": SITE_ID, "internalReference": "default", "name": "Default"}]})
        if p.startswith("/proxy/network/v2/api/site/default/clients/active"):
            return self._send(200, Fake.clients) if Fake.v2 else self._send(404, "not found")
        if p == "/proxy/network/api/s/default/stat/sta":
            if not Fake.legacy:
                return self._send(404, {"meta": {"rc": "error"}})
            return self._send(200, {"data": [{"mac": c["mac"], "ip": c["ip"], "hostname": c["hostname"],
                                              "is_wired": c["is_wired"], "ap_mac": AP} for c in Fake.clients]})
        if p.startswith(f"/proxy/network/integration/v1/sites/{SITE_ID}/clients"):
            return self._send(200, {"data": [{"macAddress": c["mac"], "ipAddress": c["ip"], "name": c["display_name"],
                                              "type": "WIRELESS"} for c in Fake.clients], "totalCount": len(Fake.clients)})
        if p == "/proxy/network/api/s/default/stat/health":
            return self._send(200, {"data": HEALTH})
        if p == "/proxy/network/api/s/default/stat/device":
            return self._send(200, {"data": DEVICES})
        if p == "/proxy/network/api/s/default/stat/device-basic":
            return self._send(200, {"data": [{"mac": d["mac"], "name": d["name"]} for d in DEVICES]})
        if p == "/proxy/network/api/s/default/stat/rogueap":
            return self._send(200, {"data": [{"channel": 11, "is_ubnt": False}, {"channel": 11, "is_ubnt": True}]})
        if p == "/proxy/network/api/s/default/stat/report/5minutes.site":
            return self._send(200, {"data": [{"time": 1_700_000_000_000, "wan-rx_bytes": 3e8, "wan-tx_bytes": 3e7, "latency_avg": 20}]})
        if p == "/proxy/network/api/s/default/stat/report/archive.speedtest":
            return self._send(200, {"data": [{"time": 1_700_000_000_000, "xput_download": 927.0, "xput_upload": 42.0, "latency": 29}]})
        if p == "/proxy/network/v2/api/site/default/traffic-flows":
            return self._send(200, FLOWS)
        if p == "/proxy/network/v2/api/site/default/system-log/admin-activity":
            return self._send(200, CHANGES)
        return self._send(404, "not found")

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")


DIR = tmpdir()
CERT, PRIV = cert(DIR)
server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
ctx.minimum_version = ssl.TLSVersion.TLSv1_2
ctx.load_cert_chain(CERT, PRIV)
server.socket = ctx.wrap_socket(server.socket, server_side=True)
threading.Thread(target=server.serve_forever, daemon=True).start()
PORT = server.server_address[1]
FP = uc.fingerprint_of(ssl.PEM_cert_to_DER_cert(Path(CERT).read_text()))
Fake.clients = [v2_client(PHONE, "Pat's iPhone", "192.168.1.20", host="pats-iphone"),
                v2_client(TV, "Lobby TV", "192.168.1.21", wired=True, port=2, up=SW),
                v2_client(CAM, "PTZ Cam 1", "192.168.1.30", wired=True, port=3, up=SW)]


with section("cleaning what an admin types"):
    eq("a pasted console URL", uc.clean_host("https://192.168.1.1/network/default/dashboard"), "192.168.1.1")
    eq("host:port", uc.clean_host("unifi.local:8443"), "unifi.local")
    eq("an IPv6 address", uc.clean_host("[fe80::1]"), "fe80::1")
    eq("nonsense", uc.clean_host("not a host!"), "")
    eq("fingerprint with colons", uc.clean_fingerprint(uc.show_fingerprint(FP)), FP)
    eq("a short fingerprint", uc.clean_fingerprint("AB:CD"), "")
    eq("MAC from dashes", uc.clean_mac("AA-BB-CC-00-00-01"), PHONE)
    eq("not a MAC", uc.clean_mac("aa:bb"), "")
    s = clean_settings({"host": "https://10.0.0.1", "port": "x", "site": "de fault!", "poll_s": 1, "away_s": 99999,
                        "api_key": " k ", "probe_target": ""})
    eq("settings cleaned", (s["host"], s["port"], s["site"], s["poll_s"], s["away_s"], s["api_key"], s["probe_target"]),
       ("10.0.0.1", 443, "default", 10, 3600, "k", "1.1.1.1"))
    pub = public({**s, "password": "p"})
    check("public() hides the key and the password", "api_key" not in pub and "password" not in pub
          and pub["api_key_set"] and pub["password_set"])

with section("the certificate"):
    c = uc.Controller("127.0.0.1", PORT, KEY)
    try:
        c.find_site()
        check("an untrusted certificate is refused", False)
    except uc.Untrusted as e:
        eq("refused, with the certificate's fingerprint", e.fingerprint, FP)
    c = uc.Controller("127.0.0.1", PORT, KEY, "00" * 32)
    try:
        c.find_site()
        check("a different pinned certificate is refused", False)
    except uc.Untrusted:
        check("a different pin is not 'untrusted'", False)
    except uc.Problem as e:
        check("a changed certificate is refused", "changed" in str(e))
    c = uc.Controller("127.0.0.1", PORT, KEY, FP)
    eq("pinned: the site is found", c.find_site()["id"], SITE_ID)
    bad = uc.Controller("127.0.0.1", PORT, "wrong", FP)
    try:
        bad.find_site()
    except uc.Problem as e:
        check("a wrong key says so", "API key" in str(e))
    try:
        uc.Controller("127.0.0.1", PORT, KEY, FP).query("/proxy/network/api/s/default/cmd/devmgr", {})
        check("a POST that is not a read is refused", False)
    except ValueError:
        check("a POST that is not a read is refused", True)

with section("the client list: v2, then stat/sta, then the integration API"):
    c = uc.Controller("127.0.0.1", PORT, KEY, FP)
    cl = c.clients()
    eq("v2 first", c.api, "v2")
    phone = next(x for x in cl if x["mac"] == PHONE)
    eq("the name the UniFi app shows, hostname and model", (phone["name"], phone["hostname"], phone["model"]),
       ("Pat's iPhone", "pats-iphone", "Apple iPhone 13"))
    tv = next(x for x in cl if x["mac"] == TV)
    eq("a wired client's switch and port", (tv["wired"], tv["uplink"], tv["port"]), (True, SW, 2))
    Fake.v2 = False
    eq("stat/sta when v2 does not answer", (len(c.clients()), c.api), (3, "legacy"))
    Fake.legacy = False
    eq("the integration API last", (len(c.clients()), c.api), (3, "integration"))
    Fake.v2 = Fake.legacy = True
    check("only GETs and read queries were sent",
          all(m == "GET" or any(k in p for k in uc.READ_POSTS) for m, p in Fake.seen))

with section("presence"):
    pr = Presence()
    a = {"mac": PHONE, "uplink": AP, "port": None}
    eq("the first read makes no joins", pr.update([a], 0, 300), [])
    eq("a new client joins", [e["kind"] for e in pr.update([a, {"mac": TV, "uplink": SW, "port": 2}], 30, 300)], ["join"])
    eq("moving port is a roam", [e["kind"] for e in pr.update([a, {"mac": TV, "uplink": SW, "port": 4}], 60, 300)], ["roam"])
    eq("missing for a while is not a leave yet", pr.update([a], 90, 300), [])
    check("still online while missing", any(c["mac"] == TV for c in pr.online()))
    eq("back within the grace: nothing", pr.update([a, {"mac": TV, "uplink": SW, "port": 4}], 120, 300), [])
    pr.update([a], 150, 300)
    eq("gone past the grace: a leave", [e["kind"] for e in pr.update([a], 460, 300)], ["leave"])
    heard = []
    stop = pr.listen(heard.append)
    pr.update([a, {"mac": CAM}], 500, 300)
    stop()
    pr.update([a, {"mac": CAM}, {"mac": TV}], 530, 300)
    eq("listeners hear events until stopped", [e["mac"] for e in heard], [CAM])
    pr.clients[PHONE]["ip"] = "192.168.1.20"
    eq("by address", pr.by_ip("192.168.1.20")["mac"], PHONE)

with section("readings"):
    net = readings.internet(HEALTH)
    eq("internet now", (net["status"], net["latency_ms"], net["drops"], net["availability"], net["down_bps"]),
       ("ok", 21.0, 2.0, 99.5, 2_000_000.0))
    clients = uc.Controller("127.0.0.1", PORT, KEY, FP).clients()
    g = readings.gear(DEVICES, clients)
    eq("gateway first, then switch, then AP", [x["kind"] for x in g], ["gateway", "switch", "access point"])
    sw = g[1]
    eq("firmware update", sw["update"], "2.1.7")
    eq("who is on the port", sw["ports"][1]["client"], "Lobby TV")
    check("100 Mbps on a gigabit port is a warning", "100 Mbps" in sw["ports"][1]["warn"])
    check("an uplink 81% full is a warning", "81% used" in sw["ports"][0]["warn"])
    eq("PoE draw", (sw["ports"][1]["poe_w"], sw["poe_w"]), (4.2, 4.2))
    g2 = readings.gear([{**DEVICES[1], "port_table": [{**p, "rx_errors": p["rx_errors"] + (20 if p["port_idx"] == 3 else 0)}
                                                       for p in DEVICES[1]["port_table"]]}], clients, readings.counters(g))
    check("rising errors are a warning", "20 new errors" in g2[0]["ports"][2]["warn"])
    w = readings.wifi(g, clients, [{"channel": 11, "is_ubnt": False}, {"channel": 11, "is_ubnt": True}])
    ng = w["radios"][0]
    eq("a busy channel, neighbours not counting our own", (ng["warn"][:13], ng["neighbours"], ng["satisfaction"]),
       ("Channel 11 is", 1, None))
    ch = readings.changes(CHANGES["data"])
    eq("the change log, newest first, as sentences", ch[0]["text"],
       "Pat Admin made a change to name in Church wifi settings.")
    eq("a sign-in", (ch[1]["change"], ch[1]["text"]),
       (False, "Pat Admin accessed UniFi Network using the web. Source IP: 192.168.1.9"))
    av = readings.av(FLOWS["data"], clients, 300)
    kinds = {x["mac"]: x["kinds"] for x in av}
    eq("NDI on both ends of the flow", (kinds.get(CAM), kinds.get(PHONE)), (["ndi"], ["ndi"]))
    eq("PTP clocking", kinds.get(TV), ["ptp"])
    eq("an NDI stream's rate", round(next(x for x in av if x["mac"] == CAM)["bps"] / 1e6), 100)
    eq("speed tests", readings.speedtests([{"time": 1_700_000_000_000, "xput_download": 900.0}])[0]["down_mbps"], 900.0)

with section("history"):
    t = 2_000_000_000
    for i in range(3 * 24 * 60 // 10):              # three days, a reading every 10 minutes
        history.record({"down_bps": 1e6 * (i % 7), "latency_ms": 20 + i % 3, "ext_loss": 0.0 if i % 50 else 33.3,
                        "availability": 100.0}, t - i * 600)
    history.fold(t)
    from app import db
    eq("no minute rows older than 48 hours", db.one("select count(*) as n from unifi_wan_1m where ts < ?",
                                                    (t - 48 * 3600 - 300,))["n"], 0)
    check("they became 5-minute rows", db.one("select count(*) as n from unifi_wan_5m")["n"] > 0)
    rows = history.series(72, t)
    check("72 hours read back across both tables", len(rows) >= 400, len(rows))
    check("oldest first", rows[0]["ts"] < rows[-1]["ts"])
    pk = history.worst(72, t)
    eq("peaks", (pk["down_bps"], pk["ext_loss"], pk["latency_ms"]), (6e6, 33.3, 22.0))
    history.fold(t + 31 * 86400)
    eq("nothing kept past 30 days", history.series(24 * 30, t + 31 * 86400), [])
    ms, loss = history.probe("127.0.0.1", PORT, tries=2)
    check("Relay's probe reaches a listening port", ms is not None and loss == 0.0)
    eq("a closed port is lost", history.probe("127.0.0.1", 1, tries=1), (None, 100.0))

config.ensure_file()
config.save({"admin_token": "t0ken", "port": 8000, "admin_port": 8001,
             "modules": {"unifi": {"enabled": True}, "crew": {"enabled": True}}})
admin = _db.client(app, base_url="http://relay.local:8001")
viewer = _db.client(app, role="viewer", base_url="http://relay.local:8001")

with section("the admin routes"):
    r = admin.put("/api/admin/unifi/settings", json={"host": "127.0.0.1", "port": PORT, "api_key": KEY})
    d = r.json()
    eq("saving shows the certificate to trust", (r.status_code, d["level"], d["untrusted"]),
       (200, "warn", uc.show_fingerprint(FP)))
    check("the key is never sent back", KEY not in r.text)
    check("the key is encrypted in config.json", KEY not in Path(os.environ["RELAY_CONFIG"]).read_text())
    eq("trusting another certificate is refused",
       admin.post("/api/admin/unifi/trust", json={"fingerprint": "AB" * 32}).status_code, 409)
    d = admin.post("/api/admin/unifi/trust", json={"fingerprint": d["untrusted"]}).json()
    eq("trusted and reading", (d["level"] in ("ok", "warn"), d["api"], len(d["clients"])), (True, "v2", 3))
    d = admin.post("/api/admin/unifi/refresh").json()
    check("everything read", sorted(d["part_at"]) == sorted(["av", "changes", "fold", "gear", "internet",
                                                             "neighbours", "speedtests"]), d["part_problems"])
    eq("the change log hides sign-ins and counts them", (len(d["changes"]), d["signins"]), (1, 1))
    check("warnings reach the page", any("100 Mbps" in w for w in d["warnings"]))
    pills = registry.pills()
    check("the internet pill is on the top bar", any(p["id"] == "unifi-internet" for p in pills), pills)
    h = admin.get("/api/admin/unifi/history?hours=24").json()
    check("history answers", isinstance(h["rows"], list) and "peaks" in h)
    eq("a viewer reads the page", viewer.get("/api/admin/unifi").status_code, 200)
    eq("a viewer cannot change it", viewer.put("/api/admin/unifi/settings", json={"poll_s": 20}).status_code, 403)
    r = admin.put("/api/admin/unifi/settings", json={"username": "relay-ro", "password": "local-pass"})
    check("the local user's password is never sent back", "local-pass" not in r.text and r.json()["settings"]["password_set"])
    r = admin.put("/api/admin/unifi/settings", json={"host": "https://192.168.1.1/x"})
    eq("a new address forgets the trusted certificate", r.json()["settings"]["fingerprint"], "")
    admin.put("/api/admin/unifi/settings", json={"host": "127.0.0.1"})
    d = admin.post("/api/admin/unifi/trust", json={"fingerprint": uc.show_fingerprint(FP)}).json()
    eq("trusted again", d["api"], "v2")

with section("crew Wi-Fi check-in (#356)"):
    from app.modules.crew import MODULE as crew, calltimes, checkin, devices

    mike_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="mike")
    sara_c = _db.client(app, role="member", base_url="http://relay.local:8001", username="sara")
    mike, sara = users.get_by_username("mike"), users.get_by_username("sara")
    asyncio.run(service.check())
    registry._running.add("unifi")
    r = admin.post("/api/admin/crew/devices", json={"user_id": mike.id, "kind": "mac", "value": "AA-BB-CC-00-00-01"})
    eq("an admin adds a phone by MAC", (r.status_code, r.json()["devices"][0]["value"]), (200, PHONE))
    eq("someone else's device is refused",
       sara_c.post("/crew/api/devices", json={"kind": "mac", "value": PHONE}).status_code, 400)
    eq("a bad MAC is refused", sara_c.post("/crew/api/devices", json={"kind": "mac", "value": "nope"}).status_code, 400)
    eq("sara adds hers by hostname",
       sara_c.post("/crew/api/devices", json={"kind": "hostname", "value": "Lobby-TV"}).status_code, 200)
    service.presence.clients[TV]["hostname"] = "lobby-tv"
    crew._wifi()
    tracks, zone = calltimes._state()
    key = checkin.plan_key(tracks, zone, time.time())
    eq("mike arrived by Wi-Fi", (checkin.mine(mike.id, key) or {}).get("method"), "wifi")
    eq("sara arrived by hostname", (checkin.mine(sara.id, key) or {}).get("method"), "wifi")
    devices.remove(devices.mine(mike.id)[0]["id"])
    service.presence.clients[PHONE]["ip"] = "testclient"
    r = mike_c.get("/crew/api/devices").json()
    eq("the crew page sees this phone", (r["wifi"], r["this"] and r["this"]["name"]), (True, "Pat's iPhone"))
    r = mike_c.post("/crew/api/devices/this", json={})
    eq("This is my phone", (r.status_code, r.json()["this"]["yours"]), (200, True))
    registry._running.discard("unifi")
    eq("with UniFi off it cannot see the phone", mike_c.post("/crew/api/devices/this", json={}).status_code, 409)
    eq("and the crew loop does nothing", crew._wifi(), None)

server.shutdown()
done()
