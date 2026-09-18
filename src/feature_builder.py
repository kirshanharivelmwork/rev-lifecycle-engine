"""Roll live telemetry + billing fields into the production model frame."""

from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from src.models_db import CustomerAccount, TelemetryEvent, utcnow


def parse_timestamp(value: object) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, (int, float)):
        return datetime.utcfromtimestamp(float(value))
    if isinstance(value, str) and value:
        cleaned = value.replace("Z", "+00:00")
        try:
            parsed = datetime.fromisoformat(cleaned)
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            pass
    return utcnow()


def build_feature_row(session: Session, account: CustomerAccount, now: datetime | None = None) -> pd.Series:
    """Aggregate TelemetryEvent history into the churn-model feature schema."""
    now = now or utcnow()
    events = (
        session.query(TelemetryEvent)
        .filter(
            TelemetryEvent.org_id == account.org_id,
            TelemetryEvent.customer_account_id == account.id,
        )
        .all()
    )

    logins = [e for e in events if e.event_name in {"login", "session_start"}]
    tickets = [e for e in events if e.event_name in {"ticket_opened", "support_ticket"}]
    features = [e for e in events if e.event_name in {"feature_used", "feature_adopted"}]
    failures = [e for e in events if e.event_name in {"invoice.payment_failed", "payment_failed"}]

    last_login = max((e.timestamp for e in logins), default=None)
    days_since = (now - last_login).days if last_login else 45
    week_ago = now - timedelta(days=7)
    avg_weekly_logins = float(sum(1 for e in logins if e.timestamp >= week_ago))
    unique_features = {
        str((e.properties or {}).get("feature") or (e.properties or {}).get("name") or e.id) for e in features
    }
    adoption = min(10.0, 1.5 + 1.4 * len(unique_features))
    if days_since > 21:
        adoption = max(0.0, adoption - 2.5)

    channel = account.channel if account.channel else "Paid Search"
    contract = account.contract_type if account.contract_type in {"Monthly", "Annual"} else "Monthly"

    return pd.Series(
        {
            "customer_id": account.customer_external_id,
            "acquisition_channel": channel,
            "contract_type": contract,
            "avg_weekly_logins": avg_weekly_logins,
            "feature_adoption_score": round(adoption, 2),
            "support_tickets_raised": len(tickets) + len(failures),
            "days_since_last_login": max(int(days_since), 0),
            "monthly_recurring_revenue": float(account.mrr or 0.0),
            "cac_usd": 500.0,
            "sales_touchpoints": 4,
        }
    )
