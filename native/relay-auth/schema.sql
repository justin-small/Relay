-- The auth tables as they stand after app/migrations 0001 to 0004, 0009 and 0010 (#655). A new database gets them in one
-- go; the old migrations are not replayed. An existing database is never changed by this file: relay-auth reads
-- schema_migrations and only opens a file that already has every version in AUTH_VERSIONS. The tables are identical to
-- what Python's migrations build, so either server can open the file.

create table users (
    id                   integer primary key autoincrement,
    username             text        not null,
    name                 text        not null,
    role                 text        not null,
    password_hash        text        not null,
    must_change_password integer     not null default 0 check (must_change_password in (0, 1)),
    disabled             integer     not null default 0 check (disabled in (0, 1)),
    pin_hash             text,
    pin_failures         integer     not null default 0,
    last_login           text,
    created_at           text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at           text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
create unique index users_username_key on users (username);

create table devices (
    id          integer primary key autoincrement,
    user_id     integer not null references users (id) on delete cascade,
    name        text    not null default '',
    platform    text    not null default 'web',
    user_agent  text    not null default '',
    last_seen   text,
    created_at  text    not null
);
create index devices_user on devices (user_id);

create table trusted_devices (
    id         integer primary key autoincrement,
    name       text        not null,
    token_hash text        not null,
    created_by integer     references users (id) on delete set null,
    created_at text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_used  text,
    device_id  integer     references devices (id) on delete set null
);
create unique index trusted_devices_token_key on trusted_devices (token_hash);
create index trusted_devices_device on trusted_devices (device_id);

create table sessions (
    id         integer primary key autoincrement,
    user_id    integer     not null references users (id) on delete cascade,
    token_hash text        not null,
    via        text        not null check (via in ('password', 'pin')),
    device_id  integer     references trusted_devices (id) on delete cascade,
    created_at text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at text        not null,
    user_agent text        not null default ''
);
create unique index sessions_token_key on sessions (token_hash);
create index sessions_user_idx on sessions (user_id);
create index sessions_device_idx on sessions (device_id);
create index sessions_expires_idx on sessions (expires_at);

create table api_tokens (
    id         integer primary key autoincrement,
    user_id    integer     not null references users (id) on delete cascade,
    name       text        not null,
    token_hash text        not null,
    hint       text        not null,
    created_at text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at text,
    last_used  text
);
create unique index api_tokens_token_key on api_tokens (token_hash);
create index api_tokens_user_idx on api_tokens (user_id);

create table audit_log (
    id             integer primary key autoincrement,
    at             text    not null,
    actor_id       integer,
    actor_name     text    not null default '',
    actor_username text    not null default '',
    token_name     text    not null default '',
    action         text    not null,
    summary        text    not null,
    target         text    not null default '',
    detail         text    not null default '',
    ip             text    not null default ''
);
create index audit_log_at_idx on audit_log (at);
create index audit_log_actor_idx on audit_log (actor_id, id);
create index audit_log_action_idx on audit_log (action, id);

create table user_prefs (
    user_id    integer not null references users (id) on delete cascade,
    scope      text    not null,
    name       text    not null,
    value      text    not null,
    updated_at text    not null,
    primary key (user_id, scope, name)
) without rowid;
