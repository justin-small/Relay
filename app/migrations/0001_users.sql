-- Users, sessions and trusted devices (the first core migration).
-- Usernames are stored lowercased by app/users.py; the unique index makes that the rule, not a habit.
-- `role` is plain text, validated in code, so a roles table can replace the fixed list later.
-- Booleans are 0/1 integers; timestamps are UTC ISO text ("2026-11-12T09:30:00Z"), which sorts correctly.
-- autoincrement: an id is never reused, so a stale reference can never land on a different person.

create table users (
    id                   integer primary key autoincrement,
    username             text        not null,
    name                 text        not null,
    role                 text        not null,
    password_hash        text        not null,
    must_change_password integer     not null default 0 check (must_change_password in (0, 1)),
    disabled             integer     not null default 0 check (disabled in (0, 1)),
    -- A PIN signs in only from a trusted device; five wrong ones in a row clear it (pin_failures counts them).
    pin_hash             text,
    pin_failures         integer     not null default 0,
    last_login           text,
    created_at           text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at           text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
create unique index users_username_key on users (username);

-- A device an admin has marked as trusted: its browser holds a token, only the sha256 is stored here.
create table trusted_devices (
    id         integer primary key autoincrement,
    name       text        not null,
    token_hash text        not null,
    created_by integer     references users (id) on delete set null,
    created_at text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used  text
);
create unique index trusted_devices_token_key on trusted_devices (token_hash);

-- Sign-in sessions: the cookie holds a token, only the sha256 is stored here. A PIN session belongs to the
-- device it came from, so revoking the device ends it.
create table sessions (
    id         integer primary key autoincrement,
    user_id    integer     not null references users (id) on delete cascade,
    token_hash text        not null,
    via        text        not null check (via in ('password', 'pin')),
    device_id  integer     references trusted_devices (id) on delete cascade,
    created_at text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at text        not null
);
create unique index sessions_token_key on sessions (token_hash);
create index sessions_user_idx on sessions (user_id);
create index sessions_device_idx on sessions (device_id);
create index sessions_expires_idx on sessions (expires_at);
