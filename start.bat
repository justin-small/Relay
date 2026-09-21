@echo off
REM Relay - start the server on Windows. Run setup.bat first.
cd /d "%~dp0"

if not exist .venv\Scripts\python.exe (
    echo Relay is not set up yet.
    echo   Double-click setup.bat first - it installs the dependencies
    echo   and asks for your OpenAI API key and admin token.
    echo.
    pause
    exit /b 1
)

if not exist config.json (
    echo config.json is missing.
    echo   Double-click setup.bat to create it.
    echo.
    pause
    exit /b 1
)

.venv\Scripts\python run.py
pause
