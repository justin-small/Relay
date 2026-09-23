@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

REM Live Caption Relay on Windows - stops the container start.bat started.
REM Compose runs inside WSL, as it does in start.bat.

echo Live Caption Relay - stopping...
echo.

set "PROJDIR="
for /f "usebackq delims=" %%i in (`wsl -- wslpath -a "'%CD%'"`) do set "PROJDIR=%%i"
if "!PROJDIR!"=="" (
  echo   Could not map "%CD%" to a WSL path.
  echo.
  pause & exit /b 1
)

wsl -- bash -lc "cd '!PROJDIR!' && docker compose -f docker/docker-compose.yml -f docker/docker-compose.wsl.yml down"
if errorlevel 1 (
  echo.
  echo   Stopping failed - see above.
  echo.
  pause & exit /b 1
)

echo.
echo Stopped.
timeout /t 5
exit /b 0
