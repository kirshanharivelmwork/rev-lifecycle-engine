"""Commercial multi-tenant engine: isolation, Stripe ingest, action dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from src.auth import hash_api_key
from src.database import get_session_factory, init_db, reset_engine
from src.dispatcher import evaluate_and_trigger_actions
from src.models_db import (
    CustomerAccount,
    DispatchedAction,
    Organization,
    TelemetryEvent,
    utcnow,
)
from src.seed_commercial_demo import (
    ACME_API_KEY,
    ACME_ORG_ID,
    GLOBEX_API_KEY,
    GLOBEX_ORG_ID,
    seed_commercial_demo,
)


class _HighRiskEngine:
    def predict_proba(self, df):
        return np.array([0.91])


@pytest.fixture()
def commercial_db(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'commercial.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    reset_engine()
    init_db()
    keys = seed_commercial_demo()
    session = get_session_factory()()
    yield session, keys
    session.close()
    reset_engine()


def test_multi_tenant_isolation(commercial_db) -> None:
    session, _ = commercial_db
    acme_ids = {
        row.customer_external_id
        for row in session.query(CustomerAccount).filter(CustomerAccount.org_id == ACME_ORG_ID).all()
    }
    globex_ids = {
        row.customer_external_id
        for row in session.query(CustomerAccount).filter(CustomerAccount.org_id == GLOBEX_ORG_ID).all()
    }
    assert acme_ids
    assert globex_ids
    assert acme_ids.isdisjoint(globex_ids)
    leaked = (
        session.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == ACME_ORG_ID,
            CustomerAccount.customer_external_id.like("cus_globex_%"),
        )
        .all()
    )
    assert leaked == []
    assert session.query(Organization).filter(Organization.org_id == ACME_ORG_ID).one().api_key == hash_api_key(
        ACME_API_KEY
    )


def test_stripe_webhook_ingests_customer_and_subscription(commercial_db, monkeypatch) -> None:
    session, _ = commercial_db
    monkeypatch.setenv("ALLOW_INSECURE_WEBHOOKS", "1")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    monkeypatch.setattr("src.tasks.evaluate_and_trigger_actions", lambda *a, **k: {"triggered": False})
    from src.api import app

    with TestClient(app) as client:
        created = client.post(
            "/api/v1/webhooks/stripe",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={
                "type": "customer.created",
                "data": {
                    "object": {
                        "id": "cus_stripe_99",
                        "email": "finance@acme.test",
                        "metadata": {"channel": "Paid Search", "contract_type": "Monthly", "mrr": 250},
                    }
                },
            },
        )
        assert created.status_code == 202, created.text
        body = created.json()
        assert body["accepted"] is True
        assert body["job_id"]
        assert body["org_id"] == ACME_ORG_ID

        updated = client.post(
            "/api/v1/webhooks/stripe",
            headers={"X-Org-Id": ACME_ORG_ID},
            json={
                "type": "customer.subscription.updated",
                "data": {
                    "object": {
                        "customer": "cus_stripe_99",
                        "items": {
                            "data": [
                                {
                                    "quantity": 2,
                                    "price": {
                                        "unit_amount": 4900,
                                        "recurring": {"interval": "month"},
                                    },
                                }
                            ]
                        },
                    }
                },
            },
        )
        assert updated.status_code == 202, updated.text
        assert updated.json()["job_id"]

    session.expire_all()
    account = (
        session.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == ACME_ORG_ID,
            CustomerAccount.customer_external_id == "cus_stripe_99",
        )
        .one()
    )
    assert account.mrr == pytest.approx(98.0)
    globex_copy = (
        session.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == GLOBEX_ORG_ID,
            CustomerAccount.customer_external_id == "cus_stripe_99",
        )
        .one_or_none()
    )
    assert globex_copy is None


def test_action_dispatching_on_high_churn_accounts(commercial_db, monkeypatch) -> None:
    session, _ = commercial_db
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("RESEND_API_KEY", raising=False)
    monkeypatch.delenv("WEBHOOK_URL", raising=False)

    now = utcnow()
    account = CustomerAccount(
        org_id=ACME_ORG_ID,
        customer_external_id="cus_dispatch_high",
        channel="Outbound Cold Email",
        mrr=720.0,
        contract_type="Monthly",
        created_at=now,
    )
    session.add(account)
    session.flush()
    session.add(
        TelemetryEvent(
            org_id=ACME_ORG_ID,
            customer_account_id=account.id,
            event_name="login",
            timestamp=now - timedelta(days=30),
            properties={},
        )
    )
    session.add(
        TelemetryEvent(
            org_id=ACME_ORG_ID,
            customer_account_id=account.id,
            event_name="ticket_opened",
            timestamp=now - timedelta(days=1),
            properties={},
        )
    )
    session.commit()

    result = evaluate_and_trigger_actions(
        account.id,
        ACME_ORG_ID,
        session=session,
        engine=_HighRiskEngine(),
        force=False,
    )
    session.commit()
    assert result["triggered"] is True
    assert result["probability"] >= 0.65
    actions = (
        session.query(DispatchedAction)
        .filter(DispatchedAction.customer_account_id == account.id)
        .all()
    )
    channels = {row.channel for row in actions}
    assert channels == {"slack", "resend_email"}
    assert {row.status for row in actions} == {"simulated"}
    slack = next(row for row in actions if row.channel == "slack")
    assert "blocks" in (slack.payload or {})
    globex_actions = (
        session.query(DispatchedAction)
        .filter(
            DispatchedAction.org_id == GLOBEX_ORG_ID,
            DispatchedAction.customer_account_id == account.id,
        )
        .all()
    )
    assert globex_actions == []
