#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
cd "$project_dir"
docker compose up -d db
cd "$project_dir/backend"
python_bin="$project_dir/backend/.venv/bin/python"
"$python_bin" - <<'PY'
from app.config import settings
if not settings.research_automation_enabled or settings.event_source_mode != "live":
    raise SystemExit("Production tracking requires RESEARCH_AUTOMATION_ENABLED=true and EVENT_SOURCE_MODE=live")
if settings.market_data_provider != "nse_mcp":
    raise SystemExit("Production tracking requires the existing free nse_mcp market provider")
PY
"$python_bin" -m alembic upgrade head
exec "$python_bin" -m uvicorn app.production:app --host 127.0.0.1 --port 8000 --workers 1
