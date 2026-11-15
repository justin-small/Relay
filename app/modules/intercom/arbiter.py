"""Talk leases and channel priority modes (#274).

A phone never publishes on its own say-so: it asks (`talk.request`), Relay checks membership, mutes and the channel's
mode, and only then grants a lease, which is what unmutes the phone's armed track (rooms.set_talking, #239). Leases
and queues live in memory only: nothing here touches the database on the request path, and after a restart nobody
holds one. The decision itself (`_decide`, `_end`, `_promote`) is synchronous, so two requests can never both take
the last place; only the effects (replies, unmuting, the log) are awaited, after the state has changed.

Hooks the module wires up (presence.py), all async:
  lookup(user_id, channel_id) -> (rights | None, channel | None)   what the user may do and how the channel works
  send(device_id, message)                                          a message to one phone
  apply(channel_id, user_id, device_id, on)                         unmute or mute that phone's track in LiveKit
  record(kind, user_id, device_id, channel_id, detail)              a line in the connection log
  changed(channel_id)                                               who is talking on a channel changed
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

log = logging.getLogger("relay.intercom")

BEAT_EXPIRES_S = 6.0          # a phone beats every 2 s while it holds; this long without one and the lease is gone
LATCH_WARN_S = 5.0
REQUESTS_PER_S = 10           # per device
SWEEP_EVERY_S = 0.5
MAX_CHANNELS = 32             # in one request


@dataclass
class Lease:
    user_id: int
    device_id: int
    name: str
    priority: int
    granted_at: float
    last_beat: float
    latched: bool
    latch_seconds: int
    warned: bool = False


@dataclass
class Waiter:
    user_id: int
    device_id: int
    name: str
    priority: int
    order: int
    last_beat: float
    latched: bool
    latch_seconds: int
    seq: Any = None


class Arbiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self.clock = clock
        self.leases: dict[int, dict[int, Lease]] = {}          # channel -> user -> lease
        self.queues: dict[int, list[Waiter]] = {}              # One-at-a-time channels only
        self.muted_channels: set[int] = set()
        self.muted_users: set[int] = set()
        self.muted_all = False
        self._rate: dict[int, deque[float]] = {}
        self._flagged: set[int] = set()
        self._order = itertools.count(1)
        self._tasks: set[asyncio.Task] = set()
        self.lookup: Callable[[int, int], Awaitable[tuple]] | None = None
        self.send: Callable[[int, dict], Awaitable[None]] | None = None
        self.apply: Callable[[int, int, int, bool], Awaitable[None]] | None = None
        self.record: Callable[..., Awaitable[None]] | None = None
        self.changed: Callable[[int], Awaitable[None]] | None = None

    # ------------------------------------------------------------------ views
    def talkers(self, channel_id: int) -> list[Lease]:
        return sorted(self.leases.get(channel_id, {}).values(), key=lambda l: l.granted_at)

    def holds(self, channel_id: int, user_id: int) -> bool:
        return user_id in self.leases.get(channel_id, {})

    def position(self, channel_id: int, user_id: int) -> int:
        for i, w in enumerate(self.queues.get(channel_id, []), 1):
            if w.user_id == user_id:
                return i
        return 0

    def reset(self) -> None:
        self.leases.clear(), self.queues.clear(), self._rate.clear(), self._flagged.clear()
        self.muted_channels.clear(), self.muted_users.clear()
        self.muted_all = False

    # ------------------------------------------------------------------ the synchronous core
    def _full(self, ch: int, channel: dict) -> bool:
        return len(self.leases.get(ch, {})) >= channel["max_talkers"]

    def _queue_order(self, ch: int) -> None:
        self.queues[ch].sort(key=lambda w: (w.priority, w.order))

    def _grant(self, ch: int, w: Lease | Waiter, channel: dict, ops: list, dirty: set, seq: Any) -> None:
        now = self.clock()
        self.leases.setdefault(ch, {})[w.user_id] = Lease(
            w.user_id, w.device_id, w.name, w.priority, now, now, w.latched, channel["latch_seconds"])
        ops.append(("apply", ch, w.user_id, w.device_id, True))
        ops.append(("send", w.device_id, {"type": "talk.granted", "channel": ch, "seq": seq}))
        dirty.add(ch)

    def _end(self, ch: int, user_id: int, ops: list, dirty: set, message: dict | None) -> None:
        lease = self.leases.get(ch, {}).pop(user_id, None)
        if lease is None:
            return
        if not self.leases[ch]:
            del self.leases[ch]
        ops.append(("apply", ch, lease.user_id, lease.device_id, False))
        if message is not None:
            ops.append(("send", lease.device_id, {**message, "channel": ch}))
        dirty.add(ch)

    def _drop_waiter(self, ch: int, user_id: int) -> bool:
        q = self.queues.get(ch, [])
        for w in q:
            if w.user_id == user_id:
                q.remove(w)
                if not q:
                    self.queues.pop(ch, None)
                return True
        return False

    def _tell_positions(self, ch: int, ops: list, dirty: set) -> None:
        for i, w in enumerate(self.queues.get(ch, []), 1):
            ops.append(("send", w.device_id, {"type": "talk.queued", "channel": ch, "position": i, "seq": w.seq}))
        dirty.add(ch)

    def _promote(self, ch: int, channel: dict, ops: list, dirty: set) -> None:
        """Give free places to the head of the queue. Someone who let go is no longer in it."""
        moved = False
        while self.queues.get(ch) and not self._full(ch, channel):
            head = self.queues[ch].pop(0)
            if not self.queues[ch]:
                del self.queues[ch]
            self._grant(ch, head, channel, ops, dirty, head.seq)
            moved = True
        if moved:
            self._tell_positions(ch, ops, dirty)

    def _decide(self, user_id: int, device_id: int, name: str, ch: int, rights: dict | None, channel: dict | None,
                latched: bool, seq: Any, ops: list, dirty: set) -> str:
        """One channel's answer to one request: granted, queued, or a refusal reason. Pure state, no awaiting."""
        def deny(reason: str) -> str:
            ops.append(("send", device_id, {"type": "talk.denied", "channel": ch, "reason": reason, "seq": seq}))
            return reason

        if rights is None or channel is None or not rights["can_talk"]:
            return deny("not_allowed")
        if self.muted_all or ch in self.muted_channels or user_id in self.muted_users:
            return deny("muted")
        priority = rights["priority"]
        held = self.leases.get(ch, {}).get(user_id)
        if held is not None:                                   # pressing again, or from another phone
            if held.device_id != device_id:
                ops.append(("apply", ch, user_id, held.device_id, False))
                ops.append(("send", held.device_id, {"type": "talk.released", "channel": ch, "reason": "replaced"}))
                held.device_id = device_id
                ops.append(("apply", ch, user_id, device_id, True))
            held.last_beat, held.latched = self.clock(), latched
            ops.append(("send", device_id, {"type": "talk.granted", "channel": ch, "seq": seq}))
            return "granted"
        self._drop_waiter(ch, user_id)
        me = Waiter(user_id, device_id, name, priority, next(self._order), self.clock(), latched,
                    channel["latch_seconds"], seq)
        mode = channel["mode"]
        if not self._full(ch, channel):
            if mode == "queue" and self.queues.get(ch):
                pass                                           # others are waiting: join the line
            else:
                self._grant(ch, me, channel, ops, dirty, seq)
                return "granted"
        elif mode == "preempt":
            victim = max(self.leases[ch].values(), key=lambda l: (l.priority, l.granted_at))
            if priority < victim.priority:                     # strictly higher; equal never pre-empts
                self._end(ch, victim.user_id, ops, dirty, {"type": "talk.preempted", "by": name})
                ops.append(("record", "talk_preempted", victim.user_id, victim.device_id, ch,
                            {"by": user_id, "by_priority": priority, "priority": victim.priority}))
                self._grant(ch, me, channel, ops, dirty, seq)
                return "granted"
            ops.append(("record", "talk_denied", user_id, device_id, ch, {"reason": "busy"}))
            return deny("busy")
        elif mode == "party":
            ops.append(("record", "talk_denied", user_id, device_id, ch, {"reason": "full"}))
            return deny("full")
        self.queues.setdefault(ch, []).append(me)              # One at a time, channel full or others waiting
        self._queue_order(ch)
        self._promote(ch, channel, ops, dirty)
        if self.holds(ch, user_id):
            return "granted"
        self._tell_positions(ch, ops, dirty)
        return "queued"

    # ------------------------------------------------------------------ effects
    async def _run(self, ops: list, dirty: set) -> None:
        sends = [self.send(o[1], o[2]) for o in ops if o[0] == "send"] if self.send else []
        applies = [self.apply(o[1], o[2], o[3], o[4]) for o in ops if o[0] == "apply"] if self.apply else []
        await asyncio.gather(*sends, *applies, return_exceptions=True)
        if self.record:
            for o in ops:
                if o[0] == "record":
                    self._later(self.record(o[1], o[2], o[3], o[4], o[5]))
        if self.changed:
            for ch in dirty:
                self._later(self.changed(ch))

    def _later(self, coro) -> None:
        task = asyncio.ensure_future(self._quiet(coro))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    @staticmethod
    async def _quiet(coro) -> None:
        try:
            await coro
        except Exception:
            log.exception("intercom: talk bookkeeping failed")

    def _allow(self, device_id: int) -> bool:
        now = self.clock()
        q = self._rate.setdefault(device_id, deque())
        while q and now - q[0] >= 1.0:
            q.popleft()
        if len(q) >= REQUESTS_PER_S:
            return False
        q.append(now)
        self._flagged.discard(device_id)
        return True

    # ------------------------------------------------------------------ what phones ask
    async def request(self, user_id: int, device_id: int, name: str, channels: Any, seq: Any = None,
                      latched: bool = False) -> dict[int, str]:
        """`talk.request`: answer for each channel (each answers independently). Returns {channel: outcome}."""
        ops: list = []
        if not self._allow(device_id):
            ops.append(("send", device_id, {"type": "error", "for": "talk", "seq": seq,
                                            "text": "Too many requests. Wait a moment."}))
            if device_id not in self._flagged:
                self._flagged.add(device_id)               # logged once per burst, not per refused request
                ops.append(("record", "talk_rate_limited", user_id, device_id, None, {}))
            await self._run(ops, set())
            return {}
        if not isinstance(channels, list) or not channels or len(channels) > MAX_CHANNELS:
            return {}
        wanted = list(dict.fromkeys(c for c in channels if isinstance(c, int) and not isinstance(c, bool)))
        looked = await asyncio.gather(*(self.lookup(user_id, c) for c in wanted)) if self.lookup else []
        dirty: set = set()
        out: dict[int, str] = {}
        for ch, (rights, channel) in zip(wanted, looked):
            out[ch] = self._decide(user_id, device_id, name, ch, rights, channel, bool(latched), seq, ops, dirty)
        await self._run(ops, dirty)
        return out

    async def release(self, user_id: int, device_id: int, channels: Any = None) -> None:
        """`talk.release`: let go on some channels (default: all this phone holds or waits for)."""
        ops: list = []
        dirty: set = set()
        mine = [ch for ch, ls in self.leases.items() if user_id in ls and ls[user_id].device_id == device_id]
        mine += [ch for ch, q in self.queues.items() if any(w.user_id == user_id and w.device_id == device_id
                                                            for w in q)]
        if isinstance(channels, list):
            mine = [c for c in dict.fromkeys(mine) if c in channels]
        touched = []
        for ch in dict.fromkeys(mine):
            lease = self.leases.get(ch, {}).get(user_id)
            if lease is not None and lease.device_id == device_id:
                self._end(ch, user_id, ops, dirty, None)
                touched.append(ch)
            elif self._drop_waiter(ch, user_id):
                self._tell_positions(ch, ops, dirty)
        await self._promote_all(touched, ops, dirty)

    def beat(self, device_id: int) -> None:
        """`talk.beat`: this phone is still there and still holding."""
        now = self.clock()
        for ls in self.leases.values():
            for lease in ls.values():
                if lease.device_id == device_id:
                    lease.last_beat = now
        for q in self.queues.values():
            for w in q:
                if w.device_id == device_id:
                    w.last_beat = now

    async def _promote_all(self, chans: list[int], ops: list, dirty: set) -> None:
        for ch in dict.fromkeys(chans):
            if self.queues.get(ch) and self.lookup:
                # The channel row is needed for its limits; any member's lookup returns it.
                _, channel = await self.lookup(self.queues[ch][0].user_id, ch)
                if channel is not None:
                    self._promote(ch, channel, ops, dirty)
        await self._run(ops, dirty)

    # ------------------------------------------------------------------ things that happen to a phone or channel
    async def disconnected(self, device_id: int) -> None:
        """The phone's socket closed: everything it held goes at once."""
        ops: list = []
        dirty: set = set()
        touched = []
        for ch, ls in list(self.leases.items()):
            for lease in [l for l in ls.values() if l.device_id == device_id]:
                self._end(ch, lease.user_id, ops, dirty, None)
                touched.append(ch)
        for ch, q in list(self.queues.items()):
            for w in [w for w in q if w.device_id == device_id]:
                self._drop_waiter(ch, w.user_id)
                self._tell_positions(ch, ops, dirty)
        self._rate.pop(device_id, None), self._flagged.discard(device_id)
        await self._promote_all(touched, ops, dirty)

    async def member_changed(self, user_id: int | None, channel_id: int) -> None:
        """A member's rights changed or went, or (user None) the channel did."""
        ops: list = []
        dirty: set = set()
        if user_id is None:
            for uid in list(self.leases.get(channel_id, {})):
                self._end(channel_id, uid, ops, dirty, {"type": "talk.released", "reason": "channel_removed"})
            self.queues.pop(channel_id, None)
            await self._run(ops, dirty)
            return
        rights, _ = await self.lookup(user_id, channel_id) if self.lookup else (None, None)
        if rights is None or not rights["can_talk"]:
            self._end(channel_id, user_id, ops, dirty, {"type": "talk.released", "reason": "rights"})
            self._drop_waiter(channel_id, user_id)
            self._tell_positions(channel_id, ops, dirty)
            await self._promote_all([channel_id], ops, dirty)
        else:
            held = self.leases.get(channel_id, {}).get(user_id)
            if held is not None:
                held.priority = rights["priority"]
            await self._run(ops, dirty)

    async def _revoke_matching(self, match: Callable[[int, Lease], bool], reason: str) -> None:
        ops: list = []
        dirty: set = set()
        touched = []
        for ch, ls in list(self.leases.items()):
            for lease in [l for l in ls.values() if match(ch, l)]:
                self._end(ch, lease.user_id, ops, dirty, {"type": "talk.released", "reason": reason})
                touched.append(ch)
        for ch, q in list(self.queues.items()):
            for w in [w for w in q if match(ch, w)]:        # type: ignore[arg-type]
                self._drop_waiter(ch, w.user_id)
                ops.append(("send", w.device_id, {"type": "talk.released", "channel": ch, "reason": reason}))
                self._tell_positions(ch, ops, dirty)
        await self._promote_all(touched, ops, dirty)

    # Operator overrides (#241): revoke what is affected and refuse new requests until unmuted.
    async def mute_channel(self, channel_id: int, on: bool = True) -> None:
        if on:
            self.muted_channels.add(channel_id)
            await self._revoke_matching(lambda ch, _l: ch == channel_id, "muted")
        else:
            self.muted_channels.discard(channel_id)

    async def mute_user(self, user_id: int, on: bool = True) -> None:
        if on:
            self.muted_users.add(user_id)
            await self._revoke_matching(lambda _c, l: l.user_id == user_id, "muted")
        else:
            self.muted_users.discard(user_id)

    async def mute_all(self, on: bool = True) -> None:
        self.muted_all = on
        if on:
            await self._revoke_matching(lambda _c, _l: True, "muted")

    async def release_all(self) -> None:
        await self._revoke_matching(lambda _c, _l: True, "released_by_operator")

    # ------------------------------------------------------------------ time
    async def sweep(self) -> None:
        """Expire leases with no heartbeat and latches past their limit; warn five seconds before a latch ends."""
        now = self.clock()
        ops: list = []
        dirty: set = set()
        touched = []
        for ch, ls in list(self.leases.items()):
            for lease in list(ls.values()):
                if now - lease.last_beat > BEAT_EXPIRES_S:
                    self._end(ch, lease.user_id, ops, dirty, {"type": "talk.released", "reason": "expired"})
                    touched.append(ch)
                elif lease.latched and lease.latch_seconds > 0:
                    left = lease.latch_seconds - (now - lease.granted_at)
                    if left <= 0:
                        self._end(ch, lease.user_id, ops, dirty, {"type": "talk.released", "reason": "latch_limit"})
                        touched.append(ch)
                    elif left <= LATCH_WARN_S and not lease.warned:
                        lease.warned = True
                        ops.append(("send", lease.device_id, {"type": "talk.warning", "channel": ch,
                                                              "seconds_left": int(left + 0.999)}))
        for ch, q in list(self.queues.items()):
            for w in [w for w in q if now - w.last_beat > BEAT_EXPIRES_S]:
                self._drop_waiter(ch, w.user_id)
                ops.append(("send", w.device_id, {"type": "talk.released", "channel": ch, "reason": "expired"}))
                self._tell_positions(ch, ops, dirty)
        for device_id in [d for d, q in self._rate.items() if not q or now - q[-1] >= 1.0]:
            self._rate.pop(device_id, None)
        if ops:
            await self._promote_all(touched, ops, dirty)

    async def loop(self) -> None:
        while True:
            await asyncio.sleep(SWEEP_EVERY_S)
            try:
                await self.sweep()
            except Exception:
                log.exception("intercom: talk sweep failed")


arbiter = Arbiter()
