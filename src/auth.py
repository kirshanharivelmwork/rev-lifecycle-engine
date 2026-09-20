"""Clerk JWT authentication and role-based access control."""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass, field
from typing import Any, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import InvalidTokenError, PyJWKClient
from sqlalchemy.orm import Session

from src.billing import raise_if_inactive
from src.database import get_db
from src.entitlements import FREE_PLAN, apply_reverse_trial_from_claims, is_reverse_trial
from src.models_db import Organization

ADMIN_ROLE = "Admin"
MEMBER_ROLE = "Member"
_BEARER = HTTPBearer(auto_error=False)
_jwks_client: Optional[PyJWKClient] = None

_ADMIN_ALIASES = frozenset({"admin", "org:admin", "org_admin", ADMIN_ROLE.lower()})
_ROLE_NORMALIZE = {
    "admin": ADMIN_ROLE,
    "org:admin": ADMIN_ROLE,
    "org_admin": ADMIN_ROLE,
    "member": MEMBER_ROLE,
    "org:member": MEMBER_ROLE,
    "org_member": MEMBER_ROLE,
    "basic": MEMBER_ROLE,
}


@dataclass
class AuthUser:
    """Authenticated Clerk principal bound to a tenant."""

    user_id: str
    org_id: str
    roles: list[str] = field(default_factory=list)
    claims: dict[str, Any] = field(default_factory=dict)

    @property
    def role(self) -> str:
        if self.is_admin:
            return ADMIN_ROLE
        return self.roles[0] if self.roles else MEMBER_ROLE

    @property
    def is_admin(self) -> bool:
        return any(_is_admin_role(role) for role in self.roles)


def hash_api_key(plaintext: str) -> str:
    """SHA-256 digest used for legacy webhook write keys and seeded tenants."""
    return hashlib.sha256(plaintext.encode("utf-8")).hexdigest()


def generate_api_key(prefix: str = "rle") -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def clerk_issuer() -> Optional[str]:
    issuer = os.getenv("CLERK_ISSUER") or os.getenv("CLERK_FRONTEND_API")
    return issuer.rstrip("/") if issuer else None


def clerk_jwks_url() -> str:
    explicit = os.getenv("CLERK_JWKS_URL")
    if explicit:
        return explicit
    issuer = clerk_issuer()
    if issuer:
        return f"{issuer}/.well-known/jwks.json"
    domain = os.getenv("CLERK_DOMAIN")
    if domain:
        return f"https://{domain.rstrip('/')}/.well-known/jwks.json"
    raise HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="Clerk JWKS endpoint is not configured",
    )


def get_jwks_client() -> PyJWKClient:
    global _jwks_client
    if _jwks_client is None:
        _jwks_client = PyJWKClient(clerk_jwks_url(), cache_jwk_set=True)
    return _jwks_client


def reset_jwks_client() -> None:
    global _jwks_client
    _jwks_client = None


def _is_admin_role(role: str) -> bool:
    return str(role).strip().lower() in _ADMIN_ALIASES


def _normalize_role(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return _ROLE_NORMALIZE.get(text.lower(), text)


def _roles_from_claims(claims: dict[str, Any]) -> list[str]:
    collected: list[str] = []
    org_claim = claims.get("o") if isinstance(claims.get("o"), dict) else {}
    candidates: list[Any] = [
        claims.get("roles"),
        claims.get("org_roles"),
        claims.get("role"),
        claims.get("org_role"),
        org_claim.get("rol") if isinstance(org_claim, dict) else None,
        org_claim.get("role") if isinstance(org_claim, dict) else None,
    ]
    metadata = claims.get("metadata") if isinstance(claims.get("metadata"), dict) else {}
    public_metadata = claims.get("public_metadata") if isinstance(claims.get("public_metadata"), dict) else {}
    candidates.extend([metadata.get("role"), metadata.get("roles"), public_metadata.get("role"), public_metadata.get("roles")])

    for value in candidates:
        if value is None:
            continue
        items = value if isinstance(value, (list, tuple, set)) else [value]
        for item in items:
            normalized = _normalize_role(item)
            if normalized and normalized not in collected:
                collected.append(normalized)
    return collected


def _org_id_from_claims(claims: dict[str, Any]) -> Optional[str]:
    org_claim = claims.get("o")
    if isinstance(org_claim, dict):
        nested = org_claim.get("id") or org_claim.get("org_id")
        if nested:
            return str(nested)
    for key in ("org_id", "orgId", "org"):
        value = claims.get(key)
        if isinstance(value, str) and value:
            return value
        if isinstance(value, dict) and value.get("id"):
            return str(value["id"])
    return None


def decode_clerk_jwt(token: str) -> dict[str, Any]:
    """Validate a Clerk bearer token against the configured JWKS endpoint."""
    try:
        client = get_jwks_client()
        signing_key = client.get_signing_key_from_jwt(token)
        issuer = clerk_issuer()
        audience = os.getenv("CLERK_AUDIENCE")
        options = {
            "verify_aud": bool(audience),
            "verify_iss": bool(issuer),
            "require": ["sub", "exp"],
        }
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience or None,
            issuer=issuer or None,
            leeway=30,
            options=options,
        )
    except HTTPException:
        raise
    except InvalidTokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Clerk JWT",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Unable to validate Clerk JWT",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc


def extract_org_id_from_bearer(token: str) -> Optional[str]:
    """Best-effort org id for RLS. Invalid tokens are ignored (webhooks still work)."""
    try:
        claims = decode_clerk_jwt(token)
    except HTTPException:
        return None
    return _org_id_from_claims(claims)


def auth_user_from_claims(claims: dict[str, Any]) -> AuthUser:
    user_id = str(claims.get("sub") or "").strip()
    org_id = _org_id_from_claims(claims)
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clerk JWT missing subject",
        )
    if not org_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Clerk JWT missing org_id claim",
        )
    roles = _roles_from_claims(claims) or [MEMBER_ROLE]
    return AuthUser(user_id=user_id, org_id=org_id, roles=roles, claims=claims)


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_BEARER),
) -> AuthUser:
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    claims = decode_clerk_jwt(credentials.credentials)
    return auth_user_from_claims(claims)


def require_admin_role(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )
    return user


def _org_display_name(user: AuthUser) -> str:
    claims = user.claims or {}
    org_claim = claims.get("o") if isinstance(claims.get("o"), dict) else {}
    for key in ("org_name", "orgName"):
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:160]
    nested = org_claim.get("nam") or org_claim.get("name") if isinstance(org_claim, dict) else None
    if isinstance(nested, str) and nested.strip():
        return nested.strip()[:160]
    return "New workspace"


def ensure_organization_for_user(db: Session, user: AuthUser) -> Organization:
    """Load the Clerk org row, creating a free-tier tenant on first authenticated request."""
    org = db.query(Organization).filter(Organization.org_id == user.org_id).one_or_none()
    if org is not None:
        return org
    org = Organization(
        org_id=user.org_id,
        name=_org_display_name(user),
        api_key=hash_api_key(generate_api_key()),
        plan_tier=FREE_PLAN,
        subscription_status="incomplete",
    )
    db.add(org)
    db.flush()
    return org


def load_organization_for_user(db: Session, user: AuthUser) -> Organization:
    org = ensure_organization_for_user(db, user)
    apply_reverse_trial_from_claims(org, user.claims)
    return org


def get_current_org(
    user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    org = load_organization_for_user(db, user)
    if not is_reverse_trial(org):
        raise_if_inactive(org)
    return org


def get_current_org_unpaid(
    user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Organization:
    """Load the Clerk org even when subscription_status would 402 the product APIs."""
    return load_organization_for_user(db, user)
