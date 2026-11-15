-- Intercom (#272): the module's own tables. Everything that must survive a restart lives here; presence, talk
-- leases and queues change many times a second and live in memory (#274), never per event in this file.
--
-- No LiveKit room names or track ids are stored as configuration: a room is named from its channel
-- ("ch-<channel id>"), so LiveKit could be replaced without a schema change. Timestamps are UTC ISO text from
-- Python (db.now()), as in core. One organisation per install; every table carries org_id.

create table intercom_orgs (
    id    integer primary key,
    name  text not null
);
insert into intercom_orgs (id, name) values (1, 'Relay');

-- The production label ("Sunday 9:00"). A label only: it never changes who is on which channel.
create table intercom_production (
    org_id       integer primary key default 1 references intercom_orgs (id),
    label        text    not null default '',
    pco_plan_id  text,
    set_by       integer references users (id) on delete set null,
    set_at       text
);

create table intercom_devices (
    id                  integer primary key autoincrement,
    org_id              integer not null default 1 references intercom_orgs (id),
    user_id             integer not null references users (id) on delete cascade,
    name                text    not null default '',
    platform            text    not null default 'web' check (platform in ('web', 'ios', 'android')),
    user_agent          text    not null default '',
    credential_hash     text    not null,
    previous_hash       text,
    previous_valid_until text,
    issued_at           text    not null,
    rotates_at          text,
    last_seen_at        text,
    revoked_at          text
);
create index intercom_devices_user on intercom_devices (user_id);

create table intercom_channels (
    id             integer primary key autoincrement,
    org_id         integer not null default 1 references intercom_orgs (id),
    name           text    not null,
    colour         text    not null default '',
    sort           integer not null default 0,
    mode           text    not null default 'preempt' check (mode in ('preempt', 'party', 'queue')),
    max_talkers    integer not null default 1 check (max_talkers between 1 and 16),
    latch_seconds  integer not null default 0 check (latch_seconds >= 0),
    created_at     text    not null,
    updated_at     text    not null,
    unique (org_id, name)
);

create table intercom_channel_members (
    channel_id  integer not null references intercom_channels (id) on delete cascade,
    user_id     integer not null references users (id) on delete cascade,
    org_id      integer not null default 1 references intercom_orgs (id),
    can_talk    integer not null default 1 check (can_talk in (0, 1)),
    can_listen  integer not null default 1 check (can_listen in (0, 1)),
    priority    integer not null default 5 check (priority between 1 and 9),
    added_by    integer references users (id) on delete set null,
    added_at    text    not null,
    primary key (channel_id, user_id)
);
create index intercom_members_user on intercom_channel_members (user_id);

-- What a user picked on the phone, so the choice follows them to another phone.
create table intercom_user_choices (
    user_id          integer not null references users (id) on delete cascade,
    channel_id       integer not null references intercom_channels (id) on delete cascade,
    org_id           integer not null default 1 references intercom_orgs (id),
    talk_selected    integer not null default 0 check (talk_selected in (0, 1)),
    listen_selected  integer not null default 0 check (listen_selected in (0, 1)),
    volume           integer not null default 100 check (volume between 0 and 100),
    primary key (user_id, channel_id)
);

-- One row per phone connection, for history and the live page after a restart. The live set is in memory.
create table intercom_sessions (
    id          integer primary key autoincrement,
    org_id      integer not null default 1 references intercom_orgs (id),
    device_id   integer references intercom_devices (id) on delete set null,
    user_id     integer not null references users (id) on delete cascade,
    via         text    not null default 'local' check (via in ('local', 'remote')),
    started_at  text    not null,
    ended_at    text,
    end_reason  text
);
create index intercom_sessions_user on intercom_sessions (user_id);

-- The connection and permission log (#276). Audio is never logged. Kept 30 days by default.
create table intercom_events (
    id          integer primary key autoincrement,
    org_id      integer not null default 1 references intercom_orgs (id),
    at          text    not null,
    kind        text    not null,
    user_id     integer references users (id) on delete set null,
    device_id   integer references intercom_devices (id) on delete set null,
    channel_id  integer references intercom_channels (id) on delete set null,
    actor_id    integer references users (id) on delete set null,
    detail      text    not null default '{}'
);
create index intercom_events_at on intercom_events (at);
