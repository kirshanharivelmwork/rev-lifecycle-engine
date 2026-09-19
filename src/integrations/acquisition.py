"""Apollo lead ingest and Instantly outbound push."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Optional

import httpx
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.conversion_model import score_intent_signals
from src.dead_letter import record_dead_letter
from src.models_db import Organization, OutboundCampaign, ProspectLead, utcnow
from src.security import decrypt_secret

LOGGER = logging.getLogger(__name__)
APOLLO_SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/search"
INSTANTLY_LEADS_URL = "https://api.instantly.ai/api/v2/leads"
MAX_PUSH_ATTEMPTS = 3
ICEBREAKER_PROMPT = (
    "Write a casual, highly personalized 12-to-15 word opening sentence for a B2B cold email "
    "based on this company news/intent signal: {intent_signals}. Do not use greetings or sign-offs. "
    "Sound like a peer."
)


def _apollo_headers(api_key: str) -> dict[str, str]:
    return {"X-Api-Key": api_key, "Content-Type": "application/json", "Cache-Control": "no-cache"}


def _handle_status(response: httpx.Response, vendor: str) -> dict[str, Any]:
    code = int(response.status_code)
    if code == 401:
        LOGGER.warning("%s unauthorized", vendor)
        return {"ok": False, "vendor": vendor, "status": 401, "error": "unauthorized"}
    if code == 429:
        LOGGER.warning("%s rate-limited", vendor)
        return {"ok": False, "vendor": vendor, "status": 429, "error": "rate_limited"}
    if 200 <= code < 300:
        body: Any
        try:
            body = response.json()
        except Exception:
            body = {}
        return {"ok": True, "vendor": vendor, "status": code, "body": body}
    LOGGER.warning("%s failed HTTP %s %s", vendor, code, response.text[:300])
    return {"ok": False, "vendor": vendor, "status": code, "error": response.text[:500]}


def _person_to_lead_fields(person: dict[str, Any], extra_signals: Optional[list] = None) -> dict[str, Any]:
    org = person.get("organization") or person.get("account") or {}
    first = person.get("first_name") or ""
    last = person.get("last_name") or ""
    name = person.get("name") or f"{first} {last}".strip()
    signals = list(extra_signals or [])
    title = person.get("title") or person.get("headline") or ""
    if title:
        signals.append(title)
    for key in ("keywords", "intent_signals", "signals"):
        raw = person.get(key) or org.get(key)
        if isinstance(raw, list):
            signals.extend(str(x) for x in raw)
        elif isinstance(raw, str) and raw:
            signals.append(raw)
    email = (person.get("email") or person.get("work_email") or "").strip().lower()
    return {
        "company_name": org.get("name") or person.get("organization_name") or "Unknown company",
        "decision_maker_name": name or email,
        "email": email,
        "linkedin_url": person.get("linkedin_url") or person.get("linkedin") or None,
        "intent_signals": signals,
        "conversion_score": score_intent_signals(signals),
    }


def upsert_prospect(session: Session, org_id: str, fields: dict[str, Any]) -> Optional[ProspectLead]:
    email = (fields.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return None
    existing = (
        session.query(ProspectLead)
        .filter(ProspectLead.org_id == org_id, ProspectLead.email == email)
        .one_or_none()
    )
    now = utcnow()
    if existing:
        existing.company_name = fields.get("company_name") or existing.company_name
        existing.decision_maker_name = fields.get("decision_maker_name") or existing.decision_maker_name
        existing.linkedin_url = fields.get("linkedin_url") or existing.linkedin_url
        existing.intent_signals = fields.get("intent_signals") or existing.intent_signals
        existing.conversion_score = float(fields.get("conversion_score") or existing.conversion_score)
        existing.updated_at = now
        session.flush()
        return existing
    lead = ProspectLead(
        org_id=org_id,
        company_name=fields["company_name"],
        decision_maker_name=fields.get("decision_maker_name") or "",
        email=email,
        linkedin_url=fields.get("linkedin_url"),
        intent_signals=fields.get("intent_signals") or [],
        conversion_score=float(fields.get("conversion_score") or 0),
        status="uncontacted",
        created_at=now,
        updated_at=now,
    )
    try:
        with session.begin_nested():
            session.add(lead)
            session.flush()
    except IntegrityError:
        return (
            session.query(ProspectLead)
            .filter(ProspectLead.org_id == org_id, ProspectLead.email == email)
            .one_or_none()
        )
    return lead


async def generate_icebreaker(
    company_name: str,
    intent_signals: Any,
    *,
    client: Any = None,
) -> Optional[str]:
    """Ask OpenAI for a 12–15 word Instantly icebreaker. No-ops without OPENAI_API_KEY."""
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key and client is None:
        return None
    blob = intent_signals
    if not isinstance(blob, str):
        blob = ", ".join(str(x) for x in (blob or []) if str(x).strip()) or "none"
    if company_name:
        blob = f"{company_name} — {blob}"
    prompt = ICEBREAKER_PROMPT.format(intent_signals=blob)
    try:
        if client is None:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=api_key)
        response = await client.chat.completions.create(
            model=os.getenv("OPENAI_ICEBREAKER_MODEL", "gpt-4o-mini"),
            messages=[
                {"role": "system", "content": "You write B2B cold email openers. No greetings or sign-offs."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=60,
            temperature=0.7,
        )
        text = (response.choices[0].message.content or "").strip().strip('"')
        return text or None
    except Exception as exc:
        LOGGER.warning("Icebreaker generation failed: %s", exc)
        return None


async def fetch_apollo_leads(
    org_id: str,
    search_params: Optional[dict[str, Any]] = None,
    *,
    session: Optional[Session] = None,
    client: Optional[httpx.AsyncClient] = None,
    auto_enqueue: bool = True,
) -> dict[str, Any]:
    """Query Apollo mixed-people search and persist unique ProspectLead rows."""
    from src.database import get_session_factory

    owns_session = session is None
    if session is None:
        session = get_session_factory()()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        org = session.get(Organization, org_id)
        if org is None or not org.apollo_api_key:
            return {"ok": False, "error": "missing_apollo_api_key", "upserted": 0}
        payload = dict(search_params or {})
        payload.setdefault("per_page", 25)
        extra_signals = list(payload.pop("intent_signals", []) or [])
        response = await client.post(APOLLO_SEARCH_URL, headers=_apollo_headers(org.apollo_api_key), json=payload)
        handled = _handle_status(response, "apollo")
        if not handled["ok"]:
            return {**handled, "upserted": 0}
        people = (handled.get("body") or {}).get("people") or (handled.get("body") or {}).get("contacts") or []
        upserted = []
        for person in people:
            if not isinstance(person, dict):
                continue
            fields = _person_to_lead_fields(person, extra_signals)
            lead = upsert_prospect(session, org_id, fields)
            if lead:
                upserted.append(lead.id)
                from src.conversion_model import is_high_intent

                if (
                    auto_enqueue
                    and is_high_intent(lead.conversion_score)
                    and lead.status == "uncontacted"
                ):
                    from src.tasks import enqueue_instantly_push

                    enqueue_instantly_push(
                        lead.id,
                        os.getenv("INSTANTLY_CAMPAIGN_ID") or "default",
                    )
        if owns_session:
            session.commit()
        else:
            session.flush()
        return {"ok": True, "upserted": len(upserted), "lead_ids": upserted, "status": handled.get("status")}
    finally:
        if owns_client:
            await client.aclose()
        if owns_session:
            session.close()


async def push_to_instantly(
    lead_id: str,
    campaign_id: str,
    *,
    session: Optional[Session] = None,
    client: Optional[httpx.AsyncClient] = None,
    max_attempts: int = MAX_PUSH_ATTEMPTS,
) -> dict[str, Any]:
    """Push one scored lead into an Instantly campaign; DLQ after 3 failures."""
    from src.database import get_session_factory

    owns_session = session is None
    if session is None:
        session = get_session_factory()()
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
    last_error = "unknown"
    org_id = None
    payload: dict[str, Any] = {"lead_id": lead_id, "campaign_id": campaign_id}
    try:
        lead = session.get(ProspectLead, lead_id)
        if lead is None:
            return {"ok": False, "error": "lead_not_found"}
        org = session.get(Organization, lead.org_id)
        org_id = lead.org_id
        instantly_key = decrypt_secret(org.instantly_api_key) if org else None
        if org is None or not instantly_key:
            return {"ok": False, "error": "missing_instantly_api_key"}
        campaign_id = campaign_id or os.getenv("INSTANTLY_CAMPAIGN_ID") or "default"
        icebreaker = await generate_icebreaker(lead.company_name, lead.intent_signals)
        payload = {
            "lead_id": lead.id,
            "campaign_id": campaign_id,
            "email": lead.email,
            "company_name": lead.company_name,
        }
        if icebreaker:
            payload["custom_icebreaker"] = icebreaker
        body = {
            "campaign": campaign_id,
            "email": lead.email,
            "first_name": (lead.decision_maker_name or "").split(" ")[0],
            "last_name": " ".join((lead.decision_maker_name or "").split(" ")[1:]),
            "company_name": lead.company_name,
            "linkedin": lead.linkedin_url,
        }
        if icebreaker:
            body["custom_icebreaker"] = icebreaker
            body["custom_variables"] = {"custom_icebreaker": icebreaker}
        headers = {
            "Authorization": f"Bearer {instantly_key}",
            "Content-Type": "application/json",
        }
        for attempt in range(1, max_attempts + 1):
            try:
                response = await client.post(INSTANTLY_LEADS_URL, headers=headers, json=body)
                handled = _handle_status(response, "instantly")
                if handled["ok"]:
                    vendor_id = None
                    resp_body = handled.get("body") or {}
                    if isinstance(resp_body, dict):
                        vendor_id = str(resp_body.get("id") or resp_body.get("lead_id") or "") or None
                    lead.status = "in_sequence"
                    lead.updated_at = utcnow()
                    campaign = OutboundCampaign(
                        org_id=lead.org_id,
                        prospect_lead_id=lead.id,
                        vendor="instantly",
                        campaign_id=campaign_id,
                        vendor_lead_id=vendor_id,
                        status="synced",
                        created_at=utcnow(),
                        last_synced_at=utcnow(),
                    )
                    session.add(campaign)
                    if owns_session:
                        session.commit()
                    else:
                        session.flush()
                    return {"ok": True, "campaign_row_id": campaign.id, "attempts": attempt}
                last_error = handled.get("error") or f"HTTP {handled.get('status')}"
                if handled.get("status") == 401:
                    break
            except Exception as exc:
                last_error = str(exc)
                LOGGER.warning("Instantly push attempt %s failed: %s", attempt, exc)
            if attempt < max_attempts:
                await asyncio.sleep(min(0.05 * attempt, 0.2))
        record_dead_letter(
            lead.org_id,
            "instantly_push",
            payload,
            last_error,
            max_attempts,
            session=session if not owns_session else None,
        )
        return {"ok": False, "error": last_error, "dead_letter": True}
    finally:
        if owns_client:
            await client.aclose()
        if owns_session:
            session.close()
