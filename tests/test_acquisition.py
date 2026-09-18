"""Apollo ingest, Instantly push, scoring, and dead-letter routing."""

from __future__ import annotations

import asyncio

from src.conversion_model import is_high_intent, score_intent_signals
from src.database import get_session_factory, init_db, reset_engine
from src.integrations.acquisition import fetch_apollo_leads, push_to_instantly, upsert_prospect
from src.models_db import DeadLetterJob, Organization, OutboundCampaign, ProspectLead
from src.seed_commercial_demo import ACME_ORG_ID, seed_commercial_demo
from src.tasks import run_daily_outbound_engine


class _Resp:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text or str(payload or "")

    def json(self):
        return self._payload


class _AsyncClient:
    def __init__(self, responses) -> None:
        self._responses = list(responses)
        self.calls = []

    async def post(self, url, headers=None, json=None):
        self.calls.append({"url": url, "headers": headers, "json": json})
        if not self._responses:
            return _Resp(500, text="exhausted")
        item = self._responses.pop(0)
        if isinstance(item, _Resp):
            return item
        return _Resp(int(item))

    async def aclose(self):
        return None


def test_scoring_funding_and_hiring_vp_sales() -> None:
    score = score_intent_signals(["recently raised funding", "hiring VP of Sales"])
    assert score == 35
    rich = score_intent_signals(
        [
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
    )
    assert rich > 80
    assert is_high_intent(rich)


def test_fetch_apollo_upserts_and_skips_duplicate_email(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'acq.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    org.apollo_api_key = "apollo_test_key"
    session.commit()

    people = {
        "people": [
            {
                "first_name": "Ada",
                "last_name": "Lovelace",
                "email": "ada@analytical.example",
                "linkedin_url": "https://linkedin.com/in/ada",
                "organization": {"name": "Analytical Engines"},
                "intent_signals": ["recently raised funding"],
            },
            {
                "first_name": "Ada",
                "last_name": "Clone",
                "email": "ada@analytical.example",
                "organization": {"name": "Analytical Engines"},
            },
        ]
    }
    client = _AsyncClient([_Resp(200, people)])
    monkeypatch.setattr("src.tasks.enqueue_instantly_push", lambda *a, **k: None)

    result = asyncio.run(
        fetch_apollo_leads(ACME_ORG_ID, {"q_keywords": "saas"}, session=session, client=client)
    )
    assert result["ok"] is True
    assert result["upserted"] == 2
    rows = (
        session.query(ProspectLead)
        .filter(ProspectLead.org_id == ACME_ORG_ID, ProspectLead.email == "ada@analytical.example")
        .all()
    )
    assert len(rows) == 1
    assert rows[0].company_name == "Analytical Engines"
    assert client.calls[0]["headers"]["X-Api-Key"] == "apollo_test_key"
    session.close()
    reset_engine()


def test_fetch_apollo_unauthorized_and_rate_limited(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'acq401.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    org.apollo_api_key = "bad"
    session.commit()

    unauthorized = asyncio.run(
        fetch_apollo_leads(ACME_ORG_ID, session=session, client=_AsyncClient([_Resp(401, text="nope")]))
    )
    assert unauthorized["ok"] is False
    assert unauthorized["status"] == 401
    limited = asyncio.run(
        fetch_apollo_leads(ACME_ORG_ID, session=session, client=_AsyncClient([_Resp(429, text="slow")]))
    )
    assert limited["ok"] is False
    assert limited["error"] == "rate_limited"
    session.close()
    reset_engine()


def test_push_to_instantly_success(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'inst.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    org.instantly_api_key = "inst_live"
    lead = upsert_prospect(
        session,
        ACME_ORG_ID,
        {
            "company_name": "Waveform",
            "decision_maker_name": "Sam Rivera",
            "email": "sam.rivera@waveform.example",
            "intent_signals": ["recently raised funding"],
            "conversion_score": 20,
        },
    )
    session.commit()
    client = _AsyncClient([_Resp(200, {"id": "lead_99"})])
    result = asyncio.run(push_to_instantly(lead.id, "camp_1", session=session, client=client))
    assert result["ok"] is True
    session.refresh(lead)
    assert lead.status == "in_sequence"
    synced = session.query(OutboundCampaign).filter(OutboundCampaign.prospect_lead_id == lead.id).one()
    assert synced.campaign_id == "camp_1"
    assert synced.vendor_lead_id == "lead_99"
    assert "Bearer inst_live" in client.calls[0]["headers"]["Authorization"]
    session.close()
    reset_engine()


def test_instantly_failure_routes_to_dead_letter(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'dlq.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    org.instantly_api_key = "inst_down"
    lead = upsert_prospect(
        session,
        ACME_ORG_ID,
        {
            "company_name": "DownCo",
            "decision_maker_name": "Pat Lee",
            "email": "pat.lee@downco.example",
            "intent_signals": [],
            "conversion_score": 0,
        },
    )
    session.commit()
    client = _AsyncClient([_Resp(503, text="down"), _Resp(503, text="down"), _Resp(503, text="down")])
    result = asyncio.run(push_to_instantly(lead.id, "camp_dlq", session=session, client=client))
    assert result["ok"] is False
    assert result["dead_letter"] is True
    assert len(client.calls) == 3
    session.expire_all()
    jobs = (
        session.query(DeadLetterJob)
        .filter(DeadLetterJob.org_id == ACME_ORG_ID, DeadLetterJob.task_name == "instantly_push")
        .all()
    )
    assert len(jobs) == 1
    assert jobs[0].payload["email"] == "pat.lee@downco.example"
    assert jobs[0].retry_count == 3
    session.close()
    reset_engine()


def test_daily_outbound_engine_dispatches_high_intent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path / 'daily.db'}")
    reset_engine()
    init_db()
    seed_commercial_demo()
    session = get_session_factory()()
    org = session.get(Organization, ACME_ORG_ID)
    org.apollo_api_key = "apollo"
    org.instantly_api_key = "inst"
    session.commit()
    signals = [
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
    apollo = _Resp(
        200,
        {
            "people": [
                {
                    "first_name": "Kim",
                    "last_name": "Park",
                    "email": "kim.park@icp.example",
                    "organization": {"name": "ICP Corp"},
                    "intent_signals": signals,
                }
            ]
        },
    )
    instantly_ok = [_Resp(200, {"id": f"inst_{i}"}) for i in range(8)]
    client = _AsyncClient([apollo, *instantly_ok])
    result = asyncio.run(
        run_daily_outbound_engine(ACME_ORG_ID, campaign_id="camp_daily", session=session, client=client)
    )
    assert result["dispatched"] >= 1
    lead = (
        session.query(ProspectLead)
        .filter(ProspectLead.org_id == ACME_ORG_ID, ProspectLead.email == "kim.park@icp.example")
        .one()
    )
    assert lead.status == "in_sequence"
    assert lead.conversion_score > 80
    session.close()
    reset_engine()
