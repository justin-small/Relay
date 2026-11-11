---
title: Python 3.15 image runs on Debian trixie and installs uvicorn without the standard extra
author: Justin
date: 2026-11-11
branch: dependabot/docker/docker/python-3.15-slim-bookworm
cost: { minutes: 6, tokens: 41186 }
id: l-37b7783a
scope: repo
status: active
src: agent
touches:
  - docker/Dockerfile
  - tests/smoke_test.py
  - app/hub.py
  - requirements.txt
  - ".github/workflows/release.yml"
---

## Decided
Moved the Docker image to Python 3.15 (dependabot #61). Because pip only installs prebuilt packages (--only-binary=:all:), every dependency needs a ready-made cp315 build for Linux:
- The base image is python:3.15-slim-trixie, not bookworm. orjson only ships cp315 builds tagged manylinux_2_39 (glibc 2.39). Bookworm has glibc 2.36, trixie has 2.41.
- uvicorn is listed without [standard], plus uvloop and httptools pinned on their own lines. [standard] pulls in PyYAML, which has no cp315 build at all (6.0.3). The app uses no YAML log config, --reload (watchfiles) or --env-file (python-dotenv).
- numpy 2.1.3 became 2.5.4 and orjson 3.12.0 became 3.13.0; the old pins had no cp315 builds.
- CI (release.yml) now tests on 3.15, the version the image ships.

## Tried and ruled out
Staying on bookworm: orjson has no 3.15 build there. Dropping orjson for the json module would have meant a code change in the hot SSE path (app/hub.py sse_frame).

## Watch out
- Before the next Python bump, check every pin for a cp3XX Linux build, glibc tag included. CodeQL is the only check on dependabot PRs and it does not build the image.
- Text-mode translations run in parallel, so the order they publish in is not fixed. On 3.15 the smoke test's French/Spanish order flipped; the check now sorts them.
- When running tests inside the image, unset every RELAY_* variable. The image points config, blocklist and recordings at /app/config, so runs share state and fail.

In the code: docker/Dockerfile FROM line, requirements.txt (uvicorn block), tests/smoke_test.py (text-mode publish check), .github/workflows/release.yml python-version.
