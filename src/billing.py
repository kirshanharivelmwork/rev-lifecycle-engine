"""Tenant billing guard and in-process rate limiter."""

from __future__ import annotations

import os

from fastapi import Request
from fastapi.responses import JSONResponse
from slowapi import Limiter
from slowapi.util import get_remote_address

from src.models_db import Organization

INACTIVE = {"past_due", "canceled", "cancelled"}
BILLING_ERROR = "Tenant subscription inactive. Please update billing."


class SubscriptionInactive(Exception):
    """Raised when the tenant is past_due or canceled."""


def _rate_limit_enabled() -> bool:
    return os.getenv("DISABLE_RATE_LIMIT", "").lower() not in {"1", "true", "yes"}


def _tenant_rate_key(request: Request) -> str:
    authorization = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    bearer = authorization.split(" ", 1)[1].strip() if authorization.lower().startswith("bearer ") else ""
    return (
        bearer
        or request.headers.get("x-api-key")
        or request.headers.get("X-API-Key")
        or request.headers.get("x-org-id")
        or request.headers.get("X-Org-Id")
        or get_remote_address(request)
    )


limiter = Limiter(key_func=_tenant_rate_key, default_limits=[], enabled=_rate_limit_enabled())
INGEST_LIMIT = "120/minute"


def billing_json_response() -> JSONResponse:
    return JSONResponse(status_code=402, content={"error": BILLING_ERROR})


def raise_if_inactive(org: Organization) -> None:
    status_value = (getattr(org, "subscription_status", None) or "active").lower()
    if status_value in INACTIVE:
        raise SubscriptionInactive()
