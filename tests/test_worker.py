"""Celery app is brokered by REDIS_URL and tasks execute eagerly in tests."""

from __future__ import annotations

from src.worker import celery_app, redis_url


def test_celery_app_uses_redis_url(monkeypatch) -> None:
    monkeypatch.setenv("REDIS_URL", "redis://broker.example:6379/2")
    assert redis_url() == "redis://broker.example:6379/2"
    assert celery_app.conf.task_always_eager is True
    assert "src.tasks" in (celery_app.conf.include or [])
