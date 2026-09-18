"""Pytest fixtures: pipeline data plus a mocked Clerk JWKS/JWT stack."""

from __future__ import annotations

import os
import time
from typing import Any, Optional

os.environ.setdefault("DISABLE_RATE_LIMIT", "1")
os.environ.setdefault("CLERK_ISSUER", "https://clerk.test")
os.environ.setdefault("CLERK_JWKS_URL", "https://clerk.test/.well-known/jwks.json")

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from src.auth import reset_jwks_client
from src.data_pipeline import run_pipeline
from src.generate_data import generate_datasets
from src.paths import ACQUISITION_LEADS_PATH, N_CUSTOMERS, USER_TELEMETRY_PATH

CLERK_ISSUER = "https://clerk.test"
CLERK_KID = "test-clerk-key"
_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_PUBLIC_KEY = _PRIVATE_KEY.public_key()
_PUBLIC_PEM = _PUBLIC_KEY.public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)


class _FakeSigningKey:
    def __init__(self) -> None:
        self.key = _PUBLIC_PEM
        self.key_id = CLERK_KID


class FakePyJWKClient:
    """Stand-in for Clerk's JWKS HTTP endpoint."""

    def get_signing_key_from_jwt(self, _token: str) -> _FakeSigningKey:
        return _FakeSigningKey()


def mint_clerk_token(
    *,
    org_id: str = "org_test",
    role: str = "Admin",
    sub: str = "user_test",
    extra_claims: Optional[dict[str, Any]] = None,
    expired: bool = False,
) -> str:
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": sub,
        "org_id": org_id,
        "role": role,
        "iss": CLERK_ISSUER,
        "iat": now,
        "nbf": now - 5,
        "exp": now - 60 if expired else now + 3600,
    }
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, _PRIVATE_KEY, algorithm="RS256", headers={"kid": CLERK_KID})


def clerk_auth_headers(
    *,
    org_id: str = "org_test",
    role: str = "Admin",
    sub: str = "user_test",
    extra_claims: Optional[dict[str, Any]] = None,
) -> dict[str, str]:
    token = mint_clerk_token(org_id=org_id, role=role, sub=sub, extra_claims=extra_claims)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="session")
def clerk_jwks_client() -> FakePyJWKClient:
    return FakePyJWKClient()


@pytest.fixture(autouse=True)
def mock_clerk_jwks(monkeypatch, clerk_jwks_client: FakePyJWKClient):
    """Every test validates JWTs against the in-process Clerk JWKS mock."""
    monkeypatch.setenv("CLERK_ISSUER", CLERK_ISSUER)
    monkeypatch.setenv("CLERK_JWKS_URL", f"{CLERK_ISSUER}/.well-known/jwks.json")
    monkeypatch.delenv("CLERK_AUDIENCE", raising=False)
    reset_jwks_client()
    monkeypatch.setattr("src.auth.get_jwks_client", lambda: clerk_jwks_client)
    yield
    reset_jwks_client()


@pytest.fixture(scope="session")
def raw_tables():
    """Generate the canonical 5,000-row extracts into the project data/raw folder."""
    leads, telemetry = generate_datasets(n=N_CUSTOMERS, seed=42)
    assert ACQUISITION_LEADS_PATH.exists()
    assert USER_TELEMETRY_PATH.exists()
    return leads, telemetry


@pytest.fixture(scope="session")
def processed_frame(raw_tables):
    return run_pipeline()
