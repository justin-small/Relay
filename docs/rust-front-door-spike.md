# Spike: a Rust front door in front of the unchanged FastAPI (#651, part of #650)

**Verdict: go.** A hyper front door forwards everything, streams and websockets included, for about 4 to 9% of
requests per second at low concurrency, with no change to Relay. Write it directly on hyper, not on `axum-strangler`.

## What was built

`native/relay-front`: a small program on hyper 1 (hyper-util client and server, tokio-rustls). It listens on a plain HTTP
port and an HTTPS port (a throwaway self-signed certificate) and forwards every request to Relay's viewer port and panel
port. It adds `X-Forwarded-For` and `X-Forwarded-Proto`, which Relay already trusts from a loopback peer (`_client_ip`,
`_is_https` in `app/main.py`); without the forwarded address Relay refuses first-run setup. Response bodies are handed on
frame by frame, never collected. A websocket upgrade becomes a byte pipe once both sides have switched protocols. An
unreachable Relay is a 502.

```bash
cargo build --release --manifest-path native/relay-front/Cargo.toml
native/relay-front/target/release/relay-front --listen 127.0.0.1:19080 --upstream http://127.0.0.1:18080 \
    --tls-listen 127.0.0.1:19443 --tls-upstream https://127.0.0.1:18443
RELAY_STRESS_VIEWER=http://127.0.0.1:19080 RELAY_STRESS_ADMIN=https://127.0.0.1:19443 .venv/bin/python tools/stress.py http
.venv/bin/python tools/front_check.py      # streams, websocket, 502
```

`tools/stress.py` now takes `RELAY_STRESS_VIEWER` and `RELAY_STRESS_ADMIN` to aim its load at the front door while it
still starts and samples Relay itself.

## Numbers

One Mac, load generator on the same machine, one run each of 15 seconds a step, so differences of a few percent are
noise. Direct = straight to Relay; front = through the front door.

| Scenario | Direct | Front |
|---|---|---|
| `http` 10 workers | 970 req/s, canary p99 11.7 ms | 930 req/s (-4%), 14.1 ms |
| `http` 50 workers | 723 req/s, 24.0 ms | 707 req/s (-2%), 23.3 ms |
| `http` 200 workers | 430 req/s, 165 ms | 533 req/s, 46.5 ms |
| `http --loaded` 10 workers | 272 req/s, 34.9 ms | 258 req/s (-5%), 35.6 ms |
| `http --loaded` 50 workers | 256 req/s, 84.5 ms | 232 req/s (-9%), 139 ms |
| `sse` 500 viewers | 3.67 events/viewer/s, 0 dropped | 3.44, 0 dropped |
| `sse --loaded` 200 viewers | 3.75, 0 dropped | 4.46, 0 dropped |
| `alerts` 100 listeners, 20/s | delivery p50 8.2 ms, p99 15.8, none lost | p50 8.1 ms, p99 15.5, none lost |

`--loaded` is 6 NDI cameras, a full 32-lane bus and 32 meters. The 200-worker rows favour the front door because it
queues connections in front of a saturated Relay; read them as noise, not a gain. The loaded 50-worker row is the
worst case seen (-9% and a higher canary p99, which also jumps between identical runs).

The front door itself: 50 MB resident at its peak (500 held-open viewers), 3% CPU on average. Relay's memory over the
runs was the same with and without it. Each held viewer costs one extra connection to Relay (open files rose 80 to 580
for 500 viewers).

## Streaming and websockets (`tools/front_check.py`, 7 of 7)

- Server-sent events keep the sender's rhythm: events sent every 200 ms arrive 201 to 202 ms apart, and the first
  arrives at once. Nothing is buffered. `/stream`, the status stream and the alerts pages also ran through the front
  door in the `sse` and `alerts` scenarios above with no drops and no lost messages.
- A client that leaves ends the upstream's stream, so nothing is held for a gone viewer.
- Websocket echo works through the front door: 0.84 ms median round trip, and a 1 MB message arrives intact.
  Relay's own `/ws/helper` was not driven (it needs a paired helper); the proxying is generic, so it has no special case.
- A down Relay is a 502 and the front door keeps serving.

## axum-strangler or hyper

Hyper. `axum-strangler` 0.4.0:

- cannot trust a self-signed upstream certificate, so it cannot forward to the panel port ("certificate was not trusted");
- `unwrap()`s a failed upstream connection, so a Relay that is down panics a worker instead of answering 502;
- was last released in December 2022 and pins axum 0.6 and hyper 0.14, while the epic targets axum on hyper 1.

Its plain HTTP path measured the same as the hyper one on the viewer port (999 vs 1001 req/s at 10 workers), so the
decision is about correctness and age, not speed. The hyper version is about 150 lines.

## What the real server must decide (#652, #653)

- The front door should reach Relay over plain HTTP on loopback. The spike skipped certificate checks on the HTTPS
  upstream only because Relay's panel port is TLS-only today.
- `X-Forwarded-For` is only trustworthy because Relay accepts it from loopback peers: keep that rule, and have the front
  door overwrite, never append to, a client-supplied header.
- A hop adds a connection per held stream; at 500 viewers that is cheap, but the port rules in #653 should count them.

## Found on the way

`tools/stress.py` left Relay running after a run (its child outlived the launcher), so a later run measured a stale
server; it now stops the whole process group. `tools/stress_ndi_sender.py` passed a dict to the Rust `Sender.send`
(which takes bytes and audio) and crashed on its first frame, so `--loaded` never worked from a clean checkout.
