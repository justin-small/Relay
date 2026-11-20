-- Devices (#528, part of the shared-concepts epic #521): one row per phone or computer, owned by a user. A ref is
-- "device:relay:<id>". What a device can do is a part, and each part stays in the table of the feature that owns it:
-- `trusted_devices` here (sign-in trust), `intercom_devices` (the intercom credential), `crew_devices` (check-in by MAC
-- address or hostname) and `crew_push_subs` (web push). Each of those has a `device_id` that points here and goes with
-- the device (on delete cascade), so forgetting a phone removes every part, including the parts of a module that is
-- switched off at the time. The module migrations add their `device_id` and attach what they already hold. Sign-in trust
-- is the one exception to the cascade: it outlives the person (a trusted booth iPad keeps working when the admin who
-- trusted it leaves), so it only loses its device (`on delete set null`) and `devices.revoke` deletes it by hand.
--
-- user_agent is what the phone's browser said when its first part was made; a later part from the same user and the
-- same user agent that has no part of its kind yet joins that device (app/devices.py `for_part`), anything else is a
-- device of its own that an admin can merge. last_seen is the latest any part was used.

create table devices (
    id          integer primary key autoincrement,
    user_id     integer not null references users (id) on delete cascade,
    name        text    not null default '',
    platform    text    not null default 'web',
    user_agent  text    not null default '',
    last_seen   text,
    created_at  text    not null
);
create index devices_user on devices (user_id);

alter table trusted_devices add column device_id integer references devices (id) on delete set null;
create index trusted_devices_device on trusted_devices (device_id);

-- A device already trusted becomes a device of its own, owned by the admin who trusted it. One whose admin has since
-- been deleted has no owner and keeps working as it did, listed on the Users page as before. The devices table is new,
-- so a row's number is its place in the order of trusted_devices.id.
insert into devices (id, user_id, name, platform, user_agent, last_seen, created_at)
    select (select count(*) from trusted_devices t2 where t2.created_by is not null and t2.id <= t.id),
           t.created_by, t.name, 'web', '', t.last_used, t.created_at
    from trusted_devices t where t.created_by is not null;
update trusted_devices set device_id =
    (select count(*) from trusted_devices t2 where t2.created_by is not null and t2.id <= trusted_devices.id)
    where created_by is not null;
