-- Routes (#544, part of the gear/ports/channels epic #539): what an operator wants fed to a destination channel.
--
-- A route is one destination channel taking one source channel, with a gain in dB where the gear has one (NULL when it has
-- none). A destination that takes only one source (a video output) has at most one row; one that mixes (an audio output
-- channel) has a row per source. The module that owns the destination applies a route and reports what the gear really
-- does, so this table is what was asked for; the gear is the truth and a difference is shown (app/routing.py).
--
-- A salvo is a saved set of routes taken in one click. `scope` says whose it is ("ndi", "blackmagic:<gear id>", or '' for
-- any). A salvo route with no source says "this destination takes nothing".

create table routes (
    id          integer primary key autoincrement,
    destination integer not null references channels (id) on delete cascade,
    source      integer not null references channels (id) on delete cascade,
    gain_db     real,
    created_at  text not null,
    updated_at  text not null,
    check (destination != source)
);
create unique index routes_pair on routes (destination, source);
create index routes_source on routes (source);

create table salvos (
    id          integer primary key autoincrement,
    name        text not null,
    scope       text not null default '',
    created_at  text not null
);

create table salvo_routes (
    salvo_id    integer not null references salvos (id) on delete cascade,
    destination integer not null references channels (id) on delete cascade,
    source      integer references channels (id) on delete cascade,
    gain_db     real
);
create unique index salvo_routes_pair on salvo_routes (salvo_id, destination, coalesce(source, 0));

-- The audio bus now reports one destination per output channel ("out:<id>:<n>"); an output's old end was its first channel.
update channel_ends set key = key || ':1'
 where provider = 'audiobus' and key like 'out:%' and key not like 'out:%:%';
