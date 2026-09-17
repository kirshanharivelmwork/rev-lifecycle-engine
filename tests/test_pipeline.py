"""Unit tests for generation, feature engineering, inference, and scoring."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from src.churn_model import ChurnScoringEngine
from src.data_pipeline import ANNUAL_CHURN_MARGIN_FACTOR, run_pipeline
from src.paths import (
    ACQUISITION_LEADS_PATH,
    N_CUSTOMERS,
    USER_TELEMETRY_PATH,
)
from src.stats_engine import chi_square_channel_churn, two_sample_ttest_adoption


def test_data_generation(raw_tables) -> None:
    leads, telemetry = raw_tables
    assert ACQUISITION_LEADS_PATH.exists()
    assert USER_TELEMETRY_PATH.exists()
    assert len(leads) == N_CUSTOMERS
    assert len(telemetry) == N_CUSTOMERS
    assert leads["customer_id"].notna().all()
    assert telemetry["customer_id"].notna().all()
    assert leads["customer_id"].is_unique
    assert telemetry["customer_id"].is_unique
    assert set(leads["customer_id"]) == set(telemetry["customer_id"])
    assert leads["customer_id"].iloc[0] == "CUST_10000"
    disk_leads = pd.read_csv(ACQUISITION_LEADS_PATH)
    disk_telem = pd.read_csv(USER_TELEMETRY_PATH)
    assert len(disk_leads) == N_CUSTOMERS
    assert len(disk_telem) == N_CUSTOMERS
    assert disk_leads["customer_id"].notna().all()
    assert disk_telem["customer_id"].notna().all()
    assert set(disk_telem["churned"].unique()).issubset({0, 1})


def test_pipeline_merge_and_features(processed_frame: pd.DataFrame) -> None:
    df = processed_frame
    assert df.shape[0] == N_CUSTOMERS
    assert df.shape[1] >= 16
    assert df.isna().sum().sum() == 0

    expected_ltv = (df["monthly_recurring_revenue"] * 12.0) / ANNUAL_CHURN_MARGIN_FACTOR
    expected_ratio = expected_ltv / df["cac_usd"]
    pd.testing.assert_series_equal(df["ltv_est"], expected_ltv, check_names=False)
    pd.testing.assert_series_equal(df["ltv_cac_ratio"], expected_ratio, check_names=False)

    expected_inactivity = (df["days_since_last_login"] > 14).astype(int)
    pd.testing.assert_series_equal(
        df["high_risk_inactivity"],
        expected_inactivity,
        check_names=False,
        check_dtype=False,
    )
    expected_engagement = df["avg_weekly_logins"] * 0.4 + df["feature_adoption_score"] * 0.6
    pd.testing.assert_series_equal(
        df["engagement_index"], expected_engagement, check_names=False
    )

    channel_dummies = [c for c in df.columns if c.startswith("acquisition_channel_")]
    contract_dummies = [c for c in df.columns if c.startswith("contract_type_")]
    assert len(channel_dummies) == 3  # 4 levels with drop_first=True
    assert len(contract_dummies) == 1  # 2 levels with drop_first=True
    assert "acquisition_channel" in df.columns
    assert "contract_type" in df.columns

    rerun = run_pipeline(persist=False)
    assert rerun.shape == df.shape


def test_stats_significance(processed_frame: pd.DataFrame) -> None:
    ttest = two_sample_ttest_adoption(processed_frame)
    chi = chi_square_channel_churn(processed_frame)

    assert 0.0 <= ttest.p_value <= 1.0
    assert np.isfinite(ttest.t_statistic)
    assert ttest.degrees_of_freedom > 0
    assert np.isfinite(ttest.cohens_d)
    assert ttest.n_a + ttest.n_b == len(processed_frame)

    assert 0.0 <= chi.p_value <= 1.0
    assert np.isfinite(chi.chi2)
    assert chi.degrees_of_freedom >= 1
    assert (chi.expected >= 0).all().all()
    assert chi.observed.to_numpy().sum() == len(processed_frame)


def test_model_inference(processed_frame: pd.DataFrame) -> None:
    engine = ChurnScoringEngine()
    engine.fit(processed_frame, tune=False)

    sample = processed_frame.drop(columns=["churned"]).head(12)
    proba = engine.predict_proba(sample)
    assert len(proba) == 12
    assert np.all((proba >= 0.0) & (proba <= 1.0))
    assert np.all(np.isfinite(proba))

    unseen = processed_frame.drop(columns=["churned"]).sample(5, random_state=7)
    unseen_proba = engine.predict_proba(unseen)
    assert unseen_proba.shape == (5,)
    assert np.all((unseen_proba >= 0.0) & (unseen_proba <= 1.0))

    alerts = engine.score_active_customers(processed_frame, threshold=0.65, persist=False)
    assert "churn_risk_score" in alerts.columns
    if not alerts.empty:
        assert alerts["churn_risk_score"].between(0.65, 1.0).all()
        assert alerts["recommended_intervention"].notna().all()
