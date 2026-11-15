-- Scheduled call-time pages (#229). A rule pages one Planning Center team at a set time, with the admin's own
-- words. kind 'before' is minutes before each service time of today's plan; kind 'fixed' is a weekday and a clock
-- time in Planning Center's time zone. crew_calltime_runs holds one row per rule and occasion (a plan time, or a
-- date), claimed before the page is sent, so a rule fires at most once per occasion even if two ticks overlap.

create table crew_calltimes (
    id          integer primary key autoincrement,
    team        text    not null,
    kind        text    not null check (kind in ('before', 'fixed')),
    minutes     integer,
    weekday     integer,
    clock       text,
    message     text    not null,
    enabled     integer not null default 1,
    created_at  text    not null
);

create table crew_calltime_runs (
    rule_id   integer not null references crew_calltimes (id) on delete cascade,
    occasion  text    not null,
    fired_ts  real    not null,
    page_id   integer references crew_pages (id) on delete set null,
    people    integer not null default 0,
    primary key (rule_id, occasion)
);
