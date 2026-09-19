"""Closed-loop Instantly ROI webhooks and Stripe tenant billing."""

from __future__ import annotations

import json
import os
from typing import Any, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Depends, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from src.billing import INGEST_LIMIT, limiter
from src.database import get_db
from src.models_db import OutboundCampaign, ProspectLead, utcnow
from src.routers.ingestion import (
    _enqueue_job,
    _resolve_org,
    _stripe_customer_hint,
    claim_idempotent_event,
    extract_event_id,
    verify_stripe_signature,
)
from src.security import decrypt_secret

router = APIRouter(tags=["webhooks"])

POSITIVE_INSTANTLY_EVENTS = {
    "reply",
    "reply_received",
    "positive_reply",
    "email_replied",
    "lead_replied",
    "lead.replied",
    "meeting_booked",
    "meeting_scheduled",
    "meeting_booked_via_link",
    "lead_interested",
    "interested",
    "opportunity",
}


def _nested_get(payload: dict[str, Any], *keys: str) -> Any:
    cur: Any = payload
    for key in keys:
        if not isinstance(cur, dict) or key not in cur:
            return None
        cur = cur[key]
    return cur


def _instantly_event_name(payload: dict[str, Any]) -> str:
    raw = (
        payload.get("event_type")
        or payload.get("event")
        or payload.get("type")
        or payload.get("label")
        or _nested_get(payload, "data", "event_type")
        or ""
    )
    return str(raw).strip().lower().replace(" ", "_")


def _is_positive_instantly_event(payload: dict[str, Any]) -> bool:
    name = _instantly_event_name(payload)
    if name in POSITIVE_INSTANTLY_EVENTS or "meeting" in name or name.endswith("_reply"):
        if name in {"auto_reply", "unsubscribe", "bounce", "complaint"}:
            return False
        return True
    reply = payload.get("reply") or payload.get("data") or {}
    if isinstance(reply, dict):
        sentiment = str(reply.get("sentiment") or reply.get("classification") or "").lower()
        if sentiment in {"positive", "interested", "meeting"}:
            return True
    if payload.get("is_interested") is True or payload.get("meeting_booked") is True:
        return True
    return False


def _lead_email(payload: dict[str, Any]) -> Optional[str]:
    lead = payload.get("lead") or payload.get("contact") or {}
    raw = (
        payload.get("email")
        or payload.get("to_email")
        or payload.get("lead_email")
        or (lead.get("email") if isinstance(lead, dict) else None)
        or _nested_get(payload, "data", "email")
    )
    if not raw:
        return None
    return str(raw).strip().lower()


def _custom_vars(payload: dict[str, Any]) -> dict[str, Any]:
    blob = (
        payload.get("custom_variables")
        or payload.get("customVariables")
        or payload.get("metadata")
        or payload.get("custom_params")
        or {}
    )
    return blob if isinstance(blob, dict) else {}


@router.post("/webhooks/instantly")
@limiter.limit(INGEST_LIMIT)
def instantly_webhook(
    request: Request,
    payload: dict[str, Any] = Body(...),
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    org_id: Optional[str] = Query(default=None),
) -> Any:
    """Closed-loop Instantly events: positive reply / meeting booked → ProspectLead.meeting_booked."""
    custom = _custom_vars(payload)
    metadata = {"org_id": org_id or custom.get("org_id") or payload.get("org_id")}
    org = _resolve_org(
        db,
        x_org_id or org_id or custom.get("org_id"),
        x_api_key,
        metadata=metadata,
        write_key=custom.get("write_key") or payload.get("api_key"),
        enforce_billing=False,
    )
    if not _is_positive_instantly_event(payload):
        return {
            "ok": True,
            "updated": False,
            "reason": "ignored_event",
            "event": _instantly_event_name(payload),
            "org_id": org.org_id,
        }
    email = _lead_email(payload)
    lead = None
    if email:
        lead = (
            db.query(ProspectLead)
            .filter(ProspectLead.org_id == org.org_id, ProspectLead.email == email)
            .one_or_none()
        )
    vendor_id = payload.get("lead_id") or payload.get("id") or _nested_get(payload, "lead", "id")
    if lead is None and vendor_id:
        campaign = (
            db.query(OutboundCampaign)
            .filter(
                OutboundCampaign.org_id == org.org_id,
                OutboundCampaign.vendor_lead_id == str(vendor_id),
            )
            .order_by(OutboundCampaign.created_at.desc())
            .first()
        )
        if campaign:
            lead = db.get(ProspectLead, campaign.prospect_lead_id)
    if lead is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Prospect lead not found")
    lead.status = "meeting_booked"
    lead.updated_at = utcnow()
    db.flush()
    return {
        "ok": True,
        "updated": True,
        "org_id": org.org_id,
        "lead_id": lead.id,
        "email": lead.email,
        "status": lead.status,
    }


@router.post("/webhooks/stripe")
@limiter.limit(INGEST_LIMIT)
async def stripe_webhook(
    request: Request,
    background: BackgroundTasks,
    db: Session = Depends(get_db),
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    stripe_signature: Optional[str] = Header(default=None, alias="Stripe-Signature"),
) -> Any:
    """Stripe billing + book ingestion. checkout.session.completed → active; invoice.payment_failed → past_due."""
    raw = await request.body()
    try:
        preview = json.loads(raw.decode("utf-8"))
        obj = (preview.get("data") or {}).get("object") or {}
        metadata_hint = obj.get("metadata") or preview.get("metadata") or {}
    except Exception:
        preview = {}
        metadata_hint = {}
    org = _resolve_org(
        db,
        x_org_id,
        x_api_key,
        metadata=metadata_hint,
        enforce_billing=False,
        stripe_customer_id=_stripe_customer_hint(preview),
    )
    secret = decrypt_secret(org.stripe_webhook_secret) or os.getenv("STRIPE_WEBHOOK_SECRET")
    event = verify_stripe_signature(raw, stripe_signature, secret)
    event_id = extract_event_id(event, "stripe")
    if not claim_idempotent_event(db, org.org_id, event_id, "stripe"):
        return JSONResponse(
            status_code=200,
            content={"status": "skipped", "reason": "duplicate", "event_id": event_id, "org_id": org.org_id},
        )
    job = _enqueue_job(db, org, "stripe", event, background)
    return JSONResponse(
        status_code=202,
        content={
            "ok": True,
            "accepted": True,
            "job_id": job.id,
            "status": "queued",
            "org_id": org.org_id,
            "event_type": event.get("type"),
            "event_id": event_id,
        },
    )
