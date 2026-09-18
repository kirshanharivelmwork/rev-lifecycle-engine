"""Shared account upsert / Stripe transform helpers (imported by tasks + routers)."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from src.feature_builder import parse_timestamp
from src.models_db import CustomerAccount, Organization, TelemetryEvent, utcnow


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


def mrr_from_subscription(obj: dict[str, Any]) -> tuple[float, str]:
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


def apply_stripe_event(db: Session, org: Organization, payload: dict[str, Any]) -> CustomerAccount:
    event_type = payload.get("type") or payload.get("event")
    obj = (payload.get("data") or {}).get("object") or payload.get("object") or {}
    metadata = obj.get("metadata") or payload.get("metadata") or {}
    customer_id = obj.get("customer") or obj.get("id")
    if event_type == "customer.created":
        customer_id = obj.get("id")
    if not customer_id:
        raise ValueError("Stripe object missing customer id")

    channel = metadata.get("channel") or metadata.get("acquisition_channel")
    contract = metadata.get("contract_type")
    mrr = None
    if event_type in {"customer.subscription.updated", "customer.subscription.created"}:
        mrr, inferred_contract = mrr_from_subscription(obj)
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
    return account


def persist_telemetry_items(db: Session, org: Organization, payload: dict[str, Any]) -> int:
    items = payload.get("batch") or []
    if payload.get("event") and (payload.get("user_id") or payload.get("userId")):
        items = [
            {
                "user_id": payload.get("user_id") or payload.get("userId"),
                "event": payload.get("event"),
                "timestamp": payload.get("timestamp"),
                "properties": payload.get("properties") or {},
            }
        ]
    persisted = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        external_id = item.get("user_id") or item.get("userId") or str((item.get("properties") or {}).get("user_id") or "")
        event_name = item.get("event") or item.get("event_name")
        if not external_id or not event_name:
            continue
        account = upsert_customer_account(db, org, str(external_id))
        db.add(
            TelemetryEvent(
                org_id=org.org_id,
                customer_account_id=account.id,
                event_name=str(event_name),
                timestamp=parse_timestamp(item.get("timestamp")) if item.get("timestamp") else utcnow(),
                properties=item.get("properties") or {},
            )
        )
        persisted += 1
    db.flush()
    return persisted
