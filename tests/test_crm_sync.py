"""HubSpot / Salesforce risk-score PATCH helpers."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import httpx
import pytest

from src.database import get_session_factory, init_db, reset_engine
from src.dispatcher import run_dispatcher_cron
from src.integrations.crm import (
    CRM_RATE_LIMITED,
    CRM_SOURCE,
    crm_record_id,
    enqueue_crm_sync,
    exponential_backoff_seconds,
    parse_retry_after,
    patch_hubspot_company,
    patch_salesforce_account,
    persist_crm_sync_job,
    process_crm_sync_job,
    process_due_crm_sync_jobs,
    sync_account_to_crm,
)
from src.models_db import CustomerAccount, IngestionJob, Organization, utcnow
from src.security import decrypt_secret
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo


class _Resp:
    def __init__(self, status_code: int, text: str = "", headers: dict | None = None) -> None:
        self.status_code = status_code
        self.text = text
        self.headers = headers or {}


class _AsyncClient:
    def __init__(self, status_code: int = 200) -> None:
        self.status_code = status_code
        self.calls = []

    async def patch(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        return _Resp(self.status_code)

    async def aclose(self):
        return None


def test_crm_record_id_prefers_crm_account_id() -> None:
    account = CustomerAccount(
        org_id="org_acme",
        customer_external_id="cus_ext",
        crm_account_id="001SF",
    )
    assert crm_record_id(account) == "001SF"


def test_patch_hubspot_company_success() -> None:
    client = _AsyncClient(200)

    async def _run():
        return await patch_hubspot_company("pat-hub", "comp_1", 0.91, client=client)

    result = asyncio.run(_run())
    assert result["ok"] is True
    assert result["vendor"] == "hubspot"
    assert "comp_1" in client.calls[0]["url"]
    assert client.calls[0]["json"]["properties"]["Rev_Lifecycle_Risk_Score"] == "0.91"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer pat-hub"


def test_patch_hubspot_unauthorized() -> None:
    client = _AsyncClient(401)

    async def _run():
        return await patch_hubspot_company("bad", "comp_1", 0.5, client=client)

    result = asyncio.run(_run())
    assert result["ok"] is False
    assert result["status"] == 401
    assert result["error"] == "unauthorized"


def test_patch_salesforce_rate_limited() -> None:
    client = _AsyncClient(429)

    async def _run():
        return await patch_salesforce_account(
            "sf-token",
            "001xx",
            0.88,
            instance_url="https://example.my.salesforce.com",
            client=client,
        )

    result = asyncio.run(_run())
    assert result["ok"] is False
    assert result["status"] == 429
    assert "sobjects/Account/001xx" in client.calls[0]["url"]


def test_sync_account_to_crm_patches_both(monkeypatch) -> None:
    calls = []

    def fake_patch(url, headers=None, json=None, timeout=None):
        calls.append({"url": url, "headers": headers, "json": json})
        return _Resp(200)

    monkeypatch.setattr(httpx, "patch", fake_patch)
    org = Organization(
        org_id="org_acme",
        name="Acme",
        api_key="x",
        hubspot_access_token="hs",
        salesforce_access_token="sf",
        salesforce_instance_url="https://acme.my.salesforce.com",
    )
    account = CustomerAccount(org_id="org_acme", customer_external_id="cus_9", crm_account_id="crm_9")
    results = sync_account_to_crm(org, account, 0.77)
    assert len(results) == 2
    assert all(row["ok"] for row in results)
    vendors = {c["url"] for c in calls}
    assert any("hubapi.com" in u for u in vendors)
    assert any("salesforce.com" in u for u in vendors)
    assert calls[0]["headers"]["Authorization"] == f"Bearer {decrypt_secret(org.hubspot_token)}"
    assert any(c["headers"]["Authorization"] == "Bearer sf" for c in calls)


def test_parse_retry_after_seconds_and_http_date() -> None:
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after(None) == 1.0
    assert exponential_backoff_seconds(12, 0) == 12.0
    assert exponential_backoff_seconds(12, 1) == 24.0
    assert exponential_backoff_seconds(12, 2) == 48.0


def test_async_429_includes_retry_after() -> None:
    client = _AsyncClient(429)
    client.headers_override = {"Retry-After": "7"}

    async def patch(url, headers=None, json=None):
        client.calls.append({"url": url})
        return _Resp(429, headers={"Retry-After": "7"})

    client.patch = patch  # type: ignore[method-assign]

    async def _run():
        return await patch_hubspot_company("pat", "comp_1", 0.5, client=client)

    result = asyncio.run(_run())
    assert result["status"] == 429
    assert result["retry_after"] == 7.0
    assert result["error"] == "rate_limited"


@pytest.fixture()
def crm_db(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'crm.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    org.hubspot_access_token = "hs-token"
    org.salesforce_access_token = None
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID)
        .first()
    )
    account.crm_account_id = "comp_rate"
    session.commit()
    yield session
    session.close()
    reset_engine()


def test_enqueue_429_persists_next_attempt_at_without_sleeping(crm_db, monkeypatch) -> None:
    slept = []

    def boom(_seconds):
        slept.append(_seconds)
        raise AssertionError("CRM 429 must not sleep in the worker")

    monkeypatch.setattr("time.sleep", boom)
    monkeypatch.setattr("asyncio.sleep", boom)

    def fake_patch(url, headers=None, json=None, timeout=None):
        return _Resp(429, headers={"Retry-After": "15"})

    monkeypatch.setattr(httpx, "patch", fake_patch)
    session = crm_db
    org = session.get(Organization, ACME_ORG_ID)
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID, CustomerAccount.crm_account_id == "comp_rate")
        .one()
    )
    before = utcnow()
    job = enqueue_crm_sync(org, account, 0.91, session=session)
    session.commit()
    session.refresh(job)
    assert job is not None
    assert job.source == CRM_SOURCE
    assert job.status == CRM_RATE_LIMITED
    assert job.next_attempt_at is not None
    wait = (job.next_attempt_at - before).total_seconds()
    assert 14 <= wait <= 20
    assert slept == []
    assert (job.payload or {}).get("backoff_seconds") == 15.0


def test_dispatcher_cron_skips_until_next_attempt_at(crm_db, monkeypatch) -> None:
    patches = []

    def fake_patch(url, headers=None, json=None, timeout=None):
        patches.append(url)
        return _Resp(200)

    monkeypatch.setattr(httpx, "patch", fake_patch)
    session = crm_db
    org = session.get(Organization, ACME_ORG_ID)
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID, CustomerAccount.crm_account_id == "comp_rate")
        .one()
    )
    future = utcnow() + timedelta(minutes=5)
    job = persist_crm_sync_job(session, org, account, 0.8, next_attempt_at=future)
    job.status = CRM_RATE_LIMITED
    session.commit()

    skipped = run_dispatcher_cron(session=session, now=utcnow())
    session.commit()
    assert skipped["processed"] == 0
    session.refresh(job)
    assert job.status == CRM_RATE_LIMITED
    assert patches == []

    due = process_due_crm_sync_jobs(session=session, now=future + timedelta(seconds=1))
    session.commit()
    session.refresh(job)
    assert due["processed"] == 1
    assert due["succeeded"] == 1
    assert job.status == "succeeded"
    assert job.next_attempt_at is None
    assert patches


def test_exponential_backoff_on_second_429(crm_db, monkeypatch) -> None:
    def fake_patch(url, headers=None, json=None, timeout=None):
        return _Resp(429, headers={"Retry-After": "10"})

    monkeypatch.setattr(httpx, "patch", fake_patch)
    session = crm_db
    org = session.get(Organization, ACME_ORG_ID)
    account = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == ACME_ORG_ID, CustomerAccount.crm_account_id == "comp_rate")
        .one()
    )
    job = persist_crm_sync_job(session, org, account, 0.66)
    session.commit()
    first = process_crm_sync_job(job.id, session=session)
    session.commit()
    session.refresh(job)
    assert first["status"] == CRM_RATE_LIMITED
    assert (job.payload or {}).get("backoff_seconds") == 10.0
    job.next_attempt_at = utcnow()
    session.commit()
    second = process_crm_sync_job(job.id, session=session)
    session.commit()
    session.refresh(job)
    assert second["status"] == CRM_RATE_LIMITED
    assert (job.payload or {}).get("backoff_seconds") == 20.0
    assert job.attempts == 2
