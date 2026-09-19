"""Combined CSV diagnostic ingest for the 48-hour sales motion."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient

from src.api import app
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import ChurnAssessment, CustomerAccount, TelemetryEvent
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from tests.conftest import clerk_auth_headers

COMBINED_CSV = """customer_id,acquisition_channel,sales_touchpoints,cac_usd,monthly_recurring_revenue,contract_type,avg_weekly_logins,feature_adoption_score,support_tickets_raised,days_since_last_login,churned
csv_diag_1,Paid Search,3,820,199,Monthly,6.0,7.2,1,4,0
csv_diag_2,Outbound Cold Email,8,1600,890,Monthly,0.5,2.1,5,28,0
"""


@pytest.fixture()
def csv_client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'csv.db'}")
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


@pytest.fixture()
def combined_csv_bytes() -> bytes:
    return COMBINED_CSV.encode("utf-8")


def test_csv_upload_writes_churn_assessments(csv_client: TestClient, combined_csv_bytes: bytes) -> None:
    headers = clerk_auth_headers(org_id=ACME_ORG_ID, role="Admin")
    response = csv_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(combined_csv_bytes), "text/csv")},
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["accounts_upserted"] == 2
    assert body["assessments_written"] == 2

    session = get_session_factory()()
    accounts = (
        session.query(CustomerAccount)
        .filter(
            CustomerAccount.org_id == ACME_ORG_ID,
            CustomerAccount.customer_external_id.in_(["csv_diag_1", "csv_diag_2"]),
        )
        .all()
    )
    assert len(accounts) == 2
    snapshots = (
        session.query(TelemetryEvent)
        .filter(TelemetryEvent.org_id == ACME_ORG_ID, TelemetryEvent.event_name == "csv_snapshot")
        .count()
    )
    assert snapshots == 2
    assessments = (
        session.query(ChurnAssessment)
        .filter(
            ChurnAssessment.org_id == ACME_ORG_ID,
            ChurnAssessment.customer_account_id.in_([row.id for row in accounts]),
        )
        .count()
    )
    assert assessments >= 2
    session.close()

    again = csv_client.post(
        "/api/v1/ingestion/csv",
        headers=headers,
        files={"file": ("book.csv", io.BytesIO(combined_csv_bytes), "text/csv")},
    )
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "skipped"
    session = get_session_factory()()
    assert (
        session.query(TelemetryEvent)
        .filter(TelemetryEvent.org_id == ACME_ORG_ID, TelemetryEvent.event_name == "csv_snapshot")
        .count()
        == snapshots
    )
    session.close()


def test_csv_upload_is_admin_only(csv_client: TestClient, combined_csv_bytes: bytes) -> None:
    member = clerk_auth_headers(org_id=ACME_ORG_ID, role="Member")
    response = csv_client.post(
        "/api/v1/ingestion/csv",
        headers=member,
        files={"file": ("book.csv", io.BytesIO(combined_csv_bytes), "text/csv")},
    )
    assert response.status_code == 403
