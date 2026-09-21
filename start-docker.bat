@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Live Caption Relay in Docker on Windows.
REM
REM Docker Desktop already runs on WSL2, and Windows 11's WSLg already exposes
REM the microphone to it, so this just runs docker compose inside WSL where
REM that microphone is visible. You should not need to open a Linux shell.

echo Live Caption Relay - starting in Docker...
echo.

REM --- WSL present? (Docker Desktop's default backend, so it should be) ---
wsl --status >nul 2>&1
if errorlevel 1 (
  echo   Cannot talk to WSL.
  echo.
  echo   Docker Desktop uses WSL2 as its backend, so this usually means Docker
  echo   Desktop is not installed or not running. Start Docker Desktop and try
  echo   again, or run start.bat to launch the app natively instead.
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
  echo   container cannot reach the microphone - run start.bat instead to
  echo   launch the app natively.
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

REM --- make sure the mounted config volume exists ---
if not exist docker-config mkdir docker-config
if not exist docker-config\config.json (
  echo Creating docker-config\config.json from the template.
  echo   Set your OpenAI API key and admin token in the operator panel
  echo   on first launch, or edit the file directly before starting.
  copy /y docker\config.example.json docker-config\config.json >nul
  echo.
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

echo.
echo Starting. Viewer link: http://localhost:8000/   Panel: http://localhost:8001/admin
echo Share the machine's LAN address with the room, e.g. http://192.168.1.50:8000/
echo Close this window or press Ctrl+C to stop.
echo.

wsl -- bash -lc "cd '!PROJDIR!' && docker compose -f docker/docker-compose.yml -f docker/docker-compose.wsl.yml up --build"

echo.
pause
