"""Alembic helpers that are safe on empty and already-seeded databases.

Migration scripts live in ``migrations/`` (see ``alembic.ini`` ``script_location``)
so the local folder does not shadow the PyPI ``alembic`` package.
"""

from __future__ import annotations

import os
from typing import Optional

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from src.paths import DEFAULT_DATABASE_URL, PROJECT_ROOT


def _database_url(url: Optional[str] = None) -> str:
    return (url or os.getenv("DATABASE_URL") or DEFAULT_DATABASE_URL).strip()


def alembic_config(url: Optional[str] = None) -> Config:
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", _database_url(url))
    return cfg


def _table_names(url: str) -> set[str]:
    engine = create_engine(url, future=True)
    try:
        return set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def stamp_existing_then_upgrade(url: Optional[str] = None) -> dict[str, str]:
    """Stamp head when app tables already exist without alembic_version, then upgrade.

    Prevents `alembic upgrade head` from trying to recreate a seeded schema.
    Empty databases skip stamp and run upgrade normally.
    """
    db_url = _database_url(url)
    cfg = alembic_config(db_url)
    tables = _table_names(db_url)
    app_tables = tables - {"alembic_version", "sqlite_sequence"}
    stamped = "alembic_version" in tables
    action = "upgrade"
    if app_tables and not stamped:
        command.stamp(cfg, "head")
        action = "stamp_then_upgrade"
        stamped = True
    command.upgrade(cfg, "head")
    return {
        "action": action,
        "stamped": str(stamped).lower(),
        "url_scheme": db_url.split(":", 1)[0],
    }


def current_revision(url: Optional[str] = None) -> Optional[str]:
    db_url = _database_url(url)
    engine = create_engine(db_url, future=True)
    try:
        names = set(inspect(engine).get_table_names())
        if "alembic_version" not in names:
            return None
        with engine.connect() as conn:
            row = conn.execute(text("SELECT version_num FROM alembic_version")).first()
        return str(row[0]) if row else None
    finally:
        engine.dispose()
