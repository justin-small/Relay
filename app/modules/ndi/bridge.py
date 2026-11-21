"""NDI Bridge help (#303): Relay does not run or replace NDI Bridge, it helps set it up and shows what came across.

**Spike result (2026-11-21).** NDI Bridge is NDI's own tool (it carries NDI between sites over one TCP port, encrypted). What
can be automated depends on the machine: the Windows Bridge app takes `/host`, `/join`, `/local`, `/autorun` and `/noui`; a
Windows Bridge Service has an API and optional web page; Linux has the Embedded Bridge (join only, a command line and a JSON
config) and a Bridge Service (host and join). The docs name no macOS Bridge and no way to list a Bridge's connections, so
Relay cannot start, stop or read one. It does three things instead:

* keeps a list of the booth's Bridge machines (a name, whether it is a Host or Join, its address and port);
* checks that a Host answers on its port, so a forgotten port forward shows up before Sunday;
* marks sources that arrive from a Bridge machine "Via Bridge" in the source list, and writes the start-up text for a machine
  (a Windows command line, or the Linux Embedded Bridge's JSON) with the encryption key left for the operator to type: Relay
  never stores it.
"""
from __future__ import annotations

import json
import re
import secrets
from typing import Any

MAX_MACHINES = 8
DEFAULT_PORT = 5990
_ID = re.compile(r"[a-z0-9]{4,16}")
_HOST = re.compile(r"[A-Za-z0-9.\-]{1,100}")


def new_id() -> str:
    return secrets.token_hex(4)


def clean_machines(raw: Any) -> list[dict]:
    """[{"id", "name", "role": "host" | "join", "address", "port"}]; a machine without a name or address is dropped."""
    out: list[dict] = []
    for m in raw if isinstance(raw, list) else []:
        if not isinstance(m, dict):
            continue
        name = " ".join(m["name"].split())[:60] if isinstance(m.get("name"), str) else ""
        addr = m["address"].strip() if isinstance(m.get("address"), str) else ""
        port = m.get("port")
        if not name or not _HOST.fullmatch(addr) or name.lower() in [x["name"].lower() for x in out]:
            continue
        mid = m["id"] if isinstance(m.get("id"), str) and _ID.fullmatch(m["id"]) and m["id"] not in [x["id"] for x in out] else new_id()
        out.append({"id": mid, "name": name, "role": "host" if m.get("role") == "host" else "join", "address": addr,
                    "port": port if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535 else DEFAULT_PORT})
    return out[:MAX_MACHINES]


def check(machines: list[dict]) -> dict[str, dict]:
    """Does each Host answer on its port? {machine id: {"reachable", "error"}} (Blocking; run in a thread). A Join machine
    has nothing to connect to from here, so it is not checked."""
    from ... import netsock
    from .discovery import TRAFFIC

    out: dict[str, dict] = {}
    for m in machines:
        if m["role"] != "host":
            continue
        ok, err = False, ""
        try:
            netsock.connect_tcp(TRAFFIC, m["address"], m["port"], timeout=1.5).close()
            ok = True
        except netsock.RouteError as exc:
            err = str(exc)[:120]
        except OSError:
            err = "Nothing answered on that address and port. Check the port forward."
        out[m["id"]] = {"reachable": ok, "error": err}
    return out


def via(source_address: str, machines: list[dict]) -> str:
    """The name of the Bridge machine a source's address belongs to, or ""."""
    host = (source_address or "").rsplit(":", 1)[0] if source_address.count(":") == 1 else (source_address or "")
    return next((m["name"] for m in machines if host and m["address"].lower() == host.lower()), "")


def start_text(m: dict) -> dict:
    """What to run on a Bridge machine: {"windows", "linux"} (either may be ""), with the key as a placeholder."""
    port = m["port"]
    if m["role"] == "host":
        return {"windows": "NDI Bridge.exe /host /autorun",
                "linux": "Run the NDI Bridge Service in host mode, listening on TCP " + str(port) + ".",
                "notes": ["Forward TCP " + str(port) + " from the router to this machine.",
                          "Set the same encryption key on every Join."]}
    cfg = {"ndi": {"machinename": m["name"], "vendor": {"name": "Your church", "id": "YOUR-VENDOR-ID"}},
           "bridge": {"address": f"{m['address']}:{port}", "port": port, "secure": "YOUR-ENCRYPTION-KEY"}}
    return {"windows": "NDI Bridge.exe /join /autorun",
            "linux": "./ndi-embedded-bridge /etc/ndi/bridge.json\n\n" + json.dumps(cfg, indent=2),
            "notes": ["On Windows, set the Host's address, port and key in the Join tab of the Bridge window once; "
                      "/autorun then reuses them.",
                      "The Linux Embedded Bridge needs a vendor ID from NDI, and only joins: the Host runs elsewhere."]}
