---
title: Panel mDNS discovery passed on a real CrowPanel; a Relay move is faked with a second announcement of the same name
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 5, tokens: 70355 }
id: l-18055242
scope: repo
status: active
src: agent
---

## Decided
Issue #124's mDNS item was checked on a real CrowPanel against a Mac announcing with dns-sd: it finds Relay about 4 s after the panel has an address, keeps the first Relay with a second announced, and follows the same install name to a new address and back within one 30 s refresh. The README checklist was ticked on that basis.

## Tried and ruled out
The user reported discovery "not working on my Mac", but it could not be reproduced: the Mac's announcement was on the LAN and the panel found it on every boot. Likely causes when it does fail: Relay started some way other than start.command (nothing announces), or a Relay address typed on the setup page (discovery is then off).

## Watch out
The macOS ComputerName has a curly apostrophe (Justin’s), which relay_name_clean folds to "?", so the saved name reads "Justin?s". Discovery still matches, because the folding is the same each time.

In the code:
- Check the announcement: `dns-sd -B _relay-captions._tcp local`, and `pgrep -fl "dns-sd -R"`.
- Fake a second Relay: `dns-sd -P "Other Relay" _relay-captions._tcp local 80 otherrelay.local 192.168.1.250 v=dev name=Other path=/`.
- Fake a move: pkill the `dns-sd -R` announcer, then `dns-sd -P` the same instance name and TXT name at another IP. Restore it with start.command's `dns-sd -R` line.
- Serial log: /dev/cu.wchusbserial10 at 115200 with pyserial. Opening the port with DTR/RTS low, then pulsing RTS high, resets the board.
