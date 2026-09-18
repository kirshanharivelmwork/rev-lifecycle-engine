"""HubSpot / Salesforce risk-score PATCH helpers."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from src.integrations.crm import (
    crm_record_id,
    patch_hubspot_company,
    patch_salesforce_account,
    sync_account_to_crm,
)
from src.models_db import CustomerAccount, Organization


class _Resp:
    def __init__(self, status_code: int, text: str = "") -> None:
        self.status_code = status_code
        self.text = text


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
