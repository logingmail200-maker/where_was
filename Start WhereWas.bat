@echo off
setlocal
title WhereWas
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "APP_URL=http://127.0.0.1:8000"

rem If WhereWas is already running, just open it.
powershell -NoProfile -Command "try { $r=Invoke-WebRequest -UseBasicParsing '%APP_URL%/api/health' -TimeoutSec 2; if($r.StatusCode -eq 200){exit 0} } catch {}; exit 1" >nul 2>&1
if not errorlevel 1 (
  start "" "%APP_URL%"
  exit /b 0
)

if not exist ".venv\Scripts\python.exe" (
  echo.
  echo   Setting up WhereWas for the first run...
  where py >nul 2>&1
  if not errorlevel 1 (
    py -3 -m venv .venv
  ) else (
    python -m venv .venv
  )
  if errorlevel 1 goto :setup_error
)

if not exist ".venv\.wherewas-ready" (
  echo   Installing required components...
  ".venv\Scripts\python.exe" -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 goto :setup_error
  ".venv\Scripts\python.exe" -m playwright install chromium
  if errorlevel 1 echo   Browser helper skipped; the main app will still work.
  > ".venv\.wherewas-ready" echo ready
)

echo.
echo   WhereWas is starting...
echo   Keep this window open while you use the app.
echo   Press Ctrl+C here to stop it.
echo.

start "WhereWas Browser" /b powershell -NoProfile -WindowStyle Hidden -Command "$u='%APP_URL%'; for($i=0;$i -lt 80;$i++){ try { $r=Invoke-WebRequest -UseBasicParsing ($u + '/api/health') -TimeoutSec 1; if($r.StatusCode -eq 200){ Start-Process $u; break } } catch {}; Start-Sleep -Milliseconds 500 }"
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
exit /b %errorlevel%

:setup_error
echo.
echo   Setup could not finish. Check your internet connection and Python installation.
pause
exit /b 1
