"""Signal map (#262): reads a pasted patch list and joins it with the live Dante routing. No network and no database.

A patch row is CH, NAME, PORT, SOCKET, UPSTREAM (the columns a stage plot or a console patch sheet has). `join` finds
each row's Dante transmit channel by its name and says what the live routing does with it, so a row whose channel
nobody receives, or whose subscription cannot resolve, stands out.
"""
from __future__ import annotations

from ... import text

COLUMNS = ("ch", "name", "port", "socket", "upstream")
HEADS = {"ch": "ch", "channel": "ch", "chan": "ch", "no": "ch", "#": "ch", "name": "name", "source": "name",
         "port": "port", "socket": "socket", "upstream": "upstream", "from": "upstream", "input": "port"}
MAX_ROWS = 512
MAX_CELL = 80
MAX_TEXT = 100_000


def _split(line: str) -> list[str]:
    for sep in ("\t", "|", ","):
        if sep in line:
            return [c.strip() for c in line.split(sep)]
    return [line.strip()]


def parse(raw: str) -> tuple[list[dict], list[str]]:
    """(rows, problems). A header line naming the columns is used when present; otherwise the order is
    CH, NAME, PORT, SOCKET, UPSTREAM. Blank lines are skipped. A line without a name is reported, not guessed."""
    rows: list[dict] = []
    problems: list[str] = []
    order = list(COLUMNS)
    lines = [ln for ln in (raw or "").splitlines() if ln.strip()]
    if lines:
        first = [text.fold(c) for c in _split(lines[0])]
        if "name" in first and all(c in HEADS or not c for c in first):
            order = [HEADS.get(c, "") for c in first]
            lines = lines[1:]
    for n, line in enumerate(lines, 1):
        if len(rows) >= MAX_ROWS:
            problems.append(f"Only the first {MAX_ROWS} rows are kept.")
            break
        cells = _split(line)
        row = {c: "" for c in COLUMNS}
        for key, cell in zip(order, cells):
            if key:
                row[key] = text.squeeze(cell)[:MAX_CELL]
        if not row["name"]:
            problems.append(f"Line {n} has no name: {line.strip()[:40]}")
            continue
        rows.append(row)
    return rows, problems


def _candidates(devices: list[dict], name: str) -> list[tuple[str, dict]]:
    """Every transmit channel whose name, or `name@device`, is `name`: [(`channel@device`, device)]."""
    out = []
    for d in devices:
        for c in d.get("transmit") or []:
            full = f"{c}@{d['name']}"
            if text.same_name(c, name) or text.same_name(full, name):
                out.append((full, d))
    return out


def from_desk(desk: dict) -> str:
    """The patch text for a desk whose show file Relay read (`patch.audio_mixers`): one tab-separated line per named
    input channel, PORT the kind of source and its port, SOCKET its socket number, UPSTREAM the desk's own label."""
    lines = ["CH\tName\tPort\tSocket\tUpstream"]
    for r in desk.get("inputs") or []:
        s = r.get("source") or {}
        if not r.get("named") or not r.get("name"):
            continue
        port = " ".join(x for x in (s.get("kind", "").replace("_", " "), str(s["port"]) if s.get("port") else "") if x)
        lines.append("\t".join(text.squeeze(str(x)) for x in (r.get("channel", ""), r["name"], port,
                                                              s.get("socket") or "", s.get("label", ""))))
    return "\n".join(lines) + "\n"


def switch_ports(devices: list[dict], unifi: list[dict] | None) -> dict[str, dict]:
    """Which switch port each Dante device is plugged into, by the device's Dante name, from UniFi's map list
    (`gear.unifi`): {"switch", "port", "label", "speed"}. A device UniFi does not see wired is left out."""
    if not unifi:
        return {}
    gear = {g["mac"]: g for g in unifi if g.get("kind") in ("switch", "gateway") or g.get("ports")}
    by_ip = {g["ip"]: g for g in unifi if g.get("ip") and g.get("uplink_mac") and g.get("uplink_port") is not None}
    out = {}
    for d in devices:
        c = next((by_ip[ip] for ip in d.get("ips") or [] if ip in by_ip), None)
        sw = gear.get(c["uplink_mac"]) if c else None
        if c and sw:
            label = c.get("port_name") or next((p.get("name") for p in sw.get("ports") or []
                                                if p.get("port") == c["uplink_port"]), "") or ""
            out[d["name"]] = {"switch": sw["name"], "port": c["uplink_port"], "label": label, "speed": c.get("speed")}
    return out


def join(rows: list[dict], devices: list[dict] | None, net: dict[str, dict] | None = None,
         local: frozenset[str] | set[str] = frozenset()) -> dict:
    """Each row with `live`: {"state", "source", "receivers", "why"} where state is one of
    `unknown` (Dante is off or not scanned yet), `missing` (no such transmit channel), `offline` (its device does not
    answer), `unsubscribed` (nobody receives it), `broken` (a receive channel names it but cannot find it) or `ok`.
    Also `unpatched`: transmit channels no row names."""
    if devices is None:
        return {"rows": [dict(r, live={"state": "unknown", "source": "", "receivers": [],
                                       "why": "Dante is off or has not looked yet."}) for r in rows],
                "unpatched": [], "counts": {"unknown": len(rows)}}
    out, used = [], set()
    for r in rows:
        found = _candidates(devices, r["name"])
        if not found:
            live = {"state": "missing", "source": "", "receivers": [], "why": "No Dante channel has this name."}
        else:
            source, dev = found[0]
            used.update(s for s, _ in found)
            recv = [{"device": d["name"], "channel": s["channel"], "status": s["status"],
                     "relay": bool(local.intersection(d.get("ips") or []))}
                    for d in devices for s in d.get("subscriptions") or []
                    if s["status"] != "none" and text.same_name(s["source"], source)]
            if dev.get("reachable") is False:
                state, why = "offline", f"{dev['name']} does not answer on its control port."
            elif any(x["status"] == "unresolved" for x in recv):
                state, why = "broken", "A receive channel is set to it but cannot find it."
            elif not recv:
                state, why = "unsubscribed", "No receive channel listens to it."
            else:
                state, why = "ok", ""
            live = {"state": state, "source": source, "device": dev["name"], "via": (net or {}).get(dev["name"]),
                    "receivers": recv, "why": why
                    + (" Another channel has the same name." if len(found) > 1 else "")}
        out.append(dict(r, live=live))
    counts: dict[str, int] = {}
    for r in out:
        counts[r["live"]["state"]] = counts.get(r["live"]["state"], 0) + 1
    unpatched = sorted(f"{c}@{d['name']}" for d in devices for c in d.get("transmit") or []
                       if f"{c}@{d['name']}" not in used)
    return {"rows": out, "unpatched": unpatched, "counts": counts}
