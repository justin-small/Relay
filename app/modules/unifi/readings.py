"""Turning the controller's answers into what Relay shows (#358): internet health, the gear with its ports and
firmware, Wi-Fi quality, the console's change log, and NDI and Dante traffic. Pure functions over the JSON the
controller sent, so they are tested against answers recorded from a real console (Network 10.6, UCG Max).
"""
from __future__ import annotations

import re
from typing import Any

from .controller import clean_mac

HOT = 0.70               # a link this full is worth a look
ERRORS_RISING = 10       # new errors on a port between two reads that are worth a warning


def _n(v: Any) -> float | None:
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _f(v: Any) -> float | None:
    """A number the console may send as text (PoE power, voltage and current come as "4.20")."""
    if isinstance(v, str):
        try:
            return float(v)
        except ValueError:
            return None
    return _n(v)


def _t(v: Any, n: int = 80) -> str:
    return " ".join(v.split())[:n] if isinstance(v, str) else ""


# ---------------------------------------------------------------- internet health
def internet(health: list[dict]) -> dict:
    """Now: throughput, latency, drops and availability of the WAN, from stat/health's `www` and `wan`."""
    www = next((x for x in health if x.get("subsystem") == "www"), {})
    wan = next((x for x in health if x.get("subsystem") == "wan"), {})
    up = ((wan.get("uptime_stats") or {}).get("WAN") or {}) if isinstance(wan.get("uptime_stats"), dict) else {}
    monitors = [{"target": _t(m.get("target"), 60), "latency_ms": _n(m.get("latency_average")),
                 "availability": _n(m.get("availability"))}
                for m in up.get("monitors") or [] if isinstance(m, dict)]
    rx, tx = _n(www.get("rx_bytes-r")) or _n(wan.get("rx_bytes-r")), _n(www.get("tx_bytes-r")) or _n(wan.get("tx_bytes-r"))
    return {
        "status": _t(www.get("status") or wan.get("status"), 20) or "unknown",
        "isp": _t(wan.get("isp_name") or wan.get("isp_organization")),
        "down_bps": rx * 8 if rx is not None else None, "up_bps": tx * 8 if tx is not None else None,
        "latency_ms": _n(www.get("latency")), "drops": _n(www.get("drops")),
        "availability": _n(up.get("availability")), "uptime_s": _n(www.get("uptime")),
        "speedtest": {"down_mbps": _n(www.get("xput_down")), "up_mbps": _n(www.get("xput_up")),
                      "ping_ms": _n(www.get("speedtest_ping")), "at": _n(www.get("speedtest_lastrun"))},
        "monitors": monitors,
    }


def speedtests(rows: list[dict]) -> list[dict]:
    out = [{"at": (_n(r.get("time")) or 0) / 1000, "down_mbps": _n(r.get("xput_download")),
            "up_mbps": _n(r.get("xput_upload")), "ping_ms": _n(r.get("latency"))} for r in rows]
    return sorted((r for r in out if r["at"]), key=lambda r: r["at"], reverse=True)


def wan_report(rows: list[dict]) -> list[dict]:
    """The controller's own 5-minute WAN report: bytes per 5 minutes → average bits per second."""
    out = []
    for r in rows:
        at = (_n(r.get("time")) or 0) / 1000
        rx, tx = _n(r.get("wan-rx_bytes")), _n(r.get("wan-tx_bytes"))
        if at:
            out.append({"at": at, "down_bps": rx * 8 / 300 if rx is not None else None,
                        "up_bps": tx * 8 / 300 if tx is not None else None, "latency_ms": _n(r.get("latency_avg")),
                        "clients": _n(r.get("num_sta"))})
    return sorted(out, key=lambda r: r["at"])


# ---------------------------------------------------------------- gear, ports and firmware
KIND = {"udm": "gateway", "ugw": "gateway", "uxg": "gateway", "usw": "switch", "uap": "access point"}


def _max_speed(caps: Any) -> int | None:
    """speed_caps is a bit mask: 10/100/1000 half and full, then 2.5G, 5G, 10G... Return the fastest it can do."""
    if not isinstance(caps, int) or isinstance(caps, bool) or caps <= 0:
        return None
    speeds = [(1 << 12, 10000), (1 << 11, 5000), (1 << 10, 2500), (1 << 5, 1000), (1 << 4, 1000), (1 << 3, 100),
              (1 << 2, 100), (1 << 1, 10), (1 << 0, 10)]
    for bit, mbps in speeds:
        if caps & bit:
            return mbps
    return None


def gear(devices: list[dict], clients: list[dict], before: dict[str, dict] | None = None) -> list[dict]:
    """Each switch, AP and gateway: firmware, ports (what is plugged in, speed, PoE, traffic, new errors) and the
    warnings worth showing. `before` is the last read's port counters (mac → port → errors), for errors rising."""
    before = before or {}
    on_port: dict[tuple[str, int], dict] = {}
    for c in clients:
        if c.get("wired") and c.get("uplink") and c.get("port") is not None:
            on_port[(c["uplink"], c["port"])] = c
    out = []
    for d in devices:
        mac = clean_mac(d.get("mac"))
        if not mac:
            continue
        name = _t(d.get("name")) or _t(d.get("model")) or mac
        ports, warns = [], []
        uplink = d.get("uplink") if isinstance(d.get("uplink"), dict) else {}
        for p in d.get("port_table") or []:
            if not isinstance(p, dict) or not isinstance(p.get("port_idx"), int):
                continue
            idx = p["port_idx"]
            up = bool(p.get("up"))
            speed = _n(p.get("speed")) if up else None
            best = _max_speed(p.get("speed_caps"))
            errors = int((_n(p.get("rx_errors")) or 0) + (_n(p.get("tx_errors")) or 0))
            prev = (before.get(mac) or {}).get(idx)
            new_errors = errors - prev if isinstance(prev, int) and errors >= prev else 0
            rate = ((_n(p.get("rx_bytes-r")) or 0) + (_n(p.get("tx_bytes-r")) or 0)) * 8
            use = rate / (speed * 1_000_000) if speed else None
            who = on_port.get((mac, idx))
            row = {"port": idx, "name": _t(p.get("name"), 40), "up": up, "speed": speed, "best": best,
                   "duplex": bool(p.get("full_duplex")) if up else None, "uplink": bool(p.get("is_uplink")),
                   "poe": bool(p.get("poe_enable")) if "poe_enable" in p else None,
                   "poe_w": _f(p.get("poe_power")), "poe_good": p.get("poe_good") if "poe_good" in p else None,
                   "bps": rate, "use": round(use, 3) if use is not None else None, "errors": errors,
                   "new_errors": new_errors, "client": (who.get("name") or who.get("hostname") or who["mac"]) if who else "",
                   "client_mac": who["mac"] if who else "", "warn": ""}
            if up and speed and best and speed < best and speed <= 100:
                row["warn"] = f"Linked at {int(speed)} Mbps; the port can do {best} Mbps. Check the cable."
            elif up and row["duplex"] is False:
                row["warn"] = "Linked at half duplex. Check the cable and the other end's settings."
            elif row["poe"] and row["poe_good"] is False and up:
                row["warn"] = "Power over Ethernet is on but the device is not drawing power properly."
            elif new_errors >= ERRORS_RISING:
                row["warn"] = f"{new_errors} new errors since the last read. Check the cable."
            elif use is not None and use >= HOT:
                row["warn"] = f"{round(use * 100)}% used."
            if row["warn"]:
                warns.append(f"{name} port {idx}: {row['warn']}")
            ports.append(row)
        version = _t(d.get("version") or d.get("displayable_version"), 40)
        target = _t(d.get("upgrade_to_firmware"), 40)
        out.append({
            "mac": mac, "name": name, "model": _t(d.get("model")), "kind": KIND.get(str(d.get("type")), str(d.get("type") or "")),
            "ip": _t(d.get("ip"), 45), "online": d.get("state") == 1, "version": version,
            "update": target if d.get("upgradable") and target else ("available" if d.get("upgradable") else ""),
            "uptime_s": _n(d.get("uptime")),
            "uplink": {"to": _t(uplink.get("uplink_device_name")), "port": uplink.get("uplink_remote_port"),
                       "speed": _n(uplink.get("speed")), "type": _t(uplink.get("type"), 20)} if uplink else None,
            "poe_w": round(sum(p["poe_w"] or 0 for p in ports), 1) if any(p["poe_w"] for p in ports) else None,
            "poe_budget_w": _n(d.get("total_max_power")),
            "ports": ports, "warnings": warns,
            "radios": radios(d),
        })
    return sorted(out, key=lambda g: ({"gateway": 0, "switch": 1, "access point": 2}.get(g["kind"], 3), g["name"].lower()))


def counters(gear_list: list[dict]) -> dict[str, dict]:
    return {g["mac"]: {p["port"]: p["errors"] for p in g["ports"]} for g in gear_list}


# ---------------------------------------------------------------- Wi-Fi quality
BAND = {"ng": "2.4 GHz", "na": "5 GHz", "6e": "6 GHz"}


def radios(d: dict) -> list[dict]:
    out = []
    for r in d.get("radio_table_stats") or []:
        if not isinstance(r, dict):
            continue
        out.append({"band": BAND.get(str(r.get("radio")), str(r.get("radio") or "")), "channel": r.get("channel"),
                    "clients": int(_n(r.get("num_sta")) or 0), "busy_pct": _n(r.get("cu_total")),
                    "retries_pct": _n(r.get("tx_retries_pct")),
                    "satisfaction": sat if (sat := _n(r.get("satisfaction"))) is not None and sat >= 0 else None,
                    "power_dbm": _n(r.get("tx_power"))})
    return out


def wifi(gear_list: list[dict], clients: list[dict], neighbours: list[dict]) -> dict:
    """Per AP radio: channel, how busy it is, retries, and how many other networks share the channel; and the
    clients with the weakest signal."""
    aps = []
    for g in gear_list:
        for r in g["radios"]:
            same = sum(1 for n in neighbours if n.get("channel") == r["channel"] and not n.get("is_ubnt"))
            warn = ""
            if r["busy_pct"] is not None and r["busy_pct"] >= 70:
                warn = f"Channel {r['channel']} is {int(r['busy_pct'])}% busy."
            elif r["retries_pct"] is not None and r["retries_pct"] >= 20:
                warn = f"{int(r['retries_pct'])}% of transmissions are retried."
            aps.append({**r, "ap": g["name"], "neighbours": same, "warn": warn})
    weak = sorted((c for c in clients if not c.get("wired") and isinstance(c.get("signal"), int)),
                  key=lambda c: c["signal"])[:10]
    return {"radios": aps, "weak": [{"name": c.get("name") or c.get("hostname") or c["mac"], "signal": c["signal"],
                                     "ap": c.get("uplink_name") or "", "ssid": c.get("ssid") or ""} for c in weak],
            "neighbours": len(neighbours)}


# ---------------------------------------------------------------- the console's change log
_PLACE = re.compile(r"\{([A-Z_]+)\}")


def changes(rows: list[dict]) -> list[dict]:
    """The console's admin activity (v2 system-log/admin-activity on Network 10): sign-ins (ADMIN_ACCESS, with
    `admin.name`, `platform`, `ip`) and setting changes (`meta.actor`, `meta.section`, `meta.display_property_*`,
    and `updates` with the property changed). `message` is a template ("{ADMIN} made a change to {SETTING_NAME}
    ..."), filled here from those fields; a placeholder with nothing to fill it is dropped."""
    out = []
    for r in rows:
        meta = r.get("meta") if isinstance(r.get("meta"), dict) else {}
        admin = r.get("admin") if isinstance(r.get("admin"), dict) else {}
        props = [_t(u.get("property"), 60) for u in r.get("updates") or [] if isinstance(u, dict) and u.get("property")]
        who = _t(meta.get("actor") or admin.get("name"), 60)
        fill = {"ADMIN": who, "PLATFORM": _t(r.get("platform"), 30), "IP": _t(r.get("ip"), 45),
                "SECTION": _t(meta.get("section"), 40).lower(), "OBJECT": _t(meta.get("display_property_value"), 80),
                "SETTING_NAME": _t(meta.get("display_property_name"), 60) or ", ".join(props[:3])}
        text = _PLACE.sub(lambda m: fill.get(m.group(1), ""), _t(r.get("message"), 300))
        text = re.sub(r"\s+([.,)])", r"\1", re.sub(r"\(\s*\)|\s{2,}", " ", text)).strip()
        text = re.sub(r"\s*Source IP:\s*\.?$", ".", text).replace("..", ".")
        out.append({"at": (_n(r.get("timestamp")) or 0) / 1000, "key": _t(r.get("key"), 60), "text": text,
                    "who": who, "section": fill["SECTION"], "what": fill["OBJECT"], "properties": props[:10],
                    "change": r.get("key") != "ADMIN_ACCESS"})
    return sorted(out, key=lambda r: r["at"], reverse=True)


# ---------------------------------------------------------------- NDI and Dante
NDI_TCP = range(5960, 6000)          # discovery server 5959/5960, sources from 5961
DANTE_UDP = {4440, 4444, 4455, 8700, 8701, 8702, 8703, 8704, 8705, 8706, 8708, 8800}
DANTE_AUDIO = range(14336, 14601)    # Dante audio flows (unicast)
PTP = {319, 320}


def flow_kind(proto: str, sport: int | None, dport: int | None) -> str:
    ports = {p for p in (sport, dport) if isinstance(p, int)}
    if proto in ("UDP",) and ports & PTP:
        return "ptp"
    if proto == "UDP" and (ports & DANTE_UDP or any(p in DANTE_AUDIO for p in ports)):
        return "dante"
    if proto in ("TCP", "UDP") and any(p in NDI_TCP for p in ports):
        return "ndi"
    return ""


def av(flows: list[dict], clients: list[dict], window_s: float) -> list[dict]:
    """Which clients carry NDI or Dante, from the console's traffic flows (source and destination ports): bytes
    over the window as an average rate, the peers, and the switch port or AP they are on."""
    by_mac = {c["mac"]: c for c in clients}
    by_ip = {c.get("ip"): c for c in clients if c.get("ip")}
    seen: dict[str, dict] = {}
    for f in flows:
        src = f.get("source") if isinstance(f.get("source"), dict) else {}
        dst = f.get("destination") if isinstance(f.get("destination"), dict) else {}
        kind = flow_kind(str(f.get("protocol") or "").upper(), src.get("port"), dst.get("port"))
        if not kind:
            continue
        td = f.get("traffic_data") if isinstance(f.get("traffic_data"), dict) else {}
        total = _n(td.get("bytes_total")) or 0
        for end, other in ((src, dst), (dst, src)):
            c = by_mac.get(clean_mac(end.get("mac"))) or by_ip.get(end.get("ip"))
            if not c:
                continue
            row = seen.setdefault(c["mac"], {"mac": c["mac"], "name": c.get("name") or c.get("hostname") or c["mac"],
                                             "ip": c.get("ip") or "", "kinds": set(), "bytes": 0.0, "peers": set(),
                                             "where": "", "wired": bool(c.get("wired"))})
            row["kinds"].add(kind)
            row["bytes"] += total
            peer = by_mac.get(clean_mac(other.get("mac"))) or by_ip.get(other.get("ip"))
            if peer:
                row["peers"].add(peer.get("name") or peer.get("hostname") or peer["mac"])
    out = []
    for row in seen.values():
        out.append({**row, "kinds": sorted(row["kinds"]), "peers": sorted(row["peers"])[:8],
                    "bps": row["bytes"] * 8 / window_s if window_s else None})
    return sorted(out, key=lambda r: -r["bytes"])
