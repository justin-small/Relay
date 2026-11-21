---
title: Moving Relay from FastAPI to fastrapi is blocked until fastrapi adds the features Relay uses
author: Justin
date: 2026-11-21
branch: fastrapi-618
cost: { minutes: 1, tokens: 1850 }
id: l-d3cc2cf5
scope: repo
status: active
src: agent
touches:
  - app/main.py
  - requirements.txt
---

## Decided
Issue 618 (2026-11-21): Justin confirmed "fastrapi" is the Rust/Axum PyPI package, goal is speed, and if it lacks features, stop and report gaps rather than do a partial move. Spike found fastrapi 0.2.11 cannot host Relay, so no code was changed. Recommended next step: profile Relay's real hot paths instead.

## Tried and ruled out
- Partial move (some routes on fastrapi, rest on FastAPI): needs two servers; modules register on one shared app.
- Benchmark: nothing to compare, Relay cannot run on fastrapi.

## Watch out
fastrapi 0.2.11 lacks TestClient (44 test files use it), mount/StaticFiles, exception_handler, StreamingResponse, FileResponse, Jinja2Templates, raw Response returns, form/multipart, app.state. Re-check its README "Current Limitations" before retrying.
In the code: app/main.py (mounts, handlers, templates), requirements.txt pins fastapi/starlette/uvicorn.
