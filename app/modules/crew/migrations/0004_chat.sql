-- Crew team chat (#230). History lives here, on the host; clients are shown the newest 100 of a channel.
-- A channel is "team", or "role:<crew role, lowercased>" (the free-text role from #227). author is the name at the
-- time of posting, so a message keeps reading the same after a rename. crew_chat_push holds the channels a person
-- turned quiet notifications on for: nothing is on until they choose it.

create table crew_chat_messages (
    id          integer primary key autoincrement,
    channel     text    not null,
    user_id     integer references users (id) on delete set null,
    author      text    not null,
    role        text    not null default '',
    body        text    not null,
    created_ts  real    not null
);
create index crew_chat_channel on crew_chat_messages (channel, id);
create index crew_chat_age on crew_chat_messages (created_ts);

create table crew_chat_push (
    user_id  integer not null references users (id) on delete cascade,
    channel  text    not null,
    primary key (user_id, channel)
);
