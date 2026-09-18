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
DISPATCH_LOG_PATH = PROCESSED_DIR / "dispatched_alerts_log.json"
ENGINE_BUNDLE_PATH = MODELS_DIR / "churn_engine.pkl"

MODEL_VERSION = "1.0.0"
AT_RISK_THRESHOLD = 0.65
CRITICAL_THRESHOLD = 0.70
MEDIUM_THRESHOLD = 0.40
INTERVENTION_SUCCESS_RATE = 0.35

SQLITE_DB_PATH = DATA_DIR / "rev_lifecycle.db"
DEFAULT_DATABASE_URL = f"sqlite:///{SQLITE_DB_PATH}"

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
