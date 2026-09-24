<#
Verify the Docker audio chain link by link, before event day -- the Windows
PowerShell counterpart of check-audio.sh, for Windows, where there is no bash
(start.bat runs it before going live).

  powershell -ExecutionPolicy Bypass -File tools\check-audio.ps1
  (PULSE_SERVER defaults to tcp:host.docker.internal:4713)

Each step prints PASS or FAIL with the thing to fix. Exit status is the
number of failures.
#>
$Image = if ($env:RELAY_IMAGE) { $env:RELAY_IMAGE } else { 'live-caption-relay' }
$Pulse = if ($env:PULSE_SERVER) { $env:PULSE_SERVER } else { 'tcp:host.docker.internal:4713' }
$script:fails = 0
function Pass($m) { Write-Host "  PASS  $m" }
function Fail($m, $fix) { Write-Host "  FAIL  $m"; Write-Host "        -> $fix"; $script:fails++ }

# Run Python in the relay image with the audio wiring applied. The code goes
# in on stdin: quoting it through Windows' command line mangles its quotes.
function Invoke-InImage([string]$code) {
    $out = $code | docker run --rm -i -e "PULSE_SERVER=$Pulse" $Image python - 2>&1
    # The leading comma keeps a one-line result an array: PowerShell would
    # otherwise unwrap it to a string, and [-1] would index its last character.
    , @($out | ForEach-Object { "$_" } | Where-Object { $_ -notmatch '^relay:' })
}

Write-Host 'Live Caption Relay -- audio chain check'
Write-Host "image: $Image   PULSE_SERVER=$Pulse"
Write-Host ''

# 1. image present
docker image inspect $Image *> $null
if ($LASTEXITCODE -eq 0) { Pass "image '$Image' is built" }
else {
    Fail "image '$Image' is built" 'run setup.bat, or: docker compose -f docker/docker-compose.yml build'
    Write-Host ''; Write-Host "$script:fails failure(s)."; exit $script:fails
}

# 2. server reachable. The image carries no pactl, so probe the socket from
# Python: the server waits for the client to speak first, but closes straight
# away on a client its auth-ip-acl rejects -- so a connection that stays open
# and quiet means reachable and admitted.
$probe = Invoke-InImage @'
import os, socket
srv = os.environ['PULSE_SERVER'].split()[0]
host, _, port = srv.removeprefix('tcp:').removeprefix('tcp4:').partition(':')
try:
    s = socket.create_connection((host, int(port or 4713)), timeout=3)
    s.settimeout(1)
    try:
        print('rejected: server closed the connection' if s.recv(1) == b'' else 'ok')
    except socket.timeout:
        print('ok')
except OSError as e:
    print('unreachable: %s' % e)
'@
if ($probe[-1] -eq 'ok') { Pass "PulseAudio server reachable at $Pulse" }
else {
    Fail "PulseAudio server reachable at $Pulse" "is it running? start.bat starts it; the log is %TEMP%\relay-pulseaudio.log ($($probe[-1]))"
}

# 3. PortAudio sees an input device
$pa = Invoke-InImage @'
import sounddevice as sd
print(' '.join(d['name'] for d in sd.query_devices() if d['max_input_channels']))
'@
if ($pa -and $pa[-1]) { Pass "PortAudio input devices: $($pa[-1])" }
else { Fail 'PortAudio input devices' 'ALSA is not routed; check the entrypoint wrote ~/.asoundrc' }

# 4. real samples, and are they actually moving. This is also what proves the
# server has a capture source to hand out.
$cap = Invoke-InImage @'
import sounddevice as sd, numpy as np
with sd.InputStream(device='pulse', channels=1, samplerate=48000, blocksize=1024) as s:
    s.read(4800)
    d, over = s.read(48000)
rms = float(np.sqrt((d.astype('float64')**2).mean()))
print('%.8f %s' % (rms, over))
'@
$rms = ("$($cap[-1])" -split ' ')[0]
if ($rms -notmatch '^[0-9.]+$') {
    Fail 'one second of audio captured' "stream would not open: $($cap[-1]); check windows-audio.ps1 found a recording device (see the start.bat output)"
} else {
    Pass "one second of audio captured (rms $rms)"
    if ([double]$rms -eq 0) {
        Fail 'signal is non-silent' "digital silence. Enable Settings > Privacy > Microphone > 'Allow desktop apps to access your microphone', and check the input is not muted in Sound settings."
    } else { Pass 'signal is non-silent (mic permission is granted)' }
}

Write-Host ''
if ($script:fails -eq 0) { Write-Host "All checks passed. Pick your input by name in the operator panel." }
else { Write-Host "$script:fails failure(s) above." }
exit $script:fails
