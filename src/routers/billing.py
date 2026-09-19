"""Tenant billing status, Stripe Checkout, and Customer Portal."""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.auth import AuthUser, generate_api_key, get_current_org_unpaid, hash_api_key
from src.database import get_db
from src.models_db import Organization
from src.quotas import quota_snapshot

router = APIRouter(tags=["billing"])


def _billing_payload(org: Organization) -> dict[str, Any]:
    status_value = (org.subscription_status or "incomplete").lower()
    return {
        "org_id": org.org_id,
        "name": org.name,
        "plan_tier": org.plan_tier,
        "subscription_status": org.subscription_status,
        "stripe_customer_id": org.stripe_customer_id,
        "portal_available": bool(org.stripe_customer_id),
        "needs_payment": status_value in {"incomplete", "past_due", "canceled", "cancelled", "unpaid"},
    }


@router.get("/billing")
def get_billing_status(
    org: Organization = Depends(get_current_org_unpaid),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {**_billing_payload(org), "quotas": quota_snapshot(db, org)}


def _frontend_url() -> str:
    return (os.getenv("FRONTEND_URL") or os.getenv("APP_URL") or "http://localhost:3000").rstrip("/")


def _org_display_name(user: AuthUser) -> str:
    claims = user.claims or {}
    org_claim = claims.get("o") if isinstance(claims.get("o"), dict) else {}
    for key in ("org_name", "orgName"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    nested = org_claim.get("nam") or org_claim.get("name") if isinstance(org_claim, dict) else None
    if isinstance(nested, str) and nested.strip():
        return nested.strip()[:160]
    return "New workspace"


def ensure_organization_for_user(db: Session, user: AuthUser) -> Organization:
    """Load the Clerk org row, creating a tenant on first checkout."""
    org = db.query(Organization).filter(Organization.org_id == user.org_id).one_or_none()
    if org is not None:
        return org
    org = Organization(
        org_id=user.org_id,
        name=_org_display_name(user),
        api_key=hash_api_key(generate_api_key()),
        plan_tier="growth",
        subscription_status="incomplete",
    )
    db.add(org)
    db.flush()
    return org


def create_stripe_checkout_session(user: AuthUser, db: Session) -> dict[str, Any]:
    """Create a Stripe Checkout Session and return its hosted URL."""
    import stripe

    secret = (os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or "").strip()
    price_id = (os.getenv("STRIPE_PRICE_ID") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="STRIPE_SECRET_KEY is not configured",
        )
    if not price_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="STRIPE_PRICE_ID is not configured",
        )

    org = ensure_organization_for_user(db, user)
    stripe.api_key = secret
    frontend = _frontend_url()
    params: dict[str, Any] = {
        "mode": "subscription",
        "line_items": [{"price": price_id, "quantity": 1}],
        "success_url": f"{frontend}/?session_id={{CHECKOUT_SESSION_ID}}",
        "cancel_url": f"{frontend}/pricing",
        "client_reference_id": org.org_id,
        "metadata": {"org_id": org.org_id},
        "subscription_data": {"metadata": {"org_id": org.org_id}},
    }
    if org.stripe_customer_id:
        params["customer"] = org.stripe_customer_id
    email = user.claims.get("email") if isinstance(user.claims.get("email"), str) else None
    if email and not org.stripe_customer_id:
        params["customer_email"] = email

    session = stripe.checkout.Session.create(**params)
    url = getattr(session, "url", None) or (session.get("url") if isinstance(session, dict) else None)
    session_id = getattr(session, "id", None) or (session.get("id") if isinstance(session, dict) else None)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe did not return a Checkout URL",
        )
    return {"url": url, "id": session_id, "org_id": org.org_id}


def confirm_stripe_checkout_session(user: AuthUser, db: Session, session_id: str) -> dict[str, Any]:
    """Activate the Clerk org when Stripe Checkout reports a paid session.

    Used on `/?session_id=...` so a brand-new subscriber is `active` even if the
    webhook worker has not drained yet. Does not go through get_current_org, so
    an `incomplete` tenant can complete onboarding without a 402.
    """
    session_id = (session_id or "").strip()
    if not session_id:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="session_id is required")
    org = ensure_organization_for_user(db, user)
    secret = (os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or "").strip()
    activated = org.subscription_status == "active"
    if secret:
        import stripe

        stripe.api_key = secret
        try:
            session = stripe.checkout.Session.retrieve(session_id)
        except Exception:
            return {
                "org_id": org.org_id,
                "session_id": session_id,
                "subscription_status": org.subscription_status,
                "activated": org.subscription_status == "active",
            }
        payload = session.to_dict() if hasattr(session, "to_dict") else dict(session)
        meta = payload.get("metadata") or {}
        hinted = str(meta.get("org_id") or payload.get("client_reference_id") or "")
        if hinted and hinted != org.org_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Checkout session does not match this organization")
        payment_status = str(payload.get("payment_status") or "").lower()
        session_status = str(payload.get("status") or "").lower()
        paid = payment_status == "paid" or session_status == "complete"
        if paid:
            customer = payload.get("customer")
            if isinstance(customer, dict):
                customer = customer.get("id")
            org.subscription_status = "active"
            if customer:
                org.stripe_customer_id = str(customer)
            db.flush()
            activated = True
    return {
        "org_id": org.org_id,
        "session_id": session_id,
        "subscription_status": org.subscription_status,
        "activated": activated,
    }


def create_stripe_portal_session(user: AuthUser, db: Session) -> dict[str, Any]:
    """Return a Stripe Customer Portal URL for invoices, card update, and cancel."""
    import stripe

    org = ensure_organization_for_user(db, user)
    if not org.stripe_customer_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No Stripe customer on this organization yet. Complete Checkout first.",
        )
    secret = (os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_API_KEY") or "").strip()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="STRIPE_SECRET_KEY is not configured",
        )
    stripe.api_key = secret
    frontend = _frontend_url()
    session = stripe.billing_portal.Session.create(
        customer=org.stripe_customer_id,
        return_url=f"{frontend}/billing",
    )
    url = getattr(session, "url", None) or (session.get("url") if isinstance(session, dict) else None)
    if not url:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Stripe did not return a Customer Portal URL",
        )
    return {"url": url, "org_id": org.org_id}
