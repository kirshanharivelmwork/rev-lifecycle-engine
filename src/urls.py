"""Public hostnames used in Settings copy-to-clipboard webhook URLs."""

from __future__ import annotations

import os


def public_api_base() -> str:
    return (
        os.getenv("PUBLIC_API_URL")
        or os.getenv("NEXT_PUBLIC_API_URL")
        or os.getenv("FRONTEND_URL")
        or os.getenv("APP_URL")
        or "http://localhost:3000"
    ).rstrip("/")


def webhook_urls() -> dict[str, str]:
    base = public_api_base()
    return {
        "stripe": f"{base}/api/v1/webhooks/stripe",
        "telemetry": f"{base}/api/v1/webhooks/telemetry",
    }
