"""Free vs Pro plan gates, including a 21-day Clerk reverse trial."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi.responses import JSONResponse

from src.models_db import Organization, utcnow

FREE_PLAN = "free"
PRO_REQUIRED = "pro_required"
REVERSE_TRIAL_DAYS = 21

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


def _as_naive_utc(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(timezone.utc).replace(tzinfo=None)
    return value


def parse_clerk_created_at(raw: Any) -> Optional[datetime]:
    """Parse Clerk user.created_at from JWT claims (unix s/ms or ISO-8601)."""
    if raw is None or isinstance(raw, bool):
        return None
    if isinstance(raw, datetime):
        return _as_naive_utc(raw)
    if isinstance(raw, (int, float)):
        timestamp = float(raw)
        if timestamp <= 0:
            return None
        if timestamp > 1e12:
            timestamp = timestamp / 1000.0
        return datetime.fromtimestamp(timestamp, tz=timezone.utc).replace(tzinfo=None)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return None
        if text.isdigit():
            return parse_clerk_created_at(int(text))
        try:
            return parse_clerk_created_at(float(text))
        except ValueError:
            pass
        iso = text.replace("Z", "+00:00")
        try:
            return _as_naive_utc(datetime.fromisoformat(iso))
        except ValueError:
            return None
    return None


def account_created_at_from_claims(claims: dict[str, Any] | None) -> Optional[datetime]:
    """Read the Clerk user creation timestamp from session JWT claims.

    Expected production mapping: session token custom claim `created_at` =
    `{{user.created_at}}`. Token `iat` is ignored (it would grant a rolling trial).
    """
    blob = claims or {}
    candidates: list[Any] = [
        blob.get("created_at"),
        blob.get("user_created_at"),
        blob.get("userCreatedAt"),
        blob.get("account_created_at"),
    ]
    user = blob.get("user")
    if isinstance(user, dict):
        candidates.extend([user.get("created_at"), user.get("createdAt")])
    metadata = blob.get("metadata") if isinstance(blob.get("metadata"), dict) else {}
    public_metadata = blob.get("public_metadata") if isinstance(blob.get("public_metadata"), dict) else {}
    candidates.extend([metadata.get("created_at"), public_metadata.get("created_at")])
    for raw in candidates:
        parsed = parse_clerk_created_at(raw)
        if parsed is not None:
            return parsed
    return None


def trial_days_remaining(created_at: datetime | None, *, now: datetime | None = None) -> int:
    if created_at is None:
        return 0
    current = _as_naive_utc(now or utcnow())
    ends = _as_naive_utc(created_at) + timedelta(days=REVERSE_TRIAL_DAYS)
    seconds = (ends - current).total_seconds()
    if seconds <= 0:
        return 0
    return min(REVERSE_TRIAL_DAYS, max(1, int((seconds + 86399) // 86400)))


def is_reverse_trial(org: Organization | None, *, now: datetime | None = None) -> bool:
    if org is None:
        return False
    created = getattr(org, "clerk_user_created_at", None)
    if created is None:
        return False
    current = _as_naive_utc(now or utcnow())
    return current - _as_naive_utc(created) < timedelta(days=REVERSE_TRIAL_DAYS)


def is_paid_subscription(org: Organization | None) -> bool:
    """True after Checkout success on a paid plan (growth/pro/scale), or seeded paid demo."""
    if org is None or is_free_plan(getattr(org, "plan_tier", None)):
        return False
    status_value = (getattr(org, "subscription_status", None) or "active").lower()
    return status_value not in {"incomplete", "incomplete_expired"}


def is_pro_org(org: Organization | None) -> bool:
    """Paid Stripe Pro/Growth, or an unexpired 21-day reverse trial."""
    return is_paid_subscription(org) or is_reverse_trial(org)


def apply_reverse_trial_from_claims(org: Organization, claims: dict[str, Any] | None) -> Organization:
    created = account_created_at_from_claims(claims)
    if created is not None:
        org.clerk_user_created_at = created
    return org


def require_pro(org: Organization, feature: str) -> None:
    if not is_pro_org(org):
        raise ProRequired(feature)


def plan_flags(org: Organization) -> dict[str, Any]:
    trial = is_reverse_trial(org)
    remaining = trial_days_remaining(getattr(org, "clerk_user_created_at", None)) if trial else 0
    return {
        "plan_tier": org.plan_tier,
        "pro": is_pro_org(org),
        "reverse_trial": trial,
        "reverse_trial_days_remaining": remaining,
    }
