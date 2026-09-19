"""Historical Stripe / product-analytics hydration worker."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.auth import hash_api_key
from src.database import get_session_factory, init_db, reset_engine
from src.feature_builder import build_feature_row
from src.integrations.backfill import (
    BATCH_SIZE,
    bulk_insert_in_chunks,
    fetch_historical_product_events,
    mock_historical_product_events,
    paginate_stripe_list,
    run_historical_backfill,
)
from src.models_db import CustomerAccount, Organization, TelemetryEvent, utcnow
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers


HYDRATE_ORG = "org_hydrate"


class _Page:
    def __init__(self, data, has_more=False):
        self.data = data
        self.has_more = has_more


def _customer(cid: str, created: int, channel: str = "Paid Search") -> dict:
    return {"id": cid, "created": created, "metadata": {"channel": channel, "mrr": 0}}


def _subscription(sid: str, customer: str, created: int, *, amount=4900, interval="month", status="active") -> dict:
    return {
        "id": sid,
        "customer": customer,
        "created": created,
        "status": status,
        "current_period_end": created + 30 * 86400,
        "items": {
            "data": [
                {
                    "quantity": 1,
                    "price": {"unit_amount": amount, "recurring": {"interval": interval}},
                }
            ]
        },
        "metadata": {"channel": "Inbound Organic"},
    }


@pytest.fixture()
def backfill_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'backfill.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    session.add(
        Organization(
            org_id=HYDRATE_ORG,
            name="Hydrate Co",
            api_key=hash_api_key("hydrate_key"),
            plan_tier="growth",
            created_at=utcnow(),
            subscription_status="active",
        )
    )
    session.commit()
    yield session
    session.close()
    reset_engine()
    app.dependency_overrides.clear()


def test_bulk_insert_chunks_at_500() -> None:
    assert BATCH_SIZE == 500

    class _FakeSession:
        def __init__(self) -> None:
            self.chunks: list[list[int]] = []

        def add_all(self, rows):
            self.chunks.append(list(rows))

        def flush(self):
            return None

    session = _FakeSession()
    rows = list(range(501))
    inserted = bulk_insert_in_chunks(session, rows, batch_size=500)
    assert inserted == 501
    assert len(session.chunks) == 2
    assert len(session.chunks[0]) == 500
    assert len(session.chunks[1]) == 1


def test_paginate_stripe_list_follows_has_more() -> None:
    pages = {
        None: _Page([{"id": "cus_a"}, {"id": "cus_b"}], has_more=True),
        "cus_b": _Page([{"id": "cus_c"}], has_more=False),
    }

    def list_fn(limit=100, starting_after=None, **_kwargs):
        return pages[starting_after]

    records = paginate_stripe_list(list_fn, limit=100)
    assert [row["id"] for row in records] == ["cus_a", "cus_b", "cus_c"]


def test_mock_product_events_include_login_and_adoption() -> None:
    events = mock_historical_product_events("org_x", ["cus_1", "cus_2"])
    names = {row["event"] for row in events}
    assert "login" in names
    assert "feature_used" in names
    assert all(row["properties"]["source"] == "historical_backfill" for row in events)


def test_run_historical_backfill_persists_accounts_and_telemetry(backfill_db, monkeypatch) -> None:
    now = utcnow()
    created = int((now - timedelta(days=40)).timestamp())
    customers = [_customer("cus_new_1", created), _customer("cus_new_2", created, channel="Partner Referral")]
    subscriptions = [
        _subscription("sub_1", "cus_new_1", created, amount=9900),
        _subscription("sub_2", "cus_new_2", created, status="past_due"),
    ]

    def fake_window(_key, now=None):
        return customers, subscriptions

    monkeypatch.setattr("src.integrations.backfill.fetch_stripe_billing_window", fake_window)
    result = asyncio.run(run_historical_backfill(HYDRATE_ORG, "sk_test_123", session=backfill_db, now=now))
    assert result["ok"] is True
    assert result["product_source"] == "mock"
    assert result["accounts"] >= 2
    session = backfill_db
    session.expire_all()
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == HYDRATE_ORG, CustomerAccount.customer_external_id == "cus_new_1")
        .one()
    )
    assert account.mrr == pytest.approx(99.0)
    assert account.contract_type == "Monthly"
    events = (
        session.query(TelemetryEvent)
        .filter(TelemetryEvent.org_id == HYDRATE_ORG, TelemetryEvent.customer_account_id == account.id)
        .all()
    )
    names = {row.event_name for row in events}
    assert "login" in names
    assert "feature_used" in names
    failed = (
        session.query(TelemetryEvent)
        .filter(
            TelemetryEvent.org_id == HYDRATE_ORG,
            TelemetryEvent.event_name == "invoice.payment_failed",
        )
        .all()
    )
    assert len(failed) == 1
    row = build_feature_row(session, account, now=now)
    assert row["avg_weekly_logins"] >= 0
    assert row["feature_adoption_score"] > 0
    assert row["monthly_recurring_revenue"] == pytest.approx(99.0)


def test_fetch_historical_product_events_uses_posthog_when_configured() -> None:
    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self._payload = payload

        def json(self):
            return self._payload

    class _Client:
        def __init__(self):
            self.calls = []

        async def get(self, url, headers=None, params=None):
            self.calls.append({"url": url, "headers": headers, "params": params})
            return _Resp(
                {
                    "results": [
                        {
                            "event": "Signed In",
                            "distinct_id": "cus_ph",
                            "timestamp": datetime.utcnow().isoformat(),
                            "properties": {"feature": "app"},
                        }
                    ]
                }
            )

        async def aclose(self):
            return None

    client = _Client()
    events, source = asyncio.run(
        fetch_historical_product_events(
            "org_x",
            ["cus_ph"],
            posthog_api_key="phc_test",
            posthog_project_id="123",
            client=client,
        )
    )
    assert source == "posthog"
    assert events[0]["event"] == "login"
    assert events[0]["user_id"] == "cus_ph"
    assert "Bearer phc_test" in client.calls[0]["headers"]["Authorization"]


def test_fetch_historical_product_events_mocks_without_credentials() -> None:
    events, source = asyncio.run(fetch_historical_product_events("org_x", ["cus_z"]))
    assert source == "mock"
    assert any(row["event"] == "login" for row in events)


def test_backfill_route_requires_admin(backfill_db, monkeypatch) -> None:
    async def fake_run(*_args, **_kwargs):
        return {"ok": True}

    monkeypatch.setattr("src.api.run_historical_backfill", fake_run)
    member = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member")
    admin = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    with TestClient(app) as client:
        denied = client.post("/v1/backfill", json={"stripe_api_key": "sk_test"}, headers=member)
        missing = client.post("/api/v1/backfill", json={}, headers=admin)
        queued = client.post("/v1/backfill", json={"stripe_api_key": "sk_test"}, headers=admin)
    assert denied.status_code == 403
    assert missing.status_code == 400
    assert queued.status_code == 200, queued.text
    assert queued.json()["accepted"] is True
    assert queued.json()["org_id"] == ACME_ORG_ID
    assert queued.json()["status"] == "queued"


def test_backfill_route_runs_worker(backfill_db, monkeypatch) -> None:
    calls = []

    async def fake_run(org_id, stripe_api_key, **kwargs):
        calls.append({"org_id": org_id, "key": stripe_api_key, **kwargs})
        return {"ok": True, "org_id": org_id}

    monkeypatch.setattr("src.api.run_historical_backfill", fake_run)
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/backfill",
            json={"stripe_api_key": "sk_live_demo", "posthog_api_key": "phc_x"},
            headers=headers,
        )
    assert response.status_code == 200, response.text
    assert calls[0]["org_id"] == ACME_ORG_ID
    assert calls[0]["key"] == "sk_live_demo"
    assert calls[0]["posthog_api_key"] == "phc_x"
