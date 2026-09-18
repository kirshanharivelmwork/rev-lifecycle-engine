"""Heuristic Front Door conversion scoring (0–100)."""

from __future__ import annotations

from typing import Any, Iterable, Optional

HIGH_INTENT_THRESHOLD = 80

SIGNAL_POINTS = {
    "recently raised funding": 20,
    "raised funding": 20,
    "series a": 18,
    "series b": 18,
    "hiring vp of sales": 15,
    "hiring vp sales": 15,
    "hiring cro": 15,
    "hiring revops": 12,
    "expanding sales team": 12,
    "tech stack: hubspot": 10,
    "tech stack: salesforce": 10,
    "intent: churn": 14,
    "intent: retention": 14,
    "job change": 8,
    "visited pricing": 10,
}


def _normalize(signal: str) -> str:
    return " ".join(str(signal).strip().lower().split())


def score_intent_signals(signals: Optional[Iterable[Any]]) -> float:
    """Map intent labels to a 0–100 conversion score."""
    if not signals:
        return 0.0
    score = 0
    seen = set()
    for raw in signals:
        key = _normalize(raw)
        if not key or key in seen:
            continue
        seen.add(key)
        if key in SIGNAL_POINTS:
            score += SIGNAL_POINTS[key]
            continue
        for needle, pts in SIGNAL_POINTS.items():
            if needle in key:
                score += pts
                break
    return float(min(100, max(0, score)))


def is_high_intent(score: float) -> bool:
    return float(score) > HIGH_INTENT_THRESHOLD


def apply_lead_score(lead: Any) -> float:
    """Persist a 0–100 conversion score on a ProspectLead-like object."""
    from src.models_db import utcnow

    lead.conversion_score = score_intent_signals(lead.intent_signals)
    if hasattr(lead, "updated_at"):
        lead.updated_at = utcnow()
    return float(lead.conversion_score)
