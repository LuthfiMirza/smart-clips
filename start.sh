#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
echo "🚀 Menjalankan Smart-Clips AI Studio di http://127.0.0.1:8088 ..."
./.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8088 --reload
