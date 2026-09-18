"""System audit log helpers for tenant policy and HITL actions."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from src.models_db import Organization, SystemAuditLog, utcnow


def record_audit(
    session: Session,
    org_id: str,
    actor_id: str,
    action: str,
    old_value: Optional[Any] = None,
    new_value: Optional[Any] = None,
) -> SystemAuditLog:
    entry = SystemAuditLog(
        org_id=org_id,
        actor_id=actor_id,
        action=action,
        old_value=old_value,
        new_value=new_value,
        created_at=utcnow(),
    )
    session.add(entry)
    session.flush()
    return entry


def update_tenant_policy(
    session: Session,
    org: Organization,
    *,
    actor_id: str = "dashboard",
    alert_cooldown_days: Optional[int] = None,
    hitl_mrr_threshold: Optional[float] = None,
    stripe_webhook_secret: Optional[str] = None,
    slack_webhook_url: Optional[str] = None,
    resend_api_key: Optional[str] = None,
) -> Organization:
    old = {
        "alert_cooldown_days": org.alert_cooldown_days,
        "hitl_mrr_threshold": org.hitl_mrr_threshold,
    }
    if alert_cooldown_days is not None:
        org.alert_cooldown_days = int(alert_cooldown_days)
    if hitl_mrr_threshold is not None:
        org.hitl_mrr_threshold = float(hitl_mrr_threshold)
    if stripe_webhook_secret:
        org.stripe_webhook_secret = stripe_webhook_secret
    if slack_webhook_url:
        org.slack_webhook_url = slack_webhook_url
    if resend_api_key:
        org.resend_api_key = resend_api_key
    new = {
        "alert_cooldown_days": org.alert_cooldown_days,
        "hitl_mrr_threshold": org.hitl_mrr_threshold,
    }
    record_audit(
        session,
        org.org_id,
        actor_id,
        "tenant_settings.update",
        old_value=old,
        new_value=new,
    )
    session.flush()
    return org
