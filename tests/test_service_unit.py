"""The headless install (tools/install-service.sh, tools/relay.service): the unit keeps what the container gave.

Reads the files only; it does not start systemd. Offline. Run: .venv/bin/python tests/test_service_unit.py
"""
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
fails = []


def check(label, cond, extra=""):
    print(("  ok   " if cond else "  FAIL ") + label + ((" — " + str(extra)) if extra and not cond else ""))
    if not cond:
        fails.append(label)


unit = (ROOT / "tools" / "relay.service").read_text(encoding="utf-8")
keys: dict[str, list[str]] = {}
for line in unit.splitlines():
    m = re.match(r"^([A-Za-z]+)=(.*)$", line.strip())
    if m:
        keys.setdefault(m.group(1), []).append(m.group(2))


def one(name):
    return (keys.get(name) or [""])[0]


print("the unit")
check("runs as the unprivileged relay user in the audio group", one("User") == "relay" and one("SupplementaryGroups") == "audio")
check("no new privileges", one("NoNewPrivileges") == "true")
check("the system is read-only and home is hidden", one("ProtectSystem") == "strict" and one("ProtectHome") == "true")
check("only the state folder is writable", keys.get("ReadWritePaths") == ["/var/lib/relay"], keys.get("ReadWritePaths"))
check("state folder comes from RELAY_HOME", "RELAY_HOME=/var/lib/relay" in keys.get("Environment", []))
check("a private /tmp", one("PrivateTmp") == "true")
check("ports 80 and 443 without root: only that capability, bounded and ambient",
      one("CapabilityBoundingSet") == "CAP_NET_BIND_SERVICE" and one("AmbientCapabilities") == "CAP_NET_BIND_SERVICE")
check("restarts on failure", one("Restart") == "on-failure")
check("starts at boot", "WantedBy=multi-user.target" in unit)
check("the setup code is on for a machine with no browser", "RELAY_SETUP_CODE=1" in keys.get("Environment", []))
check("output reaches the journal at once", "PYTHONUNBUFFERED=1" in keys.get("Environment", []))
check("no bytecode is written into the read-only program folder", "PYTHONDONTWRITEBYTECODE=1" in keys.get("Environment", []))

print("the installer")
script = ROOT / "tools" / "install-service.sh"
text = script.read_text(encoding="utf-8")
if sys.platform != "win32":
    ok = subprocess.run(["bash", "-n", str(script)], capture_output=True).returncode == 0
    check("is valid bash", ok)
for word in ("install", "update", "uninstall", "reset-owner", "setup-code", "--purge"):
    check(f"offers {word}", word in text)
check("every file it ships exists", all((ROOT / p).exists() for p in re.findall(r"^SHIP=\((.*)\)", text, re.M)[0].split()))
check("uninstall keeps the state folder unless asked", 'if [ "$PURGE" = 1 ]' in text)

if fails:
    print(f"\n{len(fails)} failed")
    sys.exit(1)
print("\nall passed")
