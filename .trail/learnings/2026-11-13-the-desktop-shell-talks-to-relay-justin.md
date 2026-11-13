---
title: "The desktop shell talks to Relay over stdin and stdout, and holds stdin itself so waiting on the process does not stop it"
author: Justin
date: 2026-11-13
branch: main
cost: { minutes: 1, tokens: 1249 }
id: l-d9d00fa4
scope: repo
status: active
src: agent
touches:
  - run.py
---

## Decided
The Tauri shell starts Relay with RELAY_SIDECAR=1. Relay prints `RELAY-EVENT {json}` lines (ready, network, fingerprint); the shell sends `quit` on stdin, and stdin closing also stops Relay, so a crashed shell never leaves a backend behind. The app window reaches the HTTPS panel through a loopback proxy that pins the certificate fingerprint the backend reports, because the system webviews cannot trust one self-signed cert.

## Tried and ruled out
Leaving stdin inside the child handle: tokio's `Child::wait` closes the child's stdin first, which Relay reads as "shell gone", so it exits gracefully right after starting and is restarted in a loop.

## Watch out
The shell must hold the stdin handle separately for the whole run.

In the code: app/sidecar.py, run.py, desktop/src-tauri/src/backend.rs (stdin taken at spawn), desktop/src-tauri/src/proxy.rs.
