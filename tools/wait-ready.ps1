<#
Wait for the detached relay container to come up -- the Windows PowerShell
counterpart of wait-ready.sh, for start.bat, since Windows has no bash.

  powershell -ExecutionPolicy Bypass -File tools\wait-ready.ps1 [seconds]   # default 90

Polls the same /healthz probe as the image's HEALTHCHECK, run inside the
container, and gives up early if the container stops or starts restarting. On
success prints the panel certificate's SHA-256 from the startup log; on
failure, the tail of that log. Exit status is 0 when the relay is serving.
#>
param([int]$Limit = 90)
$Name = if ($env:RELAY_CONTAINER) { $env:RELAY_CONTAINER } else { 'live-caption-relay' }

function Inspect($field) { docker inspect -f "{{$field}}" $Name 2>$null }
function Probe {
    docker exec $Name python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=3).read()" *> $null
    $LASTEXITCODE -eq 0
}

# A container left running by an earlier start may carry restarts from then,
# so only count the ones that happen while we watch.
$restarts0 = Inspect '.RestartCount'

Write-Host -NoNewline 'Waiting for the relay to come up'
$ready = $false
$state = 'missing'
for ($i = 0; $i -lt $Limit; $i++) {
    $state = Inspect '.State.Status'
    if (-not $state) { $state = 'missing' }
    # Exited, restarting, or restarted since we began: the entrypoint gave up,
    # and waiting longer will not help.
    if ($state -ne 'running' -or (Inspect '.RestartCount') -ne $restarts0) { break }
    if (Probe) { $ready = $true; break }
    Write-Host -NoNewline '.'
    Start-Sleep 1
}
Write-Host ''

if ($ready) {
    $fp = docker logs $Name 2>&1 | ForEach-Object { "$_" } | Select-String 'SHA-256 *: ' | Select-Object -Last 1
    Write-Host '  Relay is up.'
    if ($fp) { Write-Host "  Panel certificate SHA-256: $(($fp.Line -replace '.*SHA-256 *: *', ''))" }
    exit 0
}

Write-Host "  The relay did not come up (container state: $state)."
Write-Host '  Last lines of its log:'
Write-Host ''
docker logs --tail 30 $Name 2>&1 | ForEach-Object { "    $_" }
exit 1
