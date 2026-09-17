"""Canonical project paths used by every module."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
MODELS_DIR = PROJECT_ROOT / "models"

ACQUISITION_LEADS_PATH = RAW_DIR / "acquisition_leads.csv"
USER_TELEMETRY_PATH = RAW_DIR / "user_telemetry_churn.csv"
FULL_FUNNEL_FEATURES_PATH = PROCESSED_DIR / "full_funnel_features.csv"
CHURN_ALERTS_PATH = PROCESSED_DIR / "churn_risk_alerts.csv"

N_CUSTOMERS = 5000
CUSTOMER_ID_START = 10_000
RANDOM_SEED = 42

ACQUISITION_CHANNELS = (
    "Outbound Cold Email",
    "Inbound Organic",
    "Paid Search",
    "Partner Referral",
)
CONTRACT_TYPES = ("Monthly", "Annual")
