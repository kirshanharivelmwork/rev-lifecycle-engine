"""Symmetric encryption at rest for tenant secrets (Fernet)."""

from __future__ import annotations

import argparse
import base64
import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.types import TypeDecorator

ORGANIZATION_SECRET_COLUMNS = (
    "hubspot_access_token",
    "salesforce_access_token",
    "stripe_webhook_secret",
    "instantly_api_key",
    "slack_webhook_url",
    "resend_api_key",
    "apollo_api_key",
)

_fernet: Fernet | None = None
_cached_key: str | None = None


class EncryptionKeyMissing(RuntimeError):
    """Raised when ENCRYPTION_KEY is unset or not a valid Fernet key."""


def generate_encryption_key() -> str:
    """Return a url-safe Fernet key suitable for ENCRYPTION_KEY."""
    return Fernet.generate_key().decode("ascii")


def reset_fernet_cache() -> None:
    """Drop the process-local Fernet client (tests that rotate ENCRYPTION_KEY)."""
    global _fernet, _cached_key
    _fernet = None
    _cached_key = None


def get_encryption_key() -> str:
    raw = (os.getenv("ENCRYPTION_KEY") or "").strip()
    if not raw:
        raise EncryptionKeyMissing(
            "ENCRYPTION_KEY is not set. Generate one with "
            "`python -m src.security` and add it to .env and docker-compose.yml."
        )
    return raw


def get_fernet() -> Fernet:
    global _fernet, _cached_key
    key = get_encryption_key()
    if _fernet is None or _cached_key != key:
        try:
            _fernet = Fernet(key.encode("ascii") if isinstance(key, str) else key)
        except (ValueError, TypeError) as exc:
            raise EncryptionKeyMissing(
                "ENCRYPTION_KEY is not a valid Fernet key. Run `python -m src.security`."
            ) from exc
        _cached_key = key
    return _fernet


def looks_like_fernet_token(value: str) -> bool:
    """True when *value* is Fernet ciphertext (version 0x80), not necessarily ours."""
    if not value or len(value) < 80:
        return False
    try:
        raw = base64.urlsafe_b64decode(value.encode("ascii"))
    except (ValueError, UnicodeEncodeError):
        return False
    return len(raw) >= 57 and raw[0] == 0x80


def encrypt_secret(value: Optional[str]) -> Optional[str]:
    if value is None or value == "":
        return value
    if looks_like_fernet_token(value):
        return value
    return get_fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(value: Optional[str]) -> Optional[str]:
    """Decrypt a Fernet token. Legacy plaintext is returned unchanged."""
    if value is None or value == "":
        return value
    try:
        return get_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value


class EncryptedText(TypeDecorator):
    """SQLAlchemy column type: ciphertext in the database, plaintext in memory."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value, _dialect):
        return encrypt_secret(value)

    def process_result_value(self, value, _dialect):
        return decrypt_secret(value)


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a Fernet ENCRYPTION_KEY.")
    parser.parse_args()
    print(generate_encryption_key())


if __name__ == "__main__":
    main()
