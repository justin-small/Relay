-- Dante routing (#259, #260): receive channels Relay must never change, and saved routing presets.
create table dante_locks (
    device text not null,
    channel integer not null,
    primary key (device, channel)
);

create table dante_presets (
    id integer primary key autoincrement,
    name text not null unique,
    saved_at text not null,
    saved_by text not null default '',
    devices text not null default '[]',   -- JSON: the device names the preset covers, empty for all
    routes text not null default '[]'     -- JSON: [{"device", "channel", "source"}]
);
