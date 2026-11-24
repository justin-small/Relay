---
title: Rust alerts answer only the alert feed on /stream and cache which screens are in a place
author: Justin
date: 2026-11-23
branch: rust-alerts-panels-660
cost: { minutes: 1, tokens: 1167 }
id: l-a2c4f119
scope: repo
status: active
src: agent
touches:
  - docs/rust-server.md
---

## Decided
The Rust server answers /stream for the alert feed and forwards every other feed to the app, because a /stream route that took all feeds would swallow the caption feeds that the Python hub publishes. Which screens sit in a place is cached and refreshed every 5 seconds, so an alert is decided without waiting on the database. A reader that falls 16 frames behind is dropped and reconnects, as Python did.

## Tried and ruled out
A loopback door for the Python callers, as for the audio bus: Justin chose to port only what issue 660 names, and 661 ports the callers.

## Watch out
Modules that call alerts and panels keep their own Python copy of both, so a Rust alert is invisible to them. The admin page gets no live alert or panel updates from Rust, because they ride the admin status stream.
In the code: native/relay-show, native/relay-server/src/show.rs (stream_or_forward, refresh_places), docs/rust-server.md.
