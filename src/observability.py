"""Structured request logs with a request id. Never log tokens or secrets."""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextvars import ContextVar
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_CTX: ContextVar[str] = ContextVar("request_id", default="-")
_SENSITIVE_HEADERS = {
    "authorization",
    "cookie",
    "set-cookie",
    "x-api-key",
    "stripe-signature",
    "stripe-secret-key",
}
_SENSITIVE_KEYS = (
    "authorization",
    "token",
    "secret",
    "password",
    "api_key",
    "apikey",
    "stripe",
    "fernet",
    "bearer",
    "whsec",
    "sk_live",
    "sk_test",
    "rk_live",
)


class RequestIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = REQUEST_ID_CTX.get("-")
        return True


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", REQUEST_ID_CTX.get("-")),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    handler.addFilter(RequestIdFilter())
    root = logging.getLogger()
    if os.getenv("LOG_FORMAT", "json").lower() == "json":
        root.handlers = [handler]
        root.setLevel(os.getenv("LOG_LEVEL", "INFO").upper())
    logging.getLogger("src").addFilter(RequestIdFilter())


class RequestIdMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = request.headers.get("X-Request-Id") or uuid.uuid4().hex
        token = REQUEST_ID_CTX.set(request_id)
        logger = logging.getLogger("src.http")
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "unhandled_error method=%s path=%s",
                request.method,
                request.url.path,
            )
            REQUEST_ID_CTX.reset(token)
            raise
        response.headers["X-Request-Id"] = request_id
        logger.info(
            "request method=%s path=%s status=%s",
            request.method,
            request.url.path,
            response.status_code,
        )
        REQUEST_ID_CTX.reset(token)
        return response


def header_is_sensitive(name: str) -> bool:
    lowered = name.lower()
    if lowered in _SENSITIVE_HEADERS:
        return True
    return any(key in lowered for key in _SENSITIVE_KEYS)


def _redact_sentry_mapping(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if lowered in _SENSITIVE_HEADERS or any(token in lowered for token in _SENSITIVE_KEYS):
                redacted[key] = "[filtered]"
            else:
                redacted[key] = _redact_sentry_mapping(item)
        return redacted
    if isinstance(value, list):
        return [_redact_sentry_mapping(item) for item in value]
    return value


def sentry_before_send(event: dict[str, Any], _hint: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Drop auth headers, cookies, and secret-shaped fields before they leave the process."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = _redact_sentry_mapping(headers)
        elif isinstance(headers, list):
            cleaned = []
            for item in headers:
                if isinstance(item, (list, tuple)) and len(item) >= 2 and header_is_sensitive(str(item[0])):
                    cleaned.append([item[0], "[filtered]"])
                else:
                    cleaned.append(item)
            request["headers"] = cleaned
        request.pop("cookies", None)
        if "data" in request:
            request["data"] = _redact_sentry_mapping(request["data"])
        event["request"] = request
    for key in ("extra", "contexts", "user"):
        if isinstance(event.get(key), dict):
            event[key] = _redact_sentry_mapping(event[key])
    return event


def init_sentry() -> bool:
    """Initialize Sentry only when SENTRY_DSN is set. Never attach PII or auth."""
    dsn = (os.getenv("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        send_default_pii=False,
        before_send=sentry_before_send,
    )
    return True
