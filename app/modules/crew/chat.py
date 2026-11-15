"""Crew team chat (#230): a team channel and a channel per crew role.

History is kept on the host for 30 days and a client is shown the newest 100 of a channel. A new message reaches
phones that have the chat open over server-sent events (a one-way stream; posting is an ordinary request). A person
can ask for a quiet notification per channel, off until they choose it, and only while they are not already
watching the chat.

A channel is "team" (everyone who may use chat) or "role:<role>" (the free-text role an admin set, #227, lowercased),
readable by the people who hold that role and by moderators (Operator and above), who may also delete any message.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque

from ... import db
from . import paging, people

log = logging.getLogger("relay.crew")

KEEP_DAYS = 30
MIRROR = 100
BODY_MAX = 500
RATE_COUNT, RATE_S = 5, 10
TEAM = "team"


class Refused(people.Invalid):
    pass


def role_channel(role: str) -> str | None:
    text = " ".join((role or "").lower().split())
    return f"role:{text}" if text else None


def clean_body(raw) -> str:
    if not isinstance(raw, str) or any(ord(c) < 32 and c != "\n" or ord(c) == 127 for c in raw):
        raise Refused("Write a message as text.")
    text = "\n".join(line.rstrip() for line in raw.strip().splitlines())
    while "\n\n\n" in text:
        text = text.replace("\n\n\n", "\n\n")
    if not text:
        raise Refused("Write a message first.")
    if len(text) > BODY_MAX:
        raise Refused(f"A message is at most {BODY_MAX} characters.")
    return text


def own_channel(user_id: int) -> str | None:
    return role_channel(people.profile(user_id)["role"])


def can_read(user, channel) -> bool:
    if not isinstance(channel, str):
        return False
    if channel == TEAM:
        return True
    if not channel.startswith("role:") or len(channel) > 50:
        return False
    return user.can("crew.moderate") or own_channel(user.id) == channel


def channels_for(user) -> list[dict]:
    """The channels this person sees: team, their own role's, and for a moderator every role in use."""
    people.ready()
    on = {r["channel"] for r in db.query("select channel from crew_chat_push where user_id = ?", (user.id,))}
    out = [{"id": TEAM, "title": "Team", "push": TEAM in on}]
    seen = set()
    mine = own_channel(user.id)
    rows = db.query("select distinct role from crew_profiles where role != ''") if user.can("crew.moderate") else []
    wanted = [(mine, people.profile(user.id)["role"])] if mine else []
    wanted += [(role_channel(r["role"]), r["role"]) for r in rows]
    for cid, title in wanted:
        if cid and cid not in seen:
            seen.add(cid)
            out.append({"id": cid, "title": title, "push": cid in on})
    return out


# -- storage ------------------------------------------------------------------------------------------------------
def _row(r: dict) -> dict:
    return {"id": r["id"], "channel": r["channel"], "user_id": r["user_id"], "author": r["author"],
            "role": r["role"], "body": r["body"], "ts": r["created_ts"]}


def history(channel: str, limit: int = MIRROR, after: int = 0) -> list[dict]:
    """The newest `limit` messages of a channel, oldest first; with `after`, only those newer than that id."""
    people.ready()
    rows = db.query("select * from crew_chat_messages where channel = ? and id > ? order by id desc limit ?",
                    (channel, after, max(1, min(limit, MIRROR))))
    return [_row(r) for r in reversed(rows)]


_recent: dict[int, deque] = {}


def _rate_ok(user_id: int, now: float) -> bool:
    q = _recent.setdefault(user_id, deque())
    while q and q[0] <= now - RATE_S:
        q.popleft()
    if len(q) >= RATE_COUNT:
        return False
    q.append(now)
    return True


def post(user, channel: str, body, now: float | None = None) -> dict:
    ts = time.time() if now is None else now
    people.ready()
    text = clean_body(body)
    if not can_read(user, channel):
        raise Refused("You cannot post in that channel.", 403)
    if not _rate_ok(user.id, ts):
        raise Refused("Slow down: a few messages at a time.", 429)
    role = people.profile(user.id)["role"]
    row = db.one("insert into crew_chat_messages (channel, user_id, author, role, body, created_ts)"
                 " values (?, ?, ?, ?, ?, ?) returning *", (channel, user.id, user.name, role, text, ts))
    db.execute("delete from crew_chat_messages where created_ts < ?", (ts - KEEP_DAYS * 86400,))
    return _row(row)


def remove(user, message_id: int) -> dict:
    """Delete a message: its author may, and so may a moderator. Returns it (for telling the open chats)."""
    people.ready()
    row = db.one("select * from crew_chat_messages where id = ?", (message_id,))
    if row is None:
        raise Refused("No such message.", 404)
    if not (row["user_id"] == user.id or user.can("crew.moderate")) or not can_read(user, row["channel"]):
        raise Refused("You cannot delete that message.", 403)
    db.execute("delete from crew_chat_messages where id = ?", (message_id,))
    return _row(row)


def set_push(user, channel, on) -> None:
    if not can_read(user, channel) or not isinstance(on, bool):
        raise Refused("Choose a channel you can read, and on or off.")
    people.ready()
    if on:
        db.execute("insert into crew_chat_push (user_id, channel) values (?, ?) on conflict do nothing",
                   (user.id, channel))
    else:
        db.execute("delete from crew_chat_push where user_id = ? and channel = ?", (user.id, channel))


def push_targets(channel: str, author_id: int | None, watching) -> list[int]:
    """Who gets a quiet notification: opted in for this channel, allowed to read it, not the author, and not
    already looking at the chat (`watching` says who has it open)."""
    from ... import users

    out = []
    for r in db.query("select p.user_id from crew_chat_push p join users u on u.id = p.user_id"
                      " where p.channel = ? and u.disabled = 0", (channel,)):
        uid = r["user_id"]
        if uid == author_id or uid in watching:
            continue
        who = users.get(uid)
        if who is not None and who.can("crew.chat") and can_read(who, channel):
            out.append(uid)
    return out


def notify(channel: str, message: dict, watching) -> int:
    """Send the quiet pushes for one message. Blocking; run off the event loop. Returns how many phones took it."""
    ids = push_targets(channel, message["user_id"], watching)
    if not ids:
        return 0
    key = paging.vapid_key()
    payload = {"title": "Team" if channel == TEAM else message["role"] or "Chat",
               "body": f"{message['author']}: {message['body'][:80]}", "url": "/crew#chat",
               "tag": "chat-" + channel[:40], "quiet": True}
    return sum(1 for uid in ids if paging.push_user(key, uid, payload))


# -- the open chats ---------------------------------------------------------------------------------------------
class Sub:
    def __init__(self, user, role_chan: str | None):
        self.user_id = user.id
        self.moderator = user.can("crew.moderate")
        self.role_chan = role_chan
        self.queue: asyncio.Queue = asyncio.Queue(200)

    def sees(self, channel: str) -> bool:
        return channel == TEAM or self.moderator or channel == self.role_chan


class Hub:
    """The streams open right now. Lives on the event loop; a stream that falls 200 events behind is closed so its
    phone reconnects and reloads, instead of holding memory."""

    def __init__(self) -> None:
        self.subs: list[Sub] = []
        self.loop: asyncio.AbstractEventLoop | None = None

    def signed_out(self, user_id: int) -> None:
        """users.signed_out_hooks: called from any thread."""
        if self.loop is not None:
            self.loop.call_soon_threadsafe(self.drop_user, user_id)

    def subscribe(self, user, role_chan: str | None) -> Sub:
        sub = Sub(user, role_chan)
        self.subs.append(sub)
        return sub

    def unsubscribe(self, sub: Sub) -> None:
        if sub in self.subs:
            self.subs.remove(sub)

    def watching(self) -> set[int]:
        return {s.user_id for s in self.subs}

    def publish(self, channel: str, event: dict) -> None:
        for s in list(self.subs):
            if not s.sees(channel):
                continue
            try:
                s.queue.put_nowait(event)
            except asyncio.QueueFull:
                self.close(s)

    def close(self, sub: Sub) -> None:
        self.unsubscribe(sub)                # no more events for it, whatever is published next
        while not sub.queue.empty():
            sub.queue.get_nowait()
        sub.queue.put_nowait(None)           # the stream ends; the phone reconnects and reloads

    def drop_user(self, user_id: int) -> None:
        """A person signed out, was turned off, or changed role: end their streams so they reconnect fresh."""
        for s in list(self.subs):
            if s.user_id == user_id:
                self.close(s)

    def drop_all(self) -> None:
        for s in list(self.subs):
            self.close(s)


hub = Hub()
