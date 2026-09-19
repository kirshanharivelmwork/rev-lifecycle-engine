"""Wipe application tables (TRUNCATE CASCADE) and re-seed a Clerk tenant.

Intended for Railway PostgreSQL when a previous seed used a placeholder org id.

  DATABASE_URL=postgresql://... python -m src.reset_and_seed \\
      --org-id org_3JYHuxSaQRSYUXw7ZDafXD9phV2 --confirm-wipe

Refuses to run without --confirm-wipe. Does not run SQLite databases.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from urllib.parse import urlparse, urlunparse

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text

from src.database import database_url, get_engine, init_db, reset_engine
from src.models_db import Base
from src.seed_commercial_demo import resolve_org_id, seed_commercial_demo

LOGGER = logging.getLogger(__name__)

# Live Clerk organization that must own production rows (override with --org-id).
RAILWAY_CLERK_ORG_ID = "org_3JYHuxSaQRSYUXw7ZDafXD9phV2"


def _redact_database_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.password:
        netloc = parsed.netloc.replace(f":{parsed.password}", ":***", 1)
        parsed = parsed._replace(netloc=netloc)
    return urlunparse(parsed)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="TRUNCATE all mapped tables CASCADE, then seed a Clerk org."
    )
    parser.add_argument(
        "--org-id",
        default=None,
        help="Clerk organization id (default: CLERK_ORG_ID or the Railway Clerk org)",
    )
    parser.add_argument(
        "--confirm-wipe",
        action="store_true",
        help="Required. Permanently deletes all rows in application tables.",
    )
    parser.add_argument(
        "--keep-globex",
        action="store_true",
        help="Also seed the secondary org_globex isolation tenant",
    )
    return parser.parse_args(argv)


def truncate_all_tables() -> None:
    engine = get_engine()
    names = [table.name for table in reversed(Base.metadata.sorted_tables)]
    if not names:
        raise RuntimeError("No SQLAlchemy tables registered")
    quoted = ", ".join(f'"{name}"' for name in names)
    LOGGER.warning("TRUNCATE %s RESTART IDENTITY CASCADE", quoted)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {quoted} RESTART IDENTITY CASCADE"))


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args(argv)
    url = database_url()
    if not args.confirm_wipe:
        raise SystemExit(
            "Refusing to wipe the database. Re-run with --confirm-wipe after "
            f"checking DATABASE_URL={_redact_database_url(url)}"
        )
    if not url.startswith("postgresql"):
        raise SystemExit(
            f"This helper only truncates PostgreSQL (got {_redact_database_url(url)})."
        )

    org_id = resolve_org_id(args.org_id or os.getenv("CLERK_ORG_ID") or RAILWAY_CLERK_ORG_ID)
    LOGGER.warning("Wiping %s then seeding org_id=%s", _redact_database_url(url), org_id)

    reset_engine()
    init_db()
    truncate_all_tables()
    keys = seed_commercial_demo(org_id, seed_globex=args.keep_globex)
    print(f"Reset complete. Primary org_id={keys['org_id']}")


if __name__ == "__main__":
    main()
