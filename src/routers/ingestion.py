"""Stripe and product-analytics webhook ingestion."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.auth import hash_api_key
from src.database import get_db
from src.dispatcher import evaluate_and_trigger_actions
from src.feature_builder import parse_timestamp
from src.models_db import CustomerAccount, Organization, TelemetryEvent, utcnow

router = APIRouter(tags=["ingestion"])


def _resolve_org(
    db: Session,
    x_org_id: Optional[str],
    x_api_key: Optional[str],
    metadata: Optional[dict] = None,
    write_key: Optional[str] = None,
) -> Organization:
    org_id = x_org_id or (metadata or {}).get("org_id")
    if org_id:
        org = db.query(Organization).filter(Organization.org_id == org_id).one_or_none()
        if org:
            return org
    key = x_api_key or write_key
    if key:
        org = db.query(Organization).filter(Organization.api_key == hash_api_key(key)).one_or_none()
        if org:
            return org
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to resolve organization")


def upsert_customer_account(
    db: Session,
    org: Organization,
    external_id: str,
    *,
    channel: Optional[str] = None,
    mrr: Optional[float] = None,
    contract_type: Optional[str] = None,
) -> CustomerAccount:
    account = (
        db.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == org.org_id,
            CustomerAccount.customer_external_id == external_id,
        )
        .one_or_none()
    )
    if account is None:
        account = CustomerAccount(
            org_id=org.org_id,
            customer_external_id=external_id,
            channel=channel or "Paid Search",
            mrr=float(mrr or 0.0),
            contract_type=contract_type or "Monthly",
        )
        db.add(account)
        db.flush()
        return account
    if channel:
        account.channel = channel
    if mrr is not None:
        account.mrr = float(mrr)
    if contract_type:
        account.contract_type = contract_type
    db.flush()
    return account


def _mrr_from_subscription(obj: dict[str, Any]) -> tuple[float, str]:
    items = ((obj.get("items") or {}).get("data") or [])
    if not items:
        plan = obj.get("plan") or {}
        amount = float(plan.get("amount") or plan.get("unit_amount") or 0) / 100.0
        interval = (plan.get("interval") or "month").lower()
        contract = "Annual" if interval in {"year", "annual"} else "Monthly"
        if interval == "year":
            amount = amount / 12.0
        return amount, contract
    price = items[0].get("price") or items[0].get("plan") or {}
    unit = float(price.get("unit_amount") or price.get("amount") or 0) / 100.0
    recurring = price.get("recurring") or {}
    interval = (recurring.get("interval") or price.get("interval") or "month").lower()
    qty = float(items[0].get("quantity") or 1)
    mrr = unit * qty
    contract = "Annual" if interval in {"year", "annual"} else "Monthly"
    if interval == "year":
        mrr = mrr / 12.0
    return round(mrr, 2), contract


@router.post("/webhooks/stripe")
def stripe_webhook(
    payload: dict[str, Any],
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Ingest Stripe customer / subscription / invoice events and upsert accounts."""
    event_type = payload.get("type") or payload.get("event")
    obj = (payload.get("data") or {}).get("object") or payload.get("object") or {}
    metadata = obj.get("metadata") or payload.get("metadata") or {}
    org = _resolve_org(db, x_org_id, x_api_key, metadata=metadata)

    customer_id = obj.get("customer") or obj.get("id")
    if event_type == "customer.created":
        customer_id = obj.get("id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="Stripe object missing customer id")

    channel = metadata.get("channel") or metadata.get("acquisition_channel")
    contract = metadata.get("contract_type")
    mrr = None
    if event_type in {"customer.subscription.updated", "customer.subscription.created"}:
        mrr, inferred_contract = _mrr_from_subscription(obj)
        contract = contract or inferred_contract
    if event_type == "customer.created":
        mrr = float(metadata.get("mrr") or 0.0)

    account = upsert_customer_account(
        db,
        org,
        str(customer_id),
        channel=channel,
        mrr=mrr,
        contract_type=contract,
    )

    if event_type == "invoice.payment_failed":
        db.add(
            TelemetryEvent(
                org_id=org.org_id,
                customer_account_id=account.id,
                event_name="invoice.payment_failed",
                timestamp=utcnow(),
                properties={"amount_due": obj.get("amount_due"), "stripe_event": event_type},
            )
        )
        db.flush()
        try:
            evaluate_and_trigger_actions(account.id, org.org_id, session=db)
        except Exception:
            pass

    if event_type in {"customer.subscription.updated", "customer.subscription.created"}:
        try:
            evaluate_and_trigger_actions(account.id, org.org_id, session=db)
        except Exception:
            pass

    return {
        "ok": True,
        "event_type": event_type,
        "org_id": org.org_id,
        "account_id": account.id,
        "customer_external_id": account.customer_external_id,
        "mrr": account.mrr,
        "contract_type": account.contract_type,
    }


class TelemetryItem(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    user_id: Optional[str] = Field(default=None, alias="userId")
    event: str
    timestamp: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    org_id: Optional[str] = None
    write_key: Optional[str] = Field(default=None, alias="writeKey")
    batch: Optional[list[TelemetryItem]] = None
    user_id: Optional[str] = Field(default=None, alias="userId")
    event: Optional[str] = None
    timestamp: Optional[str] = None
    properties: dict[str, Any] = Field(default_factory=dict)


@router.post("/webhooks/telemetry")
def telemetry_webhook(
    payload: TelemetryBatch,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
    """Persist PostHog/Segment-style product events for a tenant."""
    org = _resolve_org(
        db,
        x_org_id or payload.org_id,
        x_api_key,
        write_key=payload.write_key,
    )
    items = payload.batch or []
    if payload.event and (payload.user_id or (payload.properties or {}).get("user_id")):
        items = [
            TelemetryItem(
                userId=payload.user_id,
                event=payload.event,
                timestamp=payload.timestamp,
                properties=payload.properties,
            )
        ]
    if not items:
        raise HTTPException(status_code=400, detail="No telemetry events in payload")

    persisted = 0
    for item in items:
        external_id = item.user_id or str((item.properties or {}).get("user_id") or "")
        if not external_id:
            continue
        account = upsert_customer_account(db, org, external_id)
        db.add(
            TelemetryEvent(
                org_id=org.org_id,
                customer_account_id=account.id,
                event_name=item.event,
                timestamp=parse_timestamp(item.timestamp) if item.timestamp else utcnow(),
                properties=item.properties or {},
            )
        )
        persisted += 1
    db.flush()
    return {"ok": True, "org_id": org.org_id, "ingested": persisted}
