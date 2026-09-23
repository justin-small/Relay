@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Live Caption Relay on Windows - starts the whole stack.
REM
REM One container holds both processes: Caddy on the network side (viewers on
REM port 80, the operator panel on 443 over HTTPS) and the relay on loopback
REM behind it. There is nothing else to launch.
REM
REM Docker Desktop already runs on WSL2, and Windows 11's WSLg already exposes
REM the microphone to it, so this just runs docker compose inside WSL where
REM that microphone is visible. You should not need to open a Linux shell.
REM
REM The container runs detached, so this window is not what keeps the relay
REM alive: once it is up the window closes itself, and the relay runs until
REM stop.bat. Use logs.bat to follow its output.
REM
REM Run setup.bat first: it writes the credentials and mints the panel
REM certificate.

echo Live Caption Relay - starting...
echo.

REM --- WSL present? (Docker Desktop's default backend, so it should be) ---
wsl --status >nul 2>&1
if errorlevel 1 (
  echo   Cannot talk to WSL.
  echo.
  echo   Docker Desktop uses WSL2 as its backend, so this usually means Docker
  echo   Desktop is not installed or not running. Start Docker Desktop and try
  echo   again.
  echo.
  pause & exit /b 1
)

REM --- docker reachable from inside WSL? (Docker Desktop > Settings >
REM     Resources > WSL integration must be on for the default distro) ---
wsl -- docker version >nul 2>&1
if errorlevel 1 (
  echo   Docker is not available inside WSL.
  echo.
  echo   Open Docker Desktop - Settings - Resources - WSL integration and
  echo   enable it for your default distro, then try again.
  echo.
  pause & exit /b 1
)

REM --- WSLg's PulseAudio socket: this is what carries the microphone ---
wsl -- test -e /mnt/wslg/PulseServer
if errorlevel 1 (
  echo   WSLg is not available ^(no /mnt/wslg/PulseServer^).
  echo.
  echo   WSLg ships with Windows 11. On Windows 10 there is no WSLg, so the
  echo   container cannot reach the microphone.
  echo.
  pause & exit /b 1
)

REM --- translate this folder to its WSL path ---
set "PROJDIR="
for /f "usebackq delims=" %%i in (`wsl -- wslpath -a "'%CD%'"`) do set "PROJDIR=%%i"
if "!PROJDIR!"=="" (
  echo   Could not map "%CD%" to a WSL path.
  echo.
  pause & exit /b 1
)

REM --- set up already done? ---
if not exist docker-config\config.json (
  echo   Not set up yet - docker-config\config.json is missing.
  echo   Double-click setup.bat first: it asks for your OpenAI API key and
  echo   admin token, and mints the operator panel's certificate.
  echo.
  pause & exit /b 1
)

REM --- check the audio chain before going live ---
echo Checking audio...
wsl -- bash -lc "cd '!PROJDIR!' && PULSE_SERVER=unix:/mnt/wslg/PulseServer ./tools/check-audio.sh"
if errorlevel 1 (
  echo.
  echo   Audio checks failed - see the FAIL lines above.
  echo.
  echo   The usual cause is microphone permission: open Settings - Privacy ^&
  echo   Security - Microphone and turn on BOTH "Let apps access your
  echo   microphone" and "Let desktop apps access your microphone".
  echo.
  choice /m "Start anyway"
  if errorlevel 2 (pause & exit /b 1)
)

REM The panel's certificate has to name the address the operator's browser will
REM dial, and the container cannot work that out for itself - inside the
REM namespace it only sees its own bridge address. So find this machine's LAN
REM address here and hand it in. It is re-checked on every start, because the
REM address changes when the machine moves to a different venue.
if "!RELAY_ADMIN_IPS!"=="" (
    for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command ^
        "(Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -ne $null} | Select-Object -First 1).IPv4Address.IPAddress"`) do set "RELAY_ADMIN_IPS=%%i"
)
if "!RELAY_ADMIN_IPS!"=="" set "RELAY_ADMIN_IPS=127.0.0.1"

REM Compose runs inside WSL, where the docker-config bind mount carries real
REM Linux ownership, so hand the container the invoking user's uid/gid.
REM RELAY_DEMO is handed in the same way: WSL does not inherit Windows
REM variables, so "set RELAY_DEMO=1" alone would otherwise start Relay live.
echo.
if "!RELAY_DEMO!"=="1" echo REHEARSAL MODE: canned captions, capture and schedules disabled.
echo Building and starting the container...
wsl -- bash -lc "cd '!PROJDIR!' && RELAY_UID=$(id -u) RELAY_GID=$(id -g) RELAY_ADMIN_IPS='!RELAY_ADMIN_IPS!' RELAY_DEMO='!RELAY_DEMO!' docker compose -f docker/docker-compose.yml -f docker/docker-compose.wsl.yml up --build -d && ./tools/wait-ready.sh"
if errorlevel 1 (
  echo.
  pause & exit /b 1
)

echo.
echo   Viewer link : http://!RELAY_ADMIN_IPS!/            ^<- share this with the room
echo   Panel       : https://!RELAY_ADMIN_IPS!/admin
echo.
echo   The panel's certificate is self-signed, so the browser warns the first
echo   time. Check the SHA-256 above against what the browser shows before
echo   accepting it.
echo.
echo   The relay keeps running after this window closes.
echo   Stop it with stop.bat; follow its log with logs.bat.
echo.

start "" "https://!RELAY_ADMIN_IPS!/admin"

timeout /t 30
exit /b 0
