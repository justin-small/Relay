@echo off
REM Relay - one-time setup for Windows. Double-click this before the first run.
REM
REM Everything the stack needs, in one pass: it checks Docker, builds the
REM image, asks for your OpenAI API key and admin token, and mints the TLS
REM certificate the operator panel is served with. All of it lands in
REM docker-config\, which survives rebuilds.
REM
REM There is no Python or virtualenv on this machine to set up. The credentials
REM and the certificate are written by the image's own Python, through
REM `docker compose run`, so the only dependency here is Docker itself.
REM
REM Compose runs inside WSL, where the docker-config bind mount carries real
REM Linux ownership - same as start.bat.
REM
REM It does not start the relay - use start.bat for that.
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "PROJDIR=%CD%"
for /f "usebackq delims=" %%p in (`wsl -- wslpath "'%PROJDIR%'"`) do set "PROJDIR=%%p"

echo Relay - setup
echo =============
echo.

REM -------------------------------------------------------------- docker
wsl -- bash -lc "command -v docker >/dev/null 2>&1"
if errorlevel 1 (
    echo   Docker is not available inside WSL.
    echo   Install Docker Desktop, enable WSL2 integration in its settings,
    echo   then run this again.
    goto :fail
)
wsl -- bash -lc "docker info >/dev/null 2>&1"
if errorlevel 1 (
    echo   Docker is installed but not running.
    echo   Start Docker Desktop, wait for it to settle, then run this again.
    goto :fail
)
echo Docker - ok

wsl -- bash -lc "cd '!PROJDIR!' && mkdir -p docker-config && chmod 700 docker-config"

echo Building the image ^(this can take a few minutes the first time^)...
wsl -- bash -lc "cd '!PROJDIR!' && RELAY_UID=$(id -u) RELAY_GID=$(id -g) docker compose -f docker/docker-compose.yml build"
if errorlevel 1 (
    echo   The build failed - see the output above.
    goto :fail
)
echo Image - ok
echo.

REM -------------------------------------------------------------- config
wsl -- bash -lc "cd '!PROJDIR!' && test -f docker-config/config.json"
if not errorlevel 1 (
    echo docker-config/config.json already exists.
    set /p ANS="Reconfigure the API key and admin token? [y/N] "
    REM A "no" skips only the credentials - setup still falls through to the
    REM certificate step, so re-running this is how you renew the panel
    REM certificate, or certify a new address, without retyping the API key.
    if /i not "!ANS!"=="y" (
        echo.
        goto :tls
    )
    echo.
)

echo OpenAI API key
echo   Create one at https://platform.openai.com/api-keys
echo   It is stored only in docker-config\config.json on this machine.
echo   Input is hidden.
:askkey
for /f "usebackq delims=" %%k in (`powershell -NoProfile -Command ^
    "$s = Read-Host -AsSecureString '  API key'; " ^
    "[Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim()"`) do set "OPENAI_KEY=%%k"
if "!OPENAI_KEY!"=="" (
    echo   The API key cannot be empty.
    goto :askkey
)
echo.

echo Admin token
echo   This is the password for the operator panel.
echo   Choose something only you know - anyone with it controls the session
echo   and can spend against your OpenAI account. Minimum 8 characters.
:asktoken
for /f "usebackq delims=" %%t in (`powershell -NoProfile -Command ^
    "$s = Read-Host -AsSecureString '  Admin token'; " ^
    "[Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim()"`) do set "ADMIN_TOKEN=%%t"

call :strlen ADMIN_TOKEN LEN
if !LEN! LSS 8 (
    echo   Too short - use at least 8 characters.
    goto :asktoken
)
if /i "!ADMIN_TOKEN!"=="changeme" ( echo   That token is guessable. Pick another. & goto :asktoken )
if /i "!ADMIN_TOKEN!"=="password" ( echo   That token is guessable. Pick another. & goto :asktoken )
if /i "!ADMIN_TOKEN!"=="admin"    ( echo   That token is guessable. Pick another. & goto :asktoken )
if /i "!ADMIN_TOKEN!"=="relay"    ( echo   That token is guessable. Pick another. & goto :asktoken )

for /f "usebackq delims=" %%c in (`powershell -NoProfile -Command ^
    "$s = Read-Host -AsSecureString '  Confirm admin token'; " ^
    "[Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($s)).Trim()"`) do set "CONFIRM=%%c"
if not "!ADMIN_TOKEN!"=="!CONFIRM!" (
    echo   Tokens did not match - try again.
    goto :asktoken
)
echo.

REM The panel is served over HTTPS and its certificate has to name whatever the
REM operator's browser will dial. The LAN address is found at every start (it
REM changes with the venue); a hostname does not, so it is asked for once here
REM and stored in config.json.
echo Panel hostname ^(optional^)
echo   The operator panel is served over HTTPS. Its certificate always covers
echo   this machine's LAN address. If you also reach this host by a hostname,
echo   type it now so the certificate covers that too.
echo   Leave blank to use the IP address only.
set "ADMIN_FQDN="
set /p ADMIN_FQDN="  Hostname (FQDN), or blank: "
echo.

wsl -- bash -lc "cd '!PROJDIR!' && RELAY_UID=$(id -u) RELAY_GID=$(id -g) OPENAI_KEY='!OPENAI_KEY!' ADMIN_TOKEN='!ADMIN_TOKEN!' RELAY_ADMIN_FQDN='!ADMIN_FQDN!' docker compose -f docker/docker-compose.yml run --rm --no-deps -e OPENAI_KEY -e ADMIN_TOKEN -e RELAY_ADMIN_FQDN relay python tools/write_config.py"
if errorlevel 1 (
    echo   Could not write docker-config\config.json - see the output above.
    goto :fail
)

set "OPENAI_KEY="
set "ADMIN_TOKEN="
set "CONFIRM="

REM ----------------------------------------------------------------- tls
REM Minted here rather than left to the first start so the operator reads the
REM fingerprint now, while they are still at the keyboard, instead of hunting
REM for it in a scrolling log on event day. Every start reuses it while it
REM still covers the current address and has 30+ days left.
:tls
for /f "usebackq delims=" %%i in (`powershell -NoProfile -Command ^
    "(Get-NetIPConfiguration | Where-Object {$_.IPv4DefaultGateway -ne $null} | Select-Object -First 1).IPv4Address.IPAddress"`) do set "LAN_IP=%%i"
if "!LAN_IP!"=="" set "LAN_IP=127.0.0.1"

echo Operator panel certificate
echo   Certifying this machine's LAN address, !LAN_IP!.
echo.
wsl -- bash -lc "cd '!PROJDIR!' && RELAY_UID=$(id -u) RELAY_GID=$(id -g) RELAY_ADMIN_IPS='!LAN_IP!' docker compose -f docker/docker-compose.yml run --rm --no-deps -e RELAY_ADMIN_IPS relay python tools/setup_caddy.py"
if errorlevel 1 (
    echo   Could not generate the certificate - see the output above.
    goto :fail
)

echo.
echo Setup complete.
echo   Next: double-click start.bat to launch Relay.
echo   Write down the SHA-256 fingerprint above - you check it against the
echo   browser the first time you open the panel.
echo   To change the key, the token or the hostname later, open the operator
echo   panel or run this again.
echo.
pause
exit /b 0

:strlen
setlocal enabledelayedexpansion
set "s=!%~1!"
set L=0
:strlen_loop
if defined s (
    set "s=!s:~1!"
    set /a L+=1
    goto :strlen_loop
)
endlocal & set "%~2=%L%"
exit /b 0

:fail
echo.
echo Setup did not finish.
pause
exit /b 1
