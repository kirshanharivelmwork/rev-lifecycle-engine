"""Enterprise reliability: signatures, async ingest, cooldown, HITL."""

from __future__ import annotations

from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from sqlalchemy import text

from src.database import get_session_factory, init_db, reset_engine
from src.dispatcher import approve_pending_dispatch, dismiss_false_positive, evaluate_and_trigger_actions
from src.feature_builder import build_feature_row
from src.models_db import CustomerAccount, DispatchedAction, IngestionJob, Organization, TelemetryEvent, utcnow
from src.outcome_tracker import audit_intervention_outcomes
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo


class _MediumRiskEngine:
    def predict_proba(self, df):
        return np.array([0.72])


class _CriticalRiskEngine:
    def predict_proba(self, df):
        return np.array([0.91])


@pytest.fixture()
def enterprise_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'enterprise.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()


def _account(session, *, external_id: str, mrr: float, last_contacted=None) -> CustomerAccount:
    now = utcnow()
    account = CustomerAccount(
        org_id=ACME_ORG_ID,
        customer_external_id=external_id,
        channel="Outbound Cold Email",
        mrr=mrr,
        contract_type="Monthly",
        created_at=now - timedelta(days=40),
        last_contacted_at=last_contacted,
        cooldown_days=14,
        contract_renewal_at=now + timedelta(days=12),
        approval_status="none",
    )
    session.add(account)
    session.flush()
    session.add(
        TelemetryEvent(
            org_id=ACME_ORG_ID,
            customer_account_id=account.id,
            event_name="login",
            timestamp=now - timedelta(days=28),
            properties={},
        )
    )
    session.commit()
    return account


def test_stripe_signature_verification_rejects_invalid(enterprise_db, monkeypatch) -> None:
    monkeypatch.delenv("ALLOW_INSECURE_WEBHOOKS", raising=False)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/stripe",
            headers={
                "X-Org-Id": ACME_ORG_ID,
                "Stripe-Signature": "t=1,v1=deadbeef",
            },
            content=b'{"type":"customer.created","data":{"object":{"id":"cus_bad"}}}',
        )
    assert response.status_code == 400
    assert "signature" in response.json()["detail"].lower() or "Stripe" in response.json()["detail"]


def test_telemetry_webhook_returns_202_job_id(enterprise_db, monkeypatch) -> None:
    monkeypatch.setattr("src.tasks.evaluate_and_trigger_actions", lambda *a, **k: {"triggered": False})
    from src.api import app

    with TestClient(app) as client:
        response = client.post(
            "/api/v1/webhooks/telemetry",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={
                "batch": [
                    {
                        "userId": "cus_async_01",
                        "event": "login",
                        "timestamp": utcnow().isoformat(),
                        "properties": {"source": "web"},
                    }
                ]
            },
        )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["accepted"] is True
    assert body["job_id"]
    session = enterprise_db
    session.expire_all()
    job = session.get(IngestionJob, body["job_id"])
    assert job is not None
    assert job.source == "telemetry"
    assert job.status in {"queued", "processing", "succeeded"}


def test_alert_suppressed_during_14_day_cooldown(enterprise_db) -> None:
    session = enterprise_db
    now = utcnow()
    account = _account(
        session,
        external_id="cus_cooldown",
        mrr=720.0,
        last_contacted=now - timedelta(days=3),
    )
    account.suppressed_until = now + timedelta(days=11)
    session.commit()

    result = evaluate_and_trigger_actions(
        account.id,
        ACME_ORG_ID,
        session=session,
        engine=_MediumRiskEngine(),
    )
    session.commit()
    assert result["status"] == "suppressed_cooldown"
    assert result["triggered"] is False
    rows = session.query(DispatchedAction).filter(DispatchedAction.customer_account_id == account.id).all()
    assert {row.status for row in rows} == {"suppressed_cooldown"}
    assert {row.channel for row in rows} == {"system"}


def test_critical_escalation_bypasses_cooldown(enterprise_db) -> None:
    session = enterprise_db
    now = utcnow()
    account = _account(
        session,
        external_id="cus_escalate",
        mrr=720.0,
        last_contacted=now - timedelta(days=1),
    )
    result = evaluate_and_trigger_actions(
        account.id,
        ACME_ORG_ID,
        session=session,
        engine=_CriticalRiskEngine(),
    )
    session.commit()
    assert result["status"] == "dispatched"
    channels = {
        row.channel
        for row in session.query(DispatchedAction).filter(DispatchedAction.customer_account_id == account.id)
    }
    assert {"slack", "resend_email"} <= channels


def test_hitl_approval_workflow(enterprise_db) -> None:
    session = enterprise_db
    account = _account(session, external_id="cus_hitl", mrr=1850.0)
    queued = evaluate_and_trigger_actions(
        account.id,
        ACME_ORG_ID,
        session=session,
        engine=_MediumRiskEngine(),
    )
    session.commit()
    session.refresh(account)
    assert queued["status"] == "pending_approval"
    assert account.approval_status == "pending"
    pending = (
        session.query(DispatchedAction)
        .filter(DispatchedAction.customer_account_id == account.id, DispatchedAction.status == "pending_approval")
        .all()
    )
    assert pending

    approved = approve_pending_dispatch(account.id, ACME_ORG_ID, session=session, engine=_MediumRiskEngine())
    session.commit()
    session.refresh(account)
    assert approved["status"] == "approved"
    assert account.approval_status == "approved"
    channels = {
        row.channel
        for row in session.query(DispatchedAction).filter(DispatchedAction.customer_account_id == account.id)
    }
    assert "slack" in channels
    assert "resend_email" in channels

    other = _account(session, external_id="cus_hitl_dismiss", mrr=2100.0)
    evaluate_and_trigger_actions(other.id, ACME_ORG_ID, session=session, engine=_MediumRiskEngine())
    dismissed = dismiss_false_positive(other.id, ACME_ORG_ID, session=session)
    session.commit()
    session.refresh(other)
    assert dismissed["status"] == "dismissed"
    assert other.approval_status == "dismissed"


def test_renewal_features_and_empirical_audit(enterprise_db) -> None:
    session = enterprise_db
    now = utcnow()
    account = _account(session, external_id="cus_renewal", mrr=400.0)
    row = build_feature_row(session, account, now=now)
    assert row["days_until_renewal"] == 12
    assert row["contract_renewal_urgency_ratio"] == pytest.approx(
        row["days_since_last_login"] / 12.0, rel=1e-3
    )

    audit = audit_intervention_outcomes(ACME_ORG_ID, session=session)
    session.commit()
    assert "verified_arr_saved" in audit
    assert audit["outcomes"] >= 0


def test_tenant_settings_persist_on_organization(enterprise_db) -> None:
    session = enterprise_db
    org = session.get(Organization, ACME_ORG_ID)
    org.stripe_webhook_secret = "whsec_rotated"
    org.slack_webhook_url = "https://hooks.slack.com/services/T/B/demo"
    org.resend_api_key = "re_test_key"
    org.hubspot_token = "pat-hubspot"
    org.salesforce_key = "sf-access"
    org.instantly_api_key = "inst_live"
    org.alert_cooldown_days = 21
    org.hitl_mrr_threshold = 2500.0
    session.commit()
    session.expire_all()
    reloaded = session.get(Organization, ACME_ORG_ID)
    assert reloaded.stripe_webhook_secret == "whsec_rotated"
    assert reloaded.stripe_secret == "whsec_rotated"
    assert reloaded.slack_webhook_url.endswith("/demo")
    assert reloaded.resend_api_key == "re_test_key"
    assert reloaded.hubspot_token == "pat-hubspot"
    assert reloaded.salesforce_key == "sf-access"
    assert reloaded.instantly_api_key == "inst_live"
    assert reloaded.alert_cooldown_days == 21
    assert reloaded.hitl_mrr_threshold == pytest.approx(2500.0)
    stored = session.execute(
        text(
            "SELECT hubspot_access_token, salesforce_access_token, "
            "stripe_webhook_secret, instantly_api_key FROM organizations WHERE org_id = :oid"
        ),
        {"oid": ACME_ORG_ID},
    ).mappings().one()
    assert stored["hubspot_access_token"] != "pat-hubspot"
    assert stored["salesforce_access_token"] != "sf-access"
    assert stored["stripe_webhook_secret"] != "whsec_rotated"
    assert stored["instantly_api_key"] != "inst_live"
