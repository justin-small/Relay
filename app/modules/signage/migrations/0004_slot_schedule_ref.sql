-- A slot can pick a named schedule (#526) instead of keeping its own days and times: `schedule_ref` is
-- "schedule:relay:<id>", or empty for a slot with its own. The slot's days, times and dates are not used while it
-- names a schedule. The signage time zone is gone too: signage reads the one Relay zone.
alter table signage_slots add column schedule_ref text not null default '';
