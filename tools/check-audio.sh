#!/usr/bin/env bash
# Verify the Docker audio chain link by link, before event day.
#
#   ./check-audio.sh                     # Linux: /dev/snd passthrough
#   PULSE_SERVER=tcp:host.docker.internal:4713 ./check-audio.sh    # macOS
#   PULSE_SERVER=unix:/mnt/wslg/PulseServer ./check-audio.sh       # WSL2
#
# Each step prints PASS or FAIL with the thing to fix. Exit status is the
# number of failures.
set -u
IMAGE="${RELAY_IMAGE:-live-caption-relay}"
fails=0
pass() { printf '  PASS  %s\n' "$1"; }
fail() { printf '  FAIL  %s\n        -> %s\n' "$1" "$2"; fails=$((fails + 1)); }

run() {  # run a command in the relay image with the audio wiring applied
    docker run --rm \
        ${PULSE_SERVER:+-e PULSE_SERVER="$PULSE_SERVER"} \
        ${PULSE_SERVER:+--add-host=host.docker.internal:host-gateway} \
        ${WSLG_MOUNT:+-v "$WSLG_MOUNT"} \
        ${PULSE_SERVER:+} ${DEV_SND:+--device /dev/snd:/dev/snd --group-add audio} \
        "$IMAGE" "$@" 2>&1
}

echo "Live Caption Relay -- audio chain check"
echo "image: $IMAGE   PULSE_SERVER=${PULSE_SERVER:-<unset, using /dev/snd>}"
echo

# Pick the wiring from the environment.
if [ -n "${PULSE_SERVER:-}" ]; then
    # WSLg's socket lives on the host filesystem, so it has to be bind
    # mounted in. RELAY_PULSE_MOUNT overrides the source (a path or a volume).
    case "$PULSE_SERVER" in
        unix:*) WSLG_MOUNT="${RELAY_PULSE_MOUNT:-/mnt/wslg:/mnt/wslg}" ;;
        *) WSLG_MOUNT="" ;;
    esac
    DEV_SND=""
else
    WSLG_MOUNT=""; DEV_SND=1
    [ -e /dev/snd ] || fail "/dev/snd exists on this host" \
        "no sound card visible; on macOS/Windows set PULSE_SERVER instead"
fi

# 1. image present
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
    pass "image '$IMAGE' is built"
else
    fail "image '$IMAGE' is built" "run: docker compose build"
    echo; echo "$fails failure(s)."; exit "$fails"
fi

# 2. server reachable (pulse paths only)
if [ -n "${PULSE_SERVER:-}" ]; then
    info=$(run pactl info)
    if printf '%s' "$info" | grep -q "Server Protocol Version"; then
        pass "PulseAudio server reachable at $PULSE_SERVER"
    else
        fail "PulseAudio server reachable at $PULSE_SERVER" \
             "is the daemon running, and does its auth-ip-acl cover the container subnet? ($(printf '%s' "$info" | tail -1))"
    fi

    # 3. at least one capture source
    # pactl prints its errors on stdout too, so keep only real source rows
    # (leading numeric index, tab separated) rather than anything non-empty.
    srcs=$(run pactl list short sources | grep -E '^[0-9]+\s')
    if [ -n "$srcs" ]; then
        pass "capture sources visible:"; printf '%s\n' "$srcs" | sed 's/^/          /'
    else
        fail "capture sources visible" "no sources; on macOS load module-coreaudio-detect, on WSL check WSLg is running"
    fi
fi

# 4. PortAudio sees an input device
pa=$(run python -c "
import sounddevice as sd
print(' '.join(d['name'] for d in sd.query_devices() if d['max_input_channels']))
" | grep -v '^relay:')
if [ -n "$pa" ]; then
    pass "PortAudio input devices: $pa"
else
    fail "PortAudio input devices" "ALSA is not routed; check /etc/asound.conf in the container"
fi

# 5. real samples, and are they actually moving
cap=$(run python -c "
import sounddevice as sd, numpy as np, os
dev = 'pulse' if os.environ.get('PULSE_SERVER') else None
with sd.InputStream(device=dev, channels=1, samplerate=48000, blocksize=1024) as s:
    s.read(4800)
    d, over = s.read(48000)
rms = float(np.sqrt((d.astype('float64')**2).mean()))
print('%.8f %s' % (rms, over))
" | grep -v '^relay:')
rms=$(printf '%s' "$cap" | awk '{print $1}')
case "$rms" in
    ''|*[!0-9.]*) fail "one second of audio captured" \
                      "stream would not open: $(printf '%s' "$cap" | tail -1)" ;;
    *)
        pass "one second of audio captured (rms $rms)"
        if awk "BEGIN{exit !($rms == 0)}"; then
            fail "signal is non-silent" \
                 "digital silence. On macOS grant Microphone permission to the terminal running pulseaudio; on Windows enable Settings > Privacy > Microphone > 'Let desktop apps access your microphone'."
        else
            pass "signal is non-silent (mic permission is granted)"
        fi
        ;;
esac

echo
if [ "$fails" -eq 0 ]; then
    echo "All checks passed. Pick 'ALSA: pulse' (or your card) in the operator panel."
else
    echo "$fails failure(s) above."
fi
exit "$fails"
