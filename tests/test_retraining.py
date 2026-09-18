"""Tenant ML retraining, S3/volume persistence, and API model resolution."""

from __future__ import annotations

import asyncio
from typing import Optional

import pytest
from fastapi.testclient import TestClient

from src.api import app, get_engine, get_predict_engine
from src.auth import get_current_org
from tests.conftest import clerk_auth_headers
from src.churn_model import ChurnScoringEngine, dumps_engine
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import Organization, TelemetryEvent
from src.retraining_pipeline import (
    clear_tenant_engine_cache,
    load_tenant_engine,
    set_model_store,
    train_tenant_model,
)
from src.seed_commercial_demo import ACME_ORG_ID, GLOBEX_ORG_ID, seed_commercial_demo


class MemoryModelStore:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.puts = 0
        self.gets = 0

    def put_bytes(self, org_id: str, payload: bytes) -> str:
        self.puts += 1
        self.objects[org_id] = payload
        return f"s3://mock-rev-models/tenants/{org_id}/churn_engine.pkl"

    def get_bytes(self, org_id: str) -> Optional[bytes]:
        self.gets += 1
        return self.objects.get(org_id)


@pytest.fixture()
def tenant_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'retrain.db'}")
    monkeypatch.delenv("MODEL_S3_BUCKET", raising=False)
    monkeypatch.delenv("AWS_S3_BUCKET", raising=False)
    reset_engine()
    init_db()
    seed_commercial_demo()
    store = MemoryModelStore()
    set_model_store(store)
    clear_tenant_engine_cache()
    session = get_session_factory()()
    yield session, store
    session.close()
    set_model_store(None)
    clear_tenant_engine_cache()
    reset_engine()
    app.dependency_overrides.clear()


def test_train_tenant_model_uses_only_that_orgs_telemetry(tenant_db) -> None:
    session, store = tenant_db
    globex_events = (
        session.query(TelemetryEvent).filter(TelemetryEvent.org_id == GLOBEX_ORG_ID).count()
    )
    assert globex_events > 0
    result = asyncio.run(train_tenant_model(ACME_ORG_ID, session=session, store=store))
    assert result["ok"] is True, result
    assert result["uri"].startswith("s3://mock-rev-models/tenants/org_acme/")
    assert result["rows"] >= 8
    assert ACME_ORG_ID in store.objects
    assert GLOBEX_ORG_ID not in store.objects


def test_load_tenant_engine_falls_back_when_s3_empty(tenant_db) -> None:
    _session, store = tenant_db
    assert load_tenant_engine(ACME_ORG_ID, store=store) is None


def test_get_engine_loads_tenant_bundle_from_mocked_s3(tenant_db, processed_frame) -> None:
    _session, store = tenant_db
    custom = ChurnScoringEngine()
    custom.fit(processed_frame.head(400), tune=False)
    store.put_bytes(ACME_ORG_ID, dumps_engine(custom, version="tenant-test"))
    clear_tenant_engine_cache()

    loaded = load_tenant_engine(ACME_ORG_ID, store=store)
    assert loaded is not None
    assert loaded.fitted_ is True
    engine = get_engine(x_org_id=ACME_ORG_ID)
    assert engine is loaded


def test_predict_uses_tenant_model_without_breaking_override(tenant_db, processed_frame) -> None:
    session, store = tenant_db
    custom = ChurnScoringEngine()
    custom.fit(processed_frame.head(400), tune=False)
    store.put_bytes(ACME_ORG_ID, dumps_engine(custom))
    clear_tenant_engine_cache()
    org = session.get(Organization, ACME_ORG_ID)
    fallback = ChurnScoringEngine()
    fallback.fit(processed_frame.head(200), tune=False)
    app.dependency_overrides[get_current_org] = lambda: org
    app.dependency_overrides[get_engine] = lambda: fallback
    with TestClient(app) as client:
        response = client.post(
            "/v1/predict",
            headers=clerk_auth_headers(org_id=ACME_ORG_ID, role="Member"),
            json={
                "customer_id": "cus_retrain",
                "acquisition_channel": "Paid Search",
                "contract_type": "Monthly",
                "avg_weekly_logins": 1.0,
                "feature_adoption_score": 2.0,
                "support_tickets_raised": 4,
                "days_since_last_login": 22,
                "monthly_recurring_revenue": 500.0,
            },
        )
    assert response.status_code == 200, response.text
    assert 0.0 <= response.json()["churn_probability"] <= 1.0
    resolved = get_predict_engine(org=org, fallback=fallback)
    assert resolved is load_tenant_engine(ACME_ORG_ID, store=store)
