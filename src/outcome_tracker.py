"""Empirical intervention attribution for finance / CFO reporting."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.database import get_session_factory
from src.models_db import CustomerAccount, DispatchedAction, InterventionOutcome, utcnow


def _lifecycle_status(current_mrr: float, initial_mrr: float) -> str:
    if current_mrr <= 0.01:
        return "Churned"
    if current_mrr < initial_mrr * 0.85:
        return "Downgraded"
    return "Active"


def _arr_saved(status: Optional[str], initial_mrr: float) -> float:
    if status == "Active":
        return round(initial_mrr * 12.0, 2)
    if status == "Downgraded":
        return round(initial_mrr * 12.0 * 0.5, 2)
    return 0.0


def record_intervention_outcome(
    session: Session,
    account: CustomerAccount,
    action: DispatchedAction,
) -> InterventionOutcome:
    existing = (
        session.query(InterventionOutcome)
        .filter(InterventionOutcome.dispatched_action_id == action.id)
        .one_or_none()
    )
    if existing:
        return existing
    outcome = InterventionOutcome(
        org_id=account.org_id,
        customer_account_id=account.id,
        dispatched_action_id=action.id,
        intervention_date=action.created_at or utcnow(),
        initial_mrr=float(account.mrr or 0.0),
        verified_arr_saved=0.0,
        attributed=False,
    )
    session.add(outcome)
    session.flush()
    return outcome


def audit_intervention_outcomes(
    org_id: str,
    session: Optional[Session] = None,
    now=None,
) -> dict[str, Any]:
    """Roll 30/60/90-day statuses and verified ARR saved for a tenant."""
    owns = session is None
    if session is None:
        session = get_session_factory()()
    now = now or utcnow()
    try:
        outcomes = (
            session.query(InterventionOutcome).filter(InterventionOutcome.org_id == org_id).all()
        )
        verified = 0.0
        attributed_n = 0
        for outcome in outcomes:
            account = session.get(CustomerAccount, outcome.customer_account_id)
            current_mrr = float(account.mrr) if account else 0.0
            status = _lifecycle_status(current_mrr, float(outcome.initial_mrr))
            age = (now - outcome.intervention_date).days
            if age >= 30 and not outcome.status_at_30d:
                outcome.status_at_30d = status
            if age >= 60 and not outcome.status_at_60d:
                outcome.status_at_60d = status
            if age >= 90 and not outcome.status_at_90d:
                outcome.status_at_90d = status
            terminal = outcome.status_at_90d or outcome.status_at_60d or outcome.status_at_30d
            if terminal:
                outcome.verified_arr_saved = _arr_saved(terminal, float(outcome.initial_mrr))
                outcome.attributed = True
                attributed_n += 1
                verified += outcome.verified_arr_saved
        if owns:
            session.commit()
        else:
            session.flush()
        return {
            "org_id": org_id,
            "outcomes": len(outcomes),
            "attributed": attributed_n,
            "verified_arr_saved": round(verified, 2),
        }
    except Exception:
        if owns:
            session.rollback()
        raise
    finally:
        if owns:
            session.close()
