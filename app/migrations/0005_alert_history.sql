-- Alert history (#217): who sent what, when, to which targets, how long until it was seen, and the reply.
-- One row per alert (written when it ends, so the outcome is known) and per stage message (written when it is
-- shown). `targets` is a JSON list of the panel and screen names as they were then. Rows older than
-- app/alerthistory.py's KEEP_DAYS are deleted by the hourly housekeeping.

create table alert_history (
    id          integer primary key autoincrement,
    at          text    not null,
    kind        text    not null,            -- 'alert' or 'message'
    name        text    not null default '', -- the button's name (empty for a message)
    text        text    not null default '', -- a message's words
    level       text    not null default '',
    sender      text    not null default '',
    targets     text    not null default '[]',
    outcome     text    not null default '', -- seen, missed, cancelled; a message is 'shown'
    seen_on     text    not null default '',
    seen_after_s integer,                    -- seconds from sending to the first tap; null when not seen
    reply       text    not null default ''
);
create index alert_history_at_idx on alert_history (at);
