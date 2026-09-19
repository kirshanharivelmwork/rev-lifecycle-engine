"""Self-serve Stripe Checkout for tenant onboarding."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import Organization
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers


@pytest.fixture()
def checkout_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'checkout.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_checkout")
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_growth_monthly")
    monkeypatch.setenv("FRONTEND_URL", "http://localhost:3000")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


def test_create_checkout_session_returns_stripe_url(checkout_db, monkeypatch) -> None:
    captured: dict = {}

    def fake_create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_test_live", id="cs_test_live")

    monkeypatch.setattr("stripe.checkout.Session.create", fake_create)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/billing/create-checkout-session",
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["url"] == "https://checkout.stripe.com/c/pay/cs_test_live"
    assert body["org_id"] == ACME_ORG_ID
    assert captured["mode"] == "subscription"
    assert captured["line_items"][0]["price"] == "price_growth_monthly"
    assert captured["client_reference_id"] == ACME_ORG_ID
    assert captured["metadata"]["org_id"] == ACME_ORG_ID
    assert captured["cancel_url"].endswith("/pricing")


def test_create_checkout_session_works_when_subscription_past_due(checkout_db, monkeypatch) -> None:
    session = checkout_db
    org = session.get(Organization, ACME_ORG_ID)
    org.subscription_status = "past_due"
    session.commit()
    monkeypatch.setattr(
        "stripe.checkout.Session.create",
        lambda **_k: SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_retry", id="cs_retry"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/billing/create-checkout-session",
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin"),
        )
    assert response.status_code == 200, response.text
    assert response.json()["url"].startswith("https://checkout.stripe.com/")


def test_create_checkout_session_provisions_unknown_org(checkout_db, monkeypatch) -> None:
    monkeypatch.setattr(
        "stripe.checkout.Session.create",
        lambda **_k: SimpleNamespace(url="https://checkout.stripe.com/c/pay/cs_new", id="cs_new"),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/billing/create-checkout-session",
            headers=clerk_auth_headers(org_id="org_new_tenant", role="Admin"),
        )
    assert response.status_code == 200, response.text
    session = get_session_factory()()
    created = session.get(Organization, "org_new_tenant")
    session.close()
    assert created is not None
    assert created.subscription_status == "incomplete"
    assert response.json()["org_id"] == "org_new_tenant"


def test_create_checkout_session_requires_bearer(checkout_db) -> None:
    with TestClient(app) as client:
        response = client.post("/api/v1/billing/create-checkout-session")
    assert response.status_code == 401


def test_confirm_checkout_session_activates_paid_org(checkout_db, monkeypatch) -> None:
    session = checkout_db
    org = Organization(
        org_id="org_checkout_return",
        name="Checkout Return",
        api_key="hashed-checkout-return",
        plan_tier="growth",
        subscription_status="incomplete",
    )
    session.add(org)
    session.commit()

    def fake_retrieve(_session_id):
        return {
            "id": "cs_paid_return",
            "status": "complete",
            "payment_status": "paid",
            "customer": "cus_new_paid",
            "client_reference_id": "org_checkout_return",
            "metadata": {"org_id": "org_checkout_return"},
        }

    monkeypatch.setattr("stripe.checkout.Session.retrieve", fake_retrieve)
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/billing/confirm-checkout",
            json={"session_id": "cs_paid_return"},
            headers=clerk_auth_headers(org_id="org_checkout_return", role="Admin"),
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["activated"] is True
    assert body["subscription_status"] == "active"
    session.expire_all()
    refreshed = session.get(Organization, "org_checkout_return")
    assert refreshed.subscription_status == "active"
    assert refreshed.stripe_customer_id == "cus_new_paid"
