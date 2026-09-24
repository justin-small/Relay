@echo off
REM Live Caption Relay on Windows - follow the running relay's log.
REM
REM The relay runs detached, so this is where its output went. Closing this
REM window stops following the log; it does not stop the relay.

echo Following the relay's log. Close this window to stop following it.
echo.
docker logs -f --tail 200 live-caption-relay
echo.
pause
