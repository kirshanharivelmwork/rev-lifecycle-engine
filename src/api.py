"""Production FastAPI scoring service, webhooks, and webhook alert dispatcher."""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.orm import Session
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from src.auth import AuthUser, get_current_org, get_current_user, require_admin_role
from src.billing import INGEST_LIMIT, SubscriptionInactive, billing_json_response, limiter
from src.churn_model import ChurnScoringEngine, load_or_train
from src.database import get_db, init_db
from src.models_db import Organization
from src.security import decrypt_secret
from src.paths import (
    ACQUISITION_CHANNELS,
    AT_RISK_THRESHOLD,
    CONTRACT_TYPES,
    CRITICAL_THRESHOLD,
    DISPATCH_LOG_PATH,
    MODEL_VERSION,
    PROCESSED_DIR,
)
from src.retraining_pipeline import load_tenant_engine
from src.routers import billing, customers, dashboard, ingestion, webhooks
from src.scoring import assign_risk_tier, recommend_playbook, risk_drivers
from src.tasks import run_historical_backfill

LOGGER = logging.getLogger(__name__)

_ENGINE: ChurnScoringEngine | None = None
_LOADED_VERSION = MODEL_VERSION


class CustomerTelemetry(BaseModel):
    """Inbound customer telemetry accepted by /v1/predict."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    customer_id: str = "CUST_ANON"
    acquisition_channel: str = Field(..., examples=["Paid Search"])
    contract_type: str = Field(..., examples=["Monthly"])
    avg_weekly_logins: float = Field(..., ge=0)
    feature_adoption_score: float = Field(..., ge=0, le=10)
    support_tickets_raised: int = Field(0, ge=0)
    days_since_last_login: int = Field(0, ge=0)
    monthly_recurring_revenue: float = Field(..., gt=0)
    cac_usd: float = Field(500.0, gt=0)
    sales_touchpoints: int = Field(4, ge=1)

    @field_validator("acquisition_channel")
    @classmethod
    def _channel(cls, value: str) -> str:
        if value not in ACQUISITION_CHANNELS:
            raise ValueError(f"acquisition_channel must be one of {ACQUISITION_CHANNELS}")
        return value

    @field_validator("contract_type")
    @classmethod
    def _contract(cls, value: str) -> str:
        if value not in CONTRACT_TYPES:
            raise ValueError(f"contract_type must be one of {CONTRACT_TYPES}")
        return value


class PredictionResponse(BaseModel):
    customer_id: str
    churn_probability: float
    risk_tier: str
    arr_at_risk: float
    recommended_playbook: str
    risk_drivers: str
    model_version: str
    org_id: Optional[str] = None


class OrganizationSecrets(BaseModel):
    """Plaintext tenant secrets. Persist via EncryptedText columns on Organization."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    hubspot_token: Optional[str] = Field(default=None, alias="hubspot_access_token")
    salesforce_key: Optional[str] = Field(default=None, alias="salesforce_access_token")
    stripe_secret: Optional[str] = Field(default=None, alias="stripe_webhook_secret")
    instantly_api_key: Optional[str] = None
    slack_webhook_url: Optional[str] = None
    resend_api_key: Optional[str] = None
    apollo_api_key: Optional[str] = None


def apply_organization_secrets(org: Organization, secrets: OrganizationSecrets) -> Organization:
    """Write plaintext secrets onto the org; SQLAlchemy encrypts on flush."""
    if secrets.hubspot_token:
        org.hubspot_access_token = secrets.hubspot_token
    if secrets.salesforce_key:
        org.salesforce_access_token = secrets.salesforce_key
    if secrets.stripe_secret:
        org.stripe_webhook_secret = secrets.stripe_secret
    if secrets.instantly_api_key:
        org.instantly_api_key = secrets.instantly_api_key
    if secrets.slack_webhook_url:
        org.slack_webhook_url = secrets.slack_webhook_url
    if secrets.resend_api_key:
        org.resend_api_key = secrets.resend_api_key
    if secrets.apollo_api_key:
        org.apollo_api_key = secrets.apollo_api_key
    return org


def plaintext_organization_secrets(org: Organization) -> OrganizationSecrets:
    """Decode secrets for in-process use (no-op when already decrypted by ORM)."""
    return OrganizationSecrets(
        hubspot_token=decrypt_secret(org.hubspot_access_token),
        salesforce_key=decrypt_secret(org.salesforce_access_token),
        stripe_secret=decrypt_secret(org.stripe_webhook_secret),
        instantly_api_key=decrypt_secret(org.instantly_api_key),
        slack_webhook_url=decrypt_secret(org.slack_webhook_url),
        resend_api_key=decrypt_secret(org.resend_api_key),
        apollo_api_key=decrypt_secret(org.apollo_api_key),
    )


class DispatchRequest(CustomerTelemetry):
    webhook_url: Optional[str] = Field(
        default=None,
        description="Slack/Discord/CRM incoming webhook URL. Falls back to WEBHOOK_URL env.",
    )
    force: bool = False


class DispatchResponse(BaseModel):
    dispatched: bool
    reason: str
    http_status: Optional[int] = None
    log_path: str
    prediction: PredictionResponse


def _default_engine() -> ChurnScoringEngine:
    global _ENGINE, _LOADED_VERSION
    if _ENGINE is None:
        _ENGINE, _LOADED_VERSION = load_or_train(tune=False, persist=True)
    return _ENGINE


def get_engine(
    x_org_id: Optional[str] = Header(default=None, alias="X-Org-Id"),
) -> ChurnScoringEngine:
    """Resolve a tenant XGBoost bundle from S3/volume; else models/churn_engine.pkl."""
    tenant = load_tenant_engine(x_org_id)
    if tenant is not None:
        return tenant
    return _default_engine()


def get_predict_engine(
    org: Organization = Depends(get_current_org),
    fallback: ChurnScoringEngine = Depends(get_engine),
) -> ChurnScoringEngine:
    tenant = load_tenant_engine(org.org_id)
    if tenant is not None:
        return tenant
    return fallback


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Rev Lifecycle Engine API",
    description="Multi-tenant B2B revenue intelligence: live ingestion, scoring, and automated save motions.",
    version=MODEL_VERSION,
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_middleware(SlowAPIMiddleware)
_cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",") if origin.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(SubscriptionInactive)
async def _subscription_inactive(_request: Request, _exc: SubscriptionInactive):
    return billing_json_response()


@app.exception_handler(RateLimitExceeded)
async def _rate_limited(_request: Request, _exc: RateLimitExceeded):
    return JSONResponse(status_code=429, content={"error": "Too Many Requests"})


app.include_router(ingestion.router, prefix="/api/v1")
app.include_router(webhooks.router, prefix="/api/v1")
app.include_router(customers.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/api/v1")
app.include_router(billing.router, prefix="/v1")
app.include_router(dashboard.router, prefix="/api/v1")
app.include_router(dashboard.router, prefix="/v1")


@app.post("/api/v1/billing/create-checkout-session")
@app.post("/v1/billing/create-checkout-session")
def create_checkout_session(
    user: AuthUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Return a Stripe Checkout URL for the authenticated Clerk organization."""
    return billing.create_stripe_checkout_session(user, db)


class HistoricalBackfillRequest(BaseModel):
    stripe_api_key: Optional[str] = None
    segment_access_token: Optional[str] = None
    posthog_api_key: Optional[str] = None
    posthog_host: Optional[str] = None
    posthog_project_id: Optional[str] = None


class HistoricalBackfillResponse(BaseModel):
    accepted: bool
    org_id: str
    status: str
    source: str = "historical_backfill"


@app.post("/v1/backfill", response_model=HistoricalBackfillResponse)
@app.post("/api/v1/backfill", response_model=HistoricalBackfillResponse)
def trigger_historical_backfill(
    payload: HistoricalBackfillRequest,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
) -> HistoricalBackfillResponse:
    stripe_key = (
        (payload.stripe_api_key or "").strip()
        or os.getenv("STRIPE_SECRET_KEY")
        or os.getenv("STRIPE_API_KEY")
        or ""
    )
    if not stripe_key:
        raise HTTPException(status_code=400, detail="stripe_api_key is required to hydrate billing history")
    run_historical_backfill.delay(
        org.org_id,
        stripe_key,
        segment_access_token=payload.segment_access_token,
        posthog_api_key=payload.posthog_api_key,
        posthog_host=payload.posthog_host,
        posthog_project_id=payload.posthog_project_id,
    )
    return HistoricalBackfillResponse(
        accepted=True,
        org_id=org.org_id,
        status="queued",
    )


def score_payload(
    payload: CustomerTelemetry,
    engine: ChurnScoringEngine,
    org: Organization | None = None,
) -> PredictionResponse:
    data = payload.model_dump()
    data.pop("webhook_url", None)
    data.pop("force", None)
    frame = pd.DataFrame([data])
    probability = float(engine.predict_proba(frame)[0])
    row = frame.iloc[0]
    arr = float(payload.monthly_recurring_revenue) * 12.0
    return PredictionResponse(
        customer_id=payload.customer_id,
        churn_probability=round(probability, 6),
        risk_tier=assign_risk_tier(probability),
        arr_at_risk=round(arr, 2),
        recommended_playbook=recommend_playbook(row),
        risk_drivers=risk_drivers(row),
        model_version=MODEL_VERSION,
        org_id=org.org_id if org else None,
    )


def _webhook_body(prediction: PredictionResponse, payload: CustomerTelemetry) -> dict[str, Any]:
    """Payload compatible with Slack incoming webhooks and Discord `content`."""
    text = (
        f":rotating_light: *Critical churn risk* `{prediction.customer_id}`\n"
        f"Probability: *{prediction.churn_probability:.1%}* · Tier: *{prediction.risk_tier}*\n"
        f"ARR at risk: *${prediction.arr_at_risk:,.0f}* · "
        f"{payload.acquisition_channel} / {payload.contract_type}\n"
        f"Drivers: {prediction.risk_drivers}\n"
        f"Playbook: {prediction.recommended_playbook}"
    )
    return {
        "text": text,
        "content": text.replace("*", "**"),
        "username": "Rev Lifecycle Engine",
        "attachments": [
            {
                "color": "#ff5c7a",
                "title": f"{prediction.customer_id} crossed the {CRITICAL_THRESHOLD:.0%} critical threshold",
                "fields": [
                    {"title": "Churn probability", "value": f"{prediction.churn_probability:.1%}", "short": True},
                    {"title": "ARR at risk", "value": f"${prediction.arr_at_risk:,.0f}", "short": True},
                    {"title": "Playbook", "value": prediction.recommended_playbook, "short": False},
                ],
            }
        ],
        "crm": {
            "event": "churn.critical",
            "customer_id": prediction.customer_id,
            "churn_probability": prediction.churn_probability,
            "arr_at_risk": prediction.arr_at_risk,
            "recommended_playbook": prediction.recommended_playbook,
            "model_version": prediction.model_version,
        },
    }


def _append_dispatch_log(record: dict[str, Any]) -> None:
    PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
    history: list[dict[str, Any]] = []
    if DISPATCH_LOG_PATH.exists():
        try:
            loaded = json.loads(DISPATCH_LOG_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                history = loaded
        except json.JSONDecodeError:
            history = []
    history.append(record)
    DISPATCH_LOG_PATH.write_text(json.dumps(history, indent=2), encoding="utf-8")


@app.get("/health")
def health() -> dict[str, Any]:
    """Liveness probe: must not train or read gitignored CSVs (Railway / Docker boot)."""
    model_name = (
        getattr(_ENGINE, "production_name_", None) if _ENGINE is not None else "not_loaded"
    )
    return {
        "status": "ok",
        "model_version": _LOADED_VERSION if _ENGINE is not None else MODEL_VERSION,
        "model_name": model_name or "not_loaded",
        "at_risk_threshold": AT_RISK_THRESHOLD,
        "critical_threshold": CRITICAL_THRESHOLD,
        "auth": "Clerk JWT bearer token required on non-webhook routes; Admin role for billing and dispatcher",
    }


@app.post("/v1/predict", response_model=PredictionResponse)
@app.post("/api/v1/predict", response_model=PredictionResponse)
@limiter.limit(INGEST_LIMIT)
def predict(
    request: Request,
    payload: CustomerTelemetry,
    org: Organization = Depends(get_current_org),
    engine: ChurnScoringEngine = Depends(get_predict_engine),
) -> PredictionResponse:
    try:
        return score_payload(payload, engine, org=org)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Prediction failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/dispatch-alert", response_model=DispatchResponse)
@limiter.limit(INGEST_LIMIT)
def dispatch_alert(
    request: Request,
    payload: DispatchRequest,
    _admin: AuthUser = Depends(require_admin_role),
    org: Organization = Depends(get_current_org),
    engine: ChurnScoringEngine = Depends(get_predict_engine),
) -> DispatchResponse:
    prediction = score_payload(payload, engine, org=org)
    webhook_url = payload.webhook_url or os.getenv("WEBHOOK_URL")
    body = _webhook_body(prediction, payload)

    if prediction.churn_probability <= CRITICAL_THRESHOLD and not payload.force:
        record = {
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped",
            "reason": f"probability {prediction.churn_probability:.4f} <= {CRITICAL_THRESHOLD}",
            "org_id": org.org_id,
            "prediction": prediction.model_dump(),
            "webhook_payload": body,
        }
        _append_dispatch_log(record)
        return DispatchResponse(
            dispatched=False,
            reason=record["reason"],
            http_status=None,
            log_path=str(DISPATCH_LOG_PATH),
            prediction=prediction,
        )

    http_status: int | None = None
    dispatch_status = "simulated"
    reason = "Webhook simulated (no WEBHOOK_URL). Payload persisted to dispatched_alerts_log.json."
    if webhook_url:
        try:
            response = requests.post(webhook_url, json=body, timeout=8)
            http_status = int(response.status_code)
            dispatch_status = "sent" if response.ok else "failed"
            reason = f"Webhook {dispatch_status} with HTTP {http_status}"
        except requests.RequestException as exc:
            dispatch_status = "failed"
            reason = f"Webhook transport error: {exc}"

    record = {
        "dispatched_at": datetime.now(timezone.utc).isoformat(),
        "status": dispatch_status,
        "reason": reason,
        "http_status": http_status,
        "org_id": org.org_id,
        "prediction": prediction.model_dump(),
        "webhook_payload": body,
    }
    _append_dispatch_log(record)
    return DispatchResponse(
        dispatched=dispatch_status in {"sent", "simulated"},
        reason=reason,
        http_status=http_status,
        log_path=str(DISPATCH_LOG_PATH),
        prediction=prediction,
    )
