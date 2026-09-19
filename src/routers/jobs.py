"""Failed ingestion jobs and dead-letter queue for a tenant."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from src.auth import AuthUser, get_current_org, require_admin_role
from src.database import get_db
from src.models_db import DeadLetterJob, IngestionJob, Organization
from src.tasks import process_ingestion_job

router = APIRouter(tags=["jobs"])


def _when(value: Any) -> Optional[str]:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


@router.get("/jobs")
def list_failed_jobs(
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    dead = (
        db.query(DeadLetterJob)
        .filter(DeadLetterJob.org_id == org.org_id)
        .order_by(DeadLetterJob.failed_at.desc())
        .limit(100)
        .all()
    )
    failed_ingest = (
        db.query(IngestionJob)
        .filter(IngestionJob.org_id == org.org_id, IngestionJob.status == "failed")
        .order_by(IngestionJob.created_at.desc())
        .limit(100)
        .all()
    )
    return {
        "org_id": org.org_id,
        "dead_letter": [
            {
                "id": row.id,
                "task_name": row.task_name,
                "error_message": row.error_message,
                "retry_count": row.retry_count,
                "failed_at": _when(row.failed_at),
                "resolved": row.resolved,
            }
            for row in dead
        ],
        "ingestion_failures": [
            {
                "id": row.id,
                "source": row.source,
                "status": row.status,
                "attempts": row.attempts,
                "last_error": row.last_error,
                "created_at": _when(row.created_at),
                "completed_at": _when(row.completed_at),
            }
            for row in failed_ingest
        ],
    }


@router.post("/jobs/dead-letter/{job_id}/resolve")
def resolve_dead_letter(
    job_id: str,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = (
        db.query(DeadLetterJob)
        .filter(DeadLetterJob.id == job_id, DeadLetterJob.org_id == org.org_id)
        .one_or_none()
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dead-letter job not found")
    job.resolved = True
    db.flush()
    return {"ok": True, "id": job.id, "resolved": True}


@router.post("/jobs/dead-letter/{job_id}/retry")
def retry_dead_letter(
    job_id: str,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = (
        db.query(DeadLetterJob)
        .filter(DeadLetterJob.id == job_id, DeadLetterJob.org_id == org.org_id)
        .one_or_none()
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dead-letter job not found")
    payload = job.payload if isinstance(job.payload, dict) else {}
    ingestion_id = payload.get("job_id") or payload.get("ingestion_job_id")
    if ingestion_id:
        ingest = (
            db.query(IngestionJob)
            .filter(IngestionJob.id == str(ingestion_id), IngestionJob.org_id == org.org_id)
            .one_or_none()
        )
        if ingest is not None:
            ingest.status = "queued"
            ingest.last_error = None
            db.flush()
            process_ingestion_job.delay(ingest.id)
            job.resolved = True
            db.flush()
            return {"ok": True, "id": job.id, "retried": "ingestion", "job_id": ingest.id}
    ingest = IngestionJob(
        org_id=org.org_id,
        source=str(payload.get("source") or "stripe"),
        payload=payload,
        status="queued",
    )
    db.add(ingest)
    job.resolved = True
    db.flush()
    process_ingestion_job.delay(ingest.id)
    return {"ok": True, "id": job.id, "retried": "ingestion", "job_id": ingest.id}


@router.post("/jobs/ingestion/{job_id}/retry")
def retry_ingestion_job(
    job_id: str,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    job = (
        db.query(IngestionJob)
        .filter(IngestionJob.id == job_id, IngestionJob.org_id == org.org_id)
        .one_or_none()
    )
    if job is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ingestion job not found")
    job.status = "queued"
    job.last_error = None
    db.flush()
    process_ingestion_job.delay(job.id)
    return {"ok": True, "id": job.id, "status": "queued"}
