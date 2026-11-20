-- Cables (#541, part of the gear/ports/channels epic #539): one row per cable between two core ports (app/ports.py). A
-- cable joins any two ports on any gear: an ATEM's SDI output to a Videohub input, an ATEM's Ethernet to a switch port.
-- A ref is "cable:relay:<id>".
--
-- `by` says who said so: "operator" for a cable a person recorded (they can remove it), or a module id ("unifi") for a
-- cable that module reports (an operator can add a note to it but not remove it). `missing` is 1 when its source no longer
-- reports it; the row and its note stay until the source reports it again. End `a` is the one entered first (an output, say);
-- a pair is one cable whichever way round it was entered. Deleting a port removes its cables.

create table cables (
    id          integer primary key autoincrement,
    port_a      integer not null references ports (id) on delete cascade,
    port_b      integer not null references ports (id) on delete cascade,
    by          text not null default 'operator',
    note        text not null default '',
    missing     integer not null default 0,
    created_at  text not null,
    check (port_a != port_b)
);
create unique index cables_pair on cables (min(port_a, port_b), max(port_a, port_b));
create index cables_b on cables (port_b);
