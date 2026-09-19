"""Stripe and product-analytics webhook ingestion."""

from __future__ import annotations

import hashlib
import json
import os
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import hash_api_key
from src.billing import INGEST_LIMIT, limiter, raise_if_inactive
from src.database import get_db
from src.models_db import IdempotentEvent, IngestionJob, Organization, utcnow
from src.tasks import process_ingestion_job

router = APIRouter(tags=["ingestion"])


def _insecure_webhooks_allowed() -> bool:
    return os.getenv("ALLOW_INSECURE_WEBHOOKS", "").lower() in {"1", "true", "yes"}


def verify_stripe_signature(
    payload: bytes,
    sig_header: Optional[str],
    secret: Optional[str],
) -> dict[str, Any]:
    """Verify Stripe-Signature using stripe.Webhook.construct_event."""
    if _insecure_webhooks_allowed() and not secret:
        try:
            return json.loads(payload.decode("utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid JSON: {exc}") from exc
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Stripe webhook secret not configured",
        )
    if not sig_header:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing Stripe-Signature header",
        )
    try:
        import stripe

        event = stripe.Webhook.construct_event(payload, sig_header, secret)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Stripe signature verification failed: {exc}",
        ) from exc
    if hasattr(event, "to_dict"):
        return event.to_dict()
    return dict(event)


def _resolve_org(
    db: Session,
    x_org_id: Optional[str],
    x_api_key: Optional[str],
    metadata: Optional[dict] = None,
    write_key: Optional[str] = None,
    *,
    enforce_billing: bool = True,
    stripe_customer_id: Optional[str] = None,
) -> Organization:
    org_id = x_org_id or (metadata or {}).get("org_id")
    org: Optional[Organization] = None
    if org_id:
        org = db.query(Organization).filter(Organization.org_id == org_id).one_or_none()
    if org is None:
        key = x_api_key or write_key
        if key:
            org = db.query(Organization).filter(Organization.api_key == hash_api_key(key)).one_or_none()
    if org is None and stripe_customer_id:
        org = (
            db.query(Organization)
            .filter(Organization.stripe_customer_id == str(stripe_customer_id))
            .one_or_none()
        )
    if org is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Unable to resolve organization")
    if enforce_billing:
        raise_if_inactive(org)
    return org


def extract_event_id(payload: dict[str, Any], source: str) -> str:
    explicit = payload.get("id") or payload.get("event_id") or payload.get("messageId") or payload.get("message_id")
    if explicit:
        return str(explicit)[:128]
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return f"{source}_{digest[:40]}"


def _stripe_customer_hint(payload: dict[str, Any]) -> Optional[str]:
    obj = (payload.get("data") or {}).get("object") or payload.get("object") or {}
    customer = obj.get("customer") or obj.get("customer_id")
    if isinstance(customer, dict):
        customer = customer.get("id")
    if customer:
        return str(customer)
    return None


def claim_idempotent_event(db: Session, org_id: str, event_id: str, source: str) -> bool:
    """Return True if this event is newly claimed; False if it is a duplicate."""
    existing = (
        db.query(IdempotentEvent)
        .filter(IdempotentEvent.org_id == org_id, IdempotentEvent.event_id == event_id)
        .one_or_none()
    )
    if existing:
        return False
    db.add(
        IdempotentEvent(
            org_id=org_id,
            event_id=event_id,
            source=source,
            created_at=utcnow(),
        )
    )
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return False
    return True


def _enqueue_job(
    db: Session,
    org: Organization,
    source: str,
    payload: dict[str, Any],
) -> IngestionJob:
    job = IngestionJob(
        org_id=org.org_id,
        source=source,
        payload=payload,
        status="queued",
        created_at=utcnow(),
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    process_ingestion_job.delay(job.id)
    return job


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
    message_id: Optional[str] = Field(default=None, alias="messageId")
    event_id: Optional[str] = None


@router.post("/webhooks/telemetry")
@limiter.limit(INGEST_LIMIT)
def telemetry_webhook(
    request: Request,
    payload: TelemetryBatch,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> Any:
    """Persist a job for PostHog/Segment-style events and return 202 immediately."""
    org = _resolve_org(
        db,
        x_org_id or payload.org_id,
        x_api_key,
        write_key=payload.write_key,
    )
    body = payload.model_dump(by_alias=True)
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
    if not items and not body.get("batch"):
        raise HTTPException(status_code=400, detail="No telemetry events in payload")
    if items:
        body["batch"] = [
            {
                "userId": item.user_id,
                "user_id": item.user_id,
                "event": item.event,
                "timestamp": item.timestamp,
                "properties": item.properties,
            }
            for item in items
        ]
    event_id = extract_event_id(
        {
            "id": payload.event_id or payload.message_id,
            "event_id": payload.event_id,
            "messageId": payload.message_id,
            "batch": body.get("batch"),
        },
        "telemetry",
    )
    if not claim_idempotent_event(db, org.org_id, event_id, "telemetry"):
        return JSONResponse(
            status_code=200,
            content={"status": "skipped", "reason": "duplicate", "event_id": event_id, "org_id": org.org_id},
        )
    job = _enqueue_job(db, org, "telemetry", body)
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "accepted": True,
            "job_id": job.id,
            "status": "queued",
            "org_id": org.org_id,
            "event_id": event_id,
        },
    )
