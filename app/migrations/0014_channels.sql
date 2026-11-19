-- Channels (#542, part of the gear/ports/channels epic #539): one row per channel an operator thinks in, such as "Camera 2"
-- (video), "Vocal 1" (audio) or "Stage" (talk). A ref is "channel:relay:<id>".
--
-- A channel is made of ends: what each module reports (NDI's "BOOTH (Camera 2)", a Videohub input, an ATEM input, a tally
-- camera). One channel can have several ends, so every page shows it under one name. A module reports ends with
-- channels.sync; an operator confirms which ends are one channel (a suggestion is never merged on its own).
--
-- `name` is a Relay-only name an operator typed ("" shows the first reporting end's own name). `merged_into` is set on a
-- channel whose ends were moved onto another: its ref keeps working and follows the link.
-- An end is known by (provider, key): the module id and that module's own id for it. `gear_id` is the gear it is on, when
-- there is one; `number` is its number on the gear (an input number, a console channel). `missing` is 1 when its source no
-- longer reports it: the row and its name stay until an operator forgets it.
-- `channel_apart` holds pairs of channels an operator said are not the same, so they are not suggested again.

create table channels (
    id          integer primary key autoincrement,
    kind        text not null check (kind in ('audio', 'video', 'talk')),
    name        text not null default '',
    note        text not null default '',
    merged_into integer references channels (id) on delete set null,
    created_at  text not null
);
create index channels_kind on channels (kind);

create table channel_ends (
    id          integer primary key autoincrement,
    channel_id  integer not null references channels (id) on delete cascade,
    provider    text not null,
    key         text not null,
    gear_id     integer references gear (id) on delete set null,
    direction   text not null default 'source' check (direction in ('source', 'destination')),
    number      integer,
    gear_name   text not null default '',
    position    integer not null default 0,
    missing     integer not null default 0,
    last_seen   text not null default '',
    created_at  text not null
);
create unique index channel_ends_key on channel_ends (provider, key);
create index channel_ends_channel on channel_ends (channel_id);
create index channel_ends_gear on channel_ends (gear_id);

create table channel_apart (
    a  integer not null references channels (id) on delete cascade,
    b  integer not null references channels (id) on delete cascade,
    primary key (a, b),
    check (a < b)
);
