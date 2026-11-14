-- Signage (#205): the media library, playlists and their items, screens and screen groups.
--
-- Media files themselves are on disk in the state folder (signage/media/<sha256>.<ext>), named by their content,
-- and mirrored into backups/signage-media/. A row here is one upload; two uploads of the same file share one file.
-- An item's `source` names who supplies it ("media" for an upload; other modules add their own) and `ref` is that
-- source's own reference (for "media", the media id). Dates are YYYY-MM-DD in Relay's local time, inclusive.

create table signage_media (
    id          text    primary key,
    name        text    not null,
    kind        text    not null check (kind in ('image', 'video')),
    mime        text    not null,
    ext         text    not null,
    size        integer not null,
    sha256      text    not null,
    width       integer not null default 0,
    height      integer not null default 0,
    duration_s  real    not null default 0,
    has_thumb   integer not null default 0,
    created_at  text    not null
);
create index signage_media_sha on signage_media (sha256);

create table signage_playlists (
    id          integer primary key autoincrement,
    name        text    not null,
    created_at  text    not null,
    updated_at  text    not null
);

create table signage_items (
    id          integer primary key autoincrement,
    playlist_id integer not null references signage_playlists (id) on delete cascade,
    position    integer not null,
    source      text    not null,
    ref         text    not null,
    duration_s  real    not null default 10,
    start_date  text    not null default '',
    end_date    text    not null default '',
    transition  text    not null default 'fade' check (transition in ('cut', 'fade')),
    fit         text    not null default 'fit' check (fit in ('fit', 'fill'))
);
create index signage_items_playlist on signage_items (playlist_id, position);

create table signage_groups (
    id          integer primary key autoincrement,
    name        text    not null,
    playlist_id integer references signage_playlists (id) on delete set null
);

create table signage_screens (
    id          integer primary key autoincrement,
    slug        text    not null unique,
    name        text    not null,
    sound       integer not null default 0,
    playlist_id integer references signage_playlists (id) on delete set null,
    group_id    integer references signage_groups (id) on delete set null,
    created_at  text    not null
);
