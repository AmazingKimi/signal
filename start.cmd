@echo off
setlocal EnableExtensions DisableDelayedExpansion
cd /d "%~dp0"
echo == AMAZING KIMI / SIGNAL 0.8.4G ==

set "SEARXNG_BASE_URL="
if exist .env for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"SEARXNG_BASE_URL=" .env`) do set "SEARXNG_BASE_URL=%%B"

echo [Search] Checking configured SearXNG endpoint...
if not defined SEARXNG_BASE_URL (
  echo [Search] UNAVAILABLE - configure SEARXNG_BASE_URL in .env
) else (
  powershell -NoProfile -Command "try {$r=Invoke-WebRequest -UseBasicParsing -Uri ($env:SEARXNG_BASE_URL.TrimEnd('/') + '/') -TimeoutSec 3;if($r.Content -match 'SearXNG'){exit 0}else{exit 1}}catch{exit 1}"
  if errorlevel 1 (echo [Search] UNAVAILABLE) else (echo [Search] READY)
)

if not exist .venv\Scripts\python.exe (
  py -3.11 -m venv .venv 2>nul || python -m venv .venv || goto :python_error
)
echo [SIGNAL] Checking dependencies...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt || goto :dependency_error

echo [SIGNAL] Starting...
if not exist data mkdir data
powershell -NoProfile -WindowStyle Hidden -Command "$p=Start-Process -FilePath '.venv\Scripts\python.exe' -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','8765' -WorkingDirectory '%CD%' -WindowStyle Hidden -PassThru; Set-Content -Path 'server.pid' -Value $p.Id"
for /l %%I in (1,1,30) do (
  powershell -NoProfile -Command "try{Invoke-WebRequest -UseBasicParsing -Uri 'http://127.0.0.1:8765/api/health' -TimeoutSec 1 ^| Out-Null;exit 0}catch{exit 1}"
  if not errorlevel 1 goto :ready
  timeout /t 1 /nobreak >nul
)
echo [SIGNAL] Startup failed. See server.log.
exit /b 1

:ready
start "" "http://127.0.0.1:8765/?build=amazing-kimi-0.8.4g-parallels-searxng"
exit /b 0
:python_error
echo [SIGNAL] Compatible Python was not found.
exit /b 1
:dependency_error
echo [SIGNAL] Dependency installation failed.
exit /b 1
