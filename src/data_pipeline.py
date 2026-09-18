"""Unified SaaS data pipeline: ingest, validate, merge, and engineer features."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from src.paths import (
    ACQUISITION_LEADS_PATH,
    FULL_FUNNEL_FEATURES_PATH,
    PROCESSED_DIR,
    USER_TELEMETRY_PATH,
)

LOGGER = logging.getLogger(__name__)

ANNUAL_CHURN_MARGIN_FACTOR = 0.05

LEAD_SCHEMA = {
    "customer_id": "object",
    "acquisition_channel": "object",
    "sales_touchpoints": "int64",
    "cac_usd": "float64",
    "monthly_recurring_revenue": "float64",
    "contract_type": "object",
}
TELEMETRY_SCHEMA = {
    "customer_id": "object",
    "avg_weekly_logins": "float64",
    "feature_adoption_score": "float64",
    "support_tickets_raised": "int64",
    "days_since_last_login": "int64",
    "churned": "int64",
}


class SchemaValidationError(ValueError):
    """Raised when a raw extract fails schema or null-ID checks."""


def _read_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"Missing raw extract {path}. Run `python -m src.generate_data` first."
        )
    return pd.read_csv(path)


def _validate(df: pd.DataFrame, schema: dict[str, str], name: str) -> pd.DataFrame:
    missing = [col for col in schema if col not in df.columns]
    if missing:
        raise SchemaValidationError(f"{name} missing required columns: {missing}")

    out = df.loc[:, list(schema)].copy()
    numeric_ints = {k for k, v in schema.items() if v == "int64"}
    numeric_floats = {k for k, v in schema.items() if v == "float64"}
    for col in numeric_ints:
        out[col] = pd.to_numeric(out[col], errors="coerce").astype("Int64")
    for col in numeric_floats:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    null_ids = int(out["customer_id"].isna().sum())
    if null_ids:
        raise SchemaValidationError(f"{name} contains {null_ids} null customer_id values")
    if out["customer_id"].duplicated().any():
        raise SchemaValidationError(f"{name} contains duplicate customer_id values")

    null_cells = int(out.isna().sum().sum())
    if null_cells:
        LOGGER.warning("%s contains %s null cells after type coercion", name, null_cells)
        raise SchemaValidationError(f"{name} contains null values after validation")
    return out


def engineer_lifecycle_features(df: pd.DataFrame) -> pd.DataFrame:
    """Compute LTV, LTV:CAC, inactivity risk, and engagement index."""
    out = df.copy()
    out["ltv_est"] = (out["monthly_recurring_revenue"] * 12.0) / ANNUAL_CHURN_MARGIN_FACTOR
    out["ltv_cac_ratio"] = out["ltv_est"] / out["cac_usd"].clip(lower=1e-6)
    out["high_risk_inactivity"] = (out["days_since_last_login"] > 14).astype(int)
    out["engagement_index"] = (
        out["avg_weekly_logins"] * 0.4 + out["feature_adoption_score"] * 0.6
    )
    if "days_until_renewal" not in out.columns:
        inactivity = out["days_since_last_login"].clip(lower=0)
        is_annual = (
            out["contract_type"].astype(str).eq("Annual")
            if "contract_type" in out.columns
            else pd.Series(False, index=out.index)
        )
        monthly_left = (30 - (inactivity % 30)).clip(lower=1)
        annual_left = (365 - (inactivity % 365)).clip(lower=1)
        out["days_until_renewal"] = monthly_left.where(~is_annual, annual_left).astype(int)
    if "contract_renewal_urgency_ratio" not in out.columns:
        out["contract_renewal_urgency_ratio"] = (
            out["days_since_last_login"].clip(lower=0) / out["days_until_renewal"].clip(lower=1)
        )
    return out


def encode_categoricals(df: pd.DataFrame) -> pd.DataFrame:
    """One-hot encode acquisition_channel and contract_type (drop_first=True)."""
    return pd.get_dummies(
        df,
        columns=["acquisition_channel", "contract_type"],
        drop_first=True,
        dtype=int,
    )


def run_pipeline(
    leads_path: Path | None = None,
    telemetry_path: Path | None = None,
    output_path: Path | None = None,
    persist: bool = True,
) -> pd.DataFrame:
    """Ingest raw extracts, validate, merge, engineer features, and persist.

    Original categorical columns are retained alongside dummy variables so that
    inferential tests (chi-square) can still use the native channel labels.
    """
    leads_path = Path(leads_path) if leads_path else ACQUISITION_LEADS_PATH
    telemetry_path = Path(telemetry_path) if telemetry_path else USER_TELEMETRY_PATH
    output_path = Path(output_path) if output_path else FULL_FUNNEL_FEATURES_PATH

    leads = _validate(_read_csv(leads_path), LEAD_SCHEMA, "acquisition_leads")
    telemetry = _validate(_read_csv(telemetry_path), TELEMETRY_SCHEMA, "user_telemetry")

    merged = leads.merge(telemetry, on="customer_id", how="inner", validate="one_to_one")
    if len(merged) != len(leads) or len(merged) != len(telemetry):
        raise SchemaValidationError(
            "customer_id mismatch between acquisition and telemetry extracts"
        )

    featured = engineer_lifecycle_features(merged)
    # Keep native categoricals for stats; append drop_first dummies.
    dummies = pd.get_dummies(
        featured[["acquisition_channel", "contract_type"]],
        columns=["acquisition_channel", "contract_type"],
        drop_first=True,
        dtype=int,
    )
    processed = pd.concat([featured, dummies], axis=1)

    if persist:
        PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
        processed.to_csv(output_path, index=False)
        LOGGER.info("Wrote processed funnel features -> %s (%s rows)", output_path, len(processed))
    return processed


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    df = run_pipeline()
    LOGGER.info("Processed shape: %s", df.shape)
    LOGGER.info("Churn rate: %.1f%%", 100.0 * df["churned"].mean())
    LOGGER.info("Median LTV:CAC: %.2f", df["ltv_cac_ratio"].median())


if __name__ == "__main__":
    main()
