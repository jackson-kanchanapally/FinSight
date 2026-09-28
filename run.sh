#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
backend_dir="$project_dir/backend"
frontend_dir="$project_dir/frontend"
python_bin="$backend_dir/.venv/bin/python"
vite_bin="$frontend_dir/node_modules/.bin/vite"

if [[ ! -f "$project_dir/.env" ]]; then
  echo "Missing .env. Copy backend/.env.example to .env and configure the database." >&2
  exit 1
fi
if [[ ! -x "$python_bin" ]]; then
  echo "Missing backend Python environment. Run: python3 -m venv backend/.venv && backend/.venv/bin/pip install -r backend/requirements.txt" >&2
  exit 1
fi
if [[ ! -x "$vite_bin" ]]; then
  echo "Missing frontend dependencies. Run: cd frontend && npm install" >&2
  exit 1
fi

"$python_bin" - <<'PY'
import socket

for port in (8000, 5173):
    with socket.socket() as listener:
        try:
            listener.bind(("127.0.0.1", port))
        except OSError:
            raise SystemExit(f"Port {port} is already in use. Stop the other server and run ./run.sh again.")
PY

database_ready() {
  (cd "$backend_dir" && "$python_bin" - <<'PY'
from sqlalchemy import text
from app.database import engine

try:
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
except Exception:
    raise SystemExit(1)
PY
  )
}

if ! database_ready; then
  if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "PostgreSQL is unavailable. Start the configured database or install Docker Compose." >&2
    exit 1
  fi
  echo "Starting PostgreSQL..."
  (cd "$project_dir" && docker compose up -d db)
  for attempt in {1..30}; do
    if database_ready; then
      break
    fi
    if (( attempt == 30 )); then
      echo "PostgreSQL did not become ready. Check docker compose logs db." >&2
      exit 1
    fi
    sleep 1
  done
fi

echo "Applying database migrations..."
(cd "$backend_dir" && "$python_bin" -m alembic upgrade head)

backend_pid=""
frontend_pid=""
cleanup() {
  trap - EXIT
  for pid in "$frontend_pid" "$backend_pid"; do
    if [[ -n "$pid" ]]; then
      kill "$pid" 2>/dev/null || true
    fi
  done
  for pid in "$frontend_pid" "$backend_pid"; do
    if [[ -n "$pid" ]]; then
      wait "$pid" 2>/dev/null || true
    fi
  done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

(cd "$backend_dir" && exec "$python_bin" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000) &
backend_pid=$!
(cd "$frontend_dir" && exec "$vite_bin" --host 127.0.0.1 --port 5173 --strictPort) &
frontend_pid=$!

echo "Almonds: http://127.0.0.1:5173"
echo "API docs: http://127.0.0.1:8000/docs"
echo "Press Ctrl+C to stop both servers."

while kill -0 "$backend_pid" 2>/dev/null && kill -0 "$frontend_pid" 2>/dev/null; do
  sleep 1
done
echo "A server stopped unexpectedly. Check the output above." >&2
exit 1
