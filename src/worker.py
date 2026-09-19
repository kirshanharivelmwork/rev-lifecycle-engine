"""Celery application: durable Redis broker for ingestion, CRM, and dispatch jobs."""

from __future__ import annotations

import os

from celery import Celery

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


def redis_url() -> str:
    return (os.getenv("REDIS_URL") or DEFAULT_REDIS_URL).strip() or DEFAULT_REDIS_URL


celery_app = Celery(
    "rev_lifecycle_engine",
    broker=redis_url(),
    backend=redis_url(),
    include=["src.tasks"],
)

celery_app.conf.update(
    broker_url=redis_url(),
    result_backend=redis_url(),
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
    task_always_eager=os.getenv("CELERY_TASK_ALWAYS_EAGER", "").lower() in {"1", "true", "yes"},
    task_eager_propagates=True,
)
