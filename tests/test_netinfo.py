"""LAN address detection (app/netinfo.py). Offline. Run: .venv/bin/python tests/test_netinfo.py"""
import asyncio
import os
import sys
import types
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.pop("RELAY_ADMIN_IPS", None)

from app import netinfo  # noqa: E402

fails = []


def eq(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label)
    if not ok:
        print(f"         got  {got!r}\n         want {want!r}")
        fails.append(label)


def adapter(*addrs):
    return types.SimpleNamespace(ips=[types.SimpleNamespace(ip=a, is_IPv4=True) if isinstance(a, str)
                                      else types.SimpleNamespace(ip=a[0], is_IPv4=False) for a in addrs])


fake = types.SimpleNamespace(get_adapters=lambda: [
    adapter("127.0.0.1"), adapter("169.254.3.4"), adapter("192.168.1.20"), adapter("10.0.0.5"),
    adapter(("fe80::1", 0, 0)), adapter("192.168.1.20")])
with mock.patch.dict(sys.modules, {"ifaddr": fake}):
    eq("loopback, link-local, IPv6 and duplicates dropped", netinfo._interface_ips(), ["192.168.1.20", "10.0.0.5"])
    with mock.patch.object(netinfo, "_default_route_ip", return_value="10.0.0.5"):
        eq("default route first", netinfo.lan_ips(), ["10.0.0.5", "192.168.1.20"])
    with mock.patch.object(netinfo, "_default_route_ip", return_value=None), \
            mock.patch.dict(os.environ, {"RELAY_ADMIN_IPS": "203.0.113.9; nope,192.168.1.20"}):
        eq("extras appended, junk and repeats ignored", netinfo.lan_ips(), ["192.168.1.20", "10.0.0.5", "203.0.113.9"])
    with mock.patch.object(netinfo, "_default_route_ip", return_value="127.0.0.1"):
        eq("a loopback default route is not a LAN address", netinfo.lan_ips(), ["192.168.1.20", "10.0.0.5"])

with mock.patch.object(netinfo, "_interface_ips", return_value=[]), mock.patch.object(netinfo, "_default_route_ip", return_value=None):
    eq("no network gives an empty list", netinfo.lan_ips(), [])

# The watcher reports a change once, not every poll.
seq = iter([["192.168.1.20"], ["192.168.1.20"], ["10.1.1.7"], ["10.1.1.7"]])
seen = []


async def run():
    with mock.patch.object(netinfo, "lan_ips", lambda: next(seq, ["10.1.1.7"])):
        task = asyncio.create_task(netinfo.watch(seen.append, interval=0.01))
        await asyncio.sleep(0.2)
        task.cancel()


asyncio.run(run())
eq("watcher reports the Wi-Fi switch once", seen, [["10.1.1.7"]])

print("\nFAILED: " + ", ".join(fails) if fails else "\nAll netinfo checks passed.")
sys.exit(1 if fails else 0)
