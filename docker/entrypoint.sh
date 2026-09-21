#!/bin/sh
# PortAudio in this image speaks ALSA. When PULSE_SERVER is set (macOS/Windows
# hosts, where Docker has no sound card of its own), point ALSA's default PCM
# at the pulse plugin so PortAudio enumerates the host's capture devices.
# With no PULSE_SERVER we leave ALSA alone and use /dev/snd directly (Linux).
#
# PortAudio reports 44100 for the ALSA `pulse` device whatever the server
# runs at, so without a nudge a 48k host source is resampled 48k -> 44.1k by
# PulseAudio and then 44.1k -> 24k by soxr -- two conversions and a slower
# start. RELAY_NATIVE_RATE (read in app/audio.py) pins the open rate instead.
# Set it to your host source's rate if that is not 48k; set it to 0 to leave
# the rate alone and take PortAudio's default.
if [ -n "$PULSE_SERVER" ]; then
    cat > /etc/asound.conf <<'ASOUND'
pcm.!default { type pulse }
ctl.!default { type pulse }
ASOUND
    export RELAY_NATIVE_RATE="${RELAY_NATIVE_RATE:-48000}"
    echo "relay: routing audio via PULSE_SERVER=$PULSE_SERVER (open rate ${RELAY_NATIVE_RATE} Hz)"
fi

exec "$@"
