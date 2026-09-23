#!/usr/bin/env bash
# Live Caption Relay on macOS — follow the running relay's log.
#
# The relay runs detached, so this is where its output went. Closing this
# window stops following the log; it does not stop the relay.
cd "$(dirname "$0")" || exit 1
echo "Following the relay's log. Close this window to stop following it."
echo
docker logs -f --tail 200 live-caption-relay
echo
read -r -p "Press return to close." _
