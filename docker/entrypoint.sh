#!/bin/bash
# Runs unprivileged (uid 10001 by default) with a read-only root filesystem,
# so everything here writes to $HOME, /tmp, or the mounted state volume --
# never /etc and never into /app.
#
# Two processes live in this container: Caddy faces the network, uvicorn stays
# on loopback inside the namespace. bash rather than sh because the supervision
# at the bottom needs `wait -n`, which dash does not have.
#
# `serve` (the image's CMD) runs that whole stack. Anything else is run as
# given, after the audio setup below -- that is how setup.command / setup.bat
# reach tools/write_config.py and tools/setup_caddy.py with no Python on the
# host at all.
set -euo pipefail

: "${HOME:=/home/relay}"

# Seed the blocklist into the writable state volume on first run. The copy
# baked into the image is read-only, and the admin panel rewrites this file
# via a temp-file rename, which a single-file bind mount cannot support.
if [ -n "${RELAY_BLOCKLIST:-}" ] && [ ! -e "$RELAY_BLOCKLIST" ]; then
    mkdir -p "$(dirname "$RELAY_BLOCKLIST")"
    cp /app/blocklist.txt "$RELAY_BLOCKLIST" 2>/dev/null || : > "$RELAY_BLOCKLIST"
fi

# PortAudio in this image speaks ALSA. When PULSE_SERVER is set (macOS/Windows
# hosts, where Docker has no sound card of its own), point ALSA's default PCM
# at the pulse plugin so PortAudio enumerates the host's capture devices.
# With no PULSE_SERVER we leave ALSA alone and use /dev/snd directly (Linux).
#
# ~/.asoundrc layers on top of the system config, so unlike /etc/asound.conf
# it needs neither root nor a writable /etc.
#
# PortAudio reports 44100 for the ALSA `pulse` device whatever the server
# runs at, so without a nudge a 48k host source is resampled 48k -> 44.1k by
# PulseAudio and then 44.1k -> 24k by soxr -- two conversions and a slower
# start. RELAY_NATIVE_RATE (read in app/audio.py) pins the open rate instead.
# Set it to your host source's rate if that is not 48k; set it to 0 to leave
# the rate alone and take PortAudio's default.
if [ -n "${PULSE_SERVER:-}" ]; then
    if ! cat > "$HOME/.asoundrc" <<'ASOUND'
pcm.!default { type pulse }
ctl.!default { type pulse }
ASOUND
    then
        echo "relay: cannot write $HOME/.asoundrc -- mount a writable \$HOME (tmpfs)" >&2
        exit 1
    fi
    export RELAY_NATIVE_RATE="${RELAY_NATIVE_RATE:-48000}"
    echo "relay: routing audio via PULSE_SERVER=$PULSE_SERVER (open rate ${RELAY_NATIVE_RATE} Hz)"
fi

if [ "${1:-}" != "serve" ]; then
    exec "$@"
fi

# --------------------------------------------------------------------- tls
# The panel binds 127.0.0.1 (config key `admin_host`), so the only way in is
# Caddy, and Caddy needs a certificate. Generating it here rather than baking
# it into the image means the private key is never in a layer and never in a
# registry, and the names on it match the host this container actually runs on.
#
# The hostname comes from `admin_fqdn` in config.json, which setup asked for;
# RELAY_ADMIN_FQDN overrides it for a one-off run.
# An existing certificate on the state volume is reused while it still covers
# the current names and has 30+ days left -- see tools/setup_caddy.py.
python /app/tools/setup_caddy.py

CADDYFILE="${RELAY_CADDY_DIR:-/app/config}/Caddyfile"
if ! caddy validate --config "$CADDYFILE" >/dev/null 2>&1; then
    echo "relay: generated Caddyfile is not valid -- refusing to start" >&2
    caddy validate --config "$CADDYFILE" >&2 || true
    exit 1
fi

# -------------------------------------------------------------- supervision
# Both processes matter: without uvicorn there is nothing to serve, and without
# Caddy the panel is unreachable and the viewer port is closed. So run them
# side by side and let the first exit take the container down -- `restart:
# unless-stopped` then rebuilds a known-good pair, which is far easier to
# reason about than a half-running container that passes a shallow probe.
caddy run --config "$CADDYFILE" --adapter caddyfile &
CADDY_PID=$!

python run.py &
APP_PID=$!

shutdown() {
    # Docker sends SIGTERM to PID 1 (tini, via `init: true`), which forwards it
    # here. Pass it on to both children rather than dying and leaving them to
    # be SIGKILLed 10 seconds later.
    trap - TERM INT
    kill -TERM "$CADDY_PID" "$APP_PID" 2>/dev/null || true
    wait "$CADDY_PID" "$APP_PID" 2>/dev/null || true
    exit 0
}
trap shutdown TERM INT

# `|| STATUS=$?` rather than a bare `wait -n`: under `set -e` a non-zero exit
# from the first child would end this script immediately and the diagnostic
# below would never be printed, leaving an operator with a container that
# vanished and no reason why.
STATUS=0
wait -n || STATUS=$?
echo "relay: a supervised process exited (status $STATUS) -- stopping the container" >&2
kill -TERM "$CADDY_PID" "$APP_PID" 2>/dev/null || true
wait "$CADDY_PID" "$APP_PID" 2>/dev/null || true
exit "$STATUS"
