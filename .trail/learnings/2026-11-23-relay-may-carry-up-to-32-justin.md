---
title: "Relay may carry up to 32 sound meters, one per bus lane, so the stress test can load every lane"
author: Justin
date: 2026-11-23
branch: helper-releases-636
cost: { minutes: 15, tokens: 208638 }
id: l-0bc49268
scope: repo
status: active
src: agent
touches:
  - ".github/release-notes.md"
  - ".github/workflows/release.yml"
  - app/main.py
  - app/static/helpers.js
  - app/templates/admin.html
  - docs/helper.md
  - native/relay-helper/Cargo.lock
  - native/relay-helper/Cargo.toml
  - native/relay-helper/packaging/macos/postinstall
  - native/relay-helper/packaging/windows/relay-helper.nsi
---

## Decided
Justin, 2026-11-22: the cap on sound meters rises from 8 to 32 (one per audio bus lane), so tools/stress.py can run Relay with every lane metered. The stress "loaded" state is 6 synthetic NDI cameras (4 at 1080p, 2 at 720p), all 32 lanes patched from their sound, three 8-channel NDI outputs with a full matrix, and 32 meters.

## Tried and ruled out
Keeping the cap at 8 and opening the extra probes straight on the Rust bus from the harness: no product change, but the admin API and status feed would only carry 8, so it would not measure the product as shipped.

## Watch out
tests/test_metering.py asserts the cap by name ("at most eight"); the wording needs updating with the number. The NDI test sources in the NDI module stop at 4 on at once (MAX_ON), so the stress cameras are separate processes instead.

In the code: app/metering.py (MAX_METERS), tests/test_metering.py, tools/stress.py (Load), tools/stress_ndi_sender.py.
