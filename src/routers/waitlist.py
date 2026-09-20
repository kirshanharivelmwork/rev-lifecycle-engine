"""Auth-protected Pro waitlist signups (one row per org)."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.auth import AuthUser, get_current_org, get_current_user
from src.database import get_db
from src.models_db import Organization, WaitlistSignup, utcnow

router = APIRouter(tags=["waitlist"])


class WaitlistRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email: str = Field(..., min_length=3, max_length=255)
    org_id: Optional[str] = None


@router.post("/waitlist")
def join_waitlist(
    payload: WaitlistRequest,
    user: AuthUser = Depends(get_current_user),
    org: Organization = Depends(get_current_org),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    email = payload.email.strip().lower()
    existing = db.query(WaitlistSignup).filter(WaitlistSignup.org_id == org.org_id).one_or_none()
    if existing is not None:
        if email and existing.email != email:
            existing.email = email
            existing.updated_at = utcnow()
            db.flush()
        return {
            "ok": True,
            "status": "already_signed_up",
            "org_id": org.org_id,
            "email": existing.email,
        }
    row = WaitlistSignup(
        org_id=org.org_id,
        email=email,
        actor_id=user.user_id,
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        existing = db.query(WaitlistSignup).filter(WaitlistSignup.org_id == org.org_id).one()
        return {
            "ok": True,
            "status": "already_signed_up",
            "org_id": org.org_id,
            "email": existing.email,
        }
    return {"ok": True, "status": "joined", "org_id": org.org_id, "email": email}
