#!/usr/bin/env bash
set -euo pipefail

# Postgres dump / restore for Rev Lifecycle Engine.
# Usage:
#   bin/backup-postgres.sh dump  [file]
#   bin/backup-postgres.sh restore <file>

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL is required" >&2
  exit 1
fi

ACTION="${1:-dump}"
FILE="${2:-backup.dump}"

case "$ACTION" in
  dump)
    pg_dump "$DATABASE_URL" -Fc -f "$FILE"
    echo "Wrote $FILE"
    ;;
  restore)
    if [[ ! -f "$FILE" ]]; then
      echo "Backup file not found: $FILE" >&2
      exit 1
    fi
    pg_restore --clean --if-exists --no-owner -d "$DATABASE_URL" "$FILE"
    echo "Restored $FILE"
    ;;
  *)
    echo "Usage: bin/backup-postgres.sh dump|restore [file]" >&2
    exit 1
    ;;
esac
