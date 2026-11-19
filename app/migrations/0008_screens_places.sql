-- Screens and places (#527, part of the shared-concepts epic #521): one row per physical display, with one name and
-- one place everywhere. `screens` is a browser (a TV, a tablet) or a hardware panel; boards, signage and alerts
-- attach to it instead of keeping their own ids. A ref is "screen:relay:<id>" and "place:relay:<id>".
--
-- `screen_keys` holds the ids the older features knew a display by ("board:<random id>" for a browser showing a
-- board, "signage:<slug>", "panel:<panel id>", "alert:<lower-cased name>"), so an old address resolves to its screen.
-- A screen shows one board or one signage playlist: `normal_*` all the time, or, while the named schedule in
-- `during_schedule` is open, `during_*`. kind is "board" (ref is the board id), "signage" (ref is the playlist id) or
-- empty (shows whatever it was opened on). `last_board` is the board it last showed, which it falls back to while
-- Signage is off. `hold_*` is what an operator put on it by hand from the Screens page, which beats the schedule until
-- it is cleared.

create table places (
    id          integer primary key autoincrement,
    name        text not null,
    created_at  text not null
);
create unique index places_name on places (name collate nocase);

create table screens (
    id               integer primary key autoincrement,
    name             text not null,
    place_id         integer references places (id) on delete set null,
    kind             text not null default 'browser' check (kind in ('browser', 'panel')),
    address          text not null default '',
    last_seen        text not null default '',
    created_at       text not null,
    normal_kind      text not null default '' check (normal_kind in ('', 'board', 'signage')),
    normal_ref       text not null default '',
    during_schedule  text not null default '',
    during_kind      text not null default '' check (during_kind in ('', 'board', 'signage')),
    during_ref       text not null default '',
    last_board       text not null default '',
    hold_kind        text not null default '' check (hold_kind in ('', 'board', 'signage')),
    hold_ref         text not null default ''
);
create unique index screens_name on screens (name collate nocase);
create index screens_place on screens (place_id);

create table screen_keys (
    key        text primary key,
    screen_id  integer not null references screens (id) on delete cascade
);
create index screen_keys_screen on screen_keys (screen_id);
