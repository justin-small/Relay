-- Per-user preferences (#173): each person's own choices (layout, default page, language...) kept in the
-- database, so they follow the person to any device they sign in from.
--
-- One row per user, scope and name. `scope` is "core" for core's own preferences, or a module's id for that
-- module's. `value` is JSON text, written and checked by app/prefs.py. Deleting the user deletes their rows.

create table user_prefs (
    user_id    integer not null references users (id) on delete cascade,
    scope      text    not null,
    name       text    not null,
    value      text    not null,
    updated_at text    not null,
    primary key (user_id, scope, name)
) without rowid;
