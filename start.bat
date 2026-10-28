@echo off
REM ---------------------------------------------------------------
REM  AI Bookkeeping Assistant - launcher
REM  ASCII only on purpose: cmd.exe reads .bat with the OEM codepage
REM  (GBK on Chinese Windows), so UTF-8 Chinese text here becomes
REM  garbage and can even break "@echo off". Keep this file ASCII.
REM ---------------------------------------------------------------
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
  echo [1/2] Creating virtual environment...
  python -m venv .venv
  echo [2/2] Installing dependencies...
  .venv\Scripts\python.exe -m pip install -r backend\requirements.txt
)

echo.
echo   AI Bookkeeping Assistant
echo   Open in browser:  http://127.0.0.1:8000
echo   Press Ctrl+C to stop.
echo.

start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --app-dir backend
