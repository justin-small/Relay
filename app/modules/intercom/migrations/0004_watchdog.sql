-- One scratch row the watchdog (#276) rewrites every few seconds to time a database write. Nothing reads it.
create table intercom_watchdog (
    id      integer primary key check (id = 1),
    org_id  integer not null default 1 references intercom_orgs (id),
    at      text    not null
);
