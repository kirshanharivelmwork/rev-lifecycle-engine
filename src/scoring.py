"""Shared risk-tier, playbook, and inference-frame helpers."""

from __future__ import annotations

import pandas as pd

from src.data_pipeline import engineer_lifecycle_features
from src.paths import CRITICAL_THRESHOLD, MEDIUM_THRESHOLD


def assign_risk_tier(probability: float) -> str:
    """Map a churn probability onto Low / Medium / Critical."""
    if probability >= CRITICAL_THRESHOLD:
        return "Critical"
    if probability >= MEDIUM_THRESHOLD:
        return "Medium"
    return "Low"


def risk_drivers(row: pd.Series) -> str:
    """Short, operator-readable reasons a CSM would act."""
    drivers: list[str] = []
    days = float(row.get("days_since_last_login", 0) or 0)
    adoption = float(row.get("feature_adoption_score", 0) or 0)
    tickets = float(row.get("support_tickets_raised", 0) or 0)
    logins = float(row.get("avg_weekly_logins", 0) or 0)
    contract = str(row.get("contract_type", "") or "")
    if days > 21:
        drivers.append(f"{int(days)}d dark (critical inactivity)")
    elif days > 14:
        drivers.append(f"{int(days)}d inactive")
    if adoption < 4.0:
        drivers.append(f"low adoption ({adoption:.1f}/10)")
    if logins < 2.5:
        drivers.append("login collapse")
    if tickets >= 4:
        drivers.append(f"{int(tickets)} open-pattern tickets")
    if contract == "Monthly":
        drivers.append("monthly term")
    return "; ".join(drivers) if drivers else "residual model risk"


def recommend_playbook(row: pd.Series) -> str:
    """Deterministic CS playbook used by the model, API, and dashboard."""
    days = float(row.get("days_since_last_login", 0) or 0)
    engagement = float(row.get("engagement_index", 0) or 0)
    tickets = float(row.get("support_tickets_raised", 0) or 0)
    adoption = float(row.get("feature_adoption_score", 0) or 0)
    ltv_cac = float(row.get("ltv_cac_ratio", 0) or 0)
    contract = str(row.get("contract_type", "") or "")

    if days > 21:
        return "CSM re-engagement sprint within 48h + executive sponsor ping"
    if adoption < 4.0:
        return "Guided onboarding reboot: core-feature activation workshop"
    if tickets >= 4:
        return "Technical account review; escalate open tickets to solutions eng"
    if engagement < 4.5:
        return "In-product adoption campaign + weekly success checklist"
    if contract == "Monthly" and ltv_cac >= 3:
        return "Annual conversion incentive (discount locked to 12-month term)"
    if ltv_cac < 3:
        return "Value review: usage vs. contracted seats; avoid over-discounting"
    return "Health-score watchlist; nurture with case studies and QBR"


def encode_model_columns(df: pd.DataFrame, feature_columns: list[str]) -> pd.DataFrame:
    """Build the production design matrix from either processed or raw-like rows.

    Dummy columns are derived from label columns so a *single* inference payload
    does not collapse `pd.get_dummies(drop_first=True)` onto the wrong baseline.
    """
    frame = df.copy()
    if "ltv_est" not in frame.columns or "engagement_index" not in frame.columns:
        if "cac_usd" not in frame.columns:
            frame["cac_usd"] = 500.0
        if "sales_touchpoints" not in frame.columns:
            frame["sales_touchpoints"] = 4
        frame = engineer_lifecycle_features(frame)

    for col in feature_columns:
        if col.startswith("acquisition_channel_") and "acquisition_channel" in frame.columns:
            level = col[len("acquisition_channel_") :]
            frame[col] = (frame["acquisition_channel"].astype(str) == level).astype(int)
        elif col.startswith("contract_type_") and "contract_type" in frame.columns:
            level = col[len("contract_type_") :]
            frame[col] = (frame["contract_type"].astype(str) == level).astype(int)

    numeric = frame.reindex(columns=feature_columns, fill_value=0)
    return numeric.apply(pd.to_numeric, errors="coerce").fillna(0.0).astype(float)
