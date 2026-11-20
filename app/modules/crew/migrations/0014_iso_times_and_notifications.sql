-- Crew times are ISO UTC text, as core uses (#529), and a crew "page" is a crew notification, so the word no longer
-- clashes with a board page, a lighting cue page or parent paging.
--
-- Every epoch column becomes `<name>_at text`: arrived_ts -> arrived_at, done_ts -> done_at, fired_ts -> fired_at,
-- added_ts -> added_at, created_ts -> created_at. crew_pages had both sent_ts and created_at, so the duplicate sent_ts
-- is dropped and created_at is the send time. The rows keep every value (to the second). The children are dropped
-- before the table they point at, so no cascade or set-null fires on the way.

create table crew_notifications (
    id          integer primary key autoincrement,
    message     text    not null,
    sent_by     integer references users (id) on delete set null,
    created_at  text    not null
);
insert into crew_notifications (id, message, sent_by, created_at)
    select id, message, sent_by, strftime('%Y-%m-%dT%H:%M:%SZ', sent_ts, 'unixepoch') from crew_pages;

create table crew_notification_recipients (
    notification_id  integer not null references crew_notifications (id) on delete cascade,
    user_id          integer not null references users (id) on delete cascade,
    token            text    not null unique,
    pushes           integer not null default 0,
    delivered        integer not null default 0,
    confirmed_at     text,
    primary key (notification_id, user_id)
);
insert into crew_notification_recipients (notification_id, user_id, token, pushes, delivered, confirmed_at)
    select page_id, user_id, token, pushes, delivered, confirmed_at from crew_page_recipients;

create table crew_calltime_runs_new (
    rule_id          integer not null references crew_calltimes (id) on delete cascade,
    occasion         text    not null,
    fired_at         text    not null,
    notification_id  integer references crew_notifications (id) on delete set null,
    people           integer not null default 0,
    primary key (rule_id, occasion)
);
insert into crew_calltime_runs_new (rule_id, occasion, fired_at, notification_id, people)
    select rule_id, occasion, strftime('%Y-%m-%dT%H:%M:%SZ', fired_ts, 'unixepoch'), page_id, people
    from crew_calltime_runs;

drop table crew_page_recipients;
drop table crew_calltime_runs;
drop table crew_pages;
alter table crew_calltime_runs_new rename to crew_calltime_runs;

create table crew_chat_messages_new (
    id          integer primary key autoincrement,
    channel     text    not null,
    user_id     integer references users (id) on delete set null,
    author      text    not null,
    role        text    not null default '',
    body        text    not null,
    created_at  text    not null
);
insert into crew_chat_messages_new (id, channel, user_id, author, role, body, created_at)
    select id, channel, user_id, author, role, body, strftime('%Y-%m-%dT%H:%M:%SZ', created_ts, 'unixepoch')
    from crew_chat_messages;
drop table crew_chat_messages;
alter table crew_chat_messages_new rename to crew_chat_messages;
create index crew_chat_channel on crew_chat_messages (channel, id);
create index crew_chat_age on crew_chat_messages (created_at);

create table crew_checklist_done_new (
    plan_id  text    not null,
    item_id  integer not null references crew_checklist_items (id) on delete cascade,
    user_id  integer not null references users (id) on delete cascade,
    done_at  text    not null,
    primary key (plan_id, item_id, user_id)
);
insert into crew_checklist_done_new (plan_id, item_id, user_id, done_at)
    select plan_id, item_id, user_id, strftime('%Y-%m-%dT%H:%M:%SZ', done_ts, 'unixepoch') from crew_checklist_done;
drop table crew_checklist_done;
alter table crew_checklist_done_new rename to crew_checklist_done;
create index crew_checklist_done_age on crew_checklist_done (done_at);

create table crew_arrivals_new (
    plan_id     text    not null,
    user_id     integer not null references users (id) on delete cascade,
    arrived_at  text    not null,
    method      text    not null check (method in ('network', 'geofence', 'wifi')),
    primary key (plan_id, user_id)
);
insert into crew_arrivals_new (plan_id, user_id, arrived_at, method)
    select plan_id, user_id, strftime('%Y-%m-%dT%H:%M:%SZ', arrived_ts, 'unixepoch'), method from crew_arrivals;
drop table crew_arrivals;
alter table crew_arrivals_new rename to crew_arrivals;

create table crew_devices_new (
    id        integer primary key,
    user_id   integer not null references users (id) on delete cascade,
    kind      text    not null check (kind in ('mac', 'hostname')),
    value     text    not null,
    label     text    not null default '',
    added_at  text    not null,
    added_by  text    not null default 'self' check (added_by in ('self', 'admin')),
    device_id integer references devices (id) on delete cascade,
    unique (kind, value)
);
insert into crew_devices_new (id, user_id, kind, value, label, added_at, added_by, device_id)
    select id, user_id, kind, value, label, strftime('%Y-%m-%dT%H:%M:%SZ', added_ts, 'unixepoch'), added_by, device_id
    from crew_devices;
drop table crew_devices;
alter table crew_devices_new rename to crew_devices;
create index crew_devices_user on crew_devices (user_id);
create index crew_devices_device on crew_devices (device_id);

create table crew_resources_new (
    id           integer primary key autoincrement,
    position     text    not null default '',
    kind         text    not null check (kind in ('file', 'link', 'embed')),
    title        text    not null,
    url          text,
    stored       text,
    ext          text,
    size         integer,
    created_by   integer references users (id) on delete set null,
    created_at   text    not null,
    position_ref text    not null default ''
);
insert into crew_resources_new (id, position, kind, title, url, stored, ext, size, created_by, created_at, position_ref)
    select id, position, kind, title, url, stored, ext, size, created_by,
           strftime('%Y-%m-%dT%H:%M:%SZ', created_ts, 'unixepoch'), position_ref
    from crew_resources;
drop table crew_resources;
alter table crew_resources_new rename to crew_resources;
create index crew_resources_position on crew_resources (position);
