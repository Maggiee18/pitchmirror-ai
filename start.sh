#!/usr/bin/env bash
# PitchMirror one-command start (macOS/Linux). Needs Python 3.10+.
set -e
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
pip install -q -r backend/requirements.txt
[ -f .env ] || cp .env.example .env
if [ ! -f frontend/dist/index.html ]; then
  (cd frontend && npm install && npm run build)
fi
echo "PitchMirror running at http://localhost:8000 (use Chrome or Edge)"
cd backend && exec python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
