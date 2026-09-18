"""Organization API-key authentication."""

from __future__ import annotations

import hashlib
import secrets
from typing import Optional

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from src.billing import raise_if_inactive
from src.database import get_db
from src.models_db import Organization


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "rle") -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def get_current_org(
    x_api_key: Optional[str] = Header(default=None, alias="X-API-Key"),
    db: Session = Depends(get_db),
) -> Organization:
    if not x_api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing X-API-Key header",
        )
    org = db.query(Organization).filter(Organization.api_key == hash_api_key(x_api_key)).one_or_none()
    if org is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
        )
    raise_if_inactive(org)
    return org
