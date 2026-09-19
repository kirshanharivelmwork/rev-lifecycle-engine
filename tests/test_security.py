"""Fernet encryption at rest for Organization tenant secrets."""

from __future__ import annotations

from sqlalchemy import text

from src.api import OrganizationSecrets, apply_organization_secrets, plaintext_organization_secrets
from src.database import get_engine, get_session_factory, init_db, migrate_plaintext_secrets, reset_engine
from src.models_db import Organization
from src.security import decrypt_secret, encrypt_secret, looks_like_fernet_token, reset_fernet_cache


def test_encrypt_decrypt_roundtrip() -> None:
    token = encrypt_secret("pat-hubspot-live")
    assert token != "pat-hubspot-live"
    assert looks_like_fernet_token(token)
    assert decrypt_secret(token) == "pat-hubspot-live"


def test_decrypt_legacy_plaintext_does_not_crash() -> None:
    assert decrypt_secret("whsec_legacy_plain") == "whsec_legacy_plain"
    assert decrypt_secret(None) is None
    assert decrypt_secret("") == ""


def test_encrypt_does_not_double_wrap() -> None:
    once = encrypt_secret("sf-key")
    twice = encrypt_secret(once)
    assert once == twice
    assert decrypt_secret(twice) == "sf-key"


def test_organization_secrets_schema_and_aliases(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'secrets.db'}")
    reset_engine()
    init_db()
    session = get_session_factory()()
    org = Organization(org_id="org_sec", name="Sec", api_key="hashed-sec", plan_tier="dev")
    apply_organization_secrets(
        org,
        OrganizationSecrets(
            hubspot_token="hs-plain",
            salesforce_key="sf-plain",
            stripe_secret="whsec_plain",
            instantly_api_key="inst-plain",
            slack_webhook_url="https://hooks.slack.com/services/T/B/plain",
            resend_api_key="re_plain",
            apollo_api_key="apollo_plain",
        ),
    )
    session.add(org)
    session.commit()
    session.expire_all()

    loaded = session.get(Organization, "org_sec")
    assert loaded.hubspot_token == "hs-plain"
    assert loaded.salesforce_key == "sf-plain"
    assert loaded.stripe_secret == "whsec_plain"
    assert loaded.instantly_api_key == "inst-plain"
    assert loaded.slack_webhook_url.endswith("/plain")
    assert loaded.resend_api_key == "re_plain"
    assert loaded.apollo_api_key == "apollo_plain"
    dumped = plaintext_organization_secrets(loaded)
    assert dumped.hubspot_token == "hs-plain"
    assert dumped.salesforce_key == "sf-plain"
    assert dumped.slack_webhook_url.endswith("/plain")
    assert dumped.resend_api_key == "re_plain"
    assert dumped.apollo_api_key == "apollo_plain"

    raw = session.execute(
        text(
            "SELECT hubspot_access_token, salesforce_access_token, "
            "stripe_webhook_secret, instantly_api_key, slack_webhook_url, "
            "resend_api_key, apollo_api_key FROM organizations WHERE org_id = :oid"
        ),
        {"oid": "org_sec"},
    ).mappings().one()
    assert raw["hubspot_access_token"] != "hs-plain"
    assert looks_like_fernet_token(raw["hubspot_access_token"])
    assert looks_like_fernet_token(raw["salesforce_access_token"])
    assert looks_like_fernet_token(raw["stripe_webhook_secret"])
    assert looks_like_fernet_token(raw["instantly_api_key"])
    assert looks_like_fernet_token(raw["slack_webhook_url"])
    assert looks_like_fernet_token(raw["resend_api_key"])
    assert looks_like_fernet_token(raw["apollo_api_key"])
    session.close()
    reset_engine()


def test_migrate_plaintext_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'legacy.db'}")
    reset_engine()
    init_db()
    engine = get_engine()
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO organizations (org_id, name, api_key, plan_tier, created_at, "
                "alert_cooldown_days, hitl_mrr_threshold, subscription_status, "
                "hubspot_access_token, salesforce_access_token, stripe_webhook_secret, instantly_api_key, "
                "slack_webhook_url, resend_api_key, apollo_api_key) "
                "VALUES ('org_legacy', 'Legacy', 'k', 'dev', CURRENT_TIMESTAMP, 14, 1000, 'active', "
                "'hs-old', 'sf-old', 'whsec-old', 'inst-old', "
                "'https://hooks.slack.com/legacy', 're-old', 'apollo-old')"
            )
        )
    migrate_plaintext_secrets(engine)
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT hubspot_access_token, instantly_api_key, slack_webhook_url, "
                "resend_api_key, apollo_api_key FROM organizations WHERE org_id = 'org_legacy'"
            )
        ).mappings().one()
    assert looks_like_fernet_token(row["hubspot_access_token"])
    assert decrypt_secret(row["instantly_api_key"]) == "inst-old"
    assert looks_like_fernet_token(row["slack_webhook_url"])
    assert decrypt_secret(row["resend_api_key"]) == "re-old"
    assert decrypt_secret(row["apollo_api_key"]) == "apollo-old"
    session = get_session_factory()()
    org = session.get(Organization, "org_legacy")
    assert org.hubspot_access_token == "hs-old"
    assert org.slack_webhook_url == "https://hooks.slack.com/legacy"
    assert org.resend_api_key == "re-old"
    assert org.apollo_api_key == "apollo-old"
    session.close()
    reset_engine()
    reset_fernet_cache()
