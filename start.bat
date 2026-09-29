@echo off
REM PitchMirror one-click start (Windows). Needs Python 3.10+.
cd /d %~dp0
if not exist .venv (
  echo Creating virtual environment...
  python -m venv .venv || goto :error
)
call .venv\Scripts\activate.bat
pip install -q -r backend\requirements.txt || goto :error
if not exist .env copy .env.example .env >nul
if not exist frontend\dist\index.html (
  echo Building frontend (needs Node.js 18+)...
  pushd frontend && call npm install && call npm run build && popd
)
echo.
echo PitchMirror running at http://localhost:8000  (use Chrome or Edge)
cd backend
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
goto :eof
:error
echo Setup failed. See the message above.
pause
