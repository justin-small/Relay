-- Crew resources per position (#233): a file kept on the Relay host, a link, or an embedded page. position is a
-- Planning Center team name (matched ignoring case) or empty for everyone, like a checklist. A file is stored as
-- crew/resources/<stored> where stored is a random name made here, never anything the uploader typed; the title and
-- original name are only shown as text. url is set for a link or an embed, stored and ext for a file.

create table crew_resources (
    id          integer primary key autoincrement,
    position    text    not null default '',
    kind        text    not null check (kind in ('file', 'link', 'embed')),
    title       text    not null,
    url         text,
    stored      text,
    ext         text,
    size        integer,
    created_by  integer references users (id) on delete set null,
    created_ts  real    not null
);
create index crew_resources_position on crew_resources (position);
