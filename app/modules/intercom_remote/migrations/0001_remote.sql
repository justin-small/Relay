-- Remote crew (#277). Who may use the intercom from outside the building, and what a remote connection was.

-- An admin marks a user "remote allowed". Everyone else is refused remotely even with a valid account.
create table intercom_remote_users (
    user_id     integer primary key references users (id) on delete cascade,
    allowed_at  text    not null,
    allowed_by  integer references users (id) on delete set null
);

-- A phone's credential made through the install's remote address. A local phone's credential never works remotely.
create table intercom_remote_devices (
    device_id   integer primary key references intercom_devices (id) on delete cascade
);

-- One row per remote connection, so it can be cut without touching a local one.
create table intercom_remote_sessions (
    id          integer primary key autoincrement,
    device_id   integer references intercom_devices (id) on delete set null,
    user_id     integer not null references users (id) on delete cascade,
    started_at  text    not null,
    ended_at    text,
    end_reason  text
);
create index intercom_remote_sessions_user on intercom_remote_sessions (user_id);
