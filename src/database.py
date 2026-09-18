"""SQLAlchemy engine, sessions, and schema bootstrap.

PostgreSQL when DATABASE_URL starts with postgresql; otherwise SQLite with
foreign keys enabled for zero-config local development.
"""

from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.models_db import Base
from src.paths import DEFAULT_DATABASE_URL, SQLITE_DB_PATH

_engine: Engine | None = None
_SessionFactory: sessionmaker[Session] | None = None


def database_url() -> str:
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


def _sqlite_connect_args(url: str) -> dict:
    if url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = database_url()
        if url.startswith("sqlite") and "memory" not in url:
            SQLITE_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(url, future=True, connect_args=_sqlite_connect_args(url))
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False, future=True)
    return _SessionFactory


def get_db() -> Generator[Session, None, None]:
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def _ensure_column(engine: Engine, table: str, column: str, ddl: str) -> None:
    inspector = inspect(engine)
    if table not in inspector.get_table_names():
        return
    existing = {col["name"] for col in inspector.get_columns(table)}
    if column in existing:
        return
    with engine.begin() as conn:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {ddl}"))


def migrate_schema(engine: Engine) -> None:
    """Add newly introduced columns/tables without a full Alembic stack."""
    Base.metadata.create_all(bind=engine)
    _ensure_column(engine, "organizations", "slack_webhook_url", "slack_webhook_url TEXT")
    _ensure_column(engine, "organizations", "stripe_webhook_secret", "stripe_webhook_secret TEXT")
    _ensure_column(engine, "organizations", "resend_api_key", "resend_api_key TEXT")
    _ensure_column(engine, "organizations", "alert_cooldown_days", "alert_cooldown_days INTEGER DEFAULT 14")
    _ensure_column(engine, "organizations", "hitl_mrr_threshold", "hitl_mrr_threshold FLOAT DEFAULT 1000")
    _ensure_column(engine, "customer_accounts", "last_contacted_at", "last_contacted_at DATETIME")
    _ensure_column(engine, "customer_accounts", "cooldown_days", "cooldown_days INTEGER DEFAULT 14")
    _ensure_column(engine, "customer_accounts", "suppressed_until", "suppressed_until DATETIME")
    _ensure_column(engine, "customer_accounts", "contract_renewal_at", "contract_renewal_at DATETIME")
    _ensure_column(engine, "customer_accounts", "approval_status", "approval_status VARCHAR(32) DEFAULT 'none'")


def init_db() -> Engine:
    engine = get_engine()
    migrate_schema(engine)
    if database_url().startswith("sqlite"):
        with engine.connect() as conn:
            conn.execute(text("PRAGMA foreign_keys=ON"))
            conn.commit()
    return engine


def reset_engine() -> None:
    """Drop cached engine/session (used by tests when DATABASE_URL changes)."""
    global _engine, _SessionFactory
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionFactory = None
