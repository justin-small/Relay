#!/usr/bin/env bash
# Live Caption Relay on macOS — starts the whole stack.
#
# One container holds both processes: Caddy on the network side (viewers on
# port 80, the operator panel on 443 over HTTPS) and the relay on loopback
# behind it. There is nothing else to launch.
#
# Docker Desktop has no sound card of its own, so capture comes from a
# PulseAudio daemon running natively on the Mac. This starts that daemon if it
# is not already up, checks the chain, then runs the container.
#
# Run setup.command first: it writes the credentials and mints the panel
# certificate.
cd "$(dirname "$0")" || exit 1
set -u

PORT=4713
# Docker Desktop's VM subnet and the default bridge range -- the container is
# not on your LAN, so an ACL of just 127.0.0.1 would refuse it.
ACL="127.0.0.1;192.168.65.0/24;172.16.0.0/12"
PA="$(command -v pulseaudio || echo /opt/homebrew/opt/pulseaudio/bin/pulseaudio)"

echo "Live Caption Relay - starting in Docker..."
echo

if [ ! -f docker-config/config.json ]; then
    echo "  Not set up yet — docker-config/config.json is missing."
    echo "  Double-click setup.command first: it asks for your OpenAI API key"
    echo "  and admin token, and mints the operator panel's certificate."
    echo; read -r -p "Press return to close." _; exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "  Docker is not running. Start Docker Desktop and try again."
    echo; read -r -p "Press return to close." _; exit 1
fi

if [ ! -x "$PA" ]; then
    echo "  PulseAudio is not installed. It is what carries the Mac's"
    echo "  microphone into the container. Install it with:"
    echo
    echo "      brew install pulseaudio"
    echo
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

# The panel's certificate has to name the address the operator's browser will
# dial, and the container cannot work that out for itself -- inside the
# namespace it only sees its own bridge address. So find the Mac's LAN address
# here and hand it in. It is re-checked on every start, because the address
# changes when the machine moves to a different venue.
LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo 127.0.0.1)"
export RELAY_ADMIN_IPS="${RELAY_ADMIN_IPS:-$LAN_IP}"

echo
echo "Starting."
echo "  Viewer link : http://$LAN_IP/            <- share this with the room"
echo "  Panel       : https://$LAN_IP/admin"
echo
echo "  The panel's certificate is self-signed, so the browser warns the first"
echo "  time. The startup log prints its SHA-256 fingerprint -- check that"
echo "  against what the browser shows before accepting it."
echo "Press Ctrl+C to stop."
echo

# Run the container as this user so the bind-mounted docker-config/ stays
# readable and writable on both sides. The image defaults to 10001:10001.
export RELAY_UID="$(id -u)" RELAY_GID="$(id -g)"

docker compose -f docker/docker-compose.yml -f docker/docker-compose.macos.yml up --build

echo
read -r -p "Press return to close." _
