"""Automated Slack + Resend action engine for high-risk accounts."""

from __future__ import annotations

import logging
import os
from typing import Any

import pandas as pd
import requests
from sqlalchemy.orm import Session

from src.churn_model import load_or_train
from src.database import get_session_factory
from src.feature_builder import build_feature_row
from src.models_db import ChurnAssessment, CustomerAccount, DispatchedAction, utcnow
from src.paths import AT_RISK_THRESHOLD, INTERVENTION_SUCCESS_RATE, MODEL_VERSION
from src.scoring import assign_risk_tier, recommend_playbook, risk_drivers

LOGGER = logging.getLogger(__name__)
RESEND_URL = "https://api.resend.com/emails"


class AccountNotFoundError(ValueError):
    pass


def _score_account(session: Session, account: CustomerAccount, engine) -> dict[str, Any]:
    row = build_feature_row(session, account)
    frame = pd.DataFrame([row])
    probability = float(engine.predict_proba(frame)[0])
    playbook = recommend_playbook(row)
    drivers = risk_drivers(row)
    arr_at_risk = round(float(account.mrr) * 12.0, 2)
    assessment = ChurnAssessment(
        org_id=account.org_id,
        customer_account_id=account.id,
        churn_probability=round(probability, 6),
        risk_tier=assign_risk_tier(probability),
        arr_at_risk=arr_at_risk,
        recommended_action=playbook,
        assessed_at=utcnow(),
    )
    session.add(assessment)
    session.flush()
    return {
        "assessment": assessment,
        "row": row,
        "probability": probability,
        "playbook": playbook,
        "drivers": drivers,
        "arr_at_risk": arr_at_risk,
    }


def _slack_blocks(account: CustomerAccount, scored: dict[str, Any]) -> dict[str, Any]:
    days = int(scored["row"]["days_since_last_login"])
    return {
        "text": f"Critical churn risk: {account.customer_external_id} ({scored['probability']:.0%})",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "Rev Lifecycle · High-risk account", "emoji": True},
            },
            {
                "type": "section",
                "fields": [
                    {"type": "mrkdwn", "text": f"*Account*\n`{account.customer_external_id}`"},
                    {"type": "mrkdwn", "text": f"*MRR*\n${account.mrr:,.0f}"},
                    {"type": "mrkdwn", "text": f"*Churn score*\n{scored['probability']:.1%}"},
                    {"type": "mrkdwn", "text": f"*Days inactive*\n{days}"},
                    {"type": "mrkdwn", "text": f"*ARR at risk*\n${scored['arr_at_risk']:,.0f}"},
                    {"type": "mrkdwn", "text": f"*Channel*\n{account.channel}"},
                ],
            },
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Drivers*\n{scored['drivers']}"}},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Trigger Retainer Playbook", "emoji": True},
                        "style": "primary",
                        "url": "https://rev-lifecycle.local/playbooks/retain",
                        "action_id": "trigger_retainer_playbook",
                    }
                ],
            },
        ],
    }


def _email_copy(account: CustomerAccount, scored: dict[str, Any]) -> dict[str, str]:
    days = int(scored["row"]["days_since_last_login"])
    adoption = float(scored["row"]["feature_adoption_score"])
    if days > 21:
        hook = (
            f"It's been {days} days since anyone on this workspace signed in. "
            "The accounts that recover do it in the next 48 hours — usually with one guided session."
        )
    elif adoption < 4:
        hook = (
            "Your team activated the workspace but has not reached the core workflow yet. "
            "We reserved a 20-minute onboarding reboot on us."
        )
    else:
        hook = (
            "Usage dipped below the health threshold we watch for logos of this size. "
            "A success manager can restore the previous baseline this week."
        )
    html = f"""
    <p>Hi there,</p>
    <p>{hook}</p>
    <p>Playbook: <strong>{scored['playbook']}</strong></p>
    <p>This note is tied to workspace <code>{account.customer_external_id}</code>
    (${account.mrr:,.0f} MRR · {account.contract_type}).</p>
    <p>— Customer Success, powered by Rev Lifecycle Engine</p>
    """
    return {
        "subject": f"Quick save plan for {account.customer_external_id}",
        "html": html,
        "from": os.getenv("RESEND_FROM", "Rev Lifecycle <noreply@revlifecycle.dev>"),
        "to": (scored["row"].get("email") if hasattr(scored["row"], "get") else None)
        or f"success+{account.customer_external_id}@example.com",
    }


def _post_slack(payload: dict[str, Any]) -> tuple[str, int | None]:
    url = os.getenv("SLACK_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
    if not url:
        return "simulated", None
    try:
        response = requests.post(url, json=payload, timeout=8)
        return ("delivered" if response.ok else "failed"), int(response.status_code)
    except requests.RequestException as exc:
        LOGGER.warning("Slack dispatch failed: %s", exc)
        return "failed", None


def _post_resend(email: dict[str, str]) -> tuple[str, int | None]:
    api_key = os.getenv("RESEND_API_KEY")
    body = {
        "from": email["from"],
        "to": [email["to"]],
        "subject": email["subject"],
        "html": email["html"],
    }
    if not api_key:
        return "simulated", None
    try:
        response = requests.post(
            RESEND_URL,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json=body,
            timeout=8,
        )
        return ("delivered" if response.ok else "failed"), int(response.status_code)
    except requests.RequestException as exc:
        LOGGER.warning("Resend dispatch failed: %s", exc)
        return "failed", None


def _record_action(
    session: Session,
    account: CustomerAccount,
    channel: str,
    payload: dict[str, Any],
    status: str,
) -> DispatchedAction:
    action = DispatchedAction(
        org_id=account.org_id,
        customer_account_id=account.id,
        channel=channel,
        payload=payload,
        status=status,
        created_at=utcnow(),
    )
    session.add(action)
    return action


def evaluate_and_trigger_actions(
    account_id: str,
    org_id: str,
    session: Session | None = None,
    engine=None,
    force: bool = False,
) -> dict[str, Any]:
    """Score one tenant account and fire Slack + Resend when p >= 0.65."""
    owns_session = session is None
    if session is None:
        session = get_session_factory()()
    try:
        account = (
            session.query(CustomerAccount)
            .filter(CustomerAccount.id == account_id, CustomerAccount.org_id == org_id)
            .one_or_none()
        )
        if account is None:
            raise AccountNotFoundError(f"Account {account_id} not found for org {org_id}")

        if engine is None:
            engine, _ = load_or_train(tune=False, persist=True)
        scored = _score_account(session, account, engine)
        actions: list[DispatchedAction] = []
        triggered = scored["probability"] >= AT_RISK_THRESHOLD or force

        if triggered:
            slack_payload = _slack_blocks(account, scored)
            slack_status, slack_http = _post_slack(slack_payload)
            slack_payload = dict(slack_payload)
            slack_payload["http_status"] = slack_http
            slack_payload["trigger_reason"] = scored["drivers"]
            slack_payload["mrr_saved_est"] = round(
                float(account.mrr) * 12.0 * INTERVENTION_SUCCESS_RATE, 2
            )
            actions.append(_record_action(session, account, "slack", slack_payload, slack_status))

            email = _email_copy(account, scored)
            email_status, email_http = _post_resend(email)
            email_payload = {
                **email,
                "http_status": email_http,
                "trigger_reason": scored["drivers"],
                "model_version": MODEL_VERSION,
                "mrr_saved_est": round(float(account.mrr) * 12.0 * INTERVENTION_SUCCESS_RATE, 2),
            }
            actions.append(_record_action(session, account, "resend_email", email_payload, email_status))

        if owns_session:
            session.commit()
        else:
            session.flush()

        return {
            "triggered": triggered,
            "probability": scored["probability"],
            "risk_tier": scored["assessment"].risk_tier,
            "assessment_id": scored["assessment"].id,
            "action_ids": [a.id for a in actions],
            "recommended_action": scored["playbook"],
        }
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
