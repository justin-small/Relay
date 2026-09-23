#!/usr/bin/env bash
# Wait for the detached relay container to come up, for the start launchers.
#
#   ./tools/wait-ready.sh [seconds]      # default 90
#
# The launchers run `docker compose up -d`, so nothing streams the container's
# output any more and a crash on start would otherwise go unnoticed. This polls
# the same /healthz probe as the image's HEALTHCHECK -- run inside the
# container, so it does not depend on which host ports are published -- and
# gives up early if the container stops or starts restarting.
#
# On success it prints the panel certificate's SHA-256 from the startup log,
# which the operator used to read as it scrolled past. On failure it prints
# the tail of that log. Exit status is 0 when the relay is serving.
set -u
NAME="${RELAY_CONTAINER:-live-caption-relay}"
LIMIT="${1:-90}"

probe() {
    docker exec "$NAME" python -c \
        "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()" \
        >/dev/null 2>&1
}

inspect() { docker inspect -f "{{$1}}" "$NAME" 2>/dev/null; }

# A container left running by an earlier start may carry restarts from then,
# so only count the ones that happen while we watch.
restarts0="$(inspect .RestartCount)"

printf 'Waiting for the relay to come up'
ready=0
for ((i = 0; i < LIMIT; i++)); do
    state="$(inspect .State.Status)" || state=missing
    # Exited, restarting, or restarted since we began: the entrypoint gave up,
    # and waiting longer will not help.
    [ "$state" = running ] && [ "$(inspect .RestartCount)" = "$restarts0" ] || break
    probe && { ready=1; break; }
    printf '.'
    sleep 1
done
echo

if [ "$ready" = 1 ]; then
    fp="$(docker logs "$NAME" 2>&1 | grep 'SHA-256 *: ' | tail -1 | sed 's/.*SHA-256 *: *//')"
    echo "  Relay is up."
    [ -n "$fp" ] && echo "  Panel certificate SHA-256: $fp"
    exit 0
fi

echo "  The relay did not come up (container state: ${state:-unknown})."
echo "  Last lines of its log:"
echo
docker logs --tail 30 "$NAME" 2>&1 | sed 's/^/    /'
exit 1
