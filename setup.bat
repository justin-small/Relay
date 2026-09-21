@echo off
REM Relay - one-time setup for Windows. Double-click this before the first run.
REM
REM Creates the virtualenv, installs dependencies, then writes config.json with
REM your OpenAI API key and an admin token of your choosing. It does not start
REM the server - use start.bat for that.
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo Relay - setup
echo =============
echo.

REM ------------------------------------------------------------- python
python --version >nul 2>&1
if errorlevel 1 (
    echo   Python is not installed, or not on PATH.
    echo   Install it from https://www.python.org/downloads/
    echo   Tick "Add python.exe to PATH" in the installer, then run this again.
    goto :fail
)

python -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)"
if errorlevel 1 (
    echo   Python 3.10 or newer is required.
    python --version
    goto :fail
)
for /f "tokens=*" %%v in ('python --version') do echo %%v - ok

REM --------------------------------------------------------- virtualenv
if exist .venv (
    echo Virtualenv already exists - updating dependencies...
) else (
    echo Creating virtualenv...
    python -m venv .venv
    if errorlevel 1 ( echo   Could not create .venv & goto :fail )
)

.venv\Scripts\python -m pip install -q --upgrade pip
if errorlevel 1 ( echo   pip upgrade failed & goto :fail )
echo Installing dependencies ^(this can take a minute^)...
.venv\Scripts\pip install -q -r requirements.txt
if errorlevel 1 ( echo   Dependency install failed & goto :fail )
echo Dependencies - ok
echo.

REM ------------------------------------------------------------- config
if exist config.json (
    echo config.json already exists.
    set /p ANS="Reconfigure the API key and admin token? [y/N] "
    if /i not "!ANS!"=="y" (
        echo.
        echo Setup complete. Run start.bat to launch.
        goto :done
    )
    echo.
)

echo OpenAI API key
echo   Create one at https://platform.openai.com/api-keys
echo   It is stored only in config.json on this machine.
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
echo   This is the password for the operator panel on port 8001.
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

.venv\Scripts\python tools\write_config.py
if errorlevel 1 goto :fail

set "OPENAI_KEY="
set "ADMIN_TOKEN="
set "CONFIRM="

echo.
echo Setup complete.
echo   Next: double-click start.bat to launch Relay.
echo   To change these later, open the operator panel or run this again.

:done
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
