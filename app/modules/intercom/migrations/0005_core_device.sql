-- A phone's intercom credential is a part of a core device (#528, app/devices.py): `device_id` points at it and goes
-- with it, so forgetting the phone removes the credential. A credential still signed in becomes a device of its own,
-- owned by its user, which an admin can merge with the same phone's other parts. A revoked one keeps no device.

alter table intercom_devices add column device_id integer references devices (id) on delete cascade;
create index intercom_devices_device on intercom_devices (device_id);

create temp table device_map as
    select id as part_id, (select coalesce(max(id), 0) from devices) + row_number() over (order by id) as device_id
    from intercom_devices where revoked_at is null;
insert into devices (id, user_id, name, platform, user_agent, last_seen, created_at)
    select m.device_id, d.user_id, case when d.name = '' then 'Phone' else d.name end, d.platform, d.user_agent,
           d.last_seen_at, d.issued_at
    from intercom_devices d join device_map m on m.part_id = d.id;
update intercom_devices set device_id = (select device_id from device_map where part_id = intercom_devices.id)
    where revoked_at is null;
drop table device_map;
