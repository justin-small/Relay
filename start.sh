#!/usr/bin/env bash
# Live Caption Relay on Linux — starts the whole stack.
#
# One container holds both processes: Caddy on the network side (viewers on
# port 80, the operator panel on 443 over HTTPS) and the relay on loopback
# behind it. There is nothing else to launch.
#
# Linux hands /dev/snd straight to the container, so there is no PulseAudio
# bridge to start — unlike the macOS and Windows paths.
#
# Run setup.sh first: it writes the credentials and mints the panel certificate.
cd "$(dirname "$0")" || exit 1
set -u

echo "Live Caption Relay - starting..."
echo

if [ ! -f docker-config/config.json ]; then
    echo "  Not set up yet — docker-config/config.json is missing."
    echo "  Run ./setup.sh first: it asks for your OpenAI API key and admin"
    echo "  token, and mints the operator panel's certificate."
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "  Cannot talk to Docker."
    echo "  Start the daemon (sudo systemctl start docker), and if this is a"
    echo "  fresh install add yourself to the docker group:"
    echo "      sudo usermod -aG docker \"$USER\"   # then log out and back in"
    exit 1
fi

if [ ! -d /dev/snd ]; then
    echo "  /dev/snd does not exist, so there is no sound card to hand in."
    echo "  Check that ALSA is present and a capture device is connected."
    exit 1
fi

echo "Checking audio..."
if ! ./tools/check-audio.sh; then
    echo
    echo "  Audio checks failed -- see the FAIL lines above."
    echo
    read -r -p "Start anyway? [y/N] " ans
    case "$ans" in [Yy]*) ;; *) exit 1 ;; esac
fi

# The panel's certificate has to name the address the operator's browser will
# dial, and the container cannot work that out for itself -- inside the
# namespace it only sees its own bridge address. So find this machine's LAN
# address here and hand it in. It is re-checked on every start, because the
# address changes when the machine moves to a different venue.
LAN_IP="$(ip route get 1.1.1.1 2>/dev/null | awk '{for (i=1;i<NF;i++) if ($i=="src") print $(i+1); exit}')"
LAN_IP="${LAN_IP:-127.0.0.1}"
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

# Run the container as this user so the bind-mounted docker-config/ carries
# real ownership on both sides. The image defaults to 10001:10001.
export RELAY_UID="$(id -u)" RELAY_GID="$(id -g)"

exec docker compose -f docker/docker-compose.yml -f docker/docker-compose.linux.yml up --build
