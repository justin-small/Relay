"""mDNS announcer tests: the records tools/announce.py builds and what it answers.

No sockets are opened. Run: python3 tests/test_announce.py
"""
import socket
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import announce as a

fails = []
def check(label, got, want):
    ok = got == want
    print(("  ok   " if ok else "  FAIL ") + label + ("" if ok else f"\n         got  {got!r}\n         want {want!r}"))
    if not ok: fails.append(label)

def parse(pkt):
    """(flags, [(name, type, ttl, rdata_parsed)] answers, additional) of a response."""
    _, flags, qd, an, ns, ar = struct.unpack(">HHHHHH", pkt[:12])
    pos = 12
    out = []
    for _ in range(an + ns + ar):
        name, pos = a.decode_name(pkt, pos)
        rtype, _cls, ttl, rdlen = struct.unpack(">HHIH", pkt[pos:pos + 10])
        pos += 10
        rd = pkt[pos:pos + rdlen]
        if rtype == a.TYPE_PTR:
            val = a.decode_name(pkt, pos)[0]
        elif rtype == a.TYPE_SRV:
            val = (struct.unpack(">HHH", rd[:6])[2], a.decode_name(pkt, pos + 6)[0])
        elif rtype == a.TYPE_A:
            val = socket.inet_ntoa(rd)
        else:
            val = rd
        out.append((name, rtype, ttl, val))
        pos += rdlen
    return flags, out, (an, ar)

def query(name, qtype):
    return struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0) + a.encode_name(name) + struct.pack(">HH", qtype, 1)

svc = a.Service("Main Hall", 80, "192.168.1.20", "1.2.0")

print("\nnames")
check("dots become spaces, bounded", a.clean_label("a.b" + "x" * 80), "a b" + "x" * 60)
check("host label slug", a.host_label("Main Hall!"), "relay-main-hall")
check("empty name falls back", a.Service("", 80, "10.0.0.1", "d").name, "Relay")

print("\nannouncement")
flags, recs, _ = parse(svc.announcement())
check("authoritative response", flags, 0x8400)
byname = {(n, t): (ttl, v) for n, t, ttl, v in recs}
check("PTR -> instance", byname[(a.SERVICE, a.TYPE_PTR)][1], "Main Hall." + a.SERVICE)
check("SRV port and host", byname[("Main Hall." + a.SERVICE, a.TYPE_SRV)][1], (80, "relay-main-hall.local"))
check("A address", byname[("relay-main-hall.local", a.TYPE_A)][1], "192.168.1.20")
txt = byname[("Main Hall." + a.SERVICE, a.TYPE_TXT)][1]
check("TXT carries version, name, path", txt, b"\x07v=1.2.0\x0ename=Main Hall\x06path=/")
check("goodbye has TTL 0", {t for _n, _t, t, _v in parse(svc.announcement(ttl_scale=0))[1]}, {0})

print("\nanswers")
_, recs, counts = parse(svc.answer_for(query(a.SERVICE, a.TYPE_PTR)))
check("PTR question: PTR answer, rest alongside", counts, (1, 3))
_, recs, counts = parse(svc.answer_for(query("main hall." + a.SERVICE.upper(), a.TYPE_SRV)))
check("SRV question is case-insensitive", counts, (2, 1))
_, recs, counts = parse(svc.answer_for(query("relay-main-hall.local", a.TYPE_A)))
check("A question", recs[0][3], "192.168.1.20")
_, recs, _ = parse(svc.answer_for(query(a.META, a.TYPE_PTR)))
check("service enumeration", recs[0][3], a.SERVICE)
check("other service ignored", svc.answer_for(query("_http._tcp.local", a.TYPE_PTR)), None)
check("a response is not answered", svc.answer_for(svc.announcement()), None)
check("garbage ignored", svc.answer_for(b"\x00\x01\x02"), None)
check("truncated question ignored", svc.answer_for(query(a.SERVICE, a.TYPE_PTR)[:20]), None)

print()
if fails:
    print(f"{len(fails)} FAILED"); sys.exit(1)
print("announce: all checks passed")
