---
title: "A panel never rests on the bench picture; it shows the caption display, and the bench picture is only a console command"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 1, tokens: 64395 }
id: l-19013bc3
scope: repo
status: active
src: agent
---

## Decided
After boot the panel always shows the regular caption display. With no feed chosen or no Relay, its header says so ("No feed", "No network", "Relay not running"), so no separate offline screens were built. The bench picture is kept only as a console command, `bench`, for bench checks and the emulator suite.

## Tried and ruled out
Deleting the bench code entirely: the emulator tests for bars, marker, title and the 180 degree turn read that picture, and the shared session-scoped `provisioned` board no longer lands on it by itself. They now send `bench` and wait for the "test screen shown" log line.

## Watch out
Nothing was compiled or run under the emulator in this session (no ESP-IDF here); only the native checks ran.

In the code: main/provision.cpp (draw_panel, the `feed` and `bench` console commands), main/screen.cpp (Request::bench), tests/test_screen.py (_shot sends `bench`).
