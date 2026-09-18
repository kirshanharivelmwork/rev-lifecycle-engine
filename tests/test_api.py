"""Integration tests for the production scoring API."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from src.api import app, get_engine
from src.auth import get_current_org
from src.churn_model import ChurnScoringEngine
from src.models_db import Organization
from src.paths import CRITICAL_THRESHOLD, MODEL_VERSION

HIGH_RISK = {
    "customer_id": "CUST_TEST_HIGH",
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

HEALTHY = {
    "customer_id": "CUST_TEST_LOW",
    "acquisition_channel": "Partner Referral",
    "contract_type": "Annual",
    "avg_weekly_logins": 8.5,
    "feature_adoption_score": 8.8,
    "support_tickets_raised": 0,
    "days_since_last_login": 1,
    "monthly_recurring_revenue": 890.0,
    "cac_usd": 420.0,
    "sales_touchpoints": 3,
}


@pytest.fixture(scope="module")
def client(processed_frame):
    engine = ChurnScoringEngine()
    engine.fit(processed_frame, tune=False)
    demo_org = Organization(org_id="org_test", name="Test", api_key="hashed", plan_tier="dev")
    app.dependency_overrides[get_engine] = lambda: engine
    app.dependency_overrides[get_current_org] = lambda: demo_org
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def test_health_ok(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["model_version"] == MODEL_VERSION
    assert body["model_name"] in {"xgboost", "random_forest"}
    assert "critical_threshold" in body


def test_predict_returns_valid_payload(client: TestClient) -> None:
    response = client.post("/v1/predict", json=HIGH_RISK)
    assert response.status_code == 200
    body = response.json()
    assert body["customer_id"] == "CUST_TEST_HIGH"
    assert 0.0 <= body["churn_probability"] <= 1.0
    assert body["risk_tier"] in {"Low", "Medium", "Critical"}
    assert body["arr_at_risk"] == pytest.approx(640.0 * 12.0)
    assert isinstance(body["recommended_playbook"], str) and body["recommended_playbook"]
    assert body["model_version"] == MODEL_VERSION

    healthy = client.post("/v1/predict", json=HEALTHY)
    assert healthy.status_code == 200
    assert healthy.json()["churn_probability"] < body["churn_probability"]


def test_predict_rejects_invalid_channel(client: TestClient) -> None:
    bad = dict(HIGH_RISK, acquisition_channel="Billboard")
    response = client.post("/v1/predict", json=bad)
    assert response.status_code == 422


def test_dispatch_alert_logs_payload(client: TestClient, tmp_path, monkeypatch) -> None:
    log_path = tmp_path / "dispatched_alerts_log.json"
    monkeypatch.setattr("src.api.DISPATCH_LOG_PATH", log_path)

    response = client.post("/v1/dispatch-alert", json=HIGH_RISK)
    assert response.status_code == 200
    body = response.json()
    assert "dispatched" in body
    assert "prediction" in body
    assert 0.0 <= body["prediction"]["churn_probability"] <= 1.0
    assert log_path.exists()
    history = json.loads(log_path.read_text(encoding="utf-8"))
    assert isinstance(history, list) and history
    assert history[-1]["prediction"]["customer_id"] == "CUST_TEST_HIGH"

    # Explicit skip path: healthy account should not fire the critical webhook.
    skip = client.post("/v1/dispatch-alert", json=HEALTHY)
    assert skip.status_code == 200
    skip_body = skip.json()
    if skip_body["prediction"]["churn_probability"] <= CRITICAL_THRESHOLD:
        assert skip_body["dispatched"] is False
        assert "skipped" in skip_body["reason"] or skip_body["prediction"]["churn_probability"] <= CRITICAL_THRESHOLD
