"""Instantly closed-loop ROI and Stripe tenant billing webhooks."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.database import get_session_factory, init_db, reset_engine
from src.models_db import Organization, ProspectLead
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo


@pytest.fixture()
def webhook_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'webhooks.db'}")
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", "1")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr("src.tasks.evaluate_and_trigger_actions", lambda *a, **k: {"triggered": False})
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


def test_instantly_positive_reply_marks_meeting_booked(webhook_db) -> None:
    session = webhook_db
    lead = (
        session.query(ProspectLead)
        .filter(ProspectLead.org_id == ACME_ORG_ID, ProspectLead.email == "priya.shah@northwind-analytics.example")
        .one()
    )
    assert lead.status != "meeting_booked"
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/instantly",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={
                "event_type": "positive_reply",
                "email": "priya.shah@northwind-analytics.example",
                "custom_variables": {"org_id": ACME_ORG_ID},
            },
        )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["updated"] is True
    assert body["status"] == "meeting_booked"
    session.expire_all()
    session.refresh(lead)
    assert lead.status == "meeting_booked"


def test_instantly_resolves_org_from_query_param(webhook_db) -> None:
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            f"/api/v1/webhooks/instantly?org_id={ACME_ORG_ID}",
            json={
                "event": "meeting_booked",
                "lead": {"email": "elena.ruiz@pinnacle-labs.example"},
            },
        )
    assert response.status_code == 200, response.text
    session = webhook_db
    session.expire_all()
    lead = (
        session.query(ProspectLead)
        .filter(ProspectLead.org_id == ACME_ORG_ID, ProspectLead.email == "elena.ruiz@pinnacle-labs.example")
        .one()
    )
    assert lead.status == "meeting_booked"


def test_instantly_ignores_bounce(webhook_db) -> None:
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/instantly",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={"event_type": "bounce", "email": "priya.shah@northwind-analytics.example"},
        )
    assert response.status_code == 200
    assert response.json()["updated"] is False


def test_stripe_checkout_completed_activates_subscription(webhook_db) -> None:
    session = webhook_db
    org = session.get(Organization, ACME_ORG_ID)
    org.subscription_status = "past_due"
    session.commit()
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_checkout_loop",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_test_loop",
                        "customer": "cus_acme_billing",
                        "metadata": {"org_id": ACME_ORG_ID},
                    }
                },
            },
        )
    assert response.status_code == 202, response.text
    session.expire_all()
    org = session.get(Organization, ACME_ORG_ID)
    assert org.subscription_status == "active"
    assert org.stripe_customer_id == "cus_acme_billing"


def test_stripe_checkout_activates_before_worker_runs(webhook_db, monkeypatch) -> None:
    session = webhook_db
    org = session.get(Organization, ACME_ORG_ID)
    org.subscription_status = "incomplete"
    session.commit()
    monkeypatch.setattr("src.routers.ingestion.process_ingestion_job.delay", lambda *_a, **_k: None)
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_checkout_immediate",
                "type": "checkout.session.completed",
                "data": {
                    "object": {
                        "id": "cs_immediate",
                        "customer": "cus_immediate",
                        "client_reference_id": ACME_ORG_ID,
                        "metadata": {"org_id": ACME_ORG_ID},
                    }
                },
            },
        )
    assert response.status_code == 202, response.text
    session.expire_all()
    org = session.get(Organization, ACME_ORG_ID)
    assert org.subscription_status == "active"
    assert org.stripe_customer_id == "cus_immediate"


def test_stripe_invoice_failed_marks_past_due_by_customer_id(webhook_db) -> None:
    session = webhook_db
    org = session.get(Organization, ACME_ORG_ID)
    org.stripe_customer_id = "cus_acme_billing"
    org.subscription_status = "active"
    session.commit()
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_invoice_fail_loop",
                "type": "invoice.payment_failed",
                "data": {"object": {"customer": "cus_acme_billing", "amount_due": 4900}},
            },
        )
    assert response.status_code == 202, response.text
    session.expire_all()
    org = session.get(Organization, ACME_ORG_ID)
    assert org.subscription_status == "past_due"


def test_tenant_invoice_failure_does_not_mark_platform_past_due(webhook_db) -> None:
    session = webhook_db
    org = session.get(Organization, ACME_ORG_ID)
    org.stripe_customer_id = "cus_acme_billing"
    org.subscription_status = "active"
    session.commit()
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/stripe",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={
                "id": "evt_customer_invoice",
                "type": "invoice.payment_failed",
                "data": {"object": {"customer": "cus_end_customer", "amount_due": 1200}},
            },
        )
    assert response.status_code == 202, response.text
    session.expire_all()
    org = session.get(Organization, ACME_ORG_ID)
    assert org.subscription_status == "active"
