"""Clerk-authenticated Command Center, staging, and settings HTTP APIs."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import CustomerAccount
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers


@pytest.fixture()
def dashboard_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dashboard.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    reset_engine()
    init_db()
    seed_commercial_demo()
    with TestClient(app) as client:
        client.headers.update(clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin"))
        yield client
    reset_engine()


def test_command_center_returns_live_book(dashboard_client: TestClient) -> None:
    response = dashboard_client.get("/api/v1/command-center")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tenant"]["org_id"] == ACME_ORG_ID
    assert body["tenant"]["name"] == "Acme SaaS"
    assert body["tenant"]["subscriber_count"] >= 1
    assert "active_arr" in body["kpis"]
    assert isinstance(body["action_stream"], list)
    assert isinstance(body["at_risk_book"], list)
    assert len(body["risk_mix"]) == 10


def test_staging_lists_pending_accounts(dashboard_client: TestClient) -> None:
    response = dashboard_client.get("/api/v1/staging")
    assert response.status_code == 200, response.text
    body = response.json()
    assert "pending" in body
    assert "verified_arr_saved" in body
    session = get_session_factory()()
    pending_ids = {
        row.id
        for row in session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID, CustomerAccount.approval_status == "pending")
        .all()
    }
    session.close()
    returned = {row["account_id"] for row in body["pending"]}
    assert returned == pending_ids


def test_settings_masks_secrets_and_patch_updates_policy(dashboard_client: TestClient) -> None:
    listed = dashboard_client.get("/api/v1/settings")
    assert listed.status_code == 200, listed.text
    payload = listed.json()
    assert "stripe_webhook_secret" not in payload
    assert "integrations" in payload
    updated = dashboard_client.patch(
        "/api/v1/settings",
        json={"alert_cooldown_days": 21, "hitl_mrr_threshold": 2500},
    )
    assert updated.status_code == 200, updated.text
    tenant = updated.json()["tenant"]
    assert tenant["alert_cooldown_days"] == 21
    assert tenant["hitl_mrr_threshold"] == pytest.approx(2500.0)


def test_command_center_requires_bearer_token(dashboard_client: TestClient) -> None:
    response = dashboard_client.get("/api/v1/command-center", headers={"Authorization": "Bearer not-a-jwt"})
    assert response.status_code == 401
