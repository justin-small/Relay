#!/usr/bin/env bash
# Live Caption Relay on Linux — follow the running relay's log (Ctrl+C to stop
# following; the relay keeps running).
exec docker logs -f --tail 200 live-caption-relay
