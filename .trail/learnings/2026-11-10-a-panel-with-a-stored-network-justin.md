---
title: "A panel with a stored network must draw its display at boot, because no network event may ever come"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 14, tokens: 39229 }
id: l-5c93a651
scope: repo
status: active
src: agent
---

## Decided
After the radio starts on a stored network, the panel draws its regular display straight away. Before this it only drew on an address or a disconnect, so a panel that never joined (and the emulator, which has no radio) sat on the boot bench picture indefinitely.

## Tried and ruled out
A test that expected the near-black ground at the top-left pixel: the caption view's header bar is a different colour there, so check the bottom-right corner instead.

## Watch out
The emulator suite refuses to run if any file in main/ is newer than the built image. Editing a source file mid-run turned 26 tests into errors that looked like real failures. Rebuild before rerunning.
No ESP-IDF install is needed: run the suite in the espressif/idf container as the README says. The vxp-build volume only holds VXP build output.

In the code: main/provision.cpp (provision_start calls draw_panel after net_radio_start), tests/test_screen.py (_shot sends `bench`; the no-feed test).
