"""PostgreSQL RLS policies and session-scoped tenant context."""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Generator
from typing import Optional

from fastapi import Request
from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, with_loader_criteria

from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DeadLetterJob,
    DispatchedAction,
    IngestionJob,
    Organization,
    OutboundCampaign,
    ProspectLead,
    SystemAuditLog,
    TelemetryEvent,
)

LOGGER = logging.getLogger(__name__)

RLS_TABLES = (
    "customer_accounts",
    "telemetry_events",
    "churn_assessments",
    "dispatched_actions",
    "system_audit_logs",
    "prospect_leads",
    "outbound_campaigns",
    "dead_letter_jobs",
    "ingestion_jobs",
)
RLS_MODELS = (
    CustomerAccount,
    TelemetryEvent,
    ChurnAssessment,
    DispatchedAction,
    SystemAuditLog,
    ProspectLead,
    OutboundCampaign,
    DeadLetterJob,
    IngestionJob,
)
_ORM_RLS_REGISTERED = False


def apply_postgres_rls(engine: Engine) -> None:
    """ENABLE ROW LEVEL SECURITY + org_id = current_setting('app.current_org_id')."""
    if engine.dialect.name != "postgresql":
        return
    statements = []
    for table in RLS_TABLES:
        statements.append(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        statements.append(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        statements.append(f"DROP POLICY IF EXISTS tenant_org_isolation ON {table}")
        statements.append(
            f"""
            CREATE POLICY tenant_org_isolation ON {table}
            FOR ALL
            USING (org_id = current_setting('app.current_org_id', true))
            WITH CHECK (org_id = current_setting('app.current_org_id', true))
            """
        )
    with engine.begin() as conn:
        for stmt in statements:
            conn.execute(text(stmt))
    LOGGER.info("PostgreSQL RLS policies applied on %s", ", ".join(RLS_TABLES))


def register_pool_rls_reset(engine: Engine) -> None:
    """RESET GUC on checkin so pooled connections cannot leak tenant context."""
    if getattr(engine, "_rls_checkin_registered", False):
        return

    @event.listens_for(engine, "checkin")
    def _reset_rls_on_checkin(dbapi_connection, _connection_record) -> None:  # type: ignore[no-untyped-def]
        reset_rls_on_dbapi(dbapi_connection, engine.dialect.name)

    engine._rls_checkin_registered = True  # type: ignore[attr-defined]


def reset_rls_on_dbapi(dbapi_connection, dialect_name):
    if dbapi_connection is None:
        return
    if dialect_name != "postgresql":
        return
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("RESET app.current_org_id;")
    finally:
        cursor.close()
        dbapi_connection.rollback()  # THIS IS THE CRITICAL FIX


def set_tenant_context(session: Session, org_id: Optional[str]) -> None:
    """Bind this Session to a tenant. SET LOCAL on Postgres; ORM criteria on SQLite."""
    session.info["rls_org_id"] = org_id
    if not org_id:
        return
    try:
        bind = session.connection()
    except Exception:
        return
    if bind.dialect.name == "postgresql":
        bind.execute(text("SELECT set_config('app.current_org_id', :oid, true)"), {"oid": org_id})


def clear_tenant_context(session: Session) -> None:
    session.info["rls_org_id"] = None
    try:
        bind = session.connection()
    except Exception:
        return
    if bind.dialect.name == "postgresql":
        bind.execute(text("RESET app.current_org_id"))


def register_orm_rls_listener() -> None:
    """SQLite / test stand-in: filter org-scoped models when rls_org_id is set."""
    global _ORM_RLS_REGISTERED
    if _ORM_RLS_REGISTERED:
        return

    @event.listens_for(Session, "do_orm_execute")
    def _apply_loader_criteria(execute_state) -> None:  # type: ignore[no-untyped-def]
        if not execute_state.is_select:
            return
        org_id = execute_state.session.info.get("rls_org_id")
        if not org_id:
            return
        options = [
            with_loader_criteria(model, lambda cls, oid=org_id: cls.org_id == oid, include_aliases=True)
            for model in RLS_MODELS
        ]
        execute_state.statement = execute_state.statement.options(*options)

    _ORM_RLS_REGISTERED = True


def resolve_org_id_from_request(session: Session, request: Optional[Request]) -> Optional[str]:
    if request is None:
        return None
    org_header = request.headers.get("X-Org-Id") or request.headers.get("x-org-id")
    if org_header:
        exists = session.query(Organization.org_id).filter(Organization.org_id == org_header).one_or_none()
        if exists:
            return org_header
    authorization = request.headers.get("Authorization") or request.headers.get("authorization") or ""
    if authorization.lower().startswith("bearer "):
        from src.auth import extract_org_id_from_bearer

        jwt_org = extract_org_id_from_bearer(authorization.split(" ", 1)[1].strip())
        if jwt_org:
            exists = session.query(Organization.org_id).filter(Organization.org_id == jwt_org).one_or_none()
            if exists:
                return jwt_org
    api_key = request.headers.get("X-API-Key") or request.headers.get("x-api-key")
    if not api_key:
        return None
    digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    row = session.query(Organization.org_id).filter(Organization.api_key == digest).one_or_none()
    return row[0] if row else None
