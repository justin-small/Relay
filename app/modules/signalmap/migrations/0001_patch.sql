-- Signal map (#262): the one patch list the operator pasted, kept as typed so it can be shown and edited again.
create table signalmap_patch (
    id integer primary key check (id = 1),
    saved_at text not null,
    saved_by text not null default '',
    source text not null
);
