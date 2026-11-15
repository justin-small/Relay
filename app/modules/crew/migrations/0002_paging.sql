-- Crew pages (#228): pages that buzz until read.
--
-- crew_vapid holds the one key pair that signs pushes (made on first use, never leaves this file). Phones
-- subscribe with its public half. crew_push_subs is one row per phone browser a person enabled alerts on.
-- A page has one row per person (crew_page_recipients). `token` is that person's own confirm link
-- (/crew/page/<token>): it can only confirm that one page, and is never sent to the admin page.
-- sent_ts is epoch seconds, because the re-fire schedule is arithmetic on it.

create table crew_vapid (
    id           integer primary key check (id = 1),
    private_pem  text not null,
    public_key   text not null
);

create table crew_push_subs (
    id          integer primary key autoincrement,
    user_id     integer not null references users (id) on delete cascade,
    endpoint    text    not null unique,
    p256dh      text    not null,
    auth        text    not null,
    created_at  text    not null
);
create index crew_push_subs_user on crew_push_subs (user_id);

create table crew_pages (
    id          integer primary key autoincrement,
    message     text    not null,
    sent_by     integer references users (id) on delete set null,
    sent_ts     real    not null,
    created_at  text    not null
);

create table crew_page_recipients (
    page_id       integer not null references crew_pages (id) on delete cascade,
    user_id       integer not null references users (id) on delete cascade,
    token         text    not null unique,
    pushes        integer not null default 0,
    delivered     integer not null default 0,
    confirmed_at  text,
    primary key (page_id, user_id)
);
