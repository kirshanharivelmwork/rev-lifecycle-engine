"""Sentry stays dark unless a DSN is configured, and never ships Authorization."""

from __future__ import annotations

from src.observability import init_sentry, sentry_before_send


def test_init_sentry_noop_without_dsn(monkeypatch) -> None:
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert init_sentry() is False


def test_sentry_before_send_strips_authorization() -> None:
    event = {
        "request": {
            "headers": {"Authorization": "Bearer secret-jwt", "Content-Type": "application/json"},
            "cookies": {"session": "abc"},
            "data": {"stripe_secret": "sk_live_x", "org_id": "org_acme"},
        },
        "extra": {"api_key": "rle_secret"},
    }
    cleaned = sentry_before_send(event, None)
    assert cleaned is not None
    assert cleaned["request"]["headers"]["Authorization"] == "[filtered]"
    assert cleaned["request"]["headers"]["Content-Type"] == "application/json"
    assert "cookies" not in cleaned["request"]
    assert cleaned["request"]["data"]["stripe_secret"] == "[filtered]"
    assert cleaned["request"]["data"]["org_id"] == "org_acme"
    assert cleaned["extra"]["api_key"] == "[filtered]"
