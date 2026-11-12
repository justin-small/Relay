#!/usr/bin/env python3
"""Lockout recovery: make <username> an enabled Owner with a password you choose.

    python tools/reset_owner.py <username>

Creates the user if they do not exist; otherwise re-enables and promotes them. Asks for the new password
twice without showing it (nothing secret is printed, so nothing lands in a terminal log), ends all their
sessions and clears their PIN. In the container:
    docker exec -it live-caption-relay python tools/reset_owner.py <username>
"""
import getpass
import os
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
    try:
        password = getpass.getpass("  New password (8 characters or more): ")
        if getpass.getpass("  Again: ") != password:
            print("  The two passwords do not match. Nothing was changed.", file=sys.stderr)
            return 1
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled. Nothing was changed.", file=sys.stderr)
        return 1
    try:
        existing = users.get_by_username(argv[1])
        if existing is None:
            user = users.create(argv[1], argv[1].strip(), "owner", password, temporary=False, actor=None)
        else:
            # The password first: if it is refused (too short), the account is left exactly as it was.
            users.set_password(existing.id, password, temporary=False, actor=None)  # also ends their sessions
            users.clear_pin(existing.id)
            user = users.update(existing.id, role="owner", disabled=False, actor=None)
    except users.Refused as e:
        print(f"  {e.message}", file=sys.stderr)
        return 1
    except db.Unavailable:
        print("  The user database became unavailable.", file=sys.stderr)
        return 1
    print(f"Owner '{user.username}' is ready. Sign in with the password you just chose.")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv)
    finally:
        db.close()      # before the interpreter tears down, or the pool complains on its way out
    raise SystemExit(code)
