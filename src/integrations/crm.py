"""Bi-directional HubSpot / Salesforce risk-score sync."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional

import httpx
from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.models_db import CustomerAccount, IngestionJob, Organization, utcnow
from src.security import decrypt_secret

LOGGER = logging.getLogger(__name__)
HUBSPOT_COMPANIES = "https://api.hubapi.com/crm/v3/objects/companies"
SF_API_VERSION = "v59.0"
RISK_PROPERTY = "Rev_Lifecycle_Risk_Score"
CRM_SOURCE = "crm_sync"
CRM_RATE_LIMITED = "rate_limited"
DEFAULT_RETRY_AFTER_SECONDS = 1.0
MAX_RETRY_AFTER_SECONDS = 3600.0
MAX_CRM_ATTEMPTS = 8


def crm_record_id(account: CustomerAccount) -> str:
    return (account.crm_account_id or account.customer_external_id or "").strip()


def parse_retry_after(value: Optional[str], *, now: Optional[datetime] = None) -> float:
    """Return seconds to wait from a CRM Retry-After header (delta-seconds or HTTP-date)."""
    if value is None:
        return DEFAULT_RETRY_AFTER_SECONDS
    raw = str(value).strip()
    if not raw:
        return DEFAULT_RETRY_AFTER_SECONDS
    try:
        return min(max(float(raw), 0.0), MAX_RETRY_AFTER_SECONDS)
    except ValueError:
        pass
    try:
        retry_at = parsedate_to_datetime(raw)
        current = now or datetime.now(timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        return min(max((retry_at - current).total_seconds(), 0.0), MAX_RETRY_AFTER_SECONDS)
    except (TypeError, ValueError, OverflowError):
        return DEFAULT_RETRY_AFTER_SECONDS


def retry_after_from_response(response: Any, *, now: Optional[datetime] = None) -> float:
    headers = getattr(response, "headers", None) or {}
    getter = headers.get if hasattr(headers, "get") else lambda _key, default=None: None
    value = getter("Retry-After") or getter("retry-after")
    return parse_retry_after(value, now=now)


def exponential_backoff_seconds(retry_after: float, attempts: int) -> float:
    """Wait Retry-After on the first 429, then double for each subsequent rate limit."""
    attempt = max(int(attempts), 0)
    delay = float(retry_after) * (2**attempt)
    return min(max(delay, 0.0), MAX_RETRY_AFTER_SECONDS)


def next_attempt_at_from_429(
    response: Any,
    attempts: int,
    *,
    now: Optional[datetime] = None,
) -> datetime:
    current = now or utcnow()
    delay = exponential_backoff_seconds(retry_after_from_response(response, now=current), attempts)
    return current + timedelta(seconds=delay)


def _handle_status(response: httpx.Response, vendor: str) -> dict[str, Any]:
    code = int(response.status_code)
    if code == 401:
        LOGGER.warning("%s CRM sync unauthorized", vendor)
        return {"ok": False, "vendor": vendor, "status": 401, "error": "unauthorized"}
    if code == 429:
        retry_after = retry_after_from_response(response)
        LOGGER.warning("%s CRM sync rate-limited retry_after=%s", vendor, retry_after)
        return {
            "ok": False,
            "vendor": vendor,
            "status": 429,
            "error": "rate_limited",
            "retry_after": retry_after,
            "next_attempt_at": next_attempt_at_from_429(response, 0),
        }
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


def _patch_hubspot_sync(token: str, record_id: str, risk_score: float) -> dict[str, Any]:
    response = httpx.patch(
        f"{HUBSPOT_COMPANIES}/{record_id}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        json={"properties": {RISK_PROPERTY: str(round(float(risk_score), 6))}},
        timeout=12.0,
    )
    return _handle_status(response, "hubspot")


def sync_account_to_crm(org: Optional[Organization], account: CustomerAccount, risk_score: float) -> list[dict[str, Any]]:
    """Synchronous fire-and-document helper used by the dispatcher (no event loop required)."""
    if org is None:
        return []
    record_id = crm_record_id(account)
    if not record_id:
        return []
    results: list[dict[str, Any]] = []
    hubspot_token = decrypt_secret(org.hubspot_access_token)
    salesforce_key = decrypt_secret(org.salesforce_access_token)
    if hubspot_token:
        try:
            results.append(_patch_hubspot_sync(hubspot_token, record_id, risk_score))
        except httpx.HTTPError as exc:
            LOGGER.warning("HubSpot CRM transport error: %s", exc)
            results.append({"ok": False, "vendor": "hubspot", "error": str(exc)})
    if salesforce_key:
        instance = org.salesforce_instance_url or os.getenv("SALESFORCE_INSTANCE_URL")
        try:
            results.append(_run_sf_sync(salesforce_key, record_id, risk_score, instance))
        except httpx.HTTPError as exc:
            results.append({"ok": False, "vendor": "salesforce", "error": str(exc)})
    return results


def _enabled_vendors(org: Organization) -> list[str]:
    vendors: list[str] = []
    if decrypt_secret(org.hubspot_access_token):
        vendors.append("hubspot")
    if decrypt_secret(org.salesforce_access_token):
        vendors.append("salesforce")
    return vendors


def persist_crm_sync_job(
    session: Session,
    org: Organization,
    account: CustomerAccount,
    risk_score: float,
    *,
    vendors: Optional[list[str]] = None,
    attempts: int = 0,
    next_attempt_at: Optional[datetime] = None,
) -> IngestionJob:
    pending = vendors or _enabled_vendors(org)
    job = IngestionJob(
        org_id=org.org_id,
        source=CRM_SOURCE,
        payload={
            "account_id": account.id,
            "customer_external_id": account.customer_external_id,
            "crm_account_id": account.crm_account_id,
            "risk_score": float(risk_score),
            "vendors": pending,
        },
        status="queued",
        attempts=attempts,
        next_attempt_at=next_attempt_at or utcnow(),
        created_at=utcnow(),
    )
    session.add(job)
    session.flush()
    return job


def _reschedule_job(job: IngestionJob, result: dict[str, Any], pending_vendors: list[str]) -> None:
    attempts = int(job.attempts or 0)
    retry_after = float(result.get("retry_after") or DEFAULT_RETRY_AFTER_SECONDS)
    delay = exponential_backoff_seconds(retry_after, attempts)
    job.status = CRM_RATE_LIMITED
    job.last_error = f"{result.get('vendor')}:429 retry_after={retry_after}"
    job.next_attempt_at = utcnow() + timedelta(seconds=delay)
    job.attempts = attempts + 1
    payload = dict(job.payload or {})
    payload["vendors"] = pending_vendors
    payload["last_retry_after"] = retry_after
    payload["backoff_seconds"] = delay
    job.payload = payload
    LOGGER.info(
        "CRM job %s rate-limited; next_attempt_at=%s backoff=%ss",
        job.id,
        job.next_attempt_at,
        delay,
    )


def process_crm_sync_job(
    job_id: str,
    *,
    session: Optional[Session] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Execute one CRM PATCH job. On 429, persist next_attempt_at instead of sleeping."""
    from src.database import get_session_factory
    from src.rls import clear_tenant_context, set_tenant_context

    owns = session is None
    if session is None:
        session = get_session_factory()()
    current = now or utcnow()
    try:
        job = session.get(IngestionJob, job_id)
        if job is None:
            return {"ok": False, "error": "job_not_found"}
        if job.next_attempt_at and job.next_attempt_at > current:
            return {
                "ok": False,
                "status": "deferred",
                "job_id": job.id,
                "next_attempt_at": job.next_attempt_at.isoformat(),
            }
        set_tenant_context(session, job.org_id)
        org = session.get(Organization, job.org_id)
        payload = job.payload or {}
        account = session.get(CustomerAccount, payload.get("account_id"))
        if org is None or account is None:
            job.status = "failed"
            job.last_error = "organization_or_account_missing"
            job.completed_at = utcnow()
            if owns:
                session.commit()
            else:
                session.flush()
            return {"ok": False, "error": "organization_or_account_missing", "job_id": job.id}

        vendors = list(payload.get("vendors") or _enabled_vendors(org))
        job.status = "processing"
        session.flush()
        results = _dispatch_vendors(org, account, float(payload.get("risk_score") or 0.0), vendors)
        rate_limited = [row for row in results if row.get("status") == 429]
        if rate_limited:
            if int(job.attempts or 0) + 1 >= MAX_CRM_ATTEMPTS:
                from src.dead_letter import record_dead_letter

                job.status = "failed"
                job.last_error = "crm_rate_limit_exhausted"
                job.completed_at = utcnow()
                record_dead_letter(
                    job.org_id,
                    "crm_sync",
                    payload,
                    job.last_error,
                    int(job.attempts or 0) + 1,
                    session=session,
                )
                if owns:
                    session.commit()
                else:
                    session.flush()
                return {"ok": False, "status": "failed", "job_id": job.id, "results": results}
            pending = [row["vendor"] for row in results if not row.get("ok")]
            _reschedule_job(job, rate_limited[0], pending or vendors)
            if owns:
                session.commit()
            else:
                session.flush()
            return {
                "ok": False,
                "status": CRM_RATE_LIMITED,
                "job_id": job.id,
                "next_attempt_at": job.next_attempt_at.isoformat() if job.next_attempt_at else None,
                "backoff_seconds": (job.payload or {}).get("backoff_seconds"),
                "results": results,
            }

        job.status = "succeeded" if all(row.get("ok") for row in results) or not results else "failed"
        job.completed_at = utcnow()
        job.next_attempt_at = None
        job.last_error = None if job.status == "succeeded" else str(results)
        if owns:
            session.commit()
        else:
            session.flush()
        return {"ok": job.status == "succeeded", "status": job.status, "job_id": job.id, "results": results}
    except Exception:
        if owns:
            session.rollback()
        raise
    finally:
        clear_tenant_context(session)
        if owns:
            session.close()


def _dispatch_vendors(
    org: Organization,
    account: CustomerAccount,
    risk_score: float,
    vendors: list[str],
) -> list[dict[str, Any]]:
    record_id = crm_record_id(account)
    if not record_id:
        return [{"ok": False, "error": "missing_crm_record_id"}]
    results: list[dict[str, Any]] = []
    hubspot_token = decrypt_secret(org.hubspot_access_token)
    salesforce_key = decrypt_secret(org.salesforce_access_token)
    if "hubspot" in vendors and hubspot_token:
        try:
            results.append(_patch_hubspot_sync(hubspot_token, record_id, risk_score))
        except httpx.HTTPError as exc:
            results.append({"ok": False, "vendor": "hubspot", "error": str(exc)})
    if "salesforce" in vendors and salesforce_key:
        instance = org.salesforce_instance_url or os.getenv("SALESFORCE_INSTANCE_URL")
        try:
            results.append(_run_sf_sync(salesforce_key, record_id, risk_score, instance))
        except httpx.HTTPError as exc:
            results.append({"ok": False, "vendor": "salesforce", "error": str(exc)})
    return results


def process_due_crm_sync_jobs(
    *,
    session: Optional[Session] = None,
    now: Optional[datetime] = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Dispatcher cron: run CRM jobs whose next_attempt_at has elapsed. Never sleeps."""
    from src.database import get_session_factory

    owns = session is None
    if session is None:
        session = get_session_factory()()
    current = now or utcnow()
    processed = 0
    deferred = 0
    rate_limited = 0
    succeeded = 0
    try:
        due = (
            session.query(IngestionJob)
            .filter(
                IngestionJob.source == CRM_SOURCE,
                IngestionJob.status.in_(["queued", CRM_RATE_LIMITED]),
                or_(IngestionJob.next_attempt_at.is_(None), IngestionJob.next_attempt_at <= current),
            )
            .order_by(IngestionJob.created_at.asc())
            .limit(limit)
            .all()
        )
        job_ids = [job.id for job in due]
        for job_id in job_ids:
            result = process_crm_sync_job(job_id, session=session, now=current)
            processed += 1
            status = result.get("status")
            if status == "deferred":
                deferred += 1
            elif status == CRM_RATE_LIMITED:
                rate_limited += 1
            elif result.get("ok"):
                succeeded += 1
        if owns:
            session.commit()
        else:
            session.flush()
        return {
            "ok": True,
            "processed": processed,
            "succeeded": succeeded,
            "rate_limited": rate_limited,
            "deferred": deferred,
        }
    finally:
        if owns:
            session.close()


def enqueue_crm_sync(
    org: Optional[Organization],
    account: CustomerAccount,
    risk_score: float,
    *,
    session: Optional[Session] = None,
) -> Optional[IngestionJob]:
    """Persist a CRM sync job and attempt it once. 429s are deferred via next_attempt_at."""
    from src.database import get_session_factory

    if org is None:
        return None
    if not crm_record_id(account):
        return None
    if not _enabled_vendors(org):
        return None

    owns = session is None
    if session is None:
        session = get_session_factory()()
    try:
        job = persist_crm_sync_job(session, org, account, risk_score)
        session.flush()
        job_id = job.id
        if owns:
            session.commit()
        else:
            session.commit()
        from src.tasks import process_crm_sync

        process_crm_sync.delay(job_id)
        session.expire_all()
        return session.get(IngestionJob, job_id)
    finally:
        if owns:
            session.close()
