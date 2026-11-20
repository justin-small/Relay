---
title: "Several ATEMs each publish their own tally source with a first camera number, and discovery follows a switcher by its mDNS unique id"
author: Justin
date: 2026-11-16
branch: atem-discovery-multi
cost: { minutes: 12, tokens: 239356 }
id: l-614417ac
scope: repo
status: active
src: agent
touches:
  - app/modules/atem/service.py
  - app/modules/atem/discovery.py
  - app/modules/atem/routes.py
  - app/modules/atem/templates/atem.html
  - app/modules/atem/static/atem.js
---

## Decided
Issue 386, 2026-11-16, Justin's choices: tally from several ATEMs stays per switcher and is opt-in. Each switcher that drives tally publishes as its own core tally source (`atem_<id>`) and has a "first camera number" (an offset), so camera 1 on the overflow switcher can be tally 101. Core still merges sources by number, so two switchers on the same range light the same screens; the page warns when ranges overlap. New switchers get the next free hundred.
The Blackmagic scanner lives inside the ATEM module for now. It lists every Blackmagic product (HyperDeck, Videohub, cameras...) but only ATEMs can be added; HyperDeck and Videohub control come as their own modules later (Justin wants both).
Extras Justin picked: per-switcher on/off, re-find by id after a DHCP move, an opt-in subnet probe, and model/protocol/firmware on each card.

## Tried and ruled out
Re-finding by MAC: the standard library has no portable ARP read, and mDNS does not carry the MAC. The TXT "unique id" (or the instance name when there is none) is used instead.
Prefixing camera names with the switcher name for tally: names are capped at 24 characters, so they would be cut off.

## Watch out
Not checked on real hardware: the mDNS service types and TXT keys. `_blackmagic._tcp`, `_hyperdeck_ctrl._tcp` and `_videohub._tcp` are asked for, plus any type in the service-type list with "blackmagic"/"bmd"/... in its name.
The subnet probe sends the ATEM hello to every address in the /24; each switcher that answers holds a session slot for a few seconds, so it runs only when the operator ticks it.
The "old firmware" warning is protocol version below 2.29 (8.0.1), the one version check the module already had.

In the code: app/modules/atem/service.py (clean_settings migrates {host, port} to devices[0] id "main"; Service._refind_once), app/modules/atem/discovery.py, routes /api/admin/atem/devices, /devices/{id}, /scan; control routes take "device" and default to the first switcher.
