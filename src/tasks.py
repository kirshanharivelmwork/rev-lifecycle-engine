"""Asynchronous ingestion workers (BackgroundTasks today; Celery/RQ-compatible)."""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

from src.database import get_session_factory
from src.dispatcher import evaluate_and_trigger_actions
from src.models_db import IngestionJob, Organization, utcnow
from src.routers.account_ops import apply_stripe_event, persist_telemetry_items

LOGGER = logging.getLogger(__name__)
MAX_ATTEMPTS = 3


def _mark(job: IngestionJob, status: str, error: Optional[str] = None) -> None:
    job.status = status
    job.last_error = error
    if status in {"succeeded", "failed"}:
        job.completed_at = utcnow()


def process_ingestion_job(job_id: str, max_attempts: int = MAX_ATTEMPTS) -> dict[str, Any]:
    """DB transform → XGBoost scoring → alert evaluation, with bounded retries.

    Celery/RQ hook::

        @shared_task(bind=True, max_retries=3)
        def process_ingestion_job_task(self, job_id: str):
            return process_ingestion_job(job_id)
    """
    last_error = None
    for attempt in range(1, max_attempts + 1):
        session = get_session_factory()()
        try:
            job = session.get(IngestionJob, job_id)
            if job is None:
                return {"ok": False, "error": "job_not_found"}
            job.attempts = attempt
            job.status = "processing"
            session.commit()

            org = session.get(Organization, job.org_id)
            if org is None:
                raise RuntimeError("organization missing for ingestion job")
            from src.rls import set_tenant_context

            set_tenant_context(session, org.org_id)
            payload = job.payload or {}
            source = job.source
            if source == "stripe":
                account = apply_stripe_event(session, org, payload)
                if payload.get("type") in {
                    "invoice.payment_failed",
                    "customer.subscription.updated",
                    "customer.subscription.created",
                }:
                    evaluate_and_trigger_actions(account.id, org.org_id, session=session)
            elif source == "telemetry":
                persist_telemetry_items(session, org, payload)
            else:
                raise RuntimeError(f"Unknown ingestion source {source}")

            job = session.get(IngestionJob, job_id)
            _mark(job, "succeeded")
            session.commit()
            return {"ok": True, "job_id": job_id, "attempts": attempt}
        except Exception as exc:
            last_error = str(exc)
            LOGGER.warning("Ingestion job %s attempt %s failed: %s", job_id, attempt, exc)
            try:
                session.rollback()
            except Exception:
                pass
            session = get_session_factory()()
            org_id = "unknown"
            payload: dict = {}
            try:
                job = session.get(IngestionJob, job_id)
                if job:
                    org_id = job.org_id
                    payload = job.payload if isinstance(job.payload, dict) else {}
                    _mark(job, "failed" if attempt >= max_attempts else "queued", last_error)
                    session.commit()
            finally:
                session.close()
            if attempt >= max_attempts:
                from src.dead_letter import record_dead_letter

                record_dead_letter(
                    org_id,
                    "process_ingestion_job",
                    payload,
                    last_error or "unknown",
                    attempt,
                )
            else:
                time.sleep(min(0.05 * attempt, 0.2))
        finally:
            session.close()
    return {"ok": False, "job_id": job_id, "error": last_error}


try:  # Optional Celery compatibility; unused unless Celery is installed.
    from celery import shared_task

    @shared_task(bind=True, max_retries=3, name="src.tasks.process_ingestion_job")
    def process_ingestion_job_celery(self, job_id: str) -> dict[str, Any]:  # pragma: no cover
        try:
            return process_ingestion_job(job_id)
        except Exception as exc:
            raise self.retry(exc=exc, countdown=2)
except Exception:  # pragma: no cover
    process_ingestion_job_celery = None  # type: ignore[assignment]
