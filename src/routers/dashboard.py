"""JWT-authenticated Command Center, HITL staging, and tenant settings APIs."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.audit import update_tenant_policy
from src.auth import AuthUser, get_current_org, get_current_user, require_admin_role
from src.database import get_db
from src.dispatcher import approve_pending_dispatch, dismiss_false_positive
from src.conversion_model import is_high_intent
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DispatchedAction,
    Organization,
    OutboundCampaign,
    ProspectLead,
    SystemAuditLog,
    utcnow,
)
from src.outcome_tracker import audit_intervention_outcomes
from src.paths import AT_RISK_THRESHOLD, HITL_MRR_THRESHOLD, INTERVENTION_SUCCESS_RATE, MODEL_VERSION
from src.tasks import trigger_outbound_engine
from src.urls import public_api_base, webhook_urls

router = APIRouter(tags=["dashboard"])

RISK_BINS = [
    "0.0–0.1",
    "0.1–0.2",
    "0.2–0.3",
    "0.3–0.4",
    "0.4–0.5",
    "0.5–0.6",
    "0.6–0.7",
    "0.7–0.8",
    "0.8–0.9",
    "0.9–1.0",
]


class TenantSettingsUpdate(BaseModel):
    alert_cooldown_days: Optional[int] = Field(default=None, ge=7, le=30)
    hitl_mrr_threshold: Optional[float] = Field(default=None, ge=0)
    stripe_webhook_secret: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    resend_api_key: Optional[str] = None
    hubspot_access_token: Optional[str] = None
    salesforce_access_token: Optional[str] = None
    salesforce_instance_url: Optional[str] = None
    apollo_api_key: Optional[str] = None
    instantly_api_key: Optional[str] = None


class OutboundRunRequest(BaseModel):
    search_params: Optional[dict[str, Any]] = None
    campaign_id: Optional[str] = None


def _latest_book(session: Session, org_id: str) -> list[dict[str, Any]]:
    accounts = session.query(CustomerAccount).filter(CustomerAccount.org_id == org_id).all()
    rows: list[dict[str, Any]] = []
    for account in accounts:
        latest = (
            session.query(ChurnAssessment)
            .filter(
                ChurnAssessment.org_id == org_id,
                ChurnAssessment.customer_account_id == account.id,
            )
            .order_by(ChurnAssessment.assessed_at.desc())
            .first()
        )
        rows.append(
            {
                "account_id": account.id,
                "customer_id": account.customer_external_id,
                "channel": account.channel,
                "mrr": float(account.mrr or 0.0),
                "arr": float(account.mrr or 0.0) * 12.0,
                "contract_type": account.contract_type,
                "churn_probability": float(latest.churn_probability) if latest else 0.0,
                "risk_tier": latest.risk_tier if latest else "Low",
                "recommended_action": latest.recommended_action if latest else "",
                "approval_status": account.approval_status,
            }
        )
    return rows


def _month_actions(session: Session, org_id: str) -> list[DispatchedAction]:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        session.query(DispatchedAction)
        .filter(DispatchedAction.org_id == org_id, DispatchedAction.created_at >= start)
        .order_by(DispatchedAction.created_at.desc())
        .all()
    )


def _bin_label(probability: float) -> str:
    clamped = min(max(float(probability), 0.0), 0.999999)
    index = min(int(clamped * 10), 9)
    return RISK_BINS[index]


def _risk_mix(book: list[dict[str, Any]]) -> list[dict[str, Any]]:
    counts = {label: {"bin": label, "Low": 0, "Medium": 0, "Critical": 0} for label in RISK_BINS}
    for row in book:
        label = _bin_label(row["churn_probability"])
        tier = row.get("risk_tier") or "Low"
        if tier not in {"Low", "Medium", "Critical"}:
            tier = "Low"
        counts[label][tier] += 1
    return [counts[label] for label in RISK_BINS]


def _tenant_payload(org: Organization) -> dict[str, Any]:
    return {
        "name": org.name,
        "org_id": org.org_id,
        "plan_tier": org.plan_tier,
        "model_version": MODEL_VERSION,
        "hitl_mrr_threshold": float(org.hitl_mrr_threshold or HITL_MRR_THRESHOLD),
        "alert_cooldown_days": int(org.alert_cooldown_days or 14),
        "subscription_status": org.subscription_status,
    }


def _integration_flags(org: Organization) -> dict[str, bool]:
    return {
        "stripe": bool(org.stripe_webhook_secret),
        "slack": bool(org.slack_webhook_url),
        "resend": bool(org.resend_api_key),
        "hubspot": bool(org.hubspot_access_token),
        "salesforce": bool(org.salesforce_access_token),
        "apollo": bool(org.apollo_api_key),
        "instantly": bool(org.instantly_api_key),
    }


@router.get("/command-center")
def get_command_center(
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    book = _latest_book(db, org.org_id)
    actions = _month_actions(db, org.org_id)
    at_risk = [row for row in book if row["churn_probability"] >= AT_RISK_THRESHOLD]
    audit = audit_intervention_outcomes(org.org_id, session=db)
    stream: list[dict[str, Any]] = []
    for action in actions[:40]:
        account = db.get(CustomerAccount, action.customer_account_id)
        payload = action.payload or {}
        arr_est = payload.get("mrr_saved_est")
        if arr_est is None and account is not None:
            arr_est = float(account.mrr or 0.0) * 12.0 * INTERVENTION_SUCCESS_RATE
        created = action.created_at
        stream.append(
            {
                "when": created.isoformat() if isinstance(created, datetime) else str(created),
                "customer_id": account.customer_external_id if account else action.customer_account_id,
                "channel": action.channel,
                "status": action.status,
                "arr_saved_est": float(arr_est or 0.0),
                "trigger_reason": payload.get("trigger_reason") or "",
            }
        )
    at_risk_sorted = sorted(at_risk, key=lambda row: row["churn_probability"], reverse=True)
    return {
        "tenant": {**_tenant_payload(org), "subscriber_count": len(book)},
        "kpis": {
            "active_arr": sum(row["arr"] for row in book),
            "at_risk_arr": sum(row["arr"] for row in at_risk),
            "verified_arr_saved": float(audit.get("verified_arr_saved") or 0.0),
            "dispatched_count": len(actions),
        },
        "action_stream": stream,
        "risk_mix": _risk_mix(book),
        "at_risk_book": at_risk_sorted,
    }


@router.get("/staging")
def get_staging_queue(
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    book = _latest_book(db, org.org_id)
    at_risk = [row for row in book if row["churn_probability"] >= AT_RISK_THRESHOLD]
    audit = audit_intervention_outcomes(org.org_id, session=db)
    pending = [row for row in book if row.get("approval_status") == "pending"]
    return {
        "tenant": _tenant_payload(org),
        "verified_arr_saved": float(audit.get("verified_arr_saved") or 0.0),
        "at_risk_arr": sum(row["arr"] for row in at_risk),
        "pending": pending,
    }


@router.post("/staging/{account_id}/approve")
def approve_staging_account(
    account_id: str,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.id == account_id, CustomerAccount.org_id == org.org_id)
        .one_or_none()
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    result = approve_pending_dispatch(account.id, org.org_id, session=db)
    return {"ok": True, **result}


@router.post("/staging/{account_id}/dismiss")
def dismiss_staging_account(
    account_id: str,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    account = (
        db.query(CustomerAccount)
        .filter(CustomerAccount.id == account_id, CustomerAccount.org_id == org.org_id)
        .one_or_none()
    )
    if account is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found")
    result = dismiss_false_positive(account.id, org.org_id, session=db)
    return {"ok": True, **result}


@router.get("/settings")
def get_tenant_settings(
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    logs = (
        db.query(SystemAuditLog)
        .filter(SystemAuditLog.org_id == org.org_id)
        .order_by(SystemAuditLog.created_at.desc())
        .limit(12)
        .all()
    )
    book = _latest_book(db, org.org_id)
    return {
        "tenant": {**_tenant_payload(org), "subscriber_count": len(book)},
        "integrations": _integration_flags(org),
        "webhook_urls": webhook_urls(),
        "api_base": public_api_base(),
        "salesforce_instance_url": org.salesforce_instance_url,
        "audit_log": [
            {
                "when": row.created_at.isoformat() if isinstance(row.created_at, datetime) else str(row.created_at),
                "actor": row.actor_id,
                "action": row.action,
                "old": row.old_value,
                "new": row.new_value,
            }
            for row in logs
        ],
    }


@router.patch("/settings")
def patch_tenant_settings(
    payload: TenantSettingsUpdate,
    user: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    def _clean(value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    update_tenant_policy(
        db,
        org,
        actor_id=user.user_id,
        alert_cooldown_days=payload.alert_cooldown_days,
        hitl_mrr_threshold=payload.hitl_mrr_threshold,
        stripe_webhook_secret=_clean(payload.stripe_webhook_secret),
        slack_webhook_url=_clean(payload.slack_webhook_url),
        resend_api_key=_clean(payload.resend_api_key),
        hubspot_access_token=_clean(payload.hubspot_access_token),
        salesforce_access_token=_clean(payload.salesforce_access_token),
        salesforce_instance_url=_clean(payload.salesforce_instance_url),
        apollo_api_key=_clean(payload.apollo_api_key),
        instantly_api_key=_clean(payload.instantly_api_key),
    )
    if payload.alert_cooldown_days is not None:
        db.query(CustomerAccount).filter(CustomerAccount.org_id == org.org_id).update(
            {CustomerAccount.cooldown_days: int(payload.alert_cooldown_days)}
        )
    db.flush()
    return get_tenant_settings(org=org, db=db)


@router.get("/tenant")
def get_tenant(
    user: AuthUser = Depends(get_current_user),
    org: Organization = Depends(get_current_org),
) -> dict[str, Any]:
    """Tenant summary plus the Clerk JWT role from the same AuthUser dependency."""
    return {
        **_tenant_payload(org),
        "role": user.role,
        "is_admin": user.is_admin,
    }


def _latest_campaign_by_lead(session: Session, org_id: str) -> dict[str, OutboundCampaign]:
    campaigns = (
        session.query(OutboundCampaign)
        .filter(OutboundCampaign.org_id == org_id)
        .order_by(OutboundCampaign.created_at.desc())
        .all()
    )
    latest: dict[str, OutboundCampaign] = {}
    for campaign in campaigns:
        latest.setdefault(campaign.prospect_lead_id, campaign)
    return latest


@router.get("/acquisition")
def get_acquisition(
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    leads = (
        db.query(ProspectLead)
        .filter(ProspectLead.org_id == org.org_id)
        .order_by(ProspectLead.conversion_score.desc(), ProspectLead.updated_at.desc())
        .all()
    )
    campaigns = _latest_campaign_by_lead(db, org.org_id)
    status_counts: dict[str, int] = {}
    prospects: list[dict[str, Any]] = []
    high_intent = 0
    for lead in leads:
        campaign = campaigns.get(lead.id)
        status_counts[lead.status] = status_counts.get(lead.status, 0) + 1
        if is_high_intent(lead.conversion_score):
            high_intent += 1
        prospects.append(
            {
                "id": lead.id,
                "company_name": lead.company_name,
                "decision_maker_name": lead.decision_maker_name,
                "email": lead.email,
                "linkedin_url": lead.linkedin_url,
                "conversion_score": float(lead.conversion_score or 0.0),
                "high_intent": is_high_intent(lead.conversion_score),
                "status": lead.status,
                "sequence_status": campaign.status if campaign else lead.status,
                "campaign_id": campaign.campaign_id if campaign else None,
                "vendor": campaign.vendor if campaign else None,
            }
        )
    return {
        "tenant": {**_tenant_payload(org), "subscriber_count": len(_latest_book(db, org.org_id))},
        "high_intent_count": high_intent,
        "prospect_count": len(prospects),
        "sequence_status": status_counts,
        "prospects": prospects,
    }


@router.post("/acquisition/run")
def run_acquisition_outbound(
    payload: OutboundRunRequest,
    org: Organization = Depends(get_current_org),
    _admin: AuthUser = Depends(require_admin_role),
) -> dict[str, Any]:
    trigger_outbound_engine.delay(org.org_id, payload.search_params, payload.campaign_id)
    return {
        "accepted": True,
        "org_id": org.org_id,
        "status": "queued",
        "source": "outbound_engine",
    }
