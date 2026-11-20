-- Teams and positions in another product (#525, part of the shared-concepts epic #521): what app/directory.py last
-- heard from the provider, so a saved ref still shows its name while the provider's module is off. ref is
-- "team:pco:<team id>" or "position:pco:<team id>/<position id>"; parent is a position's team ref. missing_at is when
-- the provider stopped listing it (deleted there), kept so a saved call time, checklist or mic mapping can say it is
-- missing instead of being silently ignored.

create table directory_items (
    ref         text primary key,
    kind        text not null,
    provider    text not null,
    parent      text,
    name        text not null,
    seen_at     text not null,
    missing_at  text
);
create index directory_items_provider on directory_items (provider, kind);
