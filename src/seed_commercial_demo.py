"""Seed a Clerk-backed primary tenant plus an optional Globex isolation tenant."""

from __future__ import annotations

import argparse
import logging
import os
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
    OutboundCampaign,
    ProspectLead,
    SystemAuditLog,
    TelemetryEvent,
    utcnow,
)
from src.paths import INTERVENTION_SUCCESS_RATE

LOGGER = logging.getLogger(__name__)

ACME_API_KEY = "rle_acme_live_demo_key"
ACME_NAME = "Acme SaaS"

GLOBEX_ORG_ID = "org_globex"
GLOBEX_API_KEY = "rle_globex_live_demo_key"
GLOBEX_NAME = "Globex Analytics"


def resolve_org_id(explicit: str | None = None) -> str:
    """Clerk organization id from CLI, then CLERK_ORG_ID. Never defaults to org_acme."""
    org_id = (explicit or os.getenv("CLERK_ORG_ID") or "").strip()
    if not org_id:
        raise ValueError("Clerk organization id required: pass --org-id or set CLERK_ORG_ID")
    return org_id


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed commercial demo tenants.")
    parser.add_argument(
        "--org-id",
        default=None,
        help="Primary Clerk organization id (overrides CLERK_ORG_ID)",
    )
    parser.add_argument(
        "--skip-globex",
        action="store_true",
        help="Seed only the primary Clerk tenant (recommended in production)",
    )
    return parser.parse_args(argv)

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
    session.query(OutboundCampaign).filter(OutboundCampaign.org_id == org_id).delete()
    session.query(ProspectLead).filter(ProspectLead.org_id == org_id).delete()
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


def _seed_prospects(session, org: Organization) -> None:
    from src.conversion_model import score_intent_signals

    now = utcnow()
    high_signals = [
        "recently raised funding",
        "hiring VP of Sales",
        "hiring CRO",
        "expanding sales team",
        "tech stack: HubSpot",
        "tech stack: Salesforce",
        "intent: churn",
        "visited pricing",
        "series a",
    ]
    rows = [
        ("Northwind Analytics", "Priya Shah", "priya.shah@northwind-analytics.example", high_signals, "uncontacted"),
        ("Helios RevOps", "Marcus Bell", "marcus.bell@helios-revops.example", high_signals, "in_sequence"),
        ("Pinnacle Labs", "Elena Ruiz", "elena.ruiz@pinnacle-labs.example", ["job change"], "uncontacted"),
        ("Harbor CRM", "Jonah Cole", "jonah.cole@harbor-crm.example", ["hiring RevOps", "visited pricing"], "meeting_booked"),
    ]
    for company, name, email, signals, status in rows:
        session.add(
            ProspectLead(
                org_id=org.org_id,
                company_name=company,
                decision_maker_name=name,
                email=email,
                linkedin_url=f"https://linkedin.com/in/{name.lower().replace(' ', '-')}",
                intent_signals=signals,
                conversion_score=score_intent_signals(signals),
                status=status,
                created_at=now,
                updated_at=now,
            )
        )
    session.flush()
    high = (
        session.query(ProspectLead)
        .filter(ProspectLead.org_id == org.org_id, ProspectLead.email == "marcus.bell@helios-revops.example")
        .one()
    )
    session.add(
        OutboundCampaign(
            org_id=org.org_id,
            prospect_lead_id=high.id,
            vendor="instantly",
            campaign_id="camp_demo_outbound",
            vendor_lead_id="inst_demo_1",
            status="synced",
            created_at=now,
            last_synced_at=now,
        )
    )


def seed_commercial_demo(
    org_id: str | None = None,
    *,
    seed_globex: bool = True,
) -> dict[str, str]:
    primary_org_id = resolve_org_id(org_id)
    reset_engine()
    init_db()
    session = get_session_factory()()
    try:
        acme = seed_organization(session, primary_org_id, ACME_NAME, ACME_API_KEY, "scale")
        _seed_acme_accounts(session, acme)
        _seed_prospects(session, acme)
        result = {
            "acme_org_id": primary_org_id,
            "org_id": primary_org_id,
            "acme_api_key": ACME_API_KEY,
            "globex_org_id": GLOBEX_ORG_ID,
            "globex_api_key": GLOBEX_API_KEY,
        }
        if seed_globex:
            globex = seed_organization(session, GLOBEX_ORG_ID, GLOBEX_NAME, GLOBEX_API_KEY, "growth")
            _seed_globex(session, globex)
            LOGGER.info("Seeded %s as %s (%s accounts) and %s", ACME_NAME, primary_org_id, 50, GLOBEX_NAME)
        else:
            result["globex_org_id"] = ""
            result["globex_api_key"] = ""
            LOGGER.info("Seeded %s as %s (%s accounts)", ACME_NAME, primary_org_id, 50)
        session.commit()
        return result
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    org_id = (args.org_id or os.getenv("CLERK_ORG_ID") or "").strip()
    if not org_id:
        LOGGER.warning("Skipping commercial seed: pass --org-id or set CLERK_ORG_ID")
        return
    keys = seed_commercial_demo(org_id, seed_globex=not args.skip_globex)
    print("Commercial demo seeded.")
    print(f"  Primary tenant  org_id={keys['org_id']}  X-API-Key={keys['acme_api_key']}")
    if keys.get("globex_org_id"):
        print(f"  Globex          org_id={keys['globex_org_id']}  X-API-Key={keys['globex_api_key']}")


if __name__ == "__main__":
    main()


def __getattr__(name: str):
    """Expose ACME_ORG_ID as the current CLERK_ORG_ID for tests (no hardcoded default)."""
    if name == "ACME_ORG_ID":
        return os.getenv("CLERK_ORG_ID", "").strip()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
