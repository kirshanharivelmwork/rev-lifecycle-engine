"""Enterprise resilience: idempotency, GDPR purge, audit logs, billing 402."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.audit import update_tenant_policy
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DispatchedAction,
    InterventionOutcome,
    Organization,
    SystemAuditLog,
    TelemetryEvent,
)
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers


@pytest.fixture()
def resilience_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'resilience.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", "1")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


def test_webhook_deduplicates_identical_event_id(resilience_db, monkeypatch) -> None:
    monkeypatch.setattr("src.tasks.evaluate_and_trigger_actions", lambda *a, **k: {"triggered": False})
    from src.api import app

    payload = {
        "id": "evt_idem_001",
        "type": "customer.created",
        "data": {
            "object": {
                "id": "cus_idem_1",
                "metadata": {"channel": "Paid Search", "contract_type": "Monthly", "mrr": 99},
            }
        },
    }
    with TestClient(app) as client:
        first = client.post("/api/v1/webhooks/stripe", headers={"X-Org-Id": ACME_ORG_ID}, json=payload)
        second = client.post("/api/v1/webhooks/stripe", headers={"X-Org-Id": ACME_ORG_ID}, json=payload)
    assert first.status_code == 202, first.text
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["status"] == "skipped"
    assert body["reason"] == "duplicate"
    session = resilience_db
    session.expire_all()
    copies = (
        session.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == ACME_ORG_ID,
            CustomerAccount.customer_external_id == "cus_idem_1",
        )
        .all()
    )
    assert len(copies) == 1


def test_gdpr_customer_deletion_cascade(resilience_db) -> None:
    session = resilience_db
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID)
        .first()
    )
    assert account is not None
    ext = account.customer_external_id
    aid = account.id
    from src.api import app

    with TestClient(app) as client:
        missing = client.delete(f"/api/v1/customers/{ext}")
        assert missing.status_code == 401
        deleted = client.delete(
            f"/api/v1/customers/{ext}",
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"),
        )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["deleted"] == ext
    session.expire_all()
    assert session.query(CustomerAccount).filter(CustomerAccount.id == aid).one_or_none() is None
    assert session.query(TelemetryEvent).filter(TelemetryEvent.customer_account_id == aid).all() == []
    assert session.query(ChurnAssessment).filter(ChurnAssessment.customer_account_id == aid).all() == []
    assert session.query(DispatchedAction).filter(DispatchedAction.customer_account_id == aid).all() == []
    assert session.query(InterventionOutcome).filter(InterventionOutcome.customer_account_id == aid).all() == []


def test_audit_log_on_tenant_policy_update(resilience_db) -> None:
    session = resilience_db
    org = session.get(Organization, ACME_ORG_ID)
    update_tenant_policy(
        session,
        org,
        actor_id="pytest",
        alert_cooldown_days=21,
        hitl_mrr_threshold=1500.0,
    )
    session.commit()
    rows = (
        session.query(SystemAuditLog)
        .filter(SystemAuditLog.org_id == ACME_ORG_ID, SystemAuditLog.action == "tenant_settings.update")
        .all()
    )
    assert rows
    latest = rows[-1]
    assert latest.actor_id == "pytest"
    assert latest.new_value["alert_cooldown_days"] == 21
    assert latest.new_value["hitl_mrr_threshold"] == 1500.0
    session.refresh(org)
    assert org.alert_cooldown_days == 21


def test_402_payment_required_for_delinquent_tenant(resilience_db) -> None:
    session = resilience_db
    org = session.get(Organization, ACME_ORG_ID)
    org.subscription_status = "past_due"
    session.commit()
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/v1/predict",
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"),
            json={
                "customer_id": "cus_bill",
                "acquisition_channel": "Paid Search",
                "contract_type": "Monthly",
                "avg_weekly_logins": 3.0,
                "feature_adoption_score": 5.0,
                "support_tickets_raised": 1,
                "days_since_last_login": 4,
                "monthly_recurring_revenue": 200.0,
            },
        )
    assert response.status_code == 402
    assert response.json()["error"] == "Tenant subscription inactive. Please update billing."
