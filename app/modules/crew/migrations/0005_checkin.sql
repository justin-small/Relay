-- Crew check-in and the arrival board (#231). An arrival belongs to the plan (or, with no plan, the day) it was for:
-- plan_id is the followed plans joined, so when a new plan takes over the old arrivals no longer match and are
-- removed. Only the time and how it was seen are kept: never an address or a coordinate.

create table crew_arrivals (
    plan_id     text    not null,
    user_id     integer not null references users (id) on delete cascade,
    arrived_ts  real    not null,
    method      text    not null check (method in ('network', 'geofence')),
    primary key (plan_id, user_id)
);
