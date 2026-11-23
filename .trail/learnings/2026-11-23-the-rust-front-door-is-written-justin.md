---
title: "The Rust front door is written directly on hyper, because axum-strangler cannot proxy the HTTPS panel port"
author: Justin
date: 2026-11-23
branch: spike-axum-front-door-651
id: l-c5538fe2
scope: repo
status: active
src: agent
touches:
  - tools/stress.py
  - tools/stress_ndi_sender.py
---

## Decided
Issue 651 spike: the front door in native/relay-front is written directly on hyper 1 (hyper-util client and server, tokio-rustls). It forwards HTTP, HTTPS and websocket upgrades to the unchanged FastAPI, and adds X-Forwarded-For and X-Forwarded-Proto, which Relay already trusts from a loopback peer.

## Tried and ruled out
axum-strangler 0.4.0 (Justin's first pick). It cannot trust the panel port's self-signed certificate ("certificate was not trusted"), it unwrap()s upstream connection errors so a down Relay panics a worker instead of returning 502, and it is unreleased since Dec 2022 on axum 0.6 / hyper 0.14.

## Watch out
- Relay's first-run setup is refused unless X-Forwarded-For is loopback, so a front door must forward the real peer address.
- tools/stress.py left a Relay running on 18080 after a run (macOS re-exec), so later runs hit a stale server; it now starts the server in its own process group.
- tools/stress_ndi_sender.py called Sender.send with a dict; the Rust API takes (bytes, audio), so --loaded never worked.
- Cargo here is replaced by an unreachable rsproxy.cn mirror in ~/.cargo/config.toml, which is read from any cwd under the home folder. Build from /private/tmp with --manifest-path and a fresh CARGO_HOME.

In the code: native/relay-front/src/main.rs, tools/stress.py (RELAY_STRESS_VIEWER, RELAY_STRESS_ADMIN), tools/front_check.py, app/main.py _client_ip and _setup_local.
