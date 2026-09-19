"""Product-loop coverage: acquisition RBAC, backfill scores, billing portal, health, jobs."""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.database import get_session_factory, init_db, reset_engine
from src.integrations.backfill import run_historical_backfill
from src.models_db import ChurnAssessment, CustomerAccount, DeadLetterJob, Organization, TelemetryEvent
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers
from tests.test_backfill import _customer, _subscription

from datetime import timedelta

from src.models_db import utcnow


@pytest.fixture()
def loop_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'loop.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_loop")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


def test_member_cannot_run_outbound(loop_db) -> None:
    member = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member")
    admin = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    with TestClient(app) as client:
        listed = client.get("/api/v1/acquisition", headers=member)
        denied = client.post("/api/v1/acquisition/run", json={}, headers=member)
        queued = client.post("/api/v1/acquisition/run", json={}, headers=admin)
    assert listed.status_code == 200
    assert denied.status_code == 403
    assert queued.status_code == 200


def test_member_can_read_billing_and_open_portal_route(loop_db, monkeypatch) -> None:
    session = loop_db
    org = session.get(Organization, ACME_ORG_ID)
    org.stripe_customer_id = "cus_portal"
    session.commit()

    def fake_portal(**kwargs):
        assert kwargs["customer"] == "cus_portal"
        return {"url": "https://billing.stripe.com/p/session/test"}

    monkeypatch.setattr("stripe.billing_portal.Session.create", fake_portal)
    member = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member")
    with TestClient(app) as client:
        listed = client.get("/api/v1/billing", headers=member)
        portal = client.post("/api/v1/billing/portal", headers=member)
    assert listed.status_code == 200
    assert listed.json()["subscription_status"] == "active"
    assert listed.json()["portal_available"] is True
    assert portal.status_code == 200
    assert portal.json()["url"].startswith("https://billing.stripe.com/")


def test_past_due_returns_402_on_command_center(loop_db) -> None:
    session = loop_db
    org = session.get(Organization, ACME_ORG_ID)
    org.subscription_status = "past_due"
    session.commit()
    with TestClient(app) as client:
        blocked = client.get("/api/v1/command-center", headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"))
        billing = client.get("/api/v1/billing", headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"))
    assert blocked.status_code == 402
    assert billing.status_code == 200
    assert billing.json()["needs_payment"] is True


def test_incomplete_returns_402_until_checkout_webhook(loop_db) -> None:
    session = loop_db
    org = session.get(Organization, ACME_ORG_ID)
    org.subscription_status = "incomplete"
    session.commit()
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    with TestClient(app) as client:
        blocked = client.get("/api/v1/command-center", headers=headers)
        webhook = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_activate_incomplete",
                "type": "checkout.session.completed",
                "data": {"object": {"customer": "cus_now_paid", "metadata": {"org_id": ACME_ORG_ID}}},
            },
        )
        live = client.get("/api/v1/command-center", headers=headers)
    assert blocked.status_code == 402
    assert webhook.status_code == 202
    assert live.status_code == 200


def test_subscription_updated_and_invoice_succeeded_keep_customer_id(loop_db) -> None:
    session = loop_db
    org = session.get(Organization, ACME_ORG_ID)
    org.stripe_customer_id = "cus_acme_bill"
    org.subscription_status = "past_due"
    session.commit()
    with TestClient(app) as client:
        updated = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_sub_upd",
                "type": "customer.subscription.updated",
                "data": {"object": {"customer": "cus_acme_bill", "status": "active", "metadata": {"org_id": ACME_ORG_ID}}},
            },
        )
        failed = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_inv_fail2",
                "type": "invoice.payment_failed",
                "data": {"object": {"customer": "cus_acme_bill"}},
            },
        )
        succeeded = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_inv_ok",
                "type": "invoice.payment_succeeded",
                "data": {"object": {"customer": "cus_acme_bill"}},
            },
        )
        deleted = client.post(
            "/api/v1/webhooks/stripe",
            json={
                "id": "evt_sub_del",
                "type": "customer.subscription.deleted",
                "data": {"object": {"customer": "cus_acme_bill", "status": "canceled"}},
            },
        )
    assert updated.status_code == 202
    assert failed.status_code == 202
    assert succeeded.status_code == 202
    assert deleted.status_code == 202
    session.expire_all()
    org = session.get(Organization, ACME_ORG_ID)
    assert org.stripe_customer_id == "cus_acme_bill"
    assert org.subscription_status == "canceled"


def test_health_payload_includes_db_and_redis() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code in {200, 503}
    body = response.json()
    assert "db" in body
    assert "redis" in body
    assert "worker" in body
    if body["db"] != "ok":
        assert response.status_code == 503
        assert body["status"] == "error"
    else:
        assert body["status"] in {"ok", "degraded"}


def test_jobs_list_and_admin_resolve(loop_db) -> None:
    session = loop_db
    job = DeadLetterJob(
        org_id=ACME_ORG_ID,
        task_name="process_ingestion_job",
        payload={"job_id": "missing"},
        error_message="boom",
        retry_count=3,
        resolved=False,
    )
    session.add(job)
    session.commit()
    job_id = job.id
    member = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member")
    admin = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    with TestClient(app) as client:
        listed = client.get("/api/v1/jobs", headers=member)
        denied = client.post(f"/api/v1/jobs/dead-letter/{job_id}/resolve", headers=member)
        resolved = client.post(f"/api/v1/jobs/dead-letter/{job_id}/resolve", headers=admin)
    assert listed.status_code == 200
    assert any(row["id"] == job_id for row in listed.json()["dead_letter"])
    assert denied.status_code == 403
    assert resolved.status_code == 200
    session.expire_all()
    assert session.get(DeadLetterJob, job_id).resolved is True


def test_gdpr_delete_route(loop_db) -> None:
    session = loop_db
    account = session.query(CustomerAccount).filter(CustomerAccount.org_id == ACME_ORG_ID).first()
    ext = account.customer_external_id
    with TestClient(app) as client:
        response = client.delete(
            f"/api/v1/customers/{ext}",
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"),
        )
    assert response.status_code == 200
    session.expire_all()
    assert (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID, CustomerAccount.customer_external_id == ext)
        .one_or_none()
        is None
    )


def test_backfill_writes_assessments_and_is_idempotent(loop_db, monkeypatch) -> None:
    now = utcnow()
    created = int((now - timedelta(days=20)).timestamp())
    customers = [_customer("cus_score_1", created), _customer("cus_score_2", created)]
    subscriptions = [
        _subscription("sub_s1", "cus_score_1", created),
        _subscription("sub_s2", "cus_score_2", created),
    ]
    monkeypatch.setattr(
        "src.integrations.backfill.fetch_stripe_billing_window",
        lambda *_a, **_k: (customers, subscriptions),
    )
    first = asyncio.run(run_historical_backfill(ACME_ORG_ID, "sk_test", session=loop_db, now=now))
    assert first["ok"] is True
    assert first["assessments"] >= 2
    session = loop_db
    session.expire_all()
    accounts = session.query(CustomerAccount).filter(CustomerAccount.org_id == ACME_ORG_ID).count()
    telemetry = session.query(TelemetryEvent).filter(TelemetryEvent.org_id == ACME_ORG_ID).count()
    assessments = session.query(ChurnAssessment).filter(ChurnAssessment.org_id == ACME_ORG_ID).count()
    second = asyncio.run(run_historical_backfill(ACME_ORG_ID, "sk_test", session=loop_db, now=now))
    session.expire_all()
    assert session.query(CustomerAccount).filter(CustomerAccount.org_id == ACME_ORG_ID).count() == accounts
    assert session.query(TelemetryEvent).filter(TelemetryEvent.org_id == ACME_ORG_ID).count() == telemetry
    assert assessments >= 1
    assert second["telemetry_events"] == 0


def test_stripe_webhook_smoke_creates_assessment(loop_db, monkeypatch) -> None:
    monkeypatch.setattr("src.dispatcher.load_or_train", lambda **_k: (_FakeEngine(), "1.0.0"))
    monkeypatch.setattr("src.churn_model.load_or_train", lambda **_k: (_FakeEngine(), "1.0.0"))
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/stripe",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={
                "id": "evt_smoke_cust",
                "type": "customer.created",
                "data": {
                    "object": {
                        "id": "cus_smoke_new",
                        "metadata": {"channel": "Paid Search", "mrr": 120, "org_id": ACME_ORG_ID},
                    }
                },
            },
        )
    assert response.status_code == 202
    session = loop_db
    session.expire_all()
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID, CustomerAccount.customer_external_id == "cus_smoke_new")
        .one_or_none()
    )
    assert account is not None
    rows = session.query(ChurnAssessment).filter(ChurnAssessment.customer_account_id == account.id).all()
    assert len(rows) >= 1


class _FakeEngine:
    production_name_ = "xgboost"

    def predict_proba(self, frame):
        return [0.2]
