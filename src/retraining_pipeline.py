"""Tenant-scoped XGBoost retraining and durable model storage (S3 or volume)."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any, Optional, Protocol

import pandas as pd
from sqlalchemy.orm import Session
from xgboost import XGBClassifier

from src.churn_model import ChurnScoringEngine, dumps_engine, load_engine_from_bytes
from src.data_pipeline import encode_categoricals, engineer_lifecycle_features
from src.feature_builder import build_feature_row
from src.models_db import ChurnAssessment, CustomerAccount, Organization, TelemetryEvent
from src.paths import ENGINE_BUNDLE_PATH, MODEL_VERSION, TENANT_MODELS_DIR
from src.rls import clear_tenant_context, set_tenant_context

LOGGER = logging.getLogger(__name__)
MIN_TRAIN_ROWS = 8
_TENANT_CACHE: dict[str, ChurnScoringEngine] = {}


class ModelStore(Protocol):
    def put_bytes(self, org_id: str, payload: bytes) -> str: ...

    def get_bytes(self, org_id: str) -> Optional[bytes]: ...


class DurableModelStore:
    """S3 when MODEL_S3_BUCKET/AWS_S3_BUCKET is set; else Railway/local volume."""

    def object_key(self, org_id: str) -> str:
        return f"tenants/{org_id}/churn_engine.pkl"

    def volume_path(self, org_id: str) -> Path:
        root = Path(os.getenv("MODEL_STORE_DIR", str(TENANT_MODELS_DIR)))
        return root / org_id / "churn_engine.pkl"

    def bucket_name(self) -> Optional[str]:
        return os.getenv("MODEL_S3_BUCKET") or os.getenv("AWS_S3_BUCKET") or None

    def _s3(self):
        import boto3

        return boto3.client("s3")

    def put_bytes(self, org_id: str, payload: bytes) -> str:
        bucket = self.bucket_name()
        if bucket:
            key = self.object_key(org_id)
            self._s3().put_object(Bucket=bucket, Key=key, Body=payload)
            uri = f"s3://{bucket}/{key}"
            LOGGER.info("Uploaded tenant model %s", uri)
            return uri
        dest = self.volume_path(org_id)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(payload)
        LOGGER.info("Wrote tenant model %s", dest)
        return str(dest)

    def get_bytes(self, org_id: str) -> Optional[bytes]:
        bucket = self.bucket_name()
        if bucket:
            try:
                response = self._s3().get_object(Bucket=bucket, Key=self.object_key(org_id))
                body = response["Body"]
                data = body.read() if hasattr(body, "read") else body
                return bytes(data)
            except Exception as exc:
                code = ""
                response = getattr(exc, "response", None) or {}
                if isinstance(response, dict):
                    code = str((response.get("Error") or {}).get("Code") or "")
                if code in {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}:
                    return None
                LOGGER.warning("S3 tenant model fetch failed org=%s: %s", org_id, exc)
                return None
        path = self.volume_path(org_id)
        if path.exists():
            return path.read_bytes()
        return None


_STORE: Optional[ModelStore] = None


def get_model_store() -> ModelStore:
    global _STORE
    if _STORE is None:
        _STORE = DurableModelStore()
    return _STORE


def set_model_store(store: Optional[ModelStore]) -> None:
    global _STORE
    _STORE = store


def clear_tenant_engine_cache(org_id: Optional[str] = None) -> None:
    if org_id:
        _TENANT_CACHE.pop(org_id, None)
    else:
        _TENANT_CACHE.clear()


def load_tenant_engine(org_id: Optional[str], *, store: Optional[ModelStore] = None) -> Optional[ChurnScoringEngine]:
    """Download a tenant bundle from S3/volume. None → caller should use churn_engine.pkl."""
    if not org_id:
        return None
    cached = _TENANT_CACHE.get(org_id)
    if cached is not None:
        return cached
    store = store or get_model_store()
    blob = store.get_bytes(org_id)
    if not blob:
        return None
    try:
        engine, _version = load_engine_from_bytes(blob)
    except Exception as exc:
        LOGGER.warning("Could not unpickle tenant model org=%s: %s", org_id, exc)
        return None
    _TENANT_CACHE[org_id] = engine
    return engine


def _label_churned(session: Session, account: CustomerAccount, days_since: float) -> int:
    latest = (
        session.query(ChurnAssessment)
        .filter(
            ChurnAssessment.org_id == account.org_id,
            ChurnAssessment.customer_account_id == account.id,
        )
        .order_by(ChurnAssessment.assessed_at.desc())
        .first()
    )
    if latest is not None:
        return int(float(latest.churn_probability) >= 0.5)
    return int(days_since >= 14)


def build_tenant_training_frame(session: Session, org_id: str) -> tuple[pd.DataFrame, int]:
    """Assemble a scoring frame from this tenant's TelemetryEvent rows only."""
    events = session.query(TelemetryEvent).filter(TelemetryEvent.org_id == org_id).all()
    account_ids = {row.customer_account_id for row in events}
    if not account_ids:
        return pd.DataFrame(), 0
    accounts = (
        session.query(CustomerAccount)
        .filter(CustomerAccount.org_id == org_id, CustomerAccount.id.in_(account_ids))
        .all()
    )
    rows = []
    for account in accounts:
        series = build_feature_row(session, account)
        payload = series.to_dict()
        payload["churned"] = _label_churned(session, account, float(payload.get("days_since_last_login") or 0))
        rows.append(payload)
    if not rows:
        return pd.DataFrame(), len(events)
    frame = engineer_lifecycle_features(pd.DataFrame(rows))
    frame = encode_categoricals(frame)
    return frame, len(events)


def _fit_tenant_xgboost(frame: pd.DataFrame) -> ChurnScoringEngine:
    engine = ChurnScoringEngine()
    X, y = engine._model_frame(frame)
    if y is None:
        raise ValueError("Training frame missing churned labels")
    if int(y.nunique()) < 2:
        raise ValueError("Tenant telemetry does not contain both churn classes")
    if len(frame) < MIN_TRAIN_ROWS:
        raise ValueError(f"Need at least {MIN_TRAIN_ROWS} tenant accounts with telemetry")
    model = XGBClassifier(
        n_estimators=48,
        max_depth=3,
        learning_rate=0.12,
        subsample=0.9,
        colsample_bytree=0.9,
        min_child_weight=1,
        eval_metric="logloss",
        n_jobs=1,
        random_state=42,
        verbosity=0,
    )
    model.fit(X, y)
    engine.feature_columns_ = list(X.columns)
    engine.production_model_ = model
    engine.production_name_ = "xgboost"
    engine.fitted_ = True
    return engine


def _train_tenant_model_sync(
    org_id: str,
    session: Optional[Session],
    store: Optional[ModelStore],
) -> dict[str, Any]:
    from src.database import get_session_factory

    owns = session is None
    if session is None:
        session = get_session_factory()()
    store = store or get_model_store()
    try:
        org = session.get(Organization, org_id)
        if org is None:
            return {"ok": False, "error": "organization_not_found", "org_id": org_id}
        set_tenant_context(session, org_id)
        leaked = (
            session.query(TelemetryEvent.org_id)
            .filter(TelemetryEvent.org_id != org_id)
            .first()
        )
        if leaked:
            return {"ok": False, "error": "rls_violation", "org_id": org_id}
        frame, n_events = build_tenant_training_frame(session, org_id)
        if frame.empty:
            return {"ok": False, "error": "no_tenant_telemetry", "org_id": org_id, "events": n_events}
        engine = _fit_tenant_xgboost(frame)
        uri = store.put_bytes(org_id, dumps_engine(engine, version=f"{MODEL_VERSION}-tenant"))
        clear_tenant_engine_cache(org_id)
        _TENANT_CACHE[org_id] = engine
        return {
            "ok": True,
            "org_id": org_id,
            "rows": int(len(frame)),
            "events": int(n_events),
            "uri": uri,
            "model_name": engine.production_name_,
        }
    except Exception as exc:
        LOGGER.warning("Tenant retrain failed org=%s: %s", org_id, exc)
        return {"ok": False, "error": str(exc), "org_id": org_id}
    finally:
        clear_tenant_context(session)
        if owns:
            session.close()


async def train_tenant_model(
    org_id: str,
    *,
    session: Optional[Session] = None,
    store: Optional[ModelStore] = None,
) -> dict[str, Any]:
    """Retrain XGBoost on one tenant's TelemetryEvent history and persist off-container."""
    if session is not None:
        return _train_tenant_model_sync(org_id, session, store)
    return await asyncio.to_thread(_train_tenant_model_sync, org_id, None, store)


def fallback_global_engine_path() -> Path:
    return ENGINE_BUNDLE_PATH
