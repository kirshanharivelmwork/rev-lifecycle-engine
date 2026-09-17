"""Inferential statistics engine (Google Advanced Data Analytics curriculum).

1. Two-sample t-test: feature_adoption_score for churned vs. retained customers.
2. Chi-square test of independence: churned vs. acquisition_channel.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd
from scipy import stats

from src.data_pipeline import run_pipeline
from src.paths import FULL_FUNNEL_FEATURES_PATH

LOGGER = logging.getLogger(__name__)
ALPHA = 0.05


@dataclass
class TTestResult:
    metric: str
    group_a: str
    group_b: str
    n_a: int
    n_b: int
    mean_a: float
    mean_b: float
    t_statistic: float
    p_value: float
    degrees_of_freedom: float
    cohens_d: float
    significant: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChiSquareResult:
    metric: str
    chi2: float
    p_value: float
    degrees_of_freedom: int
    expected: pd.DataFrame
    observed: pd.DataFrame
    significant: bool
    cramers_v: float

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["expected"] = self.expected
        payload["observed"] = self.observed
        return payload


def _cohens_d(a: np.ndarray, b: np.ndarray) -> float:
    n_a, n_b = len(a), len(b)
    var_a = np.var(a, ddof=1)
    var_b = np.var(b, ddof=1)
    pooled = np.sqrt(((n_a - 1) * var_a + (n_b - 1) * var_b) / (n_a + n_b - 2))
    if pooled == 0:
        return 0.0
    return float((np.mean(a) - np.mean(b)) / pooled)


def two_sample_ttest_adoption(df: pd.DataFrame) -> TTestResult:
    """Welch two-sample t-test on feature_adoption_score by churn status."""
    retained = df.loc[df["churned"] == 0, "feature_adoption_score"].to_numpy(dtype=float)
    churned = df.loc[df["churned"] == 1, "feature_adoption_score"].to_numpy(dtype=float)
    t_stat, p_value = stats.ttest_ind(churned, retained, equal_var=False, alternative="two-sided")
    # Welch-Satterthwaite degrees of freedom
    s1, s2 = np.var(churned, ddof=1), np.var(retained, ddof=1)
    n1, n2 = len(churned), len(retained)
    df_welch = (s1 / n1 + s2 / n2) ** 2 / (
        (s1 / n1) ** 2 / (n1 - 1) + (s2 / n2) ** 2 / (n2 - 1)
    )
    return TTestResult(
        metric="feature_adoption_score ~ churned",
        group_a="churned",
        group_b="retained",
        n_a=int(n1),
        n_b=int(n2),
        mean_a=float(np.mean(churned)),
        mean_b=float(np.mean(retained)),
        t_statistic=float(t_stat),
        p_value=float(p_value),
        degrees_of_freedom=float(df_welch),
        cohens_d=_cohens_d(churned, retained),
        significant=bool(p_value < ALPHA),
    )


def chi_square_channel_churn(df: pd.DataFrame) -> ChiSquareResult:
    """Chi-square test of independence: churned vs. acquisition_channel."""
    observed = pd.crosstab(df["acquisition_channel"], df["churned"])
    chi2, p_value, dof, expected_arr = stats.chi2_contingency(observed)
    expected = pd.DataFrame(expected_arr, index=observed.index, columns=observed.columns)
    n = observed.to_numpy().sum()
    r, c = observed.shape
    cramers_v = float(np.sqrt(chi2 / (n * min(r - 1, c - 1)))) if n else 0.0
    return ChiSquareResult(
        metric="churned ⊥ acquisition_channel",
        chi2=float(chi2),
        p_value=float(p_value),
        degrees_of_freedom=int(dof),
        expected=expected.round(2),
        observed=observed,
        significant=bool(p_value < ALPHA),
        cramers_v=cramers_v,
    )


def run_hypothesis_tests(df: pd.DataFrame | None = None) -> dict[str, Any]:
    """Execute the curriculum hypothesis tests on the processed funnel table."""
    if df is None:
        if FULL_FUNNEL_FEATURES_PATH.exists():
            df = pd.read_csv(FULL_FUNNEL_FEATURES_PATH)
        else:
            df = run_pipeline()
    ttest = two_sample_ttest_adoption(df)
    chi = chi_square_channel_churn(df)
    return {"ttest": ttest, "chi_square": chi}


def _sig_label(p_value: float) -> str:
    if p_value < 0.001:
        return "highly significant (p < 0.001)"
    if p_value < 0.01:
        return "significant at α = 0.01"
    if p_value < 0.05:
        return "significant at α = 0.05"
    return "not statistically significant at α = 0.05"


def _effect_label(d: float) -> str:
    ad = abs(d)
    if ad < 0.2:
        return "negligible"
    if ad < 0.5:
        return "small"
    if ad < 0.8:
        return "medium"
    return "large"


def print_statistical_summary(results: dict[str, Any] | None = None) -> None:
    """Log executive interpretations of the inferential tests."""
    results = results or run_hypothesis_tests()
    ttest: TTestResult = results["ttest"]
    chi: ChiSquareResult = results["chi_square"]

    print("\n" + "=" * 78)
    print("SAAS RETENTION — INFERENTIAL STATISTICS SUMMARY")
    print("=" * 78)

    print("\n[1] Two-Sample Welch T-Test")
    print(f"    Hypothesis : H0 μ_adoption(churned) = μ_adoption(retained)")
    print(f"    Metric     : {ttest.metric}")
    print(f"    n          : churned={ttest.n_a:,} | retained={ttest.n_b:,}")
    print(f"    Means      : churned={ttest.mean_a:.3f} | retained={ttest.mean_b:.3f}")
    print(f"    t-statistic: {ttest.t_statistic:.4f}")
    print(f"    p-value    : {ttest.p_value:.6g}  →  {_sig_label(ttest.p_value)}")
    print(f"    d.f.       : {ttest.degrees_of_freedom:.2f} (Welch-Satterthwaite)")
    print(f"    Cohen's d  : {ttest.cohens_d:.3f} ({_effect_label(ttest.cohens_d)} effect)")
    direction = "lower" if ttest.mean_a < ttest.mean_b else "higher"
    print(
        f"    Insight    : Churned accounts exhibit {direction} product adoption "
        f"than retained accounts. A { _effect_label(ttest.cohens_d) } effect size "
        "supports treating onboarding / feature activation as a first-order "
        "retention lever — not a vanity product metric."
    )

    print("\n[2] Chi-Square Test of Independence")
    print("    Hypothesis : H0 churn status is independent of acquisition_channel")
    print(f"    χ²          : {chi.chi2:.4f}")
    print(f"    p-value    : {chi.p_value:.6g}  →  {_sig_label(chi.p_value)}")
    print(f"    d.f.       : {chi.degrees_of_freedom}")
    print(f"    Cramér's V : {chi.cramers_v:.3f}")
    print("    Observed churn counts by channel:")
    print(chi.observed.to_string().replace("\n", "\n    "))
    print("    Expected frequencies under independence:")
    print(chi.expected.to_string().replace("\n", "\n    "))
    if chi.significant:
        print(
            "    Insight    : Acquisition channel and churn are statistically "
            "dependent. Mix quality (Partner / Organic vs. outbound cold email) "
            "is a Front Door control that flows through to Back Door retention. "
            "Reallocate CAC toward channels with structurally lower churn."
        )
    else:
        print(
            "    Insight    : No detectable association between channel and churn "
            "at α = 0.05. Retention work should focus on in-product engagement "
            "rather than channel mix in this sample."
        )
    print("=" * 78 + "\n")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    print_statistical_summary()


if __name__ == "__main__":
    main()
