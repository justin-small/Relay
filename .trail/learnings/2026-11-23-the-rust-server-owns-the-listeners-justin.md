---
title: "The Rust server owns the listeners and the Python app sits behind it on loopback, trading ports and module listeners through one token-guarded call"
author: Justin
date: 2026-11-23
branch: rust-listeners-653
cost: { minutes: 25, tokens: 381863 }
id: l-c2801c72
scope: repo
status: active
src: agent
touches:
  - native/relay-server/Cargo.toml
  - native/.cargo/config.toml
  - native/relay-server/src/netinfo.rs
  - native/relay-server/src/netroute.rs
  - native/relay-server/src/listen.rs
  - native/relay-server/src/tls.rs
  - native/relay-server/src/gate.rs
  - native/relay-server/src/forward.rs
  - native/relay-server/src/front.rs
  - native/relay-server/src/serve.rs
---

## Decided
Justin, 2026-11-23, issue 653: one commit, one PR, a Rust test plus a Python check that starts the real programs. The Rust server (native/relay-server) opens the viewer, panel and module ports, mints the panel certificate, announces over mDNS and applies the port rules; the Python app runs behind it with `python run.py --upstream-port N` and the same `RELAY_FRONT_TOKEN`. The app tells the server its module listeners and reserved prefixes (GET /api/internal/front), the server tells the app what it really bound and the certificate (POST). The server sends the port the client reached as X-Relay-Port and the app puts it back in the request scope, so every existing "which socket" check (serving.py, the gate, intercom, parent paging) works unchanged. The server polls config.json and the interfaces every 5 s to move a routed listener, renew the certificate and re-announce.

## Tried and ruled out
axum-server for TLS: it serves one bound socket, but listeners must move and the certificate must swap, so a plain hyper accept loop with a rustls cert resolver is simpler. Letting the app keep its own gate off and trusting only Rust: the app's gate stays on too, once it knows the real ports. Porting the outbound netsock now: no Rust module connects out yet, so it waits for #661.

## Watch out
The gate reads the percent-decoded path (the app decodes before routing); axum matches the raw path, so an encoded prefix never reaches a Rust handler. The server must overwrite X-Forwarded-For, X-Forwarded-Proto and X-Relay-Port and drop X-Relay-Front from clients, or any client could claim to be the front door. The cargo mirror in ~/.cargo/config.toml (rsproxy.cn) does not resolve here, so builds need a temporary native/.cargo/config.toml that is never committed, and run_all.py must be run from native/ for maturin to see it.
In the code: native/relay-server/src/{listen,serve,gate,forward,front,tls,netroute,netinfo,announce,app}.rs, app/front.py, run.py, tests/test_rust_listeners.py, native/relay-server/tests/listeners.rs.
