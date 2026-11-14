-- Personal API tokens (#174). A script sends one as `Authorization: Bearer relay_…`; it acts as its user, minus
-- what app/users.py's TOKEN_BARRED keeps from every token. Only the sha256 is stored: the secret is shown once.
-- `hint` is the first characters after the prefix, so a person can tell which token a script holds.
-- expires_at null means it never expires. Deleting the user deletes their tokens.

create table api_tokens (
    id         integer primary key autoincrement,
    user_id    integer     not null references users (id) on delete cascade,
    name       text        not null,
    token_hash text        not null,
    hint       text        not null,
    created_at text        not null default (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    expires_at text,
    last_used  text
);
create unique index api_tokens_token_key on api_tokens (token_hash);
create index api_tokens_user_idx on api_tokens (user_id);
