#!/usr/bin/env bash
# Live Caption Relay in Docker on macOS.
#
# Docker Desktop has no sound card of its own, so capture comes from a
# PulseAudio daemon running natively on the Mac. This starts that daemon if it
# is not already up, checks the chain, then runs the container.
cd "$(dirname "$0")" || exit 1
set -u

PORT=4713
# Docker Desktop's VM subnet and the default bridge range -- the container is
# not on your LAN, so an ACL of just 127.0.0.1 would refuse it.
ACL="127.0.0.1;192.168.65.0/24;172.16.0.0/12"
PA="$(command -v pulseaudio || echo /opt/homebrew/opt/pulseaudio/bin/pulseaudio)"

echo "Live Caption Relay - starting in Docker..."
echo

mkdir -p docker-config
if [ ! -f docker-config/config.json ]; then
    echo "Creating docker-config/config.json from the template."
    echo "  Set your OpenAI API key and admin token in the operator panel"
    echo "  on first launch, or edit the file directly before starting."
    cp docker/config.example.json docker-config/config.json
    chmod 600 docker-config/config.json
    echo
fi

if ! docker info >/dev/null 2>&1; then
    echo "  Docker is not running. Start Docker Desktop and try again,"
    echo "  or run ./start.command to launch the app natively instead."
    echo; read -r -p "Press return to close." _; exit 1
fi

if [ ! -x "$PA" ]; then
    echo "  PulseAudio is not installed. It is what carries the Mac's"
    echo "  microphone into the container. Install it with:"
    echo
    echo "      brew install pulseaudio"
    echo
    echo "  Or run ./start.command to launch the app natively instead."
    echo; read -r -p "Press return to close." _; exit 1
fi

if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "PulseAudio already running on port $PORT."
else
    echo "Starting PulseAudio on port $PORT..."
    # --daemonize=yes fails on the Homebrew build, so background it and keep
    # a log next to the project for when something needs diagnosing.
    LOG="${TMPDIR:-/tmp}/relay-pulseaudio.log"
    nohup "$PA" --exit-idle-time=-1 --log-target=stderr \
        --load="module-native-protocol-tcp port=$PORT auth-anonymous=1 auth-ip-acl=$ACL" \
        > "$LOG" 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do
        sleep 1
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1 && break
    done
    if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
        echo "  PulseAudio did not come up. Log: $LOG"
        tail -5 "$LOG" 2>/dev/null | sed 's/^/    /'
        read -r -p "Press return to close." _; exit 1
    fi
fi

echo "Checking audio..."
if ! PULSE_SERVER="tcp:host.docker.internal:$PORT" ./tools/check-audio.sh; then
    echo
    echo "  Audio checks failed -- see the FAIL lines above."
    echo "  If the signal is digital silence, grant Microphone permission:"
    echo "  System Settings > Privacy & Security > Microphone."
    echo
    read -r -p "Start anyway? [y/N] " ans
    case "$ans" in [Yy]*) ;; *) exit 1 ;; esac
fi

echo
echo "Starting. Viewer link: http://localhost:8000/   Panel: http://localhost:8001/admin"
echo "Share the Mac's LAN address with the room, e.g. http://192.168.1.50:8000/"
echo "Press Ctrl+C to stop."
echo

docker compose -f docker/docker-compose.yml -f docker/docker-compose.macos.yml up --build

echo
read -r -p "Press return to close." _
