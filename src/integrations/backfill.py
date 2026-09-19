"""Historical Stripe + product-analytics hydration for Day-1 tenant dashboards."""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Optional

import httpx
from sqlalchemy.orm import Session

from src.feature_builder import parse_timestamp
from src.models_db import CustomerAccount, Organization, TelemetryEvent, new_id, utcnow
from src.routers.account_ops import mrr_from_subscription

LOGGER = logging.getLogger(__name__)

BATCH_SIZE = 500
LOOKBACK_DAYS = 365
PRODUCT_EVENT_MAP = {
    "login": "login",
    "signed in": "login",
    "signed_in": "login",
    "session_start": "session_start",
    "$pageview": "login",
    "page": "login",
    "identify": None,
    "$identify": None,
    "feature_used": "feature_used",
    "feature used": "feature_used",
    "feature_adopted": "feature_adopted",
    "feature adopted": "feature_adopted",
    "ticket_opened": "ticket_opened",
    "support_ticket": "support_ticket",
    "payment_failed": "payment_failed",
    "invoice.payment_failed": "invoice.payment_failed",
    "charge.failed": "payment_failed",
}


def _cutoff(now: Optional[datetime] = None) -> datetime:
    current = now or utcnow()
    return current - timedelta(days=LOOKBACK_DAYS)


def _from_unix(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
    except (TypeError, ValueError, OSError):
        return utcnow()


def _as_dict(item: Any) -> dict[str, Any]:
    if item is None:
        return {}
    if isinstance(item, dict):
        return item
    to_dict = getattr(item, "to_dict", None)
    if callable(to_dict):
        return dict(to_dict())
    return dict(item)


def paginate_stripe_list(list_fn: Callable[..., Any], **params: Any) -> list[dict[str, Any]]:
    """Walk Stripe list() pages (auto_paging_iter, has_more, or a plain list)."""
    params.setdefault("limit", 100)
    first = list_fn(**params)
    if isinstance(first, list):
        return [_as_dict(item) for item in first]
    records: list[dict[str, Any]] = []
    iterator = getattr(first, "auto_paging_iter", None)
    if callable(iterator):
        for item in iterator():
            records.append(_as_dict(item))
        return records
    page = first
    while page is not None:
        data = page.get("data") if isinstance(page, dict) else getattr(page, "data", []) or []
        records.extend(_as_dict(item) for item in data)
        has_more = page.get("has_more") if isinstance(page, dict) else getattr(page, "has_more", False)
        if not has_more or not data:
            break
        last_id = records[-1].get("id")
        params["starting_after"] = last_id
        page = list_fn(**params)
    return records


def bulk_insert_in_chunks(session: Session, objects: list[Any], batch_size: int = BATCH_SIZE) -> int:
    """Flush ORM rows in chunks so Railway dynos do not OOM on large hydrations."""
    if not objects:
        return 0
    size = max(int(batch_size), 1)
    inserted = 0
    for start in range(0, len(objects), size):
        session.add_all(objects[start : start + size])
        session.flush()
        inserted += len(objects[start : start + size])
    return inserted


def map_product_event_name(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    mapped = PRODUCT_EVENT_MAP.get(str(raw).strip().lower())
    if mapped is None and str(raw).strip().lower() in PRODUCT_EVENT_MAP:
        return None
    if mapped:
        return mapped
    name = str(raw).strip()
    if name in {"login", "session_start", "feature_used", "feature_adopted", "ticket_opened", "support_ticket"}:
        return name
    return None


def subscription_to_account_fields(sub: dict[str, Any], customer: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    metadata = dict(sub.get("metadata") or {})
    if customer:
        metadata = {**(customer.get("metadata") or {}), **metadata}
    mrr, contract = mrr_from_subscription(sub)
    channel = metadata.get("channel") or metadata.get("acquisition_channel") or "Paid Search"
    period_end = sub.get("current_period_end")
    created = customer.get("created") if customer else sub.get("created")
    return {
        "channel": str(channel),
        "mrr": float(mrr),
        "contract_type": contract,
        "created_at": _from_unix(created) if created else utcnow(),
        "contract_renewal_at": _from_unix(period_end) if period_end else None,
    }


def customer_to_account_fields(customer: dict[str, Any]) -> dict[str, Any]:
    metadata = customer.get("metadata") or {}
    channel = metadata.get("channel") or metadata.get("acquisition_channel") or "Paid Search"
    contract = metadata.get("contract_type") or "Monthly"
    mrr = float(metadata.get("mrr") or 0.0)
    created = customer.get("created")
    return {
        "channel": str(channel),
        "mrr": mrr,
        "contract_type": contract if contract in {"Monthly", "Annual"} else "Monthly",
        "created_at": _from_unix(created) if created else utcnow(),
        "contract_renewal_at": None,
    }


def mock_historical_product_events(
    org_id: str,
    customer_external_ids: Iterable[str],
    *,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """Synthetic login / adoption / ticket history for the XGBoost feature builder."""
    current = now or utcnow()
    cutoff = _cutoff(current)
    events: list[dict[str, Any]] = []
    for index, external_id in enumerate(customer_external_ids):
        seed = sum(ord(ch) for ch in str(external_id)) + index
        login_gap = 8 + (seed % 6)
        cursor = cutoff + timedelta(days=1 + (seed % 5))
        feature_names = ("dashboard", "reports", "settings", "billing")
        while cursor <= current:
            events.append(
                {
                    "user_id": str(external_id),
                    "event": "login",
                    "timestamp": cursor,
                    "properties": {"source": "historical_backfill", "vendor": "mock", "org_id": org_id},
                }
            )
            if seed % 3 != 0:
                events.append(
                    {
                        "user_id": str(external_id),
                        "event": "feature_used",
                        "timestamp": cursor + timedelta(hours=2),
                        "properties": {
                            "source": "historical_backfill",
                            "vendor": "mock",
                            "feature": feature_names[seed % len(feature_names)],
                            "org_id": org_id,
                        },
                    }
                )
            cursor += timedelta(days=login_gap)
        if seed % 5 == 0:
            events.append(
                {
                    "user_id": str(external_id),
                    "event": "ticket_opened",
                    "timestamp": current - timedelta(days=4 + (seed % 10)),
                    "properties": {"source": "historical_backfill", "vendor": "mock", "severity": "medium"},
                }
            )
    return events


async def fetch_historical_product_events(
    org_id: str,
    customer_external_ids: list[str],
    *,
    segment_access_token: Optional[str] = None,
    posthog_api_key: Optional[str] = None,
    posthog_host: Optional[str] = None,
    posthog_project_id: Optional[str] = None,
    client: Optional[httpx.AsyncClient] = None,
    now: Optional[datetime] = None,
) -> tuple[list[dict[str, Any]], str]:
    """Pull Segment/PostHog event logs, or mock adoption/login history when credentials are absent."""
    segment_token = segment_access_token or os.getenv("SEGMENT_ACCESS_TOKEN") or os.getenv("SEGMENT_API_KEY")
    posthog_key = posthog_api_key or os.getenv("POSTHOG_API_KEY")
    owns = client is None
    if client is None:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        if posthog_key:
            fetched = await _fetch_posthog_events(
                client,
                posthog_key,
                posthog_host=posthog_host,
                posthog_project_id=posthog_project_id,
                now=now,
            )
            if fetched is not None:
                return fetched, "posthog"
        if segment_token:
            fetched = await _fetch_segment_events(client, segment_token, now=now)
            if fetched is not None:
                return fetched, "segment"
    except httpx.HTTPError as exc:
        LOGGER.warning("Product analytics backfill failed (%s); using mock events", exc)
    finally:
        if owns:
            await client.aclose()
    return mock_historical_product_events(org_id, customer_external_ids, now=now), "mock"


async def _fetch_posthog_events(
    client: httpx.AsyncClient,
    api_key: str,
    *,
    posthog_host: Optional[str],
    posthog_project_id: Optional[str],
    now: Optional[datetime],
) -> Optional[list[dict[str, Any]]]:
    project = posthog_project_id or os.getenv("POSTHOG_PROJECT_ID")
    if not project:
        return None
    host = (posthog_host or os.getenv("POSTHOG_HOST") or "https://us.posthog.com").rstrip("/")
    after = _cutoff(now).isoformat() + "Z"
    url = f"{host}/api/projects/{project}/events/"
    headers = {"Authorization": f"Bearer {api_key}"}
    events: list[dict[str, Any]] = []
    params: dict[str, Any] = {"after": after, "limit": 100}
    for _ in range(50):
        response = await client.get(url, headers=headers, params=params)
        if response.status_code >= 400:
            LOGGER.warning("PostHog events HTTP %s", response.status_code)
            return None
        payload = response.json()
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("results") or payload.get("data") or []
        else:
            rows = []
        for row in rows:
            mapped = _normalize_vendor_event(row)
            if mapped:
                events.append(mapped)
        next_url = payload.get("next") if isinstance(payload, dict) else None
        if not next_url:
            break
        url = next_url
        params = {}
    return events


async def _fetch_segment_events(
    client: httpx.AsyncClient,
    token: str,
    *,
    now: Optional[datetime],
) -> Optional[list[dict[str, Any]]]:
    url = os.getenv("SEGMENT_EVENTS_URL")
    if not url:
        return None
    after = _cutoff(now).isoformat() + "Z"
    response = await client.get(
        url,
        headers={"Authorization": f"Bearer {token}"},
        params={"after": after, "limit": 100},
    )
    if response.status_code >= 400:
        LOGGER.warning("Segment events HTTP %s", response.status_code)
        return None
    payload = response.json()
    rows = payload if isinstance(payload, list) else payload.get("data") or payload.get("batch") or []
    events = []
    for row in rows:
        mapped = _normalize_vendor_event(row)
        if mapped:
            events.append(mapped)
    return events


def _normalize_vendor_event(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    properties = dict(row.get("properties") or {})
    event_name = map_product_event_name(row.get("event") or row.get("event_name") or row.get("name"))
    if not event_name:
        return None
    user_id = (
        row.get("distinct_id")
        or row.get("user_id")
        or row.get("userId")
        or properties.get("user_id")
        or properties.get("customer_id")
    )
    if not user_id:
        return None
    properties.setdefault("source", "historical_backfill")
    return {
        "user_id": str(user_id),
        "event": event_name,
        "timestamp": row.get("timestamp") or row.get("time") or row.get("sent_at"),
        "properties": properties,
    }


def fetch_stripe_billing_window(stripe_api_key: str, *, now: Optional[datetime] = None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    import stripe

    stripe.api_key = stripe_api_key
    created = {"gte": int(_cutoff(now).replace(tzinfo=timezone.utc).timestamp())}
    customers = paginate_stripe_list(stripe.Customer.list, created=created, limit=100)
    subscriptions = paginate_stripe_list(stripe.Subscription.list, created=created, status="all", limit=100)
    return customers, subscriptions


def _subscription_customer_id(sub: dict[str, Any]) -> Optional[str]:
    customer_id = sub.get("customer")
    if isinstance(customer_id, dict):
        customer_id = customer_id.get("id")
    return str(customer_id) if customer_id else None


def _apply_accounts(
    session: Session,
    org: Organization,
    customers: list[dict[str, Any]],
    subscriptions: list[dict[str, Any]],
) -> dict[str, CustomerAccount]:
    by_id: dict[str, dict[str, Any]] = {str(row.get("id")): row for row in customers if row.get("id")}
    for sub in subscriptions:
        customer_id = _subscription_customer_id(sub)
        if customer_id and customer_id not in by_id:
            by_id[customer_id] = {
                "id": customer_id,
                "created": sub.get("created"),
                "metadata": sub.get("metadata") or {},
            }

    existing = {
        row.customer_external_id: row
        for row in session.query(CustomerAccount).filter(CustomerAccount.org_id == org.org_id).all()
    }
    pending: list[CustomerAccount] = []

    def upsert(external_id: str, fields: dict[str, Any]) -> CustomerAccount:
        account = existing.get(external_id)
        if account is None:
            account = CustomerAccount(
                id=new_id(),
                org_id=org.org_id,
                customer_external_id=external_id,
                channel=fields["channel"],
                mrr=fields["mrr"],
                contract_type=fields["contract_type"],
                created_at=fields["created_at"],
                contract_renewal_at=fields["contract_renewal_at"],
            )
            pending.append(account)
            existing[external_id] = account
            return account
        account.channel = fields["channel"] or account.channel
        if fields["mrr"]:
            account.mrr = fields["mrr"]
        account.contract_type = fields["contract_type"] or account.contract_type
        if fields["contract_renewal_at"]:
            account.contract_renewal_at = fields["contract_renewal_at"]
        return account

    for customer_id, customer in by_id.items():
        upsert(str(customer_id), customer_to_account_fields(customer))
    for sub in subscriptions:
        customer_id = _subscription_customer_id(sub)
        if customer_id:
            upsert(customer_id, subscription_to_account_fields(sub, by_id.get(customer_id)))

    bulk_insert_in_chunks(session, pending, BATCH_SIZE)
    return existing


def _telemetry_from_billing(
    org_id: str,
    accounts: dict[str, CustomerAccount],
    subscriptions: list[dict[str, Any]],
) -> list[TelemetryEvent]:
    rows: list[TelemetryEvent] = []
    for sub in subscriptions:
        customer_id = sub.get("customer")
        if isinstance(customer_id, dict):
            customer_id = customer_id.get("id")
        account = accounts.get(str(customer_id or ""))
        if account is None:
            continue
        created = _from_unix(sub.get("created")) if sub.get("created") else utcnow()
        rows.append(
            TelemetryEvent(
                id=new_id(),
                org_id=org_id,
                customer_account_id=account.id,
                event_name="feature_used",
                timestamp=created,
                properties={
                    "source": "historical_backfill",
                    "vendor": "stripe",
                    "feature": "billing",
                    "subscription_id": sub.get("id"),
                    "status": sub.get("status"),
                },
            )
        )
        if str(sub.get("status") or "") in {"past_due", "unpaid"}:
            rows.append(
                TelemetryEvent(
                    id=new_id(),
                    org_id=org_id,
                    customer_account_id=account.id,
                    event_name="invoice.payment_failed",
                    timestamp=created,
                    properties={"source": "historical_backfill", "vendor": "stripe", "subscription_id": sub.get("id")},
                )
            )
    return rows


def _telemetry_from_product_events(
    org_id: str,
    accounts: dict[str, CustomerAccount],
    product_events: list[dict[str, Any]],
) -> list[TelemetryEvent]:
    rows: list[TelemetryEvent] = []
    for item in product_events:
        external_id = str(item.get("user_id") or "")
        event_name = map_product_event_name(item.get("event")) or item.get("event")
        account = accounts.get(external_id)
        if not account or not event_name:
            continue
        ts = item.get("timestamp")
        if isinstance(ts, datetime):
            stamp = ts.replace(tzinfo=None) if ts.tzinfo else ts
        elif ts:
            stamp = parse_timestamp(ts)
        else:
            stamp = utcnow()
        properties = dict(item.get("properties") or {})
        properties.setdefault("source", "historical_backfill")
        rows.append(
            TelemetryEvent(
                id=new_id(),
                org_id=org_id,
                customer_account_id=account.id,
                event_name=str(event_name)[:80],
                timestamp=stamp,
                properties=properties,
            )
        )
    return rows


def persist_historical_rows(
    session: Session,
    org: Organization,
    customers: list[dict[str, Any]],
    subscriptions: list[dict[str, Any]],
    product_events: list[dict[str, Any]],
) -> dict[str, int]:
    accounts = _apply_accounts(session, org, customers, subscriptions)
    telemetry = _telemetry_from_billing(org.org_id, accounts, subscriptions)
    telemetry.extend(_telemetry_from_product_events(org.org_id, accounts, product_events))
    telemetry = _dedupe_historical_telemetry(session, org.org_id, telemetry)
    inserted_events = bulk_insert_in_chunks(session, telemetry, BATCH_SIZE)
    session.commit()
    return {
        "accounts": len(accounts),
        "telemetry_events": inserted_events,
    }


def _dedupe_historical_telemetry(
    session: Session,
    org_id: str,
    rows: list[TelemetryEvent],
) -> list[TelemetryEvent]:
    existing = session.query(TelemetryEvent).filter(TelemetryEvent.org_id == org_id).all()
    keys = {(row.customer_account_id, row.event_name, row.timestamp) for row in existing}
    subscription_ids = {
        str((row.properties or {}).get("subscription_id"))
        for row in existing
        if (row.properties or {}).get("subscription_id")
    }
    backfilled_accounts = {
        row.customer_account_id
        for row in existing
        if (row.properties or {}).get("source") == "historical_backfill"
    }
    unique: list[TelemetryEvent] = []
    seen_subs = set(subscription_ids)
    seen_keys = set(keys)
    seen_sub_events = {
        (row.customer_account_id, row.event_name, str((row.properties or {}).get("subscription_id")))
        for row in existing
        if (row.properties or {}).get("subscription_id")
    }
    for row in rows:
        props = row.properties or {}
        sub_id = props.get("subscription_id")
        sub_key = (row.customer_account_id, row.event_name, str(sub_id)) if sub_id else None
        if sub_key and sub_key in seen_sub_events:
            continue
        if (row.customer_account_id, row.event_name, row.timestamp) in seen_keys:
            continue
        if props.get("vendor") == "mock" and row.customer_account_id in backfilled_accounts:
            continue
        unique.append(row)
        seen_keys.add((row.customer_account_id, row.event_name, row.timestamp))
        if sub_key:
            seen_sub_events.add(sub_key)
            seen_subs.add(str(sub_id))
    return unique


async def run_historical_backfill(
    org_id: str,
    stripe_api_key: str,
    *,
    session: Optional[Session] = None,
    segment_access_token: Optional[str] = None,
    posthog_api_key: Optional[str] = None,
    posthog_host: Optional[str] = None,
    posthog_project_id: Optional[str] = None,
    product_client: Optional[httpx.AsyncClient] = None,
    customers: Optional[list[dict[str, Any]]] = None,
    subscriptions: Optional[list[dict[str, Any]]] = None,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """Authenticate to Stripe, page 12 months of billing, and hydrate this tenant."""
    from src.database import get_session_factory
    from src.rls import clear_tenant_context, set_tenant_context

    owns_session = session is None
    if session is None:
        session = get_session_factory()()
    try:
        set_tenant_context(session, org_id)
        org = session.get(Organization, org_id)
        if org is None:
            return {"ok": False, "error": "organization_not_found", "org_id": org_id}
        if customers is None or subscriptions is None:
            customers, subscriptions = await asyncio.to_thread(fetch_stripe_billing_window, stripe_api_key, now=now)
        external_ids = sorted(
            {
                str(row.get("id"))
                for row in customers
                if row.get("id")
            }
            | {
                str(sub.get("customer") if not isinstance(sub.get("customer"), dict) else sub["customer"].get("id"))
                for sub in subscriptions
                if sub.get("customer")
            }
        )
        product_events, product_source = await fetch_historical_product_events(
            org_id,
            external_ids,
            segment_access_token=segment_access_token,
            posthog_api_key=posthog_api_key,
            posthog_host=posthog_host,
            posthog_project_id=posthog_project_id,
            client=product_client,
            now=now,
        )
        counts = persist_historical_rows(session, org, customers, subscriptions, product_events)
        from src.dispatcher import score_tenant_book

        scored = score_tenant_book(org_id, session=session)
        session.commit()
        return {
            "ok": True,
            "org_id": org_id,
            "stripe_customers": len(customers),
            "stripe_subscriptions": len(subscriptions),
            "product_source": product_source,
            "assessments": int(scored.get("scored") or 0),
            **counts,
        }
    except Exception:
        session.rollback()
        raise
    finally:
        clear_tenant_context(session)
        if owns_session:
            session.close()
