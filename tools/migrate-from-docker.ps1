<#
.SYNOPSIS
  Copy the settings and data of a Docker install of Relay into the native per-user state folder.

.DESCRIPTION
  A Docker install keeps everything mutable in `docker-config\` beside the launcher scripts (or inside
  the container at /app/config). Native Relay keeps it in %APPDATA%\Relay (or the folder RELAY_HOME names).
  This copies, never moves: the Docker install is left exactly as it was.

  Copied:  config.json (OpenAI key, settings), relay.db (accounts, sessions), blocklist.txt,
           recordings\, backups\, panel-firmware\
  Skipped: Caddyfile and certs\ (the Docker front end's; native Relay does not use them), logs

  Where the data comes from, in order:
    1. -Source <folder>        a docker-config folder you point at
    2. a `docker-config` folder next to this script's parent (the usual place inside the Relay folder)
    3. the running or stopped container, with `docker cp` (-Container, default live-caption-relay)

  Run it with Relay stopped (stop.bat first). It refuses to overwrite an existing native config.json
  or relay.db unless you pass -Force, and then it keeps the old ones as *.before-migration.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\migrate-from-docker.ps1

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File tools\migrate-from-docker.ps1 -Source D:\Relay\docker-config -Force
#>
[CmdletBinding()]
param(
  [string]$Source,
  [string]$Container = 'live-caption-relay',
  [string]$Destination,
  [switch]$Force
)

# Continue, not Stop: Windows PowerShell 5.1 turns a native command's stderr into a terminating error under
# Stop. Docker's exit codes are checked explicitly; file operations pass -ErrorAction Stop.
$ErrorActionPreference = 'Continue'

function Say($msg)  { Write-Host $msg }
function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

# ---- where it goes
if (-not $Destination) {
  if ($env:RELAY_HOME) { $Destination = $env:RELAY_HOME }
  elseif ($env:APPDATA) { $Destination = Join-Path $env:APPDATA 'Relay' }
  else { Fail 'APPDATA is not set; pass -Destination.' }
}

# ---- where it comes from
$tempCopy = $null
if (-not $Source) {
  $guess = Join-Path (Split-Path -Parent $PSScriptRoot) 'docker-config'
  if (Test-Path -LiteralPath (Join-Path $guess 'config.json')) { $Source = $guess }
}
if (-not $Source) {
  $docker = Get-Command docker -ErrorAction SilentlyContinue
  if (-not $docker) {
    Fail "No docker-config folder found and Docker is not installed. Pass -Source <folder> (the docker-config folder of the old install)."
  }
  & docker inspect $Container *> $null
  if ($LASTEXITCODE -ne 0) {
    Fail "No docker-config folder found and there is no container named '$Container'. Pass -Source <folder> or -Container <name>."
  }
  $running = (& docker inspect -f '{{.State.Running}}' $Container) -eq 'true'
  if ($running) {
    Say "The container '$Container' is running. Stopping it so the database is copied in one piece..."
    & docker stop $Container | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "Could not stop '$Container'. Stop it (stop.bat) and run this again." }
  }
  $tempCopy = Join-Path ([IO.Path]::GetTempPath()) ("relay-migrate-" + [Guid]::NewGuid().ToString('N'))
  New-Item -ItemType Directory -Path $tempCopy -ErrorAction Stop | Out-Null
  & docker cp "${Container}:/app/config/." $tempCopy
  if ($LASTEXITCODE -ne 0) { Remove-Item -Recurse -Force $tempCopy; Fail "docker cp from '$Container' failed." }
  $Source = $tempCopy
}

$Source = (Resolve-Path -LiteralPath $Source).Path
if (-not (Test-Path -LiteralPath (Join-Path $Source 'config.json')) -and
    -not (Test-Path -LiteralPath (Join-Path $Source 'relay.db'))) {
  Fail "'$Source' has neither config.json nor relay.db: that is not a Relay docker-config folder."
}
if ((Resolve-Path -LiteralPath $Source).Path -eq $Destination) { Fail 'Source and destination are the same folder.' }

# A running Docker Relay holds relay.db open; copy a consistent file or none.
if (-not $tempCopy -and (Get-Command docker -ErrorAction SilentlyContinue)) {
  $up = & docker ps --filter "name=$Container" --filter 'status=running' -q 2>$null
  if ($up) { Fail "The container '$Container' is still running. Run stop.bat first, then this again." }
}

Say "From: $Source"
Say "To:   $Destination"

# ---- refuse to overwrite quietly
$clash = @('config.json', 'relay.db') | Where-Object {
  (Test-Path -LiteralPath (Join-Path $Source $_)) -and (Test-Path -LiteralPath (Join-Path $Destination $_))
}
if ($clash -and -not $Force) {
  if ($tempCopy) { Remove-Item -Recurse -Force $tempCopy }
  Fail ("Native Relay already has " + ($clash -join ' and ') + " in $Destination. Run again with -Force to replace them (the old ones are kept as *.before-migration).")
}

# ---- the destination folder, owner-only like Relay makes it
if (-not (Test-Path -LiteralPath $Destination)) {
  New-Item -ItemType Directory -Path $Destination -ErrorAction Stop | Out-Null
  & icacls $Destination /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" *> $null
}

function Keep-Old($path) {
  if (Test-Path -LiteralPath $path) {
    Move-Item -LiteralPath $path -Destination ($path + '.before-migration') -Force -ErrorAction Stop
  }
}

$copied = @()

# config.json and the database (with its WAL files if the container did not close cleanly)
foreach ($name in 'config.json', 'relay.db', 'relay.db-wal', 'relay.db-shm', 'blocklist.txt') {
  $from = Join-Path $Source $name
  if (-not (Test-Path -LiteralPath $from)) { continue }
  $to = Join-Path $Destination $name
  if ($Force) { Keep-Old $to }
  Copy-Item -LiteralPath $from -Destination $to -Force -ErrorAction Stop
  $copied += $name
}

# Folders: merge, never delete what is already there.
foreach ($dir in 'recordings', 'backups', 'panel-firmware') {
  $from = Join-Path $Source $dir
  if (-not (Test-Path -LiteralPath $from)) { continue }
  $to = Join-Path $Destination $dir
  New-Item -ItemType Directory -Path $to -Force -ErrorAction Stop | Out-Null
  Copy-Item -Path (Join-Path $from '*') -Destination $to -Recurse -Force -ErrorAction Stop
  $copied += "$dir\"
}

# Owner-only on the secrets, as Relay does itself.
foreach ($name in 'config.json', 'relay.db', 'relay.db-wal', 'relay.db-shm') {
  $p = Join-Path $Destination $name
  if (Test-Path -LiteralPath $p) { & icacls $p /inheritance:r /grant:r "$($env:USERNAME):F" *> $null }
}

if ($tempCopy) { Remove-Item -Recurse -Force $tempCopy }

if (-not $copied) { Fail 'Nothing was copied.' }
Say ''
Say ('Copied: ' + ($copied -join ', '))
Say ''
Say 'Next:'
Say '  1. pip install -r requirements.txt'
Say '  2. python run.py'
Say '  3. Open http://127.0.0.1:8001/admin on this computer and sign in with your existing account.'
Say ''
Say 'Notes:'
Say '  - The Docker install is untouched. Once native Relay works, remove it with: docker compose down'
Say '  - Audio devices saved under a PulseAudio name fall back to the default input; pick the device again in the panel.'
Say '  - The panel is on http://127.0.0.1:8001 now, not https://<address>/admin: HTTPS is not part of native Relay yet.'
