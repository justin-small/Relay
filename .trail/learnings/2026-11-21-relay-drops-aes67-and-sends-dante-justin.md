---
title: "Relay drops AES67 and sends Dante audio through Dante Virtual Soundcard; Rust ports go for GIL isolation, not speed"
author: Justin
date: 2026-11-21
branch: rust-bus-wire-601
cost: { minutes: 2, tokens: 29896 }
id: l-280501e4
scope: repo
status: active
src: agent
touches:
  - native/relay-bus/crates/relay-bus/src/config.rs
  - app/modules/dante/inputs.py
  - docs/guide/src/55-dante.md
---

## Decided
Justin, 2026-11-21: Relay no longer sends or receives AES67 itself (issue 603, supersedes 266 and 267). Dante audio in and out is a sound device: the operator installs Dante Virtual Soundcard (or Via, AVIO) and picks it. The dante module (discovery, inventory, routing, signal map) stays. Rust candidates after the audio bus (601) are issues 604 (metering and loudness), 605 (remote intercom mixer), 606 (NDI scopes, profile first) and 607 (shared protocol codecs, low priority). Reason to move code to Rust is isolation from the GIL, not speed: a synthetic profile put metering and mixreport at about 2% each because numpy's FFT is already native.

## Tried and ruled out
rust-anything 2.1.0 (erkamyaman/rust-anything) on the hot code: its analyze marks every module blocked because it blocks a whole directory if any file imports FastAPI, and only each module's routes.py does. record also cannot take numpy array arguments, which Mixer.pull, encode and decode use. Judged a poor fit; extend native/relay-bus by hand instead.

## Watch out
- rust-anything setup edited ~/.claude.json, ~/.codex/config.toml and ~/.cursor/mcp.json (backups are *.rust-anything.bak); setup --remove undoes it.
- Issue 603 is not built yet: the aes67 code is still on main.

In the code: app/modules/aes67/, native/relay-bus/crates/relay-bus/src/config.rs, app/modules/dante/inputs.py, docs/guide/src/55-dante.md.
