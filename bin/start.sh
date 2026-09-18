#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"
export STREAMLIT_BROWSER_GATHER_USAGE_STATS="${STREAMLIT_BROWSER_GATHER_USAGE_STATS:-false}"

API_PID=""
UI_PID=""

cleanup() {
  if [[ -n "${API_PID}" ]]; then
    kill "${API_PID}" 2>/dev/null || true
  fi
  if [[ -n "${UI_PID}" ]]; then
    kill "${UI_PID}" 2>/dev/null || true
  fi
}

trap cleanup SIGTERM SIGINT EXIT

python3 - <<'PY'
import os
import time

from sqlalchemy import create_engine, text

from src.database import get_session_factory, init_db, reset_engine
from src.models_db import Organization
from src.seed_commercial_demo import seed_commercial_demo

url = os.getenv("DATABASE_URL", "")
if url.startswith("postgresql"):
    last = None
    for _ in range(40):
        try:
            engine = create_engine(url, future=True)
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            engine.dispose()
            last = None
            break
        except Exception as exc:  # pragma: no cover - startup wait
            last = exc
            time.sleep(1)
    if last is not None:
        raise SystemExit(f"PostgreSQL not ready: {last}")

reset_engine()
init_db()
session = get_session_factory()()
try:
    if session.query(Organization).count() == 0:
        session.close()
        seed_commercial_demo()
    else:
        session.close()
except Exception:
    session.close()
    raise
PY

uvicorn src.api:app --host 0.0.0.0 --port 8000 &
API_PID=$!

streamlit run app/dashboard.py --server.port 8501 --server.address 0.0.0.0 --server.headless true &
UI_PID=$!

wait -n "${API_PID}" "${UI_PID}" || true
wait || true
