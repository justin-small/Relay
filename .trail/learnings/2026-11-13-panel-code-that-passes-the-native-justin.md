---
title: "Panel code that passes the native checks on a Mac can still fail the firmware build: the ESP compiler errors on snprintf that might truncate"
author: Justin
date: 2026-11-13
branch: status-view-224
cost: { minutes: 1, tokens: 10424 }
id: l-0435615c
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/status_format.h
  - ".github/workflows/firmware.yml"
---

## Decided
- Run the ESP-IDF build (build.sh --qemu in Espressif's container) before pushing panel code, not only the native checks. The native checks compile with the Mac's clang, which does not warn on -Wformat-truncation. The ESP toolchain's gcc does, and the firmware build turns it into an error (-Werror=all).

## Watch out
- Size snprintf buffers for the worst case the compiler can prove, not the case you expect. A 16-byte buffer for "live " plus an elapsed time passed natively and failed the ESP build, because the elapsed text's buffer was itself 16 bytes.

In the code: firmware/relay-display/main/status_format.h (status_stream_text, `when` is 24 bytes), build.sh --qemu, .github/workflows/firmware.yml.
