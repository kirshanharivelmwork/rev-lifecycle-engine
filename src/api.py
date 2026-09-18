"""Production FastAPI scoring service and webhook alert dispatcher."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd
import requests
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.churn_model import ChurnScoringEngine, load_or_train
from src.paths import (
    ACQUISITION_CHANNELS,
    AT_RISK_THRESHOLD,
    CONTRACT_TYPES,
    CRITICAL_THRESHOLD,
    DISPATCH_LOG_PATH,
    MODEL_VERSION,
    PROCESSED_DIR,
)
from src.scoring import assign_risk_tier, recommend_playbook, risk_drivers

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


def get_engine() -> ChurnScoringEngine:
    global _ENGINE, _LOADED_VERSION
    if _ENGINE is None:
        _ENGINE, _LOADED_VERSION = load_or_train(tune=False, persist=True)
    return _ENGINE


app = FastAPI(
    title="Rev Lifecycle Engine API",
    description="Churn scoring and critical-risk webhook dispatch for B2B SaaS.",
    version=MODEL_VERSION,
)


def score_payload(payload: CustomerTelemetry, engine: ChurnScoringEngine) -> PredictionResponse:
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
def health(engine: ChurnScoringEngine = Depends(get_engine)) -> dict[str, Any]:
    return {
        "status": "ok",
        "model_version": MODEL_VERSION,
        "model_name": engine.production_name_,
        "at_risk_threshold": AT_RISK_THRESHOLD,
        "critical_threshold": CRITICAL_THRESHOLD,
    }


@app.post("/v1/predict", response_model=PredictionResponse)
def predict(
    payload: CustomerTelemetry,
    engine: ChurnScoringEngine = Depends(get_engine),
) -> PredictionResponse:
    try:
        return score_payload(payload, engine)
    except Exception as exc:  # pragma: no cover - defensive
        LOGGER.exception("Prediction failed")
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/v1/dispatch-alert", response_model=DispatchResponse)
def dispatch_alert(
    payload: DispatchRequest,
    engine: ChurnScoringEngine = Depends(get_engine),
) -> DispatchResponse:
    prediction = score_payload(payload, engine)
    webhook_url = payload.webhook_url or os.getenv("WEBHOOK_URL")
    body = _webhook_body(prediction, payload)

    if prediction.churn_probability <= CRITICAL_THRESHOLD and not payload.force:
        record = {
            "dispatched_at": datetime.now(timezone.utc).isoformat(),
            "status": "skipped",
            "reason": f"probability {prediction.churn_probability:.4f} <= {CRITICAL_THRESHOLD}",
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
    status = "simulated"
    reason = "Webhook simulated (no WEBHOOK_URL). Payload persisted to dispatched_alerts_log.json."
    if webhook_url:
        try:
            response = requests.post(webhook_url, json=body, timeout=8)
            http_status = int(response.status_code)
            status = "sent" if response.ok else "failed"
            reason = f"Webhook {status} with HTTP {http_status}"
        except requests.RequestException as exc:
            status = "failed"
            reason = f"Webhook transport error: {exc}"

    record = {
        "dispatched_at": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "reason": reason,
        "http_status": http_status,
        "prediction": prediction.model_dump(),
        "webhook_payload": body,
    }
    _append_dispatch_log(record)
    return DispatchResponse(
        dispatched=status in {"sent", "simulated"},
        reason=reason,
        http_status=http_status,
        log_path=str(DISPATCH_LOG_PATH),
        prediction=prediction,
    )
