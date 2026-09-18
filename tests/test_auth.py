"""Clerk JWT validation, RBAC, and Admin-gated billing/dispatcher routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.api import app, get_engine
from src.auth import auth_user_from_claims, decode_clerk_jwt
from src.churn_model import ChurnScoringEngine
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import Organization
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers, mint_clerk_token

HIGH_RISK = {
    "customer_id": "CUST_AUTH_HIGH",
    "acquisition_channel": "Outbound Cold Email",
    "contract_type": "Monthly",
    "avg_weekly_logins": 0.8,
    "feature_adoption_score": 2.1,
    "support_tickets_raised": 6,
    "days_since_last_login": 32,
    "monthly_recurring_revenue": 640.0,
    "cac_usd": 1650.0,
    "sales_touchpoints": 9,
}


@pytest.fixture()
def auth_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'auth.db'}")
    monkeypatch.setenv("DISABLE_RATE_LIMIT", "1")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    yield session
    session.close()
    reset_engine()
    app.dependency_overrides.clear()


def test_decode_clerk_jwt_round_trip() -> None:
    token = mint_clerk_token(org_id=ACME_ORG_ID, role="Admin", sub="user_acme")
    claims = decode_clerk_jwt(token)
    user = auth_user_from_claims(claims)
    assert user.user_id == "user_acme"
    assert user.org_id == ACME_ORG_ID
    assert user.is_admin is True


def test_org_admin_alias_maps_to_admin_role() -> None:
    token = mint_clerk_token(org_id=ACME_ORG_ID, role="org:admin")
    user = auth_user_from_claims(decode_clerk_jwt(token))
    assert user.is_admin is True
    assert user.role == "Admin"


def test_expired_jwt_is_rejected() -> None:
    token = mint_clerk_token(org_id=ACME_ORG_ID, expired=True)
    with pytest.raises(Exception) as exc:
        decode_clerk_jwt(token)
    assert getattr(exc.value, "status_code", None) == 401


def test_predict_requires_bearer_token(auth_db, processed_frame) -> None:
    engine = ChurnScoringEngine()
    engine.fit(processed_frame.head(300), tune=False)
    app.dependency_overrides[get_engine] = lambda: engine
    with TestClient(app) as client:
        missing = client.post("/v1/predict", json=HIGH_RISK)
        assert missing.status_code == 401
        member = client.post(
            "/v1/predict",
            json=HIGH_RISK,
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"),
        )
    assert member.status_code == 200, member.text
    assert 0.0 <= member.json()["churn_probability"] <= 1.0


def test_billing_and_dispatcher_require_admin(auth_db, processed_frame) -> None:
    engine = ChurnScoringEngine()
    engine.fit(processed_frame.head(300), tune=False)
    app.dependency_overrides[get_engine] = lambda: engine
    member = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member")
    admin = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    with TestClient(app) as client:
        billing_denied = client.get("/v1/billing", headers=member)
        dispatch_denied = client.post("/v1/dispatch-alert", json=HIGH_RISK, headers=member)
        billing_ok = client.get("/api/v1/billing", headers=admin)
        dispatch_ok = client.post("/v1/dispatch-alert", json=HIGH_RISK, headers=admin)
    assert billing_denied.status_code == 403
    assert dispatch_denied.status_code == 403
    assert billing_ok.status_code == 200, billing_ok.text
    assert billing_ok.json()["org_id"] == ACME_ORG_ID
    assert billing_ok.json()["subscription_status"] == "active"
    assert dispatch_ok.status_code == 200, dispatch_ok.text
    assert "dispatched" in dispatch_ok.json()


def test_get_current_user_rejects_missing_and_accepts_member_on_predict(auth_db, processed_frame) -> None:
    engine = ChurnScoringEngine()
    engine.fit(processed_frame.head(200), tune=False)
    app.dependency_overrides[get_engine] = lambda: engine
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member", sub="user_member")
    with TestClient(app) as client:
        missing = client.get("/v1/billing")
        billing_member = client.get("/v1/billing", headers=headers)
        predict_member = client.post("/v1/predict", json=HIGH_RISK, headers=headers)
    assert missing.status_code == 401
    assert billing_member.status_code == 403
    assert predict_member.status_code == 200, predict_member.text
