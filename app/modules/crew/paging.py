"""Pages that buzz until read (#228).

A page goes to chosen crew accounts, or to a Planning Center team (the people on today's plan whose accounts
matched, #227). Each person's phone gets a web push, and the push fires again on a front-loaded schedule for at
most two minutes, stopping for that person the moment they confirm on their own link. The sender sees who has
confirmed and who is still waiting.

The schedule is worked out on this host from the page's send time (never by a phone), and survives a restart.
"""
from __future__ import annotations

import logging
import secrets
import time
from concurrent.futures import ThreadPoolExecutor

from ... import db
from . import people, webpush

log = logging.getLogger("relay.crew")

SCHEDULE_S = (0, 5, 10, 20, 40, 70, 120)       # seconds after sending; the last push is at two minutes
MESSAGE_MAX = 140
MAX_RECIPIENTS = 100
KEEP_DAYS = 30
RECENT = 10

pending = True           # something may be due: set when a page is sent, cleared when nothing is open
_pool = ThreadPoolExecutor(4, thread_name_prefix="crew-push")


class Invalid(people.Invalid):
    pass


def clean_message(raw) -> str:
    if not isinstance(raw, str) or any(ord(c) < 32 and c != "\n" or ord(c) == 127 for c in raw):
        raise Invalid("Write the page as text.")
    text = " ".join(raw.split())
    if not text:
        raise Invalid("Write what the page says.")
    if len(text) > MESSAGE_MAX:
        raise Invalid(f"A page is at most {MESSAGE_MAX} characters.")
    return text


# -- keys and subscriptions -----------------------------------------------------------------------------------
def vapid_key():
    people.ready()
    row = db.one("select private_pem from crew_vapid where id = 1")
    if row:
        return webpush.key_from_pem(row["private_pem"])
    key = webpush.new_key()
    db.execute("insert into crew_vapid (id, private_pem, public_key) values (1, ?, ?) on conflict do nothing",
               (webpush.key_to_pem(key), webpush.b64e(webpush.public_bytes(key))))
    return webpush.key_from_pem(db.one("select private_pem from crew_vapid where id = 1")["private_pem"])


def public_key() -> str:
    return webpush.b64e(webpush.public_bytes(vapid_key()))


def subscribe(user_id: int, sub) -> None:
    if not isinstance(sub, dict) or not webpush.allowed(sub.get("endpoint")):
        raise Invalid("This browser's push service is not supported.")
    keys = sub.get("keys")
    keys = keys if isinstance(keys, dict) else {}
    try:
        p256, auth = keys.get("p256dh"), keys.get("auth")
        if len(webpush.b64d(p256)) != 65 or len(webpush.b64d(auth)) < 16:
            raise ValueError
    except (TypeError, ValueError, AttributeError):
        raise Invalid("The subscription's keys are not valid.")
    people.ready()
    count = db.one("select count(*) as n from crew_push_subs where user_id = ?", (user_id,))["n"]
    if count >= 10:
        db.execute("delete from crew_push_subs where id = (select min(id) from crew_push_subs where user_id = ?)",
                   (user_id,))
    db.execute("insert into crew_push_subs (user_id, endpoint, p256dh, auth, created_at) values (?, ?, ?, ?, ?)"
               " on conflict (endpoint) do update set user_id = excluded.user_id, p256dh = excluded.p256dh,"
               " auth = excluded.auth", (user_id, sub["endpoint"], p256, auth, db.now()))


def unsubscribe(user_id: int, endpoint) -> None:
    people.ready()
    if isinstance(endpoint, str):
        db.execute("delete from crew_push_subs where user_id = ? and endpoint = ?", (user_id, endpoint))


def subscribed(user_id: int) -> bool:
    people.ready()
    return db.one("select 1 as x from crew_push_subs where user_id = ? limit 1", (user_id,)) is not None


# -- who a page reaches ------------------------------------------------------------------------------------------
def serving(tracks, time_id: str | None = None) -> dict[str, list[dict]]:
    """Today's plan people by Planning Center team name: [{"person", "name"}], declined answers left out. With a
    plan time, only the people who serve that time (a person with no times listed serves them all)."""
    out: dict[str, list[dict]] = {}
    for tr in tracks:
        for m in tr.team:
            if m.get("team") and m.get("person") and m.get("status") != "declined" and (
                    time_id is None or m.get("times") is None or time_id in m["times"]):
                row = {"person": m["person"], "name": m["name"]}
                if row not in out.setdefault(m["team"], []):
                    out[m["team"]].append(row)
    return out


def targets(tracks) -> dict:
    """What the admin page offers: crew accounts, and the teams serving today with how many have accounts."""
    people.ready()
    linked = {r["pc_person"]: r["user_id"] for r in db.query(
        "select user_id, pc_person from crew_profiles where pc_person is not null")}
    subs = {r["user_id"] for r in db.query("select distinct user_id from crew_push_subs")}
    accounts = [{"id": p["id"], "name": p["name"], "role": p["role"], "phone": p["id"] in subs}
                for p in people.people_list() if not p["disabled"]]
    teams = [{"name": name, "people": len(rows), "reachable": sum(1 for r in rows if r["person"] in linked)}
             for name, rows in sorted(serving(tracks).items())]
    return {"people": accounts, "teams": teams}


def resolve(user_ids, teams, tracks, time_id: str | None = None) -> tuple[list[int], list[str]]:
    """The accounts to page, and the names of Planning Center people with no matched account."""
    people.ready()
    want: list[int] = []
    for u in user_ids if isinstance(user_ids, list) else []:
        if isinstance(u, int) and not isinstance(u, bool) and u not in want:
            want.append(u)
    unmatched: list[str] = []
    if isinstance(teams, list) and teams:
        linked = {r["pc_person"]: r["user_id"] for r in db.query(
            "select user_id, pc_person from crew_profiles where pc_person is not null")}
        by_team = serving(tracks, time_id)
        for t in teams:
            for row in by_team.get(t, []) if isinstance(t, str) else []:
                uid = linked.get(row["person"])
                if uid is None:
                    if row["name"] not in unmatched:
                        unmatched.append(row["name"])
                elif uid not in want:
                    want.append(uid)
    live = {r["id"] for r in db.query("select id from users where disabled = 0")}
    return [u for u in want if u in live], unmatched


# -- sending ---------------------------------------------------------------------------------------------------
def create(message, sender_id, user_ids: list[int], now: float | None = None) -> int:
    global pending
    text = clean_message(message)
    if not user_ids:
        raise Invalid("Choose who to page.")
    if len(user_ids) > MAX_RECIPIENTS:
        raise Invalid(f"A page goes to at most {MAX_RECIPIENTS} people.")
    ts = time.time() if now is None else now
    with db.transaction() as tx:
        tx.execute("delete from crew_pages where sent_ts < ?", (ts - KEEP_DAYS * 86400,))
        page = tx.one("insert into crew_pages (message, sent_by, sent_ts, created_at) values (?, ?, ?, ?)"
                      " returning id", (text, sender_id if sender_id and sender_id > 0 else None, ts, db.now()))
        for u in user_ids:
            tx.execute("insert into crew_page_recipients (page_id, user_id, token) values (?, ?, ?)",
                       (page["id"], u, secrets.token_urlsafe(24)))
    pending = True
    return page["id"]


def push_user(key, user_id: int, payload: dict) -> bool:
    """Push `payload` to every phone this person enabled. True when at least one accepted it."""
    subs = db.query("select id, endpoint, p256dh, auth from crew_push_subs where user_id = ?", (user_id,))
    sent = False
    for s in subs:
        try:
            webpush.send(key, s, payload)
            sent = True
        except webpush.Gone:
            db.execute("delete from crew_push_subs where id = ?", (s["id"],))
        except webpush.Failed as exc:
            log.info("crew: a push was not delivered (%s)", exc)
    return sent


def _push_one(key, row: dict) -> bool:
    """Push to every phone this person enabled. True when at least one accepted it."""
    subs = db.query("select id, endpoint, p256dh, auth from crew_push_subs where user_id = ?", (row["user_id"],))
    sent = False
    payload = {"title": "Page", "body": row["message"], "url": "/crew/page/" + row["token"],
               "tag": f"page-{row['page_id']}"}
    for s in subs:
        try:
            webpush.send(key, s, payload)
            sent = True
        except webpush.Gone:
            db.execute("delete from crew_push_subs where id = ?", (s["id"],))
        except webpush.Failed as exc:
            log.info("crew: a push was not delivered (%s)", exc)
    return sent


def tick(now: float | None = None) -> int:
    """Fire every push that is due. Returns how many people were pushed. Blocking: run off the event loop."""
    global pending
    if not pending:
        return 0
    people.ready()
    ts = time.time() if now is None else now
    rows = db.query(
        "select r.page_id, r.user_id, r.token, r.pushes, p.message, p.sent_ts from crew_page_recipients r"
        " join crew_pages p on p.id = r.page_id where r.confirmed_at is null and r.pushes < ?"
        " and p.sent_ts > ?", (len(SCHEDULE_S), ts - SCHEDULE_S[-1] - 60))
    if not rows:
        pending = False
        return 0
    due = [r for r in rows if r["sent_ts"] + SCHEDULE_S[r["pushes"]] <= ts]
    if not due:
        return 0
    key = vapid_key()
    results = list(_pool.map(lambda r: _push_one(key, r), due))
    for r, ok in zip(due, results):
        db.execute("update crew_page_recipients set pushes = pushes + 1, delivered = delivered + ?"
                   " where page_id = ? and user_id = ?", (1 if ok else 0, r["page_id"], r["user_id"]))
    return len(due)


# -- confirming and receipts -------------------------------------------------------------------------------------
def by_token(token) -> dict | None:
    if not isinstance(token, str) or not 20 <= len(token) <= 60:
        return None
    people.ready()
    return db.one("select r.page_id, r.user_id, r.confirmed_at, p.message from crew_page_recipients r"
                  " join crew_pages p on p.id = r.page_id where r.token = ?", (token,))


def confirm(token) -> dict | None:
    row = by_token(token)
    if row is None:
        return None
    if row["confirmed_at"] is None:
        db.execute("update crew_page_recipients set confirmed_at = ? where page_id = ? and user_id = ?"
                   " and confirmed_at is null", (db.now(), row["page_id"], row["user_id"]))
        row["confirmed_at"] = "now"
    return row


def recent(now: float | None = None) -> list[dict]:
    """The last pages with each person's state: confirmed, waiting, or no phone. Tokens are never included."""
    people.ready()
    ts = time.time() if now is None else now
    pages = db.query("select p.id, p.message, p.sent_ts, u.name as sender from crew_pages p"
                     " left join users u on u.id = p.sent_by order by p.id desc limit ?", (RECENT,))
    out = []
    for p in pages:
        rec = db.query("select r.user_id, u.name, r.pushes, r.delivered, r.confirmed_at,"
                       " (select count(*) from crew_push_subs s where s.user_id = r.user_id) as phones"
                       " from crew_page_recipients r join users u on u.id = r.user_id"
                       " where r.page_id = ? order by lower(u.name)", (p["id"],))
        who = []
        for r in rec:
            state = "confirmed" if r["confirmed_at"] else ("no_phone" if not r["phones"] else "waiting")
            who.append({"id": r["user_id"], "name": r["name"], "state": state})
        waiting = sum(1 for w in who if w["state"] == "waiting")
        out.append({"id": p["id"], "message": p["message"], "sender": p["sender"] or "", "sent_ts": p["sent_ts"],
                    "buzzing": waiting > 0 and ts < p["sent_ts"] + SCHEDULE_S[-1] + 1, "people": who})
    return out
