"""Row-level security tenant isolation and pool context reset."""

from __future__ import annotations

from src.database import get_session_factory, init_db, reset_engine
from src.models_db import CustomerAccount, Organization
from src.rls import (
    RLS_TABLES,
    clear_tenant_context,
    reset_rls_on_dbapi,
    set_tenant_context,
)
from src.seed_commercial_demo import ACME_ORG_ID, GLOBEX_ORG_ID, seed_commercial_demo


def test_rls_session_cannot_read_foreign_org(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rls.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    try:
        set_tenant_context(session, ACME_ORG_ID)
        acme = session.query(CustomerAccount).filter(CustomerAccount.org_id == ACME_ORG_ID).all()
        globex = session.query(CustomerAccount).filter(CustomerAccount.org_id == GLOBEX_ORG_ID).all()
        assert acme
        assert globex == []
        leaked = session.query(CustomerAccount).all()
        assert leaked
        assert all(row.org_id == ACME_ORG_ID for row in leaked)
    finally:
        clear_tenant_context(session)
        session.close()
    reset_engine()


def test_rls_context_clears_between_sessions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rls_pool.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    factory = get_session_factory()
    first = factory()
    set_tenant_context(first, ACME_ORG_ID)
    assert first.info.get("rls_org_id") == ACME_ORG_ID
    clear_tenant_context(first)
    assert first.info.get("rls_org_id") in {None, ""}
    first.close()
    second = factory()
    assert not second.info.get("rls_org_id")
    both = {row.org_id for row in second.query(CustomerAccount).all()}
    assert ACME_ORG_ID in both and GLOBEX_ORG_ID in both
    second.close()
    reset_engine()


def test_rls_table_list_covers_required_models() -> None:
    assert "customer_accounts" in RLS_TABLES
    assert "telemetry_events" in RLS_TABLES
    assert "churn_assessments" in RLS_TABLES
    assert "dispatched_actions" in RLS_TABLES
    assert "system_audit_logs" in RLS_TABLES
    assert "prospect_leads" in RLS_TABLES
    assert "outbound_campaigns" in RLS_TABLES


def test_pool_checkin_reset_helper_is_noop_on_sqlite(tmp_path, monkeypatch) -> None:
    class _Cursor:
        def execute(self, sql):
            raise AssertionError("sqlite should not RESET GUCs")

        def close(self):
            return None

    class _Conn:
        def cursor(self):
            return _Cursor()

    reset_rls_on_dbapi(None, "postgresql")
    reset_rls_on_dbapi(_Conn(), "sqlite")
    # postgres path swallows missing GUC rather than raising
    class _PgCursor:
        def __init__(self):
            self.executed = []

        def execute(self, sql):
            self.executed.append(sql)

        def close(self):
            return None

    class _PgConn:
        def __init__(self):
            self.cur = _PgCursor()

        def cursor(self):
            return self.cur

    conn = _PgConn()
    reset_rls_on_dbapi(conn, "postgresql")
    assert conn.cur.executed
    assert "RESET app.current_org_id" in conn.cur.executed[0]


def test_organizations_remain_visible_under_rls(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'rls_org.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    try:
        set_tenant_context(session, ACME_ORG_ID)
        names = {row.org_id for row in session.query(Organization).all()}
        assert ACME_ORG_ID in names and GLOBEX_ORG_ID in names
    finally:
        clear_tenant_context(session)
        session.close()
    reset_engine()
