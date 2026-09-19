#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
export PYTHONPATH="${PYTHONPATH:-$ROOT}"
export REDIS_URL="redis://default:gSXJZXJBCVKPDJmSsBAMWBEjTKyAVoct@redis.railway.internal:6379"

# Railway injects PORT for public HTTP. FastAPI stays on an internal port that
# matches Next.js rewrites (http://127.0.0.1:8000 baked at image build).
PORT="${PORT:-3000}"
API_PORT="${API_PORT:-8000}"
export PORT
export API_PORT
export API_INTERNAL_URL="${API_INTERNAL_URL:-http://127.0.0.1:${API_PORT}}"
RUN_CELERY="${RUN_CELERY:-1}"

API_PID=""
WORKER_PID=""

cleanup() {
  if [[ -n "${API_PID}" ]]; then
    kill "${API_PID}" 2>/dev/null || true
  fi
  if [[ -n "${WORKER_PID}" ]]; then
    kill "${WORKER_PID}" 2>/dev/null || true
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

uvicorn src.api:app --host 0.0.0.0 --port "${API_PORT}" &
API_PID=$!

python3 - <<PY
import time
import urllib.request

url = "http://127.0.0.1:${API_PORT}/health"
last = None
for _ in range(40):
    try:
        with urllib.request.urlopen(url, timeout=1) as response:
            if response.status < 500:
                raise SystemExit(0)
    except SystemExit:
        raise
    except Exception as exc:
        last = exc
        time.sleep(0.25)
raise SystemExit(f"FastAPI did not become ready on {url}: {last}")
PY

if [[ "${RUN_CELERY}" == "1" || "${RUN_CELERY}" == "true" ]]; then
  celery -A src.worker.celery_app worker --loglevel=info --pool=solo &
  WORKER_PID=$!
fi

cd "$ROOT/frontend"
# Bind the public Next.js server to Railway's $PORT on all interfaces.
export HOSTNAME=0.0.0.0
npm start -- -H 0.0.0.0 -p "${PORT}"
