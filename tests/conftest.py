"""Pytest fixtures for the SaaS lifecycle engine."""

from __future__ import annotations

import os

os.environ.setdefault("DISABLE_RATE_LIMIT", "1")

import pytest

from src.data_pipeline import run_pipeline
from src.generate_data import generate_datasets
from src.paths import ACQUISITION_LEADS_PATH, N_CUSTOMERS, USER_TELEMETRY_PATH


@pytest.fixture(scope="session")
def raw_tables():
    """Generate the canonical 5,000-row extracts into the project data/raw folder."""
    leads, telemetry = generate_datasets(n=N_CUSTOMERS, seed=42)
    assert ACQUISITION_LEADS_PATH.exists()
    assert USER_TELEMETRY_PATH.exists()
    return leads, telemetry


@pytest.fixture(scope="session")
def processed_frame(raw_tables):
    return run_pipeline()
