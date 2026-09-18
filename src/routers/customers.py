"""GDPR / CCPA hard-deletion of a tenant customer."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.auth import get_current_org
from src.database import get_db
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DispatchedAction,
    InterventionOutcome,
    Organization,
    TelemetryEvent,
)

router = APIRouter(tags=["customers"])


def purge_customer_account(db: Session, org: Organization, customer_external_id: str) -> int:
    account = (
        db.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == org.org_id,
            CustomerAccount.customer_external_id == customer_external_id,
        )
        .one_or_none()
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Customer not found")
    account_id = account.id
    db.query(InterventionOutcome).filter(InterventionOutcome.customer_account_id == account_id).delete(
        synchronize_session=False
    )
    db.query(DispatchedAction).filter(DispatchedAction.customer_account_id == account_id).delete(
        synchronize_session=False
    )
    db.query(ChurnAssessment).filter(ChurnAssessment.customer_account_id == account_id).delete(
        synchronize_session=False
    )
    db.query(TelemetryEvent).filter(TelemetryEvent.customer_account_id == account_id).delete(
        synchronize_session=False
    )
    db.query(CustomerAccount).filter(CustomerAccount.id == account_id).delete(synchronize_session=False)
    db.flush()
    return 1


@router.delete("/customers/{customer_external_id}")
def delete_customer(
    customer_external_id: str,
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict:
    purge_customer_account(db, org, customer_external_id)
    return {"ok": True, "deleted": customer_external_id, "org_id": org.org_id}
