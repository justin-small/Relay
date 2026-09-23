#!/usr/bin/env bash
# Live Caption Relay on Linux — stops the container start.sh started.
cd "$(dirname "$0")" || exit 1
exec docker compose -f docker/docker-compose.yml -f docker/docker-compose.linux.yml down
