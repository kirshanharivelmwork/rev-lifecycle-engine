"""Dead-letter persistence for exhausted async / outbound work."""

from __future__ import annotations

import logging
from typing import Any, Optional

from sqlalchemy.orm import Session

from src.database import get_session_factory
from src.models_db import DeadLetterJob, utcnow

LOGGER = logging.getLogger(__name__)


def record_dead_letter(
    org_id: str,
    task_name: str,
    payload: Optional[dict[str, Any]],
    error_message: str,
    retry_count: int,
    session: Optional[Session] = None,
) -> DeadLetterJob:
    owns = session is None
    if session is None:
        session = get_session_factory()()
    try:
        job = DeadLetterJob(
            org_id=org_id,
            task_name=task_name,
            payload=payload or {},
            error_message=error_message[:4000] if error_message else None,
            retry_count=retry_count,
            failed_at=utcnow(),
            resolved=False,
        )
        session.add(job)
        LOGGER.critical(
            "Dead-letter %s org=%s retries=%s error=%s",
            task_name,
            org_id,
            retry_count,
            error_message,
        )
        if owns:
            session.commit()
        else:
            session.flush()
        return job
    except Exception:
        if owns:
            session.rollback()
        raise
    finally:
        if owns:
            session.close()
