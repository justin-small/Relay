"""Reading a UniFi Network controller (#358): a UniFi OS console (UDM, UCG, Cloud Gateway) signed in to with a
read-only API key (`X-API-KEY`). Read-only: this file only ever sends GET.

Checked against a real UCG Max on Network 10.6.109 (tools/check_unifi.py): the API key reaches the official
integration API (`/proxy/network/integration/v1/...`), the v2 API (`/proxy/network/v2/api/site/<site>/...`) and the
older `stat/*` paths alike. The client list comes from v2 `clients/active`, the richest (the name shown in the UniFi
app, hostname, model, the AP or switch port, live byte rates), then `stat/sta`, then the integration API, whichever
answers first. The integration API is still used to find the site.

A console usually has a self-signed certificate. Relay never turns verification off silently: when the certificate
is not trusted by this computer, the connection fails with `Untrusted` carrying the certificate's SHA-256
fingerprint, the admin page shows it, and only once an admin trusts that fingerprint does Relay connect, checking it
on every request. Stdlib HTTP through `app/netsock.py`, like the other modules. Blocking: call it from a thread.

Read-only: GETs, plus POSTs only to the query paths in `READ_POSTS` (UniFi's reports, flows and system log take their
time range in a POST body; they change nothing). The local user, when given, signs in only for the live event stream.
"""
from __future__ import annotations

import hashlib
import http.client
import json
import re
import ssl
import urllib.parse
from typing import Any

from ... import netsock

TRAFFIC = "unifi"
TIMEOUT_S = 10.0
LIMIT = 8_000_000            # bytes read from one answer (a large site's client list is a few MB at most)
PAGE = 200                   # clients asked for per page of the integration API
MAX_PAGES = 50
READ_POSTS = ("/stat/report/", "/traffic-flows", "/system-log/", "/api/auth/login", "/api/auth/logout")
_HOST = re.compile(r"^[A-Za-z0-9.\-:\[\]]{1,253}$")
_FP = re.compile(r"^[0-9A-F]{64}$")


class Problem(Exception):
    """A fixed sentence for the admin page. Never built from an exception's own text (CodeQL stack-trace rule)."""


class Untrusted(Problem):
    def __init__(self, fingerprint: str) -> None:
        super().__init__("The controller's certificate is not one this computer trusts. Check the fingerprint "
                         "matches the console's, then trust it.")
        self.fingerprint = fingerprint


def clean_host(raw: Any) -> str:
    """An address or name, from whatever was pasted ("https://192.168.1.1/network/default" → "192.168.1.1")."""
    if not isinstance(raw, str):
        return ""
    text = raw.strip()
    if "://" in text:
        text = urllib.parse.urlsplit(text).hostname or ""
    else:
        text = text.split("/")[0]
        if text.count(":") == 1:          # host:port; an IPv6 address has more colons
            text = text.split(":")[0]
    text = text.strip("[]")
    return text if _HOST.match(text) else ""


def clean_fingerprint(raw: Any) -> str:
    """A SHA-256 fingerprint as 64 upper-case hex digits, from any spacing or colons; "" when it is not one."""
    if not isinstance(raw, str):
        return ""
    hexed = re.sub(r"[^0-9A-Fa-f]", "", raw).upper()
    return hexed if _FP.match(hexed) else ""


def show_fingerprint(fp: str) -> str:
    return ":".join(fp[i:i + 2] for i in range(0, len(fp), 2))


def fingerprint_of(der: bytes) -> str:
    return hashlib.sha256(der).hexdigest().upper()


def _unverified() -> ssl.SSLContext:
    """For a pinned controller only: the certificate is checked against the pinned fingerprint after the handshake
    (`_check_pin`) instead of against a certificate authority. Also used to read an untrusted certificate's
    fingerprint so the admin can decide; no request is sent over that connection."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE     # nosec B501: replaced by the fingerprint pin, checked before any request
    return ctx


class Controller:
    def __init__(self, host: str, port: int, api_key: str, fingerprint: str = "", site: str = "default") -> None:
        self.host, self.port, self.api_key = host, port, api_key
        self.fingerprint, self.site = fingerprint, site or "default"
        self.site_id = ""            # the integration API's id for the site, found on first use
        self.api = ""                # "v2", "legacy" or "integration": which one answered the client list
        # The last failure, as a fixed sentence of our own, and the fingerprint of an untrusted certificate. The
        # page reads these, never an exception's text (CodeQL: information exposure through an exception).
        self.error = ""
        self.untrusted_fp = ""

    def _fail(self, text: str) -> "Problem":
        self.error = text
        return Problem(text)

    # ---------------------------------------------------------------- transport
    def _connect(self) -> http.client.HTTPSConnection:
        pinned = bool(self.fingerprint)
        conn = netsock.http_connection(TRAFFIC, self.host, self.port, tls=True, timeout=TIMEOUT_S,
                                       context=_unverified() if pinned else netsock.tls_context())
        try:
            conn.connect()
        except ssl.SSLCertVerificationError:
            conn.close()
            self.untrusted_fp = self.peer_fingerprint()
            self.error = "The controller's certificate is not one this computer trusts."
            raise Untrusted(self.untrusted_fp) from None
        except (OSError, ssl.SSLError):
            conn.close()
            raise self._fail(netsock.failure(TRAFFIC, f"Relay could not reach the controller at {self.host}.")) from None
        if pinned:
            self._check_pin(conn)
        return conn

    def _check_pin(self, conn: http.client.HTTPSConnection) -> None:
        der = conn.sock.getpeercert(binary_form=True) if conn.sock else None
        if not der or fingerprint_of(der) != self.fingerprint:
            conn.close()
            raise self._fail("The controller's certificate changed since it was trusted. If the console was replaced "
                          "or its certificate renewed, trust the new one; otherwise something else is answering.")

    def peer_fingerprint(self) -> str:
        """The fingerprint of the certificate the controller shows, read without trusting it."""
        conn = netsock.http_connection(TRAFFIC, self.host, self.port, tls=True, timeout=TIMEOUT_S,
                                       context=_unverified())
        try:
            conn.connect()
            der = conn.sock.getpeercert(binary_form=True) if conn.sock else None
        except (OSError, ssl.SSLError):
            raise self._fail(netsock.failure(TRAFFIC, f"Relay could not reach the controller at {self.host}.")) from None
        finally:
            conn.close()
        if not der:
            raise self._fail("The controller did not show a certificate.")
        return fingerprint_of(der)

    def get(self, path: str) -> tuple[int, Any]:
        """One GET. Returns the status and the decoded JSON (None when the body is not JSON)."""
        return self._request("GET", path)[:2]

    def query(self, path: str, body: dict) -> tuple[int, Any]:
        """A read that UniFi takes as a POST (a report, flows, the system log). Refuses any other path."""
        if not any(part in path for part in READ_POSTS[:3]):
            raise ValueError(f"not a read: {path}")
        return self._request("POST", path, body)[:2]

    def _request(self, method: str, path: str, body: dict | None = None, *, key: bool = True,
                 cookie: str = "") -> tuple[int, Any, dict]:
        conn = self._connect()
        headers = {"Accept": "application/json", "User-Agent": "Relay"}
        if key:
            headers["X-API-KEY"] = self.api_key
        if cookie:
            headers["Cookie"] = cookie
        data = json.dumps(body).encode("utf-8") if body is not None else None
        if data is not None:
            headers["Content-Type"] = "application/json"
        try:
            conn.request(method, path, body=data, headers=headers)
            resp = conn.getresponse()
            raw = resp.read(LIMIT)
            status = resp.status
            hdrs = {k.lower(): v for k, v in resp.getheaders()}
        except (OSError, http.client.HTTPException):
            raise self._fail(f"The controller at {self.host} stopped answering.") from None
        finally:
            conn.close()
        try:
            return status, json.loads(raw.decode("utf-8")) if raw else None, hdrs
        except (UnicodeDecodeError, ValueError):
            return status, None, hdrs

    # ---------------------------------------------------------------- the local user (event stream only)
    def login(self, username: str, password: str) -> str:
        """Sign in as the local user. Returns the session cookie ("TOKEN=..."); raises Problem when refused."""
        status, _, hdrs = self._request("POST", "/api/auth/login",
                                        {"username": username, "password": password, "rememberMe": False}, key=False)
        cookie = (hdrs.get("set-cookie") or "").split(";")[0]
        if status != 200 or not cookie:
            raise self._fail("The console refused the local user. Check the name and password, and that it is a local "
                          "account, not a UI.com one.")
        return cookie

    def logout(self, cookie: str) -> None:
        try:
            self._request("POST", "/api/auth/logout", {}, key=False, cookie=cookie)
        except Problem:
            pass

    def events_url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"wss://{host}:{self.port}/proxy/network/wss/s/{urllib.parse.quote(self.site, safe='')}/events?clients=v2"

    def ssl_context(self) -> ssl.SSLContext:
        return _unverified() if self.fingerprint else netsock.tls_context()

    # ---------------------------------------------------------------- what else the site reports
    def _site(self, rest: str) -> str:
        return f"/proxy/network/api/s/{urllib.parse.quote(self.site, safe='')}/{rest}"

    def _v2(self, rest: str) -> str:
        return f"/proxy/network/v2/api/site/{urllib.parse.quote(self.site, safe='')}/{rest}"

    def _data(self, status: int, d: Any) -> list:
        if status != 200:
            raise self._refused(status)
        if isinstance(d, dict) and isinstance(d.get("data"), list):
            return [x for x in d["data"] if isinstance(x, dict)]
        if isinstance(d, list):
            return [x for x in d if isinstance(x, dict)]
        return []

    def health(self) -> list[dict]:
        """stat/health: one entry per subsystem (wan, www, wlan, lan, vpn)."""
        return self._data(*self.get(self._site("stat/health")))

    def gear(self) -> list[dict]:
        """stat/device: every switch, AP and gateway with its port table, radios and firmware."""
        return self._data(*self.get(self._site("stat/device")))

    def wan_report(self, start_ms: int, end_ms: int) -> list[dict]:
        return self._data(*self.query(self._site("stat/report/5minutes.site"),
                                      {"attrs": ["wan-tx_bytes", "wan-rx_bytes", "latency_avg", "num_sta", "time"],
                                       "start": start_ms, "end": end_ms}))

    def speedtests(self, start_ms: int, end_ms: int) -> list[dict]:
        return self._data(*self.query(self._site("stat/report/archive.speedtest"),
                                      {"attrs": ["xput_download", "xput_upload", "latency", "time"],
                                       "start": start_ms, "end": end_ms}))

    def flows(self, start_ms: int, end_ms: int, pages: int = 10, size: int = 200) -> list[dict]:
        """v2 traffic flows in the window, newest first, at most pages × size. Empty when the console has none."""
        out: list[dict] = []
        for page in range(pages):
            status, d = self.query(self._v2("traffic-flows"), {"timestampFrom": start_ms, "timestampTo": end_ms,
                                                                "pageSize": size, "pageNumber": page})
            if status != 200 or not isinstance(d, dict):
                break
            out += [x for x in d.get("data") or [] if isinstance(x, dict)]
            if not d.get("has_next"):
                break
        return out

    def changes(self, start_ms: int, end_ms: int, size: int = 50) -> list[dict]:
        """The system log's admin activity: who signed in to the console and what settings they changed."""
        status, d = self.query(self._v2("system-log/admin-activity"),
                               {"timestampFrom": start_ms, "timestampTo": end_ms, "pageSize": size, "pageNumber": 0})
        return self._data(status, d)

    def neighbours(self) -> list[dict]:
        """stat/rogueap: other Wi-Fi networks the APs hear."""
        return self._data(*self.get(self._site("stat/rogueap")))

    def _refused(self, status: int) -> Problem:
        if status in (401, 403):
            return self._fail("The controller refused the API key. Make a new one under Control Plane, Integrations, "
                           "and paste it in the settings.")
        if status == 404:
            return self._fail("This does not look like a UniFi OS console (no Network application answered). Check the "
                           "address.")
        return self._fail(f"The controller answered with an error (HTTP {int(status)}).")

    # ---------------------------------------------------------------- sites
    def find_site(self) -> dict:
        """The site this Relay reads, from the integration API. Raises Problem when none matches."""
        status, d = self.get("/proxy/network/integration/v1/sites?limit=50")
        if status != 200 or not isinstance(d, dict):
            raise self._refused(status)
        sites = [s for s in d.get("data") or [] if isinstance(s, dict)]
        for s in sites:
            if self.site in (s.get("internalReference"), s.get("id"), s.get("name")):
                self.site_id = str(s.get("id") or "")
                return {"id": self.site_id, "name": str(s.get("name") or self.site)[:80],
                        "ref": str(s.get("internalReference") or self.site)[:80]}
        names = ", ".join(str(s.get("internalReference") or s.get("name")) for s in sites[:5])
        raise self._fail(f"The controller has no site called {self.site!r}. It has: {names or 'none'}.")

    # ---------------------------------------------------------------- clients and devices
    def clients(self) -> list[dict]:
        """Every connected client, normalised (see `client_from_legacy` / `client_from_integration`)."""
        if not self.site_id:
            self.find_site()
        ref = urllib.parse.quote(self.site, safe="")
        status, d = self.get(f"/proxy/network/v2/api/site/{ref}/clients/active")
        if status == 200 and isinstance(d, list):
            self.api = "v2"
            return [c for c in (client_from_v2(x) for x in d) if c]
        status, d = self.get(f"/proxy/network/api/s/{ref}/stat/sta")
        if status == 200 and isinstance(d, dict) and isinstance(d.get("data"), list):
            self.api = "legacy"
            return [c for c in (client_from_legacy(x) for x in d["data"]) if c]
        out: list[dict] = []
        sid = urllib.parse.quote(self.site_id, safe="")
        for page in range(MAX_PAGES):
            status, d = self.get(f"/proxy/network/integration/v1/sites/{sid}/clients?offset={page * PAGE}&limit={PAGE}")
            if status != 200 or not isinstance(d, dict):
                raise self._refused(status)
            rows = [x for x in d.get("data") or [] if isinstance(x, dict)]
            out += [c for c in (client_from_integration(x) for x in rows) if c]
            total = d.get("totalCount")
            if len(rows) < PAGE or (isinstance(total, int) and len(out) >= total):
                break
        self.api = "integration"
        return out

    def devices(self) -> dict[str, str]:
        """The site's own gear (switches, APs, gateways): MAC (and integration id) → name. Empty when unreadable."""
        names: dict[str, str] = {}
        ref = urllib.parse.quote(self.site, safe="")
        status, d = self.get(f"/proxy/network/api/s/{ref}/stat/device-basic")
        if status == 200 and isinstance(d, dict):
            for x in d.get("data") or []:
                if isinstance(x, dict) and isinstance(x.get("mac"), str):
                    names[x["mac"].lower()] = _text(x.get("name")) or _text(x.get("model")) or x["mac"].lower()
            return names
        if self.site_id:
            sid = urllib.parse.quote(self.site_id, safe="")
            status, d = self.get(f"/proxy/network/integration/v1/sites/{sid}/devices?limit=200")
            if status == 200 and isinstance(d, dict):
                for x in d.get("data") or []:
                    if not isinstance(x, dict):
                        continue
                    name = _text(x.get("name")) or _text(x.get("model"))
                    for k in ("macAddress", "id"):
                        if isinstance(x.get(k), str):
                            names[x[k].lower()] = name or x[k].lower()
        return names


_MAC = re.compile(r"^[0-9a-f]{2}(:[0-9a-f]{2}){5}$")


def clean_mac(raw: Any) -> str:
    """aa:bb:cc:dd:ee:ff from any common spelling (dashes, dots, no separators, upper case); "" when not a MAC."""
    if not isinstance(raw, str):
        return ""
    hexed = re.sub(r"[^0-9A-Fa-f]", "", raw).lower()
    if len(hexed) != 12:
        return ""
    mac = ":".join(hexed[i:i + 2] for i in range(0, 12, 2))
    return mac if _MAC.match(mac) else ""


def _text(v: Any, n: int = 80) -> str:
    return " ".join(v.split())[:n] if isinstance(v, str) else ""


def _int(v: Any) -> int | None:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def client_from_v2(x: Any) -> dict | None:
    """One client from v2 `clients/active` (Network 9 and 10)."""
    if not isinstance(x, dict):
        return None
    mac = clean_mac(x.get("mac"))
    if not mac:
        return None
    wired = bool(x.get("is_wired"))
    up = x.get("uplink_mac") if wired and x.get("uplink_mac") else x.get("last_uplink_mac") or x.get("ap_mac")
    return {"mac": mac, "ip": _text(x.get("ip"), 45), "name": _text(x.get("display_name")) or _text(x.get("name")),
            "hostname": _text(x.get("hostname")).lower(), "model": _text(x.get("model_name")), "wired": wired,
            "uplink": clean_mac(up), "uplink_name": _text(x.get("last_uplink_name")),
            "port": _int(x.get("sw_port")) if wired else None, "ssid": "" if wired else _text(x.get("essid")),
            "network": _text(x.get("network_name")), "vlan": None, "guest": bool(x.get("is_guest")),
            "private": _local(mac), "signal": _int(x.get("signal")) if not wired else None,
            "rx_bps": (_int(x.get("rx_bytes-r")) or 0) * 8,
            "tx_bps": (_int(x.get("tx_bytes-r")) or 0) * 8}


def client_from_legacy(x: Any) -> dict | None:
    if not isinstance(x, dict):
        return None
    mac = clean_mac(x.get("mac"))
    if not mac:
        return None
    wired = bool(x.get("is_wired"))
    port = x.get("sw_port")
    return {"mac": mac, "ip": _text(x.get("ip"), 45), "name": _text(x.get("name")),
            "hostname": _text(x.get("hostname")).lower(), "wired": wired,
            "uplink": clean_mac(x.get("sw_mac") if wired else x.get("ap_mac")),
            "port": port if isinstance(port, int) and not isinstance(port, bool) else None,
            "ssid": "" if wired else _text(x.get("essid")), "network": _text(x.get("network")),
            "vlan": x.get("vlan") if isinstance(x.get("vlan"), int) and not isinstance(x.get("vlan"), bool) else None,
            "guest": bool(x.get("is_guest")), "private": bool(x.get("is_locally_administered")) or _local(mac)}


def client_from_integration(x: Any) -> dict | None:
    if not isinstance(x, dict):
        return None
    mac = clean_mac(x.get("macAddress"))
    if not mac:
        return None
    uplink = x.get("uplinkDeviceId")
    return {"mac": mac, "ip": _text(x.get("ipAddress"), 45), "name": _text(x.get("name")), "hostname": "",
            "wired": str(x.get("type") or "").upper() == "WIRED",
            "uplink": uplink.lower()[:64] if isinstance(uplink, str) else "", "port": None, "ssid": "",
            "network": "", "vlan": None, "guest": str(x.get("access", {}).get("type") if isinstance(x.get("access"), dict) else "").upper() == "GUEST",
            "private": _local(mac)}


def _local(mac: str) -> bool:
    """A locally administered address: what phones use for a private (randomised) Wi-Fi address."""
    return bool(int(mac[:2], 16) & 0x02)
