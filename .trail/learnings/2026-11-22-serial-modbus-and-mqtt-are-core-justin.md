---
title: "Serial, Modbus and MQTT are core transports written on netsock, and the Waveshare serial server needs its RS-422 wired straight across to loop back"
author: Justin
date: 2026-11-22
branch: issue-629-core-transports
cost: { minutes: 35, tokens: 621399 }
id: l-ea5ced6f
scope: repo
status: active
src: agent
touches:
  - app/static/connections.js
  - app/templates/admin.html
  - docs/modules.md
  - app/connections.py
  - app/main.py
  - app/migrations/0016_connections.sql
  - app/modbus.py
  - app/mqttclient.py
  - app/netroute.py
  - app/netsock.py
---

## Decided
Justin, 2026-11-22, issue 629 part 1: Relay's own serial link, Modbus client and MQTT client live in core and open every socket through netsock, so a module's traffic stays on the network port the operator chose. Only pyserial is new; Modbus and MQTT are written here. Connections are saved in a table, an MQTT password is sealed, and connections.manage is barred from API tokens.

## Tried and ruled out
pymodbus and paho-mqtt: both open their own sockets, which would bypass the port routing, and they add to the desktop build. Putting exception text into a page: CodeQL flags it, so errors carry a fixed .message.

## Watch out
Waveshare RS232/485/422 to PoE ETH (B), firmware 1.523: the web page needs a login and times out after a reboot. It is a TCP server on port 4196 and defaults to DHCP, so its address can change.
Its three interfaces share one UART. Looping RS-422 back needs TA to RA and TB to RB: crossing them (TB to RA, TA to RB) gave a repeatable garbled echo, and nothing was returned with no jumpers or a DB9 loop that did not make contact.
Not proven: the Furman, real Modbus gear and a show-network MQTT broker.
In the code: app/serialio.py, app/modbus.py, app/mqttclient.py, app/connections.py, tests/test_serialio.py, tests/test_connections.py.
