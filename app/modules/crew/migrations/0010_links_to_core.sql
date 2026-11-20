-- The Planning Center link moved to core (#524): person_links (app/migrations/0006_person_links.sql) holds it, so it
-- survives Crew being off. Copy what is here across, then rebuild crew_profiles without the four columns.

insert into person_links (user_id, provider, external_id, name, matched_by, suggested_name, at)
    select user_id, 'pco', pc_person, coalesce(pc_name, ''), 'name', suggested_name,
           coalesce(updated_at, strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
    from crew_profiles where pc_person is not null;

insert into person_link_dismissed (user_id, provider, name)
    select user_id, 'pco', dismissed_name from crew_profiles where dismissed_name is not null;

create table crew_profiles_new (
    user_id     integer primary key references users (id) on delete cascade,
    role        text not null default '',
    updated_at  text
);
insert into crew_profiles_new (user_id, role, updated_at) select user_id, role, updated_at from crew_profiles;
drop table crew_profiles;
alter table crew_profiles_new rename to crew_profiles;
