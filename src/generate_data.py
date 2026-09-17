"""Synthetic B2B SaaS customer generator (Front Door + Back Door).

Creates 5,000 realistic customer profiles split across:
- data/raw/acquisition_leads.csv    (Front Door / acquisition economics)
- data/raw/user_telemetry_churn.csv (Back Door / product engagement & churn)

Churn labels are drawn from a logistic probability that rises with inactivity,
support load, monthly contracts, and lower-quality acquisition channels.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import pandas as pd

from src.paths import (
    ACQUISITION_CHANNELS,
    ACQUISITION_LEADS_PATH,
    CUSTOMER_ID_START,
    N_CUSTOMERS,
    RANDOM_SEED,
    RAW_DIR,
    USER_TELEMETRY_PATH,
)

LOGGER = logging.getLogger(__name__)

CHANNEL_PROBS = np.array([0.30, 0.28, 0.25, 0.17])

CHANNEL_PROFILE = {
    "Outbound Cold Email": {
        "base_cac": 1550.0,
        "cac_noise": 280.0,
        "touch_mu": 7.5,
        "mrr_mu": 5.9,
        "mrr_sigma": 0.45,
        "login_mu": 3.2,
        "adoption_mu": 4.1,
        "churn_offset": 0.55,
    },
    "Inbound Organic": {
        "base_cac": 210.0,
        "cac_noise": 70.0,
        "touch_mu": 2.8,
        "mrr_mu": 6.1,
        "mrr_sigma": 0.40,
        "login_mu": 6.4,
        "adoption_mu": 6.8,
        "churn_offset": -0.35,
    },
    "Paid Search": {
        "base_cac": 820.0,
        "cac_noise": 160.0,
        "touch_mu": 4.6,
        "mrr_mu": 6.0,
        "mrr_sigma": 0.42,
        "login_mu": 4.8,
        "adoption_mu": 5.4,
        "churn_offset": 0.18,
    },
    "Partner Referral": {
        "base_cac": 460.0,
        "cac_noise": 90.0,
        "touch_mu": 3.4,
        "mrr_mu": 6.35,
        "mrr_sigma": 0.38,
        "login_mu": 7.1,
        "adoption_mu": 7.4,
        "churn_offset": -0.50,
    },
}


def _sigmoid(z: np.ndarray) -> np.ndarray:
    z = np.clip(z, -20.0, 20.0)
    return 1.0 / (1.0 + np.exp(-z))


def _customer_ids(n: int, start: int = CUSTOMER_ID_START) -> np.ndarray:
    return np.array([f"CUST_{start + i}" for i in range(n)])


def generate_acquisition_leads(
    n: int = N_CUSTOMERS,
    seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """Simulate Front Door acquisition records."""
    rng = np.random.default_rng(seed)
    channels = rng.choice(ACQUISITION_CHANNELS, size=n, p=CHANNEL_PROBS)

    sales_touchpoints = np.empty(n, dtype=int)
    cac_usd = np.empty(n, dtype=float)
    mrr = np.empty(n, dtype=float)
    contract_type = np.empty(n, dtype=object)

    for channel, profile in CHANNEL_PROFILE.items():
        mask = channels == channel
        k = int(mask.sum())
        if k == 0:
            continue
        touches = rng.poisson(profile["touch_mu"], size=k) + 1
        sales_touchpoints[mask] = np.clip(touches, 1, 16)
        cac = rng.normal(profile["base_cac"], profile["cac_noise"], size=k)
        cac *= 1.0 + 0.08 * (sales_touchpoints[mask] - profile["touch_mu"])
        cac_usd[mask] = np.clip(cac, 40.0, 4500.0)
        mrr[mask] = np.clip(
            rng.lognormal(profile["mrr_mu"], profile["mrr_sigma"], size=k),
            49.0,
            8500.0,
        )
        annual_p = 0.72 if channel in {"Inbound Organic", "Partner Referral"} else 0.48
        contract_type[mask] = rng.choice(
            ["Annual", "Monthly"], size=k, p=[annual_p, 1.0 - annual_p]
        )

    return pd.DataFrame(
        {
            "customer_id": _customer_ids(n),
            "acquisition_channel": channels,
            "sales_touchpoints": sales_touchpoints,
            "cac_usd": np.round(cac_usd, 2),
            "monthly_recurring_revenue": np.round(mrr, 2),
            "contract_type": contract_type,
        }
    )


def generate_user_telemetry(
    leads: pd.DataFrame,
    seed: int = RANDOM_SEED,
) -> pd.DataFrame:
    """Simulate Back Door product telemetry and logistic churn labels."""
    rng = np.random.default_rng(seed + 7)
    n = len(leads)
    avg_weekly_logins = np.empty(n, dtype=float)
    feature_adoption_score = np.empty(n, dtype=float)
    support_tickets_raised = np.empty(n, dtype=int)
    days_since_last_login = np.empty(n, dtype=int)

    for channel, profile in CHANNEL_PROFILE.items():
        mask = leads["acquisition_channel"].to_numpy() == channel
        k = int(mask.sum())
        if k == 0:
            continue
        avg_weekly_logins[mask] = np.clip(
            rng.normal(profile["login_mu"], 1.8, size=k), 0.0, 18.0
        )
        feature_adoption_score[mask] = np.clip(
            rng.normal(profile["adoption_mu"], 1.6, size=k), 0.0, 10.0
        )
        support_tickets_raised[mask] = rng.poisson(1.6, size=k)
        days_since_last_login[mask] = rng.gamma(shape=1.7, scale=6.5, size=k).astype(int)

    monthly = (leads["contract_type"].to_numpy() == "Monthly").astype(float)
    channel_offset = leads["acquisition_channel"].map(
        lambda c: CHANNEL_PROFILE[c]["churn_offset"]
    ).to_numpy(dtype=float)

    # Additional engagement drop-off for a subset of monthly outbound accounts.
    inactivity_shock = rng.binomial(1, 0.12, size=n)
    days_since_last_login = np.clip(
        days_since_last_login + inactivity_shock * rng.integers(18, 55, size=n),
        0,
        120,
    )
    avg_weekly_logins = np.clip(
        avg_weekly_logins - inactivity_shock * rng.uniform(1.5, 3.5, size=n),
        0.0,
        18.0,
    )
    feature_adoption_score = np.clip(
        feature_adoption_score - inactivity_shock * rng.uniform(0.8, 2.2, size=n),
        0.0,
        10.0,
    )

    logit = (
        -1.55
        - 0.24 * avg_weekly_logins
        - 0.32 * feature_adoption_score
        + 0.055 * days_since_last_login
        + 0.18 * support_tickets_raised
        + 0.95 * monthly
        + channel_offset
    )
    churn_prob = _sigmoid(logit)
    churned = rng.binomial(1, churn_prob)

    return pd.DataFrame(
        {
            "customer_id": leads["customer_id"].to_numpy(),
            "avg_weekly_logins": np.round(avg_weekly_logins, 2),
            "feature_adoption_score": np.round(feature_adoption_score, 2),
            "support_tickets_raised": support_tickets_raised,
            "days_since_last_login": days_since_last_login,
            "churned": churned.astype(int),
        }
    )


def generate_datasets(
    n: int = N_CUSTOMERS,
    seed: int = RANDOM_SEED,
    output_dir: Path | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Generate and persist both raw SaaS tables."""
    output_dir = Path(output_dir) if output_dir is not None else RAW_DIR
    output_dir.mkdir(parents=True, exist_ok=True)

    leads = generate_acquisition_leads(n=n, seed=seed)
    telemetry = generate_user_telemetry(leads, seed=seed)

    leads_path = output_dir / ACQUISITION_LEADS_PATH.name
    telemetry_path = output_dir / USER_TELEMETRY_PATH.name
    leads.to_csv(leads_path, index=False)
    telemetry.to_csv(telemetry_path, index=False)

    churn_rate = float(telemetry["churned"].mean())
    LOGGER.info("Wrote %s (%s rows)", leads_path, f"{len(leads):,}")
    LOGGER.info("Wrote %s (%s rows)", telemetry_path, f"{len(telemetry):,}")
    LOGGER.info("Simulated churn rate: %.1f%%", 100.0 * churn_rate)
    return leads, telemetry


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic B2B SaaS acquisition and telemetry CSVs."
    )
    parser.add_argument("--n", type=int, default=N_CUSTOMERS, help="Number of customers")
    parser.add_argument("--seed", type=int, default=RANDOM_SEED, help="RNG seed")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=RAW_DIR,
        help="Directory for raw CSV output",
    )
    return parser.parse_args()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    args = parse_args()
    generate_datasets(n=args.n, seed=args.seed, output_dir=args.output_dir)


if __name__ == "__main__":
    main()
