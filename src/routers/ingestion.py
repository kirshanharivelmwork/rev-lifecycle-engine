"""Stripe and product-analytics webhook ingestion."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.auth import hash_api_key
from src.database import get_db
from src.models_db import IngestionJob, Organization, utcnow
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


def _enqueue_job(
    db: Session,
    org: Organization,
    source: str,
    payload: dict[str, Any],
    background: BackgroundTasks,
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
    background.add_task(process_ingestion_job, job.id)
    return job


@router.post("/webhooks/stripe", status_code=202)
async def stripe_webhook(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    stripe_signature: Optional[str] = Header(default=None, alias="Stripe-Signature"),
) -> dict[str, Any]:
    """Accept a Stripe event, verify signature, persist, and process asynchronously."""
    raw = await request.body()
    metadata_hint = {}
    try:
        preview = json.loads(raw.decode("utf-8"))
        obj = (preview.get("data") or {}).get("object") or {}
        metadata_hint = obj.get("metadata") or preview.get("metadata") or {}
    except json.JSONDecodeError:
        preview = {}
    org = _resolve_org(db, x_org_id, x_api_key, metadata=metadata_hint)
    secret = org.stripe_webhook_secret or os.getenv("STRIPE_WEBHOOK_SECRET")
    event = verify_stripe_signature(raw, stripe_signature, secret)
    job = _enqueue_job(db, org, "stripe", event, background)
    return {
        "ok": True,
        "accepted": True,
        "job_id": job.id,
        "status": "queued",
        "org_id": org.org_id,
        "event_type": event.get("type"),
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


@router.post("/webhooks/telemetry", status_code=202)
def telemetry_webhook(
    payload: TelemetryBatch,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
) -> dict[str, Any]:
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
    job = _enqueue_job(db, org, "telemetry", body, background)
    return {
        "ok": True,
        "accepted": True,
        "job_id": job.id,
        "status": "queued",
        "org_id": org.org_id,
    }
