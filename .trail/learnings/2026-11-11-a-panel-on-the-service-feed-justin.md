---
title: A panel on the Service feed stays on the Service pane when there is no service; it never falls back to the transcription
author: Justin
date: 2026-11-11
branch: main
cost: { minutes: 1, tokens: 1156 }
id: l-8725d714
scope: repo
status: active
src: agent
touches:
  - firmware/relay-display/main/feed_format.h
---

## Decided
A panel whose feed is Service shows the Service pane even when there is no active service, never the transcription with an error note (Justin asked for this). The Service pane says what is wrong itself ("No service planned today", "Relay does not follow this service", "Planning Center unreachable"), so a Service pick is shown as it is whether or not the panel's menu offers it at that moment.

## Tried and ruled out
Showing a Service pick only while offered and otherwise the transcription with "The service is not shown on this panel": dropped because the operator chose Service and wants it held. Caption translations still fall back to the transcription when not live; only Service is held.

## Watch out
With Planning Center disconnected, Relay answers /stream?stream=service with 404 and the pane shows its connecting text rather than the transcription. That is intended.

In the code: feed_resolve in firmware/relay-display/main/feed_format.h; check_service in main/test_native.cpp; tests/test_service_view.py test_a_service_not_offered_stays_on_the_service (emulator test).
