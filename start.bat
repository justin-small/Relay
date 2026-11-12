@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Live Caption Relay on Windows - starts the whole stack.
REM
REM One container holds both processes: Caddy on the network side (viewers on
REM port 80, the operator panel on 443 over HTTPS), the relay on loopback behind
REM it. The user accounts are a SQLite file in the state folder (no database server).
REM There is nothing else to launch.
REM
REM Docker Desktop has no sound card of its own, so capture comes from a
REM PulseAudio daemon running natively on Windows (tools\windows-audio.ps1,
REM installed by setup.bat). This starts that daemon if it is not already up,
REM checks the chain, then runs the container. You should not need to open a
REM Linux shell.
REM
REM The container runs detached, so this window is not what keeps the relay
REM alive: once it is up the window closes itself, and the relay runs until
REM stop.bat. Use logs.bat to follow its output.
REM
REM Run setup.bat first: it writes the credentials and mints the panel
REM certificate.

echo Live Caption Relay - starting...
echo.

REM --- Docker running? ---
docker info >nul 2>&1
if errorlevel 1 (
  echo   Docker is not running.
  echo.
  echo   Start Docker Desktop, wait for it to settle, and try again.
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

REM Rehearsal needs no microphone, so it skips the daemon and the check.
if "!RELAY_DEMO!"=="1" goto :up

powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows-audio.ps1 start
if errorlevel 1 (
  echo.
  pause & exit /b 1
)

REM --- check the audio chain before going live ---
echo Checking audio...
powershell -NoProfile -ExecutionPolicy Bypass -File tools\check-audio.ps1
if errorlevel 1 (
  echo.
  echo   Audio checks failed - see the FAIL lines above.
  echo.
  echo   The usual cause is microphone permission: open Settings - Privacy -
  echo   Microphone and turn on "Allow apps to access your microphone" and
  echo   "Allow desktop apps to access your microphone".
  echo.
  choice /m "Start anyway"
  if errorlevel 2 (pause & exit /b 1)
)

:up
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

REM Docker Desktop fakes bind-mount ownership here, so the image's default user
REM is right and RELAY_UID/RELAY_GID are left unset. The environment -
REM RELAY_ADMIN_IPS, RELAY_DEMO - reaches compose directly.
echo.
if "!RELAY_DEMO!"=="1" echo REHEARSAL MODE: canned captions, capture and schedules disabled.
echo Building and starting the container...
docker compose -f docker/docker-compose.yml -f docker/docker-compose.windows.yml up --build -d
if errorlevel 1 (
  echo.
  pause & exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File tools\wait-ready.ps1
if errorlevel 1 (
  echo.
  pause & exit /b 1
)

REM Announce Relay on the LAN so caption panels find it. Docker Desktop's container
REM is not on the LAN, so this runs here, with Windows' own DNS-SD. Set RELAY_ANNOUNCE=0
REM to turn it off; RELAY_INSTALL_NAME names this install (default: the computer's name).
echo.
if not "!RELAY_ANNOUNCE!"=="0" (
  powershell -NoProfile -ExecutionPolicy Bypass -File tools\announce.ps1 start -Ip "!RELAY_ADMIN_IPS!"
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
