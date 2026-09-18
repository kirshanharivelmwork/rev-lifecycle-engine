"""Admin-only tenant billing status."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from src.auth import AuthUser, get_current_org, require_admin_role
from src.models_db import Organization

router = APIRouter(tags=["billing"])


@router.get("/billing")
def get_billing_status(
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
) -> dict:
    return {
        "org_id": org.org_id,
        "plan_tier": org.plan_tier,
        "subscription_status": org.subscription_status,
        "stripe_customer_id": org.stripe_customer_id,
    }
