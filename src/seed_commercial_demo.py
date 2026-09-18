"""Seed Acme SaaS + Globex tenants for local commercial demos."""

from __future__ import annotations

import logging
import sys
from datetime import timedelta
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.auth import hash_api_key
from src.database import get_session_factory, init_db, reset_engine
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DeadLetterJob,
    DispatchedAction,
    IdempotentEvent,
    IngestionJob,
    InterventionOutcome,
    Organization,
    SystemAuditLog,
    TelemetryEvent,
    utcnow,
)
from src.paths import INTERVENTION_SUCCESS_RATE

LOGGER = logging.getLogger(__name__)

ACME_ORG_ID = "org_acme"
ACME_API_KEY = "rle_acme_live_demo_key"
ACME_NAME = "Acme SaaS"

GLOBEX_ORG_ID = "org_globex"
GLOBEX_API_KEY = "rle_globex_live_demo_key"
GLOBEX_NAME = "Globex Analytics"

CHANNELS = ("Outbound Cold Email", "Inbound Organic", "Paid Search", "Partner Referral")


def _wipe_org(session, org_id: str) -> None:
    session.query(InterventionOutcome).filter(InterventionOutcome.org_id == org_id).delete()
    session.query(IngestionJob).filter(IngestionJob.org_id == org_id).delete()
    session.query(DeadLetterJob).filter(DeadLetterJob.org_id == org_id).delete()
    session.query(IdempotentEvent).filter(IdempotentEvent.org_id == org_id).delete()
    session.query(SystemAuditLog).filter(SystemAuditLog.org_id == org_id).delete()
    session.query(DispatchedAction).filter(DispatchedAction.org_id == org_id).delete()
    session.query(ChurnAssessment).filter(ChurnAssessment.org_id == org_id).delete()
    session.query(TelemetryEvent).filter(TelemetryEvent.org_id == org_id).delete()
    session.query(CustomerAccount).filter(CustomerAccount.org_id == org_id).delete()
    session.query(Organization).filter(Organization.org_id == org_id).delete()


def seed_organization(session, org_id: str, name: str, api_key: str, plan: str) -> Organization:
    _wipe_org(session, org_id)
    org = Organization(
        org_id=org_id,
        name=name,
        api_key=hash_api_key(api_key),
        plan_tier=plan,
        created_at=utcnow(),
        alert_cooldown_days=14,
        hitl_mrr_threshold=1000.0,
        subscription_status="active",
    )
    session.add(org)
    session.flush()
    return org


def _seed_acme_accounts(session, org: Organization) -> list[CustomerAccount]:
    now = utcnow()
    accounts: list[CustomerAccount] = []
    for i in range(50):
        risky = i < 12
        channel = CHANNELS[i % 4]
        contract = "Monthly" if risky or i % 3 == 0 else "Annual"
        mrr = 180.0 + (i * 37) % 900
        if i in {0, 1, 2}:
            mrr = 1400.0 + i * 80
        period = 30 if contract == "Monthly" else 365
        account = CustomerAccount(
            org_id=org.org_id,
            customer_external_id=f"cus_acme_{i:03d}",
            channel=channel,
            mrr=float(mrr),
            contract_type=contract,
            created_at=now - timedelta(days=200 - i),
            contract_renewal_at=now + timedelta(days=period - (i % 12)),
            cooldown_days=14,
            approval_status="pending" if i in {0, 1, 2} else "none",
        )
        session.add(account)
        session.flush()
        if risky:
            session.add(
                TelemetryEvent(
                    org_id=org.org_id,
                    customer_account_id=account.id,
                    event_name="login",
                    timestamp=now - timedelta(days=28 + i % 10),
                    properties={"source": "web"},
                )
            )
            session.add(
                TelemetryEvent(
                    org_id=org.org_id,
                    customer_account_id=account.id,
                    event_name="ticket_opened",
                    timestamp=now - timedelta(days=3),
                    properties={"severity": "high"},
                )
            )
            session.add(
                TelemetryEvent(
                    org_id=org.org_id,
                    customer_account_id=account.id,
                    event_name="feature_used",
                    timestamp=now - timedelta(days=40),
                    properties={"feature": "settings"},
                )
            )
            probability = 0.72 + (i % 5) * 0.04
            session.add(
                ChurnAssessment(
                    org_id=org.org_id,
                    customer_account_id=account.id,
                    churn_probability=min(probability, 0.96),
                    risk_tier="Critical",
                    arr_at_risk=round(mrr * 12, 2),
                    recommended_action="CSM re-engagement sprint within 48h + executive sponsor ping",
                    assessed_at=now - timedelta(hours=i),
                )
            )
            if i < 8:
                for channel_name in ("slack", "resend_email"):
                    action = DispatchedAction(
                        org_id=org.org_id,
                        customer_account_id=account.id,
                        channel=channel_name,
                        status="simulated",
                        created_at=now - timedelta(days=40 + i % 5),
                        payload={
                            "trigger_reason": "28d dark (critical inactivity); low adoption",
                            "mrr_saved_est": round(mrr * 12 * INTERVENTION_SUCCESS_RATE, 2),
                            "churn_probability": probability,
                        },
                    )
                    session.add(action)
                    session.flush()
                    session.add(
                        InterventionOutcome(
                            org_id=org.org_id,
                            customer_account_id=account.id,
                            dispatched_action_id=action.id,
                            intervention_date=action.created_at,
                            initial_mrr=float(mrr),
                            status_at_30d="Active",
                            verified_arr_saved=round(mrr * 12, 2),
                            attributed=True,
                        )
                    )
            if i in {0, 1, 2}:
                session.add(
                    DispatchedAction(
                        org_id=org.org_id,
                        customer_account_id=account.id,
                        channel="approval_queue",
                        status="pending_approval",
                        created_at=now - timedelta(hours=2),
                        payload={"queue": "Pending CSM Approval", "playbook": "CSM re-engagement sprint"},
                    )
                )
        else:
            for day in range(6):
                session.add(
                    TelemetryEvent(
                        org_id=org.org_id,
                        customer_account_id=account.id,
                        event_name="login",
                        timestamp=now - timedelta(days=day, hours=2),
                        properties={"source": "web"},
                    )
                )
            for feat in ("reports", "billing", "sso", "api"):
                session.add(
                    TelemetryEvent(
                        org_id=org.org_id,
                        customer_account_id=account.id,
                        event_name="feature_used",
                        timestamp=now - timedelta(hours=feat.__hash__() % 40),
                        properties={"feature": feat},
                    )
                )
            session.add(
                ChurnAssessment(
                    org_id=org.org_id,
                    customer_account_id=account.id,
                    churn_probability=0.08 + (i % 7) * 0.02,
                    risk_tier="Low",
                    arr_at_risk=round(mrr * 12, 2),
                    recommended_action="Health-score watchlist; nurture with case studies and QBR",
                    assessed_at=now - timedelta(hours=6),
                )
            )
        accounts.append(account)
    return accounts


def _seed_globex(session, org: Organization) -> None:
    now = utcnow()
    for i in range(5):
        account = CustomerAccount(
            org_id=org.org_id,
            customer_external_id=f"cus_globex_{i:03d}",
            channel="Partner Referral",
            mrr=990.0 + i * 50,
            contract_type="Annual",
            created_at=now,
        )
        session.add(account)
        session.flush()
        session.add(
            TelemetryEvent(
                org_id=org.org_id,
                customer_account_id=account.id,
                event_name="login",
                timestamp=now,
                properties={"source": "mobile"},
            )
        )


def seed_commercial_demo() -> dict[str, str]:
    reset_engine()
    init_db()
    session = get_session_factory()()
    try:
        acme = seed_organization(session, ACME_ORG_ID, ACME_NAME, ACME_API_KEY, "scale")
        globex = seed_organization(session, GLOBEX_ORG_ID, GLOBEX_NAME, GLOBEX_API_KEY, "growth")
        _seed_acme_accounts(session, acme)
        _seed_globex(session, globex)
        session.commit()
        LOGGER.info("Seeded %s (%s accounts) and %s", ACME_NAME, 50, GLOBEX_NAME)
        return {
            "acme_org_id": ACME_ORG_ID,
            "acme_api_key": ACME_API_KEY,
            "globex_org_id": GLOBEX_ORG_ID,
            "globex_api_key": GLOBEX_API_KEY,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    keys = seed_commercial_demo()
    print("Commercial demo seeded.")
    print(f"  Acme SaaS     org_id={keys['acme_org_id']}  X-API-Key={keys['acme_api_key']}")
    print(f"  Globex        org_id={keys['globex_org_id']}  X-API-Key={keys['globex_api_key']}")


if __name__ == "__main__":
    main()
