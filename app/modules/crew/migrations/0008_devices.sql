-- Wi-Fi check-in (#356): a person's own devices, matched against who the UniFi module sees on the network. A device
-- is a MAC address or a hostname; each belongs to one person. The arrival board gains the "wifi" method, which needs
-- the table rebuilt (SQLite cannot change a check constraint).

create table crew_devices (
    id        integer primary key,
    user_id   integer not null references users (id) on delete cascade,
    kind      text    not null check (kind in ('mac', 'hostname')),
    value     text    not null,
    label     text    not null default '',
    added_ts  real    not null,
    added_by  text    not null default 'self' check (added_by in ('self', 'admin')),
    unique (kind, value)
);
create index crew_devices_user on crew_devices (user_id);

create table crew_arrivals_new (
    plan_id     text    not null,
    user_id     integer not null references users (id) on delete cascade,
    arrived_ts  real    not null,
    method      text    not null check (method in ('network', 'geofence', 'wifi')),
    primary key (plan_id, user_id)
);
insert into crew_arrivals_new select plan_id, user_id, arrived_ts, method from crew_arrivals;
drop table crew_arrivals;
alter table crew_arrivals_new rename to crew_arrivals;
