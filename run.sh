#!/usr/bin/env bash
# CULTIVA — lance le backend FastAPI (qui sert aussi le frontend)
set -e
cd "$(dirname "$0")"
python3 -m pip install -r requirements.txt
cd backend
echo "→ CULTIVA sur http://127.0.0.1:8000/"
exec python3 -m uvicorn api:app --reload --port 8000
