"""Free vs Pro PLG entitlements, monthly CSV quota, and waitlist idempotency."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.auth import hash_api_key
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import Organization, WaitlistSignup
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers
from tests.test_csv_ingestion import COMBINED_CSV

FREE_ORG_ID = "org_free_plg"
SECOND_CSV = COMBINED_CSV.replace("csv_diag_1", "csv_diag_3").replace("csv_diag_2", "csv_diag_4")


@pytest.fixture()
def plg_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'plg.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")

    class _FakeEngine:
        production_name_ = "xgboost"

        def predict_proba(self, frame):
            return [0.42]

    monkeypatch.setattr("src.churn_model.load_or_train", lambda **_k: (_FakeEngine(), "1.0.0"))
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    _free_org(session)
    session.close()
    with TestClient(app) as client:
        yield client
    reset_engine()


def _free_org(session) -> Organization:
    existing = session.get(Organization, FREE_ORG_ID)
    if existing is not None:
        return existing
    org = Organization(
        org_id=FREE_ORG_ID,
        name="Free Diagnostic",
        api_key=hash_api_key("rle_free_plg_key"),
        plan_tier="free",
        subscription_status="incomplete",
    )
    session.add(org)
    session.commit()
    return org


def test_free_org_can_ingest_csv_under_50_rows(plg_client: TestClient) -> None:
    headers = clerk_auth_headers(org_id=FREE_ORG_ID, role="Admin")
    response = plg_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(COMBINED_CSV.encode("utf-8")), "text/csv")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["accounts_upserted"] == 2


def test_free_org_second_csv_same_month_is_403(plg_client: TestClient) -> None:
    headers = clerk_auth_headers(org_id=FREE_ORG_ID, role="Admin")
    first = plg_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(COMBINED_CSV.encode("utf-8")), "text/csv")},
    )
    assert first.status_code == 202, first.text
    second = plg_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book2.csv", io.BytesIO(SECOND_CSV.encode("utf-8")), "text/csv")},
    )
    assert second.status_code == 403, second.text
    body = second.json()
    assert body["error"] == "plan_limit"
    assert body["limit"] == "csv_ingests_per_calendar_month"


def test_free_org_backfill_and_acquisition_require_pro(plg_client: TestClient) -> None:
    headers = clerk_auth_headers(org_id=FREE_ORG_ID, role="Admin")
    backfill = plg_client.post("/api/v1/backfill", json={"stripe_api_key": "sk_test"}, headers=headers)
    outbound = plg_client.post("/api/v1/acquisition/run", json={}, headers=headers)
    assert backfill.status_code == 403, backfill.text
    assert backfill.json() == {"error": "pro_required", "feature": "historical_backfill"}
    assert outbound.status_code == 403, outbound.text
    assert outbound.json() == {"error": "pro_required", "feature": "acquisition_run"}


def test_waitlist_post_is_idempotent(plg_client: TestClient) -> None:
    headers = clerk_auth_headers(org_id=FREE_ORG_ID, role="Admin", extra_claims={"email": "csm@free.example"})
    payload = {"email": "csm@free.example", "org_id": FREE_ORG_ID}
    first = plg_client.post("/api/v1/waitlist", json=payload, headers=headers)
    second = plg_client.post("/api/v1/waitlist", json=payload, headers=headers)
    assert first.status_code == 200, first.text
    assert second.status_code == 200, second.text
    assert first.json()["ok"] is True
    assert second.json()["ok"] is True
    session = get_session_factory()()
    rows = session.query(WaitlistSignup).filter(WaitlistSignup.org_id == FREE_ORG_ID).all()
    session.close()
    assert len(rows) == 1


def test_pro_org_passes_plg_gates(plg_client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr("src.api.run_historical_backfill.delay", lambda *_a, **_k: None)
    monkeypatch.setattr("src.routers.dashboard.trigger_outbound_engine.delay", lambda *_a, **_k: None)
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    billing = plg_client.get("/api/v1/billing", headers=headers)
    settings = plg_client.get("/api/v1/settings", headers=headers)
    assert billing.status_code == 200, billing.text
    assert settings.status_code == 200, settings.text
    assert billing.json()["pro"] is True
    assert settings.json()["pro"] is True
    backfill = plg_client.post("/api/v1/backfill", json={"stripe_api_key": "sk_test"}, headers=headers)
    outbound = plg_client.post("/api/v1/acquisition/run", json={}, headers=headers)
    assert backfill.status_code == 200, backfill.text
    assert outbound.status_code == 200, outbound.text
    csv_resp = plg_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(COMBINED_CSV.encode("utf-8")), "text/csv")},
    )
    assert csv_resp.status_code == 202, csv_resp.text


def test_incomplete_free_org_can_load_command_center(plg_client: TestClient) -> None:
    headers = clerk_auth_headers(org_id=FREE_ORG_ID, role="Member")
    response = plg_client.get("/api/v1/command-center", headers=headers)
    billing = plg_client.get("/api/v1/billing", headers=headers)
    assert response.status_code == 200, response.text
    assert billing.status_code == 200
    assert billing.json()["plan_tier"] == "free"
    assert billing.json()["pro"] is False
