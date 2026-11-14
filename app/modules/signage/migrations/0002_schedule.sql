-- Signage schedules (#206): time slots that pick a playlist for a screen, a group or every screen, and playlists an
-- operator has put on by hand.
--
-- A slot is weekly (`days` is a comma list, Sunday = 0) or a one-off date range (`start_date`..`end_date`, inclusive,
-- optionally limited to `days`). `start_time`/`end_time` are wall-clock HH:MM in the signage time zone, both empty for
-- all day; an end that is not after the start runs into the next day. `target_id` is 0 for "all".
-- An override's `expires_at` is a UTC time worked out when it was set (the next scheduled change), empty to last until
-- cleared.

create table signage_slots (
    id          integer primary key autoincrement,
    target      text    not null check (target in ('screen', 'group', 'all')),
    target_id   integer not null default 0,
    playlist_id integer not null references signage_playlists (id) on delete cascade,
    days        text    not null default '',
    start_time  text    not null default '',
    end_time    text    not null default '',
    start_date  text    not null default '',
    end_date    text    not null default ''
);
create index signage_slots_target on signage_slots (target, target_id);

create table signage_overrides (
    target      text    not null check (target in ('screen', 'group', 'all')),
    target_id   integer not null default 0,
    playlist_id integer not null references signage_playlists (id) on delete cascade,
    set_at      text    not null,
    expires_at  text    not null default '',
    primary key (target, target_id)
);
