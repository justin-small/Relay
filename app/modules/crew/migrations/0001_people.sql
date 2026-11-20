-- Crew roles and Planning Center names (#227): one row per person who has a crew role or a Planning Center link.
-- The role is free text ("Camera 1"). pc_person is the Planning Center person id, stored only once a name matched
-- confidently, so a later match never moves it. suggested_name is the spelling Planning Center uses when it
-- differs from the account's name, waiting for an admin to accept it; dismissed_name remembers a refusal.

create table crew_profiles (
    user_id         integer primary key references users (id) on delete cascade,
    role            text not null default '',
    pc_person       text unique,   -- allow-shared: as it was made; 0010 moved it to core's person_links
    pc_name         text,
    suggested_name  text,
    dismissed_name  text,
    updated_at      text
);
