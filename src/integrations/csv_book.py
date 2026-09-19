"""Combined CSV book ingest for the 48-hour diagnostic (no Stripe secret required)."""

from __future__ import annotations

import hashlib
import io
from typing import Any

import pandas as pd
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from src.data_pipeline import LEAD_SCHEMA, TELEMETRY_SCHEMA, SchemaValidationError
from src.dispatcher import _score_account
from src.models_db import ChurnAssessment, CustomerAccount, IdempotentEvent, Organization, TelemetryEvent, utcnow
from src.paths import ACQUISITION_CHANNELS, CONTRACT_TYPES
from src.quotas import ensure_customer_account_quota, ensure_ingest_run_quota, record_ingest_run
from src.routers.account_ops import upsert_customer_account
from src.routers.ingestion import claim_idempotent_event

CSV_SNAPSHOT_EVENT = "csv_snapshot"
COMBINED_COLUMNS = tuple(dict.fromkeys([*LEAD_SCHEMA, *TELEMETRY_SCHEMA]))
COLUMN_ALIASES = {
    "user_id": "customer_id",
    "customer_external_id": "customer_id",
    "channel": "acquisition_channel",
    "mrr": "monthly_recurring_revenue",
    "mrr_usd": "monthly_recurring_revenue",
}


def csv_digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _rename_aliases(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {}
    lowered = {str(col).strip().lower(): col for col in frame.columns}
    for alias, canonical in COLUMN_ALIASES.items():
        if canonical not in frame.columns and alias in lowered:
            rename[lowered[alias]] = canonical
    return frame.rename(columns=rename)


def parse_combined_csv(payload: bytes) -> pd.DataFrame:
    if not payload or not payload.strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV file is empty")
    try:
        frame = pd.read_csv(io.BytesIO(payload))
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unable to parse CSV: {exc}") from exc
    frame = _rename_aliases(frame)
    required = [col for col in COMBINED_COLUMNS if col != "churned"]
    missing = [col for col in required if col not in frame.columns]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"CSV missing required columns: {missing}",
        )
    try:
        leads = frame.loc[:, [col for col in LEAD_SCHEMA if col in frame.columns]].copy()
        telemetry_cols = [col for col in TELEMETRY_SCHEMA if col in frame.columns]
        telemetry = frame.loc[:, telemetry_cols].copy()
        if "churned" not in telemetry.columns:
            telemetry["churned"] = 0
        from src.data_pipeline import _validate

        leads = _validate(leads, LEAD_SCHEMA, "csv_leads")
        telemetry = _validate(telemetry, TELEMETRY_SCHEMA, "csv_telemetry")
    except SchemaValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    merged = leads.merge(telemetry, on="customer_id", how="inner", validate="one_to_one")
    if merged.empty:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="CSV contains no customer rows")
    return merged


def _normalize_channel(value: Any) -> str:
    text = str(value or "").strip()
    if text in ACQUISITION_CHANNELS:
        return text
    return "Paid Search"


def _normalize_contract(value: Any) -> str:
    text = str(value or "").strip()
    if text in CONTRACT_TYPES:
        return text
    return "Monthly"


def _replace_snapshot(session: Session, account: CustomerAccount, properties: dict[str, Any]) -> None:
    (
        session.query(TelemetryEvent)
        .filter(
            TelemetryEvent.org_id == account.org_id,
            TelemetryEvent.customer_account_id == account.id,
            TelemetryEvent.event_name == CSV_SNAPSHOT_EVENT,
        )
        .delete(synchronize_session=False)
    )
    session.add(
        TelemetryEvent(
            org_id=account.org_id,
            customer_account_id=account.id,
            event_name=CSV_SNAPSHOT_EVENT,
            timestamp=utcnow(),
            properties=properties,
        )
    )


def ingest_combined_csv(
    session: Session,
    org: Organization,
    payload: bytes,
    *,
    engine=None,
) -> dict[str, Any]:
    digest = csv_digest(payload)
    event_id = f"csv_{digest}"
    frame = parse_combined_csv(payload)
    existing_accounts = session.query(CustomerAccount).filter(CustomerAccount.org_id == org.org_id).count()
    existing_assessments = session.query(ChurnAssessment).filter(ChurnAssessment.org_id == org.org_id).count()
    already = (
        session.query(IdempotentEvent)
        .filter(IdempotentEvent.org_id == org.org_id, IdempotentEvent.event_id == event_id)
        .one_or_none()
    )
    if already:
        return {
            "ok": True,
            "accepted": False,
            "status": "skipped",
            "reason": "duplicate",
            "org_id": org.org_id,
            "event_id": event_id,
            "accounts_upserted": 0,
            "assessments_written": 0,
            "account_count": existing_accounts,
            "assessment_count": existing_assessments,
        }
    incoming_ids = {str(row["customer_id"]) for row in frame.to_dict(orient="records")}
    known = {
        row.customer_external_id
        for row in session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == org.org_id, CustomerAccount.customer_external_id.in_(incoming_ids))
        .all()
    }
    ensure_ingest_run_quota(session, org, additional=1)
    ensure_customer_account_quota(session, org, additional=len(incoming_ids - known))
    claimed = claim_idempotent_event(session, org.org_id, event_id, "csv")
    if not claimed:
        return {
            "ok": True,
            "accepted": False,
            "status": "skipped",
            "reason": "duplicate",
            "org_id": org.org_id,
            "event_id": event_id,
            "accounts_upserted": 0,
            "assessments_written": 0,
            "account_count": existing_accounts,
            "assessment_count": existing_assessments,
        }
    record_ingest_run(session, org, "csv", status="scored", payload={"event_id": event_id})
    if engine is None:
        from src.churn_model import load_or_train

        engine, _ = load_or_train(tune=False, persist=True)

    upserted: list[CustomerAccount] = []
    for row in frame.to_dict(orient="records"):
        account = upsert_customer_account(
            session,
            org,
            str(row["customer_id"]),
            channel=_normalize_channel(row.get("acquisition_channel")),
            mrr=float(row.get("monthly_recurring_revenue") or 0.0),
            contract_type=_normalize_contract(row.get("contract_type")),
        )
        snapshot = {
            "source": "csv_book",
            "digest": digest,
            "customer_id": str(row["customer_id"]),
            "acquisition_channel": account.channel,
            "contract_type": account.contract_type,
            "avg_weekly_logins": float(row["avg_weekly_logins"]),
            "feature_adoption_score": float(row["feature_adoption_score"]),
            "support_tickets_raised": int(row["support_tickets_raised"]),
            "days_since_last_login": int(row["days_since_last_login"]),
            "monthly_recurring_revenue": float(account.mrr),
            "cac_usd": float(row.get("cac_usd") or 500.0),
            "sales_touchpoints": int(row.get("sales_touchpoints") or 4),
            "churned": int(row.get("churned") or 0),
        }
        _replace_snapshot(session, account, snapshot)
        upserted.append(account)

    scored = 0
    for account in upserted:
        _score_account(session, account, engine)
        scored += 1
    session.flush()
    return {
        "ok": True,
        "accepted": True,
        "status": "scored",
        "org_id": org.org_id,
        "event_id": event_id,
        "accounts_upserted": len(upserted),
        "assessments_written": scored,
        "account_count": session.query(CustomerAccount).filter(CustomerAccount.org_id == org.org_id).count(),
        "assessment_count": session.query(ChurnAssessment).filter(ChurnAssessment.org_id == org.org_id).count(),
    }
