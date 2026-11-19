-- Teams and positions are saved as a core directory ref plus the name they had when saved (#525): team:pco:<id> or
-- position:pco:<team id>/<position id> (app/directory.py). The old typed name stays in the same column as the saved
-- name; the ref is filled in by crew/refs.py when it matches one the directory lists, and a row it cannot match stays
-- a name only and is flagged on the admin page. crew_checklist_leads is keyed by the ref (or the name while there is none).

alter table crew_calltimes  add column team_ref     text not null default '';
alter table crew_checklists add column position_ref text not null default '';
alter table crew_resources  add column position_ref text not null default '';

create table crew_checklist_leads_new (
    user_id      integer not null references users (id) on delete cascade,
    position     text    not null,
    position_ref text    not null default '',
    primary key (user_id, position_ref, position)
);
insert into crew_checklist_leads_new (user_id, position) select user_id, position from crew_checklist_leads;
drop table crew_checklist_leads;
alter table crew_checklist_leads_new rename to crew_checklist_leads;

-- The crew role is its own field, no longer called "role" (that clashes with the account's permission role). An empty
-- one falls back to the person's usual Planning Center position, learned in crew_position_history.
alter table crew_profiles rename column role to crew_role;

create table crew_position_history (
    user_id       integer not null references users (id) on delete cascade,
    position_ref  text    not null,
    name          text    not null,
    times         integer not null default 1,
    last_seen     text    not null,
    primary key (user_id, position_ref)
);
