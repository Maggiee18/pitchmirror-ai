@echo off
REM PitchMirror one-click start (Windows). Needs Python 3.10+ from python.org.
title PitchMirror
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto nopython

if not exist ".venv\Scripts\python.exe" (
  echo Creating virtual environment...
  python -m venv .venv
  if errorlevel 1 goto failed
)

echo Installing dependencies, first run takes a minute...
".venv\Scripts\python.exe" -m pip install -q -r backend\requirements.txt
if errorlevel 1 goto failed

if not exist ".env" copy ".env.example" ".env" >nul

if not exist "frontend\dist\index.html" goto nofrontend

echo.
echo PitchMirror running at http://localhost:8000  - open it in Chrome or Edge
echo Keep this window open. Press Ctrl+C to stop.
echo.
cd backend
"..\.venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
echo.
echo The server stopped.
pause
goto :eof

:nopython
echo Python was not found. Install Python 3.11 from https://www.python.org/downloads/
echo and tick "Add python.exe to PATH" during setup, then run this again.
pause
goto :eof

:nofrontend
echo The built frontend is missing: frontend\dist\index.html
echo Install Node.js 18 or newer, then run: cd frontend, npm install, npm run build
pause
goto :eof

:failed
echo.
echo Setup failed. Read the message above, or send a screenshot of this window.
pause
