-- Connections (#629): the serial, Modbus and MQTT connections an operator has set up, for any module to use.
--
-- `kind` is "serial" (serial over TCP or UDP, or a local port), "modbus" (Modbus TCP or RTU over one of those links) or
-- "mqtt". `settings` is JSON (app/connections.py says what is in it); a secret in it (an MQTT password) is sealed with
-- app/secretbox.py. `traffic` is the network traffic class the connection's sockets go through, "connections" unless the
-- operator picked another on the Network page.

create table connections (
    id          integer primary key autoincrement,
    name        text not null,
    kind        text not null check (kind in ('serial', 'modbus', 'mqtt')),
    traffic     text not null default 'connections',
    settings    text not null,
    created_at  text not null,
    updated_at  text not null
);
create unique index connections_name on connections (lower(name));
