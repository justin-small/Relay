"""Intercom talk leases and priority modes (#274): the arbiter on its own, with fake phones and a fake clock.

No database, no LiveKit, no network. Run: python tests/test_intercom_leases.py
"""
import os
import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.modules.intercom import arbiter as arb  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _harness import check, eq, fails  # noqa: E402


class World:
    """Channels, members and phones the arbiter asks about, and everything it did about them."""

    def __init__(self):
        self.now = 1000.0
        self.channels = {}
        self.rights = {}          # (user, channel) -> rights
        self.sent = []            # (device, message)
        self.applied = []         # (channel, user, device, on)
        self.logged = []          # (kind, user, channel, detail)
        self.activity = []
        self.a = arb.Arbiter(clock=lambda: self.now)
        self.a.lookup, self.a.send, self.a.apply = self.lookup, self.send, self.apply
        self.a.record, self.a.changed = self.record, self.changed

    def channel(self, cid, mode="preempt", max_talkers=1, latch=0):
        self.channels[cid] = {"id": cid, "mode": mode, "max_talkers": max_talkers, "latch_seconds": latch}

    def member(self, user, cid, priority=5, talk=True):
        self.rights[(user, cid)] = {"can_talk": int(talk), "can_listen": 1, "priority": priority, "talk_selected": 1}

    async def lookup(self, user, cid):
        return self.rights.get((user, cid)), self.channels.get(cid)

    async def send(self, device, msg):
        self.sent.append((device, msg))

    async def apply(self, cid, user, device, on):
        self.applied.append((cid, user, device, on))

    async def record(self, kind, user, device, cid, detail):
        self.logged.append((kind, user, cid, detail))

    async def changed(self, cid):
        self.activity.append(cid)

    def last(self, device, kind=None):
        got = [m for d, m in self.sent if d == device and (kind is None or m["type"] == kind)]
        return got[-1] if got else None

    async def press(self, user, cid, latched=False, device=None, name=None):
        return await self.a.request(user, device or user * 10, name or f"U{user}", [cid], seq=1, latched=latched)

    def holders(self, cid):
        return [l.user_id for l in self.a.talkers(cid)]


async def run():
    print("pre-empt")
    w = World()
    w.channel(1, "preempt", max_talkers=1)
    for u, p in ((1, 5), (2, 5), (3, 2), (4, 1)):
        w.member(u, 1, p)
    eq("a free channel grants", await w.press(1, 1), {1: "granted"})
    eq("and unmutes the phone", w.applied[-1], (1, 1, 10, True))
    eq("an equal priority never pre-empts", await w.press(2, 1), {1: "busy"})
    eq("busy is told to the phone", w.last(20, "talk.denied")["reason"], "busy")
    eq("and the holder keeps the channel", w.holders(1), [1])
    eq("a higher priority cuts the talker off", await w.press(3, 1), {1: "granted"})
    eq("the loser is told who has priority", w.last(10, "talk.preempted")["by"], "U3")
    eq("only the winner holds", w.holders(1), [3])
    check("the loser's track is muted", (1, 1, 10, False) in w.applied)
    await asyncio.sleep(0)
    check("a pre-emption is logged", any(k == "talk_preempted" and u == 1 for k, u, _c, _d in w.logged))
    check("a denial is logged", any(k == "talk_denied" for k, *_ in w.logged))
    check("a grant is not logged", not any(k == "talk_granted" for k, *_ in w.logged))

    w.channel(2, "preempt", max_talkers=3)
    for u, p in ((1, 4), (2, 6), (3, 6), (4, 1), (5, 5)):
        w.member(u, 2, p)
    await w.press(1, 2), await w.press(2, 2)
    w.now += 1
    await w.press(3, 2)
    eq("three of three talk", sorted(w.holders(2)), [1, 2, 3])
    eq("the lowest priority is cut off, the newest of a tie", await w.press(5, 2), {2: "granted"})
    eq("so user 3 went (priority 6, granted last)", sorted(w.holders(2)), [1, 2, 5])
    eq("a request below everyone is refused", await w.press(4, 2) == {2: "granted"}, True)
    eq("the highest priority takes the next lowest (6)", sorted(w.holders(2)), [1, 4, 5])

    print("party line")
    w = World()
    w.channel(1, "party", max_talkers=2)
    for u in (1, 2, 3):
        w.member(u, 1, priority=u)
    await w.press(1, 1), await w.press(2, 1)
    eq("a full party line refuses", await w.press(3, 1), {1: "full"})
    eq("priority never matters on a party line", w.holders(1), [1, 2])
    await w.a.release(1, 10)
    eq("a place frees on release", await w.press(3, 1), {1: "granted"})

    print("one at a time")
    w = World()
    w.channel(1, "queue", max_talkers=1)
    for u, p in ((1, 5), (2, 5), (3, 5), (4, 2)):
        w.member(u, 1, p)
    eq("the first is granted", await w.press(1, 1), {1: "granted"})
    eq("the next waits", await w.press(2, 1), {1: "queued"})
    eq("told its place", w.last(20, "talk.queued")["position"], 1)
    eq("and the one after", await w.press(3, 1), {1: "queued"})
    eq("third in line is second", w.last(30, "talk.queued")["position"], 2)
    eq("higher priority goes ahead of earlier requests", await w.press(4, 1), {1: "queued"})
    eq("so it is first", w.last(40, "talk.queued")["position"], 1)
    eq("and the others moved back", (w.last(20, "talk.queued")["position"], w.last(30, "talk.queued")["position"]),
       (2, 3))
    await w.a.release(1, 10)
    eq("on release the head is granted", w.holders(1), [4])
    check("and told so", w.last(40, "talk.granted") is not None)
    check("its track is unmuted", (1, 4, 40, True) in w.applied)
    eq("the rest moved up", (w.last(20, "talk.queued")["position"], w.last(30, "talk.queued")["position"]), (1, 2))
    await w.a.release(2, 20)
    await w.a.release(4, 40)
    eq("a queued user who let go is skipped", w.holders(1), [3])
    eq("equal priority keeps request order", w.a.position(1, 3), 0)

    print("one press, several channels")
    w = World()
    w.channel(1, "party", max_talkers=4)
    w.channel(2, "preempt", max_talkers=1)
    w.channel(3, "queue", max_talkers=1)
    w.member(1, 1), w.member(1, 2, 5), w.member(1, 3), w.member(9, 2, 1), w.member(8, 3)
    await w.press(9, 2), await w.press(8, 3)
    got = await w.a.request(1, 10, "U1", [1, 2, 3, 99], seq=7)
    eq("each channel answers by itself", got, {1: "granted", 2: "busy", 3: "queued", 99: "not_allowed"})

    print("rights and mutes")
    w = World()
    w.channel(1, "party", max_talkers=4)
    w.member(1, 1, talk=False), w.member(2, 1)
    eq("a listener may not talk", await w.press(1, 1), {1: "not_allowed"})
    eq("a stranger may not talk", await w.press(5, 1), {1: "not_allowed"})
    await w.press(2, 1)
    w.member(2, 1, talk=False)
    await w.a.member_changed(2, 1)
    eq("losing the Talk right releases the lease", (w.holders(1), w.last(20, "talk.released")["reason"]), ([], "rights"))
    w.member(2, 1)
    await w.press(2, 1)
    await w.a.mute_channel(1)
    eq("muting a channel revokes", (w.holders(1), w.last(20, "talk.released")["reason"]), ([], "muted"))
    eq("and refuses until unmuted", await w.press(2, 1), {1: "muted"})
    await w.a.mute_channel(1, False)
    eq("unmuted it works again", await w.press(2, 1), {1: "granted"})
    await w.a.mute_user(2)
    eq("muting a user revokes", w.holders(1), [])
    eq("and refuses", await w.press(2, 1), {1: "muted"})
    await w.a.mute_user(2, False)
    await w.press(2, 1)
    await w.a.mute_all()
    eq("mute all revokes", w.holders(1), [])
    eq("and refuses everyone", await w.press(2, 1), {1: "muted"})
    await w.a.mute_all(False)
    await w.press(2, 1)
    await w.a.release_all()
    eq("release all revokes but allows the next press", (w.holders(1), await w.press(2, 1)), ([], {1: "granted"}))
    await w.a.member_changed(None, 1)
    eq("a deleted channel drops its leases", w.holders(1), [])

    print("heartbeat, latch, disconnect")
    w = World()
    w.channel(1, "party", max_talkers=4, latch=20)
    w.member(1, 1), w.member(2, 1)
    await w.press(1, 1)
    w.now += 5
    w.a.beat(10)
    w.now += 5.9
    await w.a.sweep()
    eq("a beat keeps the lease", w.holders(1), [1])
    w.now += 0.2
    await w.a.sweep()
    eq("six seconds of silence expires it", w.holders(1), [])
    eq("and drops the phone's right to unmute", w.applied[-1], (1, 1, 10, False))
    eq("the phone is told", w.last(10, "talk.released")["reason"], "expired")

    await w.press(2, 1, latched=True)
    for _ in range(25):
        w.now += 1
        w.a.beat(20)
        await w.a.sweep()
        if not w.holders(1):
            break
    eq("a latched mic is released at the limit", (w.holders(1), w.last(20, "talk.released")["reason"]),
       ([], "latch_limit"))
    warn = w.last(20, "talk.warning")
    check("with a warning five seconds before", warn and warn["seconds_left"] <= 5)
    eq("the warning came once", len([1 for d, m in w.sent if m["type"] == "talk.warning"]), 1)
    await w.press(1, 1, latched=False)
    for _ in range(30):
        w.now += 1
        w.a.beat(10)
        await w.a.sweep()
    eq("a held (not latched) button has no limit", w.holders(1), [1])

    await w.a.disconnected(10)
    eq("a closed socket releases at once", w.holders(1), [])
    eq("and mutes the track", w.applied[-1], (1, 1, 10, False))

    print("a queue entry that stops beating, a second phone")
    w = World()
    w.channel(1, "queue", max_talkers=1)
    w.member(1, 1), w.member(2, 1), w.member(3, 1)
    await w.press(1, 1), await w.press(2, 1), await w.press(3, 1)
    w.now += 5
    w.a.beat(10), w.a.beat(30)
    w.now += 2
    await w.a.sweep()
    eq("a waiting phone that went quiet is dropped from the line", w.a.position(1, 2), 0)
    await w.a.disconnected(10)
    eq("and the line moves on when the holder's phone goes", w.holders(1), [3])
    w = World()
    w.channel(1, "party", max_talkers=2)
    w.member(1, 1)
    await w.press(1, 1, device=10)
    await w.press(1, 1, device=11)
    eq("pressing from a second phone moves the lease", (w.holders(1), w.a.talkers(1)[0].device_id), ([1], 11))
    eq("the first phone was told", w.last(10, "talk.released")["reason"], "replaced")

    print("rate limit")
    w = World()
    w.channel(1, "party", max_talkers=4)
    w.member(1, 1)
    for _ in range(10):
        await w.press(1, 1)
    eq("ten a second are fine", w.last(10, "error"), None)
    for _ in range(5):
        await w.press(1, 1)
    eq("more are refused", w.last(10, "error")["for"], "talk")
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    eq("and logged once", len([1 for k, *_ in w.logged if k == "talk_rate_limited"]), 1)
    w.now += 1.1
    eq("a second later it is fine again", await w.press(1, 1), {1: "granted"})
    eq("junk is ignored", await w.a.request(1, 10, "U1", "nope"), {})
    eq("so is a bool channel", await w.a.request(1, 10, "U1", [True]), {})

    print("load: 40 phones, 10 presses a second each way")
    w = World()
    for c in (1, 2, 3, 4):
        w.channel(c, ("preempt", "party", "queue", "party")[c - 1], max_talkers=(3, 8, 1, 40)[c - 1])
    for u in range(1, 41):
        for c in (1, 2, 3, 4):
            w.member(u, c, priority=1 + u % 9)
    w.a.clock = time.monotonic
    took = []

    async def phone(u):
        for _ in range(10):
            t = time.perf_counter()
            await w.a.request(u, u * 10, f"U{u}", [1 + u % 4], seq=1)
            took.append(time.perf_counter() - t)
            await asyncio.sleep(0.05)
            await w.a.release(u, u * 10)
            await asyncio.sleep(0.05)

    await asyncio.gather(*(phone(u) for u in range(1, 41)))
    took.sort()
    p95 = took[int(len(took) * 0.95)] * 1000
    print(f"       {len(took)} requests, p95 {p95:.2f} ms")
    check("p95 grant is under 100 ms", p95 < 100)
    eq("nobody is left holding", [w.holders(c) for c in (1, 2, 3, 4)], [[], [], [], []])


asyncio.run(run())
print()
print("all ok" if not fails else f"FAILED: {fails}")
sys.exit(1 if fails else 0)
