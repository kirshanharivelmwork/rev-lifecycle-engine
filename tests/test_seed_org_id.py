"""Primary tenant id comes from --org-id / CLERK_ORG_ID, never a hardcoded org_acme default."""

from __future__ import annotations

import pytest

from src.seed_commercial_demo import parse_args, resolve_org_id, seed_commercial_demo


def test_resolve_org_id_prefers_explicit_argument(monkeypatch) -> None:
    monkeypatch.setenv("CLERK_ORG_ID", "org_from_env")
    assert resolve_org_id("org_3JYHuxSaQRSYUXw7ZDafXD9phV2") == "org_3JYHuxSaQRSYUXw7ZDafXD9phV2"


def test_resolve_org_id_uses_clerk_org_id_env(monkeypatch) -> None:
    monkeypatch.setenv("CLERK_ORG_ID", "org_from_env")
    assert resolve_org_id() == "org_from_env"


def test_resolve_org_id_rejects_missing_value(monkeypatch) -> None:
    monkeypatch.delenv("CLERK_ORG_ID", raising=False)
    with pytest.raises(ValueError, match="Clerk organization id required"):
        resolve_org_id()


def test_parse_args_org_id() -> None:
    args = parse_args(["--org-id", "org_3JYHuxSaQRSYUXw7ZDafXD9phV2", "--skip-globex"])
    assert args.org_id == "org_3JYHuxSaQRSYUXw7ZDafXD9phV2"
    assert args.skip_globex is True


def test_seed_commercial_demo_uses_provided_org_id(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'seed_org.db'}")
    monkeypatch.delenv("CLERK_ORG_ID", raising=False)
    from src.database import get_session_factory, reset_engine
    from src.models_db import Organization

    reset_engine()
    clerk_org = "org_3JYHuxSaQRSYUXw7ZDafXD9phV2"
    keys = seed_commercial_demo(clerk_org, seed_globex=False)
    assert keys["org_id"] == clerk_org
    session = get_session_factory()()
    try:
        assert session.get(Organization, clerk_org) is not None
        assert session.get(Organization, "org_acme") is None
    finally:
        session.close()
        reset_engine()


def test_reset_and_seed_requires_confirm_wipe(monkeypatch) -> None:
    from src.reset_and_seed import main as reset_main

    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    with pytest.raises(SystemExit, match="--confirm-wipe"):
        reset_main([])


def test_reset_and_seed_refuses_sqlite(monkeypatch) -> None:
    from src.reset_and_seed import main as reset_main

    monkeypatch.setenv("DATABASE_URL", "sqlite:///tmp/demo.db")
    with pytest.raises(SystemExit, match="PostgreSQL"):
        reset_main(["--confirm-wipe"])
