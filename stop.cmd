@echo off
cd /d "%~dp0"
if not exist server.pid exit /b 0
set /p SIGNAL_PID=<server.pid
taskkill /PID %SIGNAL_PID% /T >nul 2>&1
del /q server.pid
echo SIGNAL stopped. Remote SearXNG was not changed.
