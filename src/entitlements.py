"""Free vs Pro plan gates. Paid orgs keep full product access."""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse

from src.models_db import Organization

FREE_PLAN = "free"
PRO_REQUIRED = "pro_required"

# Checkout success lands on growth (existing Stripe price) or pro.
PAID_DEFAULT_PLAN = "growth"


class ProRequired(Exception):
    """Raised when a free-tier org hits a paid automation surface."""

    def __init__(self, feature: str) -> None:
        super().__init__(feature)
        self.feature = feature

    def as_dict(self) -> dict[str, Any]:
        return {"error": PRO_REQUIRED, "feature": self.feature}


def pro_required_response(exc: ProRequired) -> JSONResponse:
    return JSONResponse(status_code=403, content=exc.as_dict())


def plan_key(plan_tier: str | None) -> str:
    return (plan_tier or FREE_PLAN).strip().lower() or FREE_PLAN


def is_free_plan(plan_tier: str | None) -> bool:
    return plan_key(plan_tier) == FREE_PLAN


def is_pro_org(org: Organization | None) -> bool:
    """True after Checkout success on a paid plan (growth/pro/scale), or seeded paid demo."""
    if org is None or is_free_plan(getattr(org, "plan_tier", None)):
        return False
    status_value = (getattr(org, "subscription_status", None) or "active").lower()
    return status_value not in {"incomplete", "incomplete_expired"}


def require_pro(org: Organization, feature: str) -> None:
    if not is_pro_org(org):
        raise ProRequired(feature)


def plan_flags(org: Organization) -> dict[str, Any]:
    return {"plan_tier": org.plan_tier, "pro": is_pro_org(org)}
