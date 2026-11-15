-- Crew checklists per position (#232). A template belongs to one Planning Center team (its "position", matched
-- ignoring case) or, with an empty position, to everyone serving. A completion is one person's tick of one item for
-- one plan (the same plan_id the arrival board uses), so each service starts fresh and the old ones stay readable.
-- crew_checklist_leads names the people who may edit the templates of one position without being an operator.

create table crew_checklists (
    id          integer primary key autoincrement,
    title       text    not null,
    position    text    not null default '',
    updated_at  text
);

create table crew_checklist_items (
    id            integer primary key autoincrement,
    checklist_id  integer not null references crew_checklists (id) on delete cascade,
    sort          integer not null,
    text          text    not null
);
create index crew_checklist_items_list on crew_checklist_items (checklist_id, sort);

create table crew_checklist_done (
    plan_id  text    not null,
    item_id  integer not null references crew_checklist_items (id) on delete cascade,
    user_id  integer not null references users (id) on delete cascade,
    done_ts  real    not null,
    primary key (plan_id, item_id, user_id)
);
create index crew_checklist_done_age on crew_checklist_done (done_ts);

create table crew_checklist_leads (
    user_id   integer not null references users (id) on delete cascade,
    position  text    not null,
    primary key (user_id, position)
);
