---
title: "A panel that never appears in Relay's admin list is probably running a stale prebuilt image; rebuild --dev in the ESP-IDF container before flashing"
author: Justin
date: 2026-11-10
branch: main
cost: { minutes: 5, tokens: 32873 }
id: l-b3b1faa2
scope: repo
status: active
src: agent
touches:
  - docker/docker-compose.yml
  - docker/docker-compose.macos.yml
---

## Decided
Before bench-flashing, check the image actually contains the feature under test, and rebuild with --dev in the pinned ESP-IDF container instead of reusing whatever is in build-dev/.

## Tried and ruled out
Blaming mDNS or the Mac: Relay's mDNS announce was visible (dns-sd -B), and setting the feed by hand over the serial console made captions stream, yet Relay's panel list stayed empty. Cause: the prebuilt build-dev image (compiled Nov 9 02:26) predates the panel registry commit (Nov 9 16:41), so it never posts a check-in.

## Watch out
Quick staleness test: `strings build-dev/relay-display-app.bin | grep -c "api/panels/checkin"` is 0 on a stale image. ESP-IDF is not installed on the Mac, but the espressif/idf image is in Docker. Docker Relay in rehearsal mode skips start.command's PulseAudio steps: set RELAY_DEMO=1 RELAY_UID RELAY_GID RELAY_ADMIN_IPS, run `docker compose -f docker/docker-compose.yml -f docker/docker-compose.macos.yml up --build -d`, then announce with dns-sd.

In the code: docker run --rm -v "$PWD":/p -w /p/firmware/relay-display espressif/idf:v5.4.4@sha256:9ce51d4ace496845678065dc86f8e55fb9c25fc9f01f45597042afa7ed546926 bash -c '. $IDF_PATH/export.sh && ./build.sh --dev'
