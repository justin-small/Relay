@echo off
setlocal
cd /d "%~dp0"

REM Live Caption Relay on Windows - stops what start.bat started: the
REM container, and the PulseAudio daemon that carries the microphone in.

echo Live Caption Relay - stopping...
echo.

docker info >nul 2>&1
if errorlevel 1 (
  echo   Docker is not running, so neither is the relay.
) else (
  docker compose -f docker/docker-compose.yml down
  if errorlevel 1 (
    echo.
    echo   Stopping failed - see above.
    echo.
    pause & exit /b 1
  )
)

powershell -NoProfile -ExecutionPolicy Bypass -File tools\windows-audio.ps1 stop

echo.
echo Stopped.
timeout /t 5
exit /b 0
