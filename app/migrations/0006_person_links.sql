-- Who a Relay user is in another product (#524, part of the shared-concepts epic #521): one row per user and provider
-- ("pco" is Planning Center), kept in core so it works whichever modules are on. external_id is the provider's own id
-- for the person and belongs to one user only. matched_by says how the link was made ("name" for a confident name
-- match, "admin" for one set by hand) and confirmed_by is the admin who accepted or set it (null while nobody has).
-- suggested_name is the spelling the provider uses when it differs from the account's name, waiting for an admin to
-- accept it. person_link_dismissed remembers a spelling or a match an admin refused, so it is not offered again.

create table person_links (
    user_id         integer not null references users (id) on delete cascade,
    provider        text    not null,
    external_id     text    not null,
    name            text    not null default '',
    matched_by      text    not null default 'name',
    confirmed_by    integer references users (id) on delete set null,
    suggested_name  text,
    at              text    not null,
    primary key (user_id, provider),
    unique (provider, external_id)
);

create table person_link_dismissed (
    user_id   integer not null references users (id) on delete cascade,
    provider  text    not null,
    name      text    not null,
    primary key (user_id, provider)
);
