-- A person's check-in device (a MAC address or hostname) and their web push subscription are parts of a core device
-- (#528, app/devices.py): `device_id` points at it and goes with it, so forgetting the phone removes both. Each row
-- becomes a device of its own, owned by its person, which an admin can merge with the same phone's other parts.

alter table crew_devices add column device_id integer references devices (id) on delete cascade;
create index crew_devices_device on crew_devices (device_id);
alter table crew_push_subs add column device_id integer references devices (id) on delete cascade;
create index crew_push_subs_device on crew_push_subs (device_id);

create temp table device_map as
    select id as part_id, (select coalesce(max(id), 0) from devices) + row_number() over (order by id) as device_id
    from crew_devices;
insert into devices (id, user_id, name, platform, user_agent, last_seen, created_at)
    select m.device_id, d.user_id, case when d.label = '' then d.value else d.label end, 'web', '', null,
           strftime('%Y-%m-%dT%H:%M:%SZ', d.added_ts, 'unixepoch')
    from crew_devices d join device_map m on m.part_id = d.id;
update crew_devices set device_id = (select device_id from device_map where part_id = crew_devices.id);
drop table device_map;

create temp table device_map as
    select id as part_id, (select coalesce(max(id), 0) from devices) + row_number() over (order by id) as device_id
    from crew_push_subs;
insert into devices (id, user_id, name, platform, user_agent, last_seen, created_at)
    select m.device_id, s.user_id, 'Browser alerts', 'web', '', null, s.created_at
    from crew_push_subs s join device_map m on m.part_id = s.id;
update crew_push_subs set device_id = (select device_id from device_map where part_id = crew_push_subs.id);
drop table device_map;
