-- Signage takeovers (#207): something an operator puts over a screen's playlist until it is cleared.
--
-- `kind` is "push" (one image or video from the media library: `media_id`) or "live" (a Relay view: `view`).
-- `mode` is how a push ends: "once" (a video to its end, a picture for `seconds`), "seconds" (for `seconds`) or
-- "cleared" (until an operator clears it). `expires_at` is a UTC time worked out when it was set, empty to last
-- until cleared. One takeover per target; setting another there replaces it. `target_id` is 0 for "all".
-- Alerts do not use this table: they are Relay's own and are read live (takeover.py).

create table signage_takeovers (
    id         integer primary key autoincrement,
    kind       text    not null check (kind in ('push', 'live')),
    target     text    not null check (target in ('screen', 'group', 'all')),
    target_id  integer not null default 0,
    media_id   text    not null default '',
    view       text    not null default '',
    mode       text    not null default 'cleared',
    seconds    integer not null default 0,
    set_at     text    not null,
    expires_at text    not null default '',
    unique (target, target_id)
);
