-- Ports (#540, part of the gear/ports/channels epic #539): one row per physical connector on a piece of gear. A port is any
-- connector: Ethernet, SFP, SDI, HDMI, XLR, TRS, USB, mains or DC power. A TCP number is never a port here; it stays a
-- field of a host address. A ref is "port:relay:<id>".
--
-- `key` is the gear's own name for the connector, unique on that gear: a switch port's number ("12"), a Blackmagic
-- connector id ("in1"), this computer's network adapter id ("mac:aa:bb:cc:dd:ee:ff"). `gear_name` is what the gear or its
-- module calls it; `name` is a Relay-only name an operator typed ("" for none); `note` is the operator's note.
--
-- The network fields are null when the source does not say: `link_up` null is "unknown", which stays apart from 0 ("no
-- link" or "no signal"). `missing` is 1 when the source no longer reports the port (an adapter unplugged, a Videohub with
-- fewer inputs): the row and its note stay until an operator forgets it.

create table ports (
    id          integer primary key autoincrement,
    gear_id     integer not null references gear (id) on delete cascade,
    key         text not null,
    kind        text not null default '',
    direction   text not null default 'both',
    gear_name   text not null default '',
    name        text not null default '',
    note        text not null default '',
    position    integer not null default 0,
    link_up     integer,
    speed_mbps  real,
    poe_w       real,
    errors      integer,
    missing     integer not null default 0,
    last_seen   text not null default '',
    created_at  text not null
);
create unique index ports_key on ports (gear_id, key);
