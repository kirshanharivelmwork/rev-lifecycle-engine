"""Per-plan caps for customer accounts, prospect leads, and daily ingest runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.entitlements import FREE_PLAN, is_free_plan
from src.models_db import CustomerAccount, IngestionJob, Organization, ProspectLead, utcnow

DEFAULT_PLAN = "growth"
INGEST_RUN_SOURCES = frozenset({"csv", "historical_backfill"})
CSV_SOURCE = "csv"

GROWTH_QUOTAS = {
    "customer_accounts": 250,
    "prospect_leads": 100,
    "ingest_runs_per_utc_day": 15,
    "csv_ingests_per_calendar_month": 15,
}

# Growth defaults. Seeded demo orgs (50 accounts, 4 prospects) stay under these caps.
PLAN_QUOTAS: dict[str, dict[str, int]] = {
    FREE_PLAN: {
        "customer_accounts": 50,
        "prospect_leads": 0,
        "ingest_runs_per_utc_day": 1,
        "csv_ingests_per_calendar_month": 1,
    },
    "growth": dict(GROWTH_QUOTAS),
    "pro": dict(GROWTH_QUOTAS),
}

LIMIT_CUSTOMER_ACCOUNTS = "customer_accounts"
LIMIT_PROSPECT_LEADS = "prospect_leads"
LIMIT_INGEST_RUNS = "ingest_runs_per_utc_day"
LIMIT_CSV_MONTHLY = "csv_ingests_per_calendar_month"


class PlanLimitExceeded(Exception):
    """Raised when a tenant would exceed Organization.plan_tier caps."""

    def __init__(self, limit: str, used: int, max_value: int) -> None:
        super().__init__(limit)
        self.limit = limit
        self.used = int(used)
        self.max = int(max_value)

    def as_dict(self) -> dict[str, Any]:
        return {"error": "plan_limit", "limit": self.limit, "used": self.used, "max": self.max}


def plan_limit_response(exc: PlanLimitExceeded) -> JSONResponse:
    return JSONResponse(status_code=403, content=exc.as_dict())


def limits_for_plan(plan_tier: str | None) -> dict[str, int]:
    key = (plan_tier or DEFAULT_PLAN).strip().lower() or DEFAULT_PLAN
    if is_free_plan(key):
        return dict(PLAN_QUOTAS[FREE_PLAN])
    return dict(PLAN_QUOTAS.get(key) or PLAN_QUOTAS[DEFAULT_PLAN])


def utc_day_start(now: datetime | None = None) -> datetime:
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is not None:
        current = current.astimezone(timezone.utc).replace(tzinfo=None)
    return current.replace(hour=0, minute=0, second=0, microsecond=0)


def count_customer_accounts(session: Session, org_id: str) -> int:
    return session.query(CustomerAccount).filter(CustomerAccount.org_id == org_id).count()


def count_prospect_leads(session: Session, org_id: str) -> int:
    return session.query(ProspectLead).filter(ProspectLead.org_id == org_id).count()


def count_ingest_runs_today(session: Session, org_id: str, *, now: datetime | None = None) -> int:
    start = utc_day_start(now)
    return (
        session.query(IngestionJob)
        .filter(
            IngestionJob.org_id == org_id,
            IngestionJob.source.in_(tuple(INGEST_RUN_SOURCES)),
            IngestionJob.created_at >= start,
        )
        .count()
    )


def utc_month_start(now: datetime | None = None) -> datetime:
    current = utc_day_start(now)
    return current.replace(day=1)


def count_csv_ingests_this_month(session: Session, org_id: str, *, now: datetime | None = None) -> int:
    start = utc_month_start(now)
    result = session.execute(
        text(
            "SELECT COUNT(*) FROM ingestion_jobs "
            "WHERE org_id = :org_id AND source = :source AND created_at >= :start"
        ),
        {"org_id": org_id, "source": CSV_SOURCE, "start": start},
    )
    return int(result.scalar() or 0)


def quota_snapshot(session: Session, org: Organization) -> dict[str, Any]:
    limits = limits_for_plan(org.plan_tier)
    accounts = count_customer_accounts(session, org.org_id)
    prospects = count_prospect_leads(session, org.org_id)
    ingest = count_ingest_runs_today(session, org.org_id)
    csv_month = count_csv_ingests_this_month(session, org.org_id)
    snapshot = {
        "customer_accounts": _meter(accounts, limits[LIMIT_CUSTOMER_ACCOUNTS]),
        "prospect_leads": _meter(prospects, limits[LIMIT_PROSPECT_LEADS]),
        "ingest_runs_per_utc_day": _meter(ingest, limits[LIMIT_INGEST_RUNS]),
        "csv_ingests_per_calendar_month": _meter(csv_month, limits[LIMIT_CSV_MONTHLY]),
    }
    if is_free_plan(org.plan_tier):
        snapshot["ingest_runs_per_utc_day"] = snapshot["csv_ingests_per_calendar_month"]
    return snapshot


def _meter(used: int, maximum: int) -> dict[str, int]:
    return {"used": used, "max": maximum, "remaining": max(0, maximum - used)}


def ensure_customer_account_quota(session: Session, org: Organization, additional: int = 1) -> None:
    if additional <= 0:
        return
    used = count_customer_accounts(session, org.org_id)
    maximum = limits_for_plan(org.plan_tier)[LIMIT_CUSTOMER_ACCOUNTS]
    if used + additional > maximum:
        raise PlanLimitExceeded(LIMIT_CUSTOMER_ACCOUNTS, used, maximum)


def ensure_prospect_lead_quota(session: Session, org: Organization, additional: int = 1) -> None:
    if additional <= 0:
        return
    used = count_prospect_leads(session, org.org_id)
    maximum = limits_for_plan(org.plan_tier)[LIMIT_PROSPECT_LEADS]
    if used + additional > maximum:
        raise PlanLimitExceeded(LIMIT_PROSPECT_LEADS, used, maximum)


def ensure_ingest_run_quota(session: Session, org: Organization, additional: int = 1) -> None:
    if additional <= 0:
        return
    if is_free_plan(org.plan_tier):
        used = count_csv_ingests_this_month(session, org.org_id)
        maximum = limits_for_plan(org.plan_tier)[LIMIT_CSV_MONTHLY]
        if used + additional > maximum:
            raise PlanLimitExceeded(LIMIT_CSV_MONTHLY, used, maximum)
        return
    used = count_ingest_runs_today(session, org.org_id)
    maximum = limits_for_plan(org.plan_tier)[LIMIT_INGEST_RUNS]
    if used + additional > maximum:
        raise PlanLimitExceeded(LIMIT_INGEST_RUNS, used, maximum)


def record_ingest_run(
    session: Session,
    org: Organization,
    source: str,
    *,
    status: str = "queued",
    payload: dict[str, Any] | None = None,
) -> IngestionJob:
    job = IngestionJob(
        org_id=org.org_id,
        source=source,
        payload=payload or {},
        status=status,
        created_at=utcnow(),
        completed_at=utcnow() if status in {"completed", "scored", "skipped"} else None,
    )
    session.add(job)
    session.flush()
    return job
