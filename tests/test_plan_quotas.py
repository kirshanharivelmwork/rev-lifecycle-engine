"""Plan quota enforcement for accounts, prospects, and daily ingest runs."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.database import get_session_factory, init_db, reset_engine
from src.integrations.acquisition import upsert_prospect
from src.models_db import Organization
from src.quotas import PLAN_QUOTAS, PlanLimitExceeded
from src.routers.account_ops import upsert_customer_account
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers
from tests.test_csv_ingestion import COMBINED_CSV


@pytest.fixture()
def quota_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'quota.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    class _FakeEngine:
        production_name_ = "xgboost"

        def predict_proba(self, frame):
            return [0.72]

    monkeypatch.setattr("src.churn_model.load_or_train", lambda **_k: (_FakeEngine(), "1.0.0"))
    reset_engine()
    init_db()
    seed_commercial_demo()
    with TestClient(app) as client:
        yield client
    reset_engine()


def test_csv_over_account_quota_is_rejected(quota_client: TestClient, monkeypatch) -> None:
    monkeypatch.setitem(PLAN_QUOTAS["growth"], "customer_accounts", 50)
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    response = quota_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(COMBINED_CSV.encode("utf-8")), "text/csv")},
    )
    assert response.status_code == 403, response.text
    body = response.json()
    assert body["error"] == "plan_limit"
    assert body["limit"] == "customer_accounts"
    assert body["used"] == 50
    assert body["max"] == 50


def test_csv_under_account_quota_succeeds(quota_client: TestClient, monkeypatch) -> None:
    monkeypatch.setitem(PLAN_QUOTAS["growth"], "customer_accounts", 60)
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    response = quota_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(COMBINED_CSV.encode("utf-8")), "text/csv")},
    )
    assert response.status_code == 202, response.text
    assert response.json()["accounts_upserted"] == 2


def test_daily_ingest_quota_rejects_backfill_and_csv(quota_client: TestClient, monkeypatch) -> None:
    monkeypatch.setitem(PLAN_QUOTAS["growth"], "ingest_runs_per_utc_day", 0)
    monkeypatch.setattr("src.api.run_historical_backfill.delay", lambda *_a, **_k: None)
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    backfill = quota_client.post("/api/v1/backfill", json={"stripe_api_key": "sk_test"}, headers=headers)
    assert backfill.status_code == 403, backfill.text
    assert backfill.json()["limit"] == "ingest_runs_per_utc_day"
    csv_resp = quota_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(COMBINED_CSV.encode("utf-8")), "text/csv")},
    )
    assert csv_resp.status_code == 403, csv_resp.text
    assert csv_resp.json()["error"] == "plan_limit"


def test_stripe_account_upsert_respects_cap(quota_client: TestClient, monkeypatch) -> None:
    monkeypatch.setitem(PLAN_QUOTAS["growth"], "customer_accounts", 50)
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    with pytest.raises(PlanLimitExceeded) as raised:
        upsert_customer_account(session, org, "cus_over_quota")
    assert raised.value.limit == "customer_accounts"
    existing = upsert_customer_account(session, org, "cus_acme_000")
    assert existing.customer_external_id == "cus_acme_000"
    session.close()


def test_prospect_upsert_respects_cap(quota_client: TestClient, monkeypatch) -> None:
    monkeypatch.setitem(PLAN_QUOTAS["growth"], "prospect_leads", 4)
    session = get_session_factory()()
    with pytest.raises(PlanLimitExceeded) as raised:
        upsert_prospect(
            session,
            ACME_ORG_ID,
            {
                "company_name": "Quota Labs",
                "decision_maker_name": "New Lead",
                "email": "new.lead@quota.example",
                "conversion_score": 10,
            },
        )
    assert raised.value.limit == "prospect_leads"
    updated = upsert_prospect(
        session,
        ACME_ORG_ID,
        {
            "company_name": "Northwind Analytics",
            "decision_maker_name": "Priya Shah",
            "email": "priya.shah@northwind-analytics.example",
            "conversion_score": 40,
        },
    )
    assert updated is not None
    session.close()


def test_billing_and_settings_include_remaining_quota(quota_client: TestClient) -> None:
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    billing = quota_client.get("/api/v1/billing", headers=headers)
    settings = quota_client.get("/api/v1/settings", headers=headers)
    assert billing.status_code == 200, billing.text
    assert settings.status_code == 200, settings.text
    for payload in (billing.json()["quotas"], settings.json()["quotas"]):
        accounts = payload["customer_accounts"]
        assert accounts["used"] == 50
        assert accounts["max"] == 250
        assert accounts["remaining"] == 200
        assert payload["prospect_leads"]["used"] == 4
        assert payload["prospect_leads"]["remaining"] == 96
        assert payload["ingest_runs_per_utc_day"]["remaining"] == 15
    assert billing.json()["pro"] is True
    assert settings.json()["pro"] is True
    assert billing.json()["plan_tier"]
