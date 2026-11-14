-- The audit log (#172): who did what and when. Sign-ins and failed sign-ins, user and role changes, and every
-- change made through the admin page or API, each with the person (or API token) that made it.
--
-- An entry outlives the account it names, so the actor is a copy of their name and username at the time, and
-- actor_id has no foreign key. actor_id null is the system itself (first-run setup, a module's own work) or
-- nobody signed in (a failed sign-in). `detail` is a small JSON object and never holds a secret. Entries
-- older than app/audit.py's KEEP_DAYS are deleted by the hourly housekeeping.

create table audit_log (
    id             integer primary key autoincrement,
    at             text    not null,
    actor_id       integer,
    actor_name     text    not null default '',
    actor_username text    not null default '',
    token_name     text    not null default '',
    action         text    not null,
    summary        text    not null,
    target         text    not null default '',
    detail         text    not null default '',
    ip             text    not null default ''
);
create index audit_log_at_idx on audit_log (at);
create index audit_log_actor_idx on audit_log (actor_id, id);
create index audit_log_action_idx on audit_log (action, id);
