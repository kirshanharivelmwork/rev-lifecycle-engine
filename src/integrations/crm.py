"""Bi-directional HubSpot / Salesforce risk-score sync."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from src.models_db import CustomerAccount, Organization

LOGGER = logging.getLogger(__name__)
HUBSPOT_COMPANIES = "https://api.hubapi.com/crm/v3/objects/companies"
SF_API_VERSION = "v59.0"
RISK_PROPERTY = "Rev_Lifecycle_Risk_Score"


def crm_record_id(account: CustomerAccount) -> str:
    return (account.crm_account_id or account.customer_external_id or "").strip()


def _handle_status(response: httpx.Response, vendor: str) -> dict[str, Any]:
    code = int(response.status_code)
    if code == 401:
        LOGGER.warning("%s CRM sync unauthorized", vendor)
        return {"ok": False, "vendor": vendor, "status": 401, "error": "unauthorized"}
    if code == 429:
        LOGGER.warning("%s CRM sync rate-limited", vendor)
        return {"ok": False, "vendor": vendor, "status": 429, "error": "rate_limited"}
    if 200 <= code < 300:
        return {"ok": True, "vendor": vendor, "status": code}
    LOGGER.warning("%s CRM sync failed HTTP %s %s", vendor, code, response.text[:300])
    return {"ok": False, "vendor": vendor, "status": code, "error": response.text[:500]}


async def patch_hubspot_company(
    access_token: str,
    record_id: str,
    risk_score: float,
    *,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """PATCH HubSpot company custom property Rev_Lifecycle_Risk_Score."""
    url = f"{HUBSPOT_COMPANIES}/{record_id}"
    payload = {"properties": {RISK_PROPERTY: str(round(float(risk_score), 6))}}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=12.0)
    try:
        response = await client.patch(url, headers=headers, json=payload)
        return _handle_status(response, "hubspot")
    except httpx.HTTPError as exc:
        LOGGER.warning("HubSpot CRM transport error: %s", exc)
        return {"ok": False, "vendor": "hubspot", "error": str(exc)}
    finally:
        if owns:
            await client.aclose()


async def patch_salesforce_account(
    access_token: str,
    record_id: str,
    risk_score: float,
    *,
    instance_url: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> dict[str, Any]:
    """PATCH Salesforce Account custom field Rev_Lifecycle_Risk_Score."""
    base = (instance_url or os.getenv("SALESFORCE_INSTANCE_URL") or "").rstrip("/")
    if not base:
        return {"ok": False, "vendor": "salesforce", "error": "missing_instance_url"}
    url = f"{base}/services/data/{SF_API_VERSION}/sobjects/Account/{record_id}"
    payload = {RISK_PROPERTY: round(float(risk_score), 6)}
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=12.0)
    try:
        response = await client.patch(url, headers=headers, json=payload)
        return _handle_status(response, "salesforce")
    except httpx.HTTPError as exc:
        LOGGER.warning("Salesforce CRM transport error: %s", exc)
        return {"ok": False, "vendor": "salesforce", "error": str(exc)}
    finally:
        if owns:
            await client.aclose()


def sync_account_to_crm(org: Optional[Organization], account: CustomerAccount, risk_score: float) -> list[dict[str, Any]]:
    """Synchronous fire-and-document helper used by the dispatcher (no event loop required)."""
    if org is None:
        return []
    record_id = crm_record_id(account)
    if not record_id:
        return []
    results: list[dict[str, Any]] = []
    if org.hubspot_access_token:
        try:
            response = httpx.patch(
                f"{HUBSPOT_COMPANIES}/{record_id}",
                headers={
                    "Authorization": f"Bearer {org.hubspot_access_token}",
                    "Content-Type": "application/json",
                },
                json={"properties": {RISK_PROPERTY: str(round(float(risk_score), 6))}},
                timeout=12.0,
            )
            results.append(_handle_status(response, "hubspot"))
        except httpx.HTTPError as exc:
            LOGGER.warning("HubSpot CRM transport error: %s", exc)
            results.append({"ok": False, "vendor": "hubspot", "error": str(exc)})
    if org.salesforce_access_token:
        instance = org.salesforce_instance_url or os.getenv("SALESFORCE_INSTANCE_URL")
        try:
            results.append(
                _run_sf_sync(org.salesforce_access_token, record_id, risk_score, instance)
            )
        except httpx.HTTPError as exc:
            results.append({"ok": False, "vendor": "salesforce", "error": str(exc)})
    return results


def _run_sf_sync(token: str, record_id: str, risk_score: float, instance: Optional[str]) -> dict[str, Any]:
    base = (instance or "").rstrip("/")
    if not base:
        return {"ok": False, "vendor": "salesforce", "error": "missing_instance_url"}
    response = httpx.patch(
        f"{base}/services/data/{SF_API_VERSION}/sobjects/Account/{record_id}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={RISK_PROPERTY: round(float(risk_score), 6)},
        timeout=12.0,
    )
    return _handle_status(response, "salesforce")


def enqueue_crm_sync(org: Optional[Organization], account: CustomerAccount, risk_score: float) -> None:
    """Run CRM PATCHes off the request thread."""
    import threading

    thread = threading.Thread(
        target=sync_account_to_crm,
        args=(org, account, risk_score),
        daemon=True,
        name="crm-sync",
    )
    thread.start()
