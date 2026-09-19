"""Alembic upgrade head matches current SQLAlchemy models.

Scripts are loaded from ``migrations/`` so ``from alembic import command``
resolves the PyPI package, not a local directory.
"""

from __future__ import annotations

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect, text

from src.database import get_engine, get_session_factory, init_db, reset_engine
from src.models_db import Organization
from src.paths import PROJECT_ROOT
from src.schema_migrations import current_revision, stamp_existing_then_upgrade


def test_alembic_upgrade_head(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "alembic.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    reset_engine()
    cfg = Config(str(PROJECT_ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.upgrade(cfg, "head")
    session = get_session_factory()()
    assert session.query(Organization).count() == 0
    session.close()
    reset_engine()


def test_stamp_then_upgrade_on_existing_tables(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "seeded.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    init_db()
    session = get_session_factory()()
    session.add(Organization(org_id="org_seeded", name="Seeded", api_key="hashed-seeded", plan_tier="growth"))
    session.commit()
    session.close()

    names = set(inspect(get_engine()).get_table_names())
    assert "organizations" in names
    assert "alembic_version" not in names

    result = stamp_existing_then_upgrade(url)
    assert result["action"] == "stamp_then_upgrade"
    assert current_revision(url) == "001_initial"

    again = stamp_existing_then_upgrade(url)
    assert again["action"] == "upgrade"
    session = get_session_factory()()
    assert session.get(Organization, "org_seeded") is not None
    session.close()
    with get_engine().connect() as conn:
        version = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert version == "001_initial"
    reset_engine()
