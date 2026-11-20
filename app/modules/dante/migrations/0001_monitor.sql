-- Dante monitoring (#258): what changed on the network, and the channels the operator starred for live levels.
create table dante_history (
    id integer primary key autoincrement,
    at text not null,
    device text not null,
    kind text not null,
    detail text not null default ''
);
create index dante_history_at on dante_history (at desc);

create table dante_starred (
    device text not null,
    channel text not null,
    primary key (device, channel)
);
