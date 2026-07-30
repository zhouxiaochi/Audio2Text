#!/bin/sh
set -eu

uvicorn backend.api:app --host 127.0.0.1 --port 8000 --workers 1 &
backend_pid=$!

cleanup() {
  kill "$backend_pid" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

cd /app/frontend
PORT="${PORT:-8080}" HOSTNAME=0.0.0.0 node server.js &
frontend_pid=$!

wait "$frontend_pid"
