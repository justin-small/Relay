-- Gear and racks (#547, part of the gear/ports/channels epic #539): one row per piece of equipment, with one name, one
-- rack and one place everywhere. `gear` is a switch, an ATEM, a Dante stage box, this computer; the network map and
-- Blackmagic attach to it instead of keeping names and lists of their own. A ref is "gear:relay:<id>" and
-- "rack:relay:<id>".
--
-- `gear_keys` holds the ids the older features knew a box by ("mac:<mac>", "ip:<address>", "host:<name>",
-- "blackmagic:<id>", "dante:<name>", "relay:this"), so two keys for the same box (the same MAC) are one record, and a
-- name or note survives an IP change. A box that has a MAC is known by it alone: its "ip:" and "host:" keys go.
--
-- `named` is 1 once an operator typed the name: until then the name follows what the source reports. `position` orders
-- gear inside a rack, top to bottom. `drawing` is the picture a module draws it with (opaque to core).
-- A rack is inside a place (Booth > Rack A); gear sits in a rack, or straight in a place. A rack's `position` is its
-- order on the Network page.

create table racks (
    id          integer primary key autoincrement,
    place_id    integer not null references places (id) on delete cascade,
    name        text not null,
    position    integer not null default 0,
    created_at  text not null
);
create unique index racks_name on racks (place_id, name collate nocase);

create table gear (
    id          integer primary key autoincrement,
    name        text not null,
    named       integer not null default 0,
    kind        text not null default '',
    model       text not null default '',
    drawing     text not null default '',
    notes       text not null default '',
    place_id    integer references places (id) on delete set null,
    rack_id     integer references racks (id) on delete set null,
    position    integer not null default 0,
    last_seen   text not null default '',
    created_at  text not null
);
create index gear_place on gear (place_id);
create index gear_rack on gear (rack_id);

create table gear_keys (
    key      text primary key,
    gear_id  integer not null references gear (id) on delete cascade
);
create index gear_keys_gear on gear_keys (gear_id);
