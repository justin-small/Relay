#!/usr/bin/env python3
"""Lockout recovery: make <username> an enabled Owner with a new temporary password.

    python tools/reset_owner.py <username>

Creates the user if they do not exist; otherwise re-enables and promotes them. Prints a random temporary
password once (it must be changed at the next sign-in) and ends all their sessions. In the container:
    docker exec -it live-caption-relay python tools/reset_owner.py <username>
"""
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# `docker exec` does not inherit what the entrypoint exported, so inside the container fall back to the
# socket Postgres listens on there (docker/postgres.sh).
CONTAINER_SOCKET = Path("/tmp/relay-pg")
if not os.environ.get("RELAY_DATABASE_URL") and CONTAINER_SOCKET.is_dir():
    os.environ["RELAY_DATABASE_URL"] = f"postgresql://relay@/relay?host={CONTAINER_SOCKET}"

from app import db, users  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1].startswith("-"):
        print("Usage: python tools/reset_owner.py <username>", file=sys.stderr)
        return 2
    if not db.init():
        why = ("RELAY_DATABASE_URL is not set" if not db.configured()
               else "the database cannot be reached or migrated (see the log above)")
        print(f"  The user database is not available: {why}.", file=sys.stderr)
        return 1
    password = secrets.token_urlsafe(12)
    try:
        existing = users.get_by_username(argv[1])
        if existing is None:
            user = users.create(argv[1], argv[1].strip(), "owner", password, temporary=True, actor=None)
        else:
            user = users.update(existing.id, role="owner", disabled=False, actor=None)
            users.set_password(user.id, password, temporary=True, actor=None)   # also ends their sessions
    except users.Refused as e:
        print(f"  {e.message}", file=sys.stderr)
        return 1
    except db.Unavailable:
        print("  The user database became unavailable.", file=sys.stderr)
        return 1
    print(f"Owner '{user.username}' is ready. Temporary password (shown once, change it at sign-in):")
    print(password)
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv)
    finally:
        db.close()      # before the interpreter tears down, or the pool complains on its way out
    raise SystemExit(code)
