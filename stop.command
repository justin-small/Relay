#!/usr/bin/env bash
# Live Caption Relay on macOS — stops what start.command started: the
# container, and the PulseAudio daemon that carries the Mac's microphone in.
cd "$(dirname "$0")" || exit 1
set -u

WIN_ID=""
[ "${TERM_PROGRAM:-}" = Apple_Terminal ] &&
    WIN_ID="$(osascript -e 'tell application "Terminal" to id of front window' 2>/dev/null)"

echo "Live Caption Relay - stopping..."
echo

if ! docker info >/dev/null 2>&1; then
    echo "  Docker is not running, so neither is the relay."
elif ! docker compose -f docker/docker-compose.yml -f docker/docker-compose.macos.yml down; then
    echo
    read -r -p "Stopping failed -- see above. Press return to close." _; exit 1
fi

# Only the daemon start.command launches -- matched on its own arguments, so a
# PulseAudio run for anything else is left alone.
if pkill -f 'module-native-protocol-tcp port=4713 auth-anonymous=1' 2>/dev/null; then
    echo "Stopped PulseAudio."
fi

echo
echo "Stopped."
read -r -t 5 -p "This window closes in 5 seconds." _
echo

[ -n "$WIN_ID" ] && nohup osascript \
    -e 'delay 1' \
    -e "tell application \"Terminal\" to close (every window whose id is $WIN_ID)" \
    >/dev/null 2>&1 &
exit 0
