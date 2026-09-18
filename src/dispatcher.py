"""Automated Slack + Resend action engine for high-risk accounts."""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from typing import Any, Optional

import pandas as pd
import requests
from sqlalchemy.orm import Session

from src.churn_model import load_or_train
from src.database import get_session_factory
from src.feature_builder import build_feature_row
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DispatchedAction,
    Organization,
    utcnow,
)
from src.outcome_tracker import record_intervention_outcome
from src.paths import (
    AT_RISK_THRESHOLD,
    DEFAULT_COOLDOWN_DAYS,
    ESCALATION_PROBABILITY,
    HITL_MRR_THRESHOLD,
    INTERVENTION_SUCCESS_RATE,
    MODEL_VERSION,
)
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


def _post_slack(payload: dict[str, Any], webhook_url: Optional[str] = None, org_id: Optional[str] = None) -> tuple[str, Optional[int]]:
    url = webhook_url or os.getenv("SLACK_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
    if not url:
        return "simulated", None
    last_error = None
    last_status = None
    for attempt in range(1, 4):
        try:
            response = requests.post(url, json=payload, timeout=8)
            last_status = int(response.status_code)
            if response.ok:
                return "delivered", last_status
            last_error = f"HTTP {last_status}"
        except requests.RequestException as exc:
            LOGGER.warning("Slack dispatch failed: %s", exc)
            last_error = str(exc)
    if org_id:
        from src.dead_letter import record_dead_letter

        record_dead_letter(org_id, "slack_dispatch", payload, last_error or "slack_failed", 3)
    return "failed", last_status


def _post_resend(email: dict[str, str], api_key: Optional[str] = None, org_id: Optional[str] = None) -> tuple[str, Optional[int]]:
    api_key = api_key or os.getenv("RESEND_API_KEY")
    body = {
        "from": email["from"],
        "to": [email["to"]],
        "subject": email["subject"],
        "html": email["html"],
    }
    if not api_key:
        return "simulated", None
    last_error = None
    last_status = None
    for _attempt in range(1, 4):
        try:
            response = requests.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=body,
                timeout=8,
            )
            last_status = int(response.status_code)
            if response.ok:
                return "delivered", last_status
            last_error = f"HTTP {last_status}"
        except requests.RequestException as exc:
            LOGGER.warning("Resend dispatch failed: %s", exc)
            last_error = str(exc)
    if org_id:
        from src.dead_letter import record_dead_letter

        record_dead_letter(org_id, "resend_dispatch", body, last_error or "resend_failed", 3)
    return "failed", last_status


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


def _org_cooldown_days(org: Optional[Organization], account: CustomerAccount) -> int:
    if org is not None and org.alert_cooldown_days:
        return int(org.alert_cooldown_days)
    return int(account.cooldown_days or DEFAULT_COOLDOWN_DAYS)


def _org_hitl_threshold(org: Optional[Organization]) -> float:
    if org is not None and org.hitl_mrr_threshold is not None:
        return float(org.hitl_mrr_threshold)
    return float(HITL_MRR_THRESHOLD)


def _in_cooldown(account: CustomerAccount, now, org: Optional[Organization] = None) -> bool:
    if account.suppressed_until and now < account.suppressed_until:
        return True
    last = account.last_contacted_at
    if not last:
        return False
    window = _org_cooldown_days(org, account)
    return (now - last) < timedelta(days=window)


def _fire_channels(
    session,
    account: CustomerAccount,
    scored: dict[str, Any],
    *,
    slack_url: Optional[str],
    resend_api_key: Optional[str] = None,
    cooldown_days: Optional[int] = None,
) -> list[DispatchedAction]:
    actions: list[DispatchedAction] = []
    slack_payload = _slack_blocks(account, scored)
    slack_status, slack_http = _post_slack(slack_payload, webhook_url=slack_url, org_id=account.org_id)
    slack_payload = dict(slack_payload)
    slack_payload["http_status"] = slack_http
    slack_payload["trigger_reason"] = scored["drivers"]
    slack_payload["mrr_saved_est"] = round(float(account.mrr) * 12.0 * INTERVENTION_SUCCESS_RATE, 2)
    slack_action = _record_action(session, account, "slack", slack_payload, slack_status)
    actions.append(slack_action)
    record_intervention_outcome(session, account, slack_action)

    email = _email_copy(account, scored)
    email_status, email_http = _post_resend(email, api_key=resend_api_key, org_id=account.org_id)
    email_payload = {
        **email,
        "http_status": email_http,
        "trigger_reason": scored["drivers"],
        "model_version": MODEL_VERSION,
        "mrr_saved_est": round(float(account.mrr) * 12.0 * INTERVENTION_SUCCESS_RATE, 2),
    }
    email_action = _record_action(session, account, "resend_email", email_payload, email_status)
    actions.append(email_action)
    record_intervention_outcome(session, account, email_action)
    now = utcnow()
    window = int(cooldown_days or account.cooldown_days or DEFAULT_COOLDOWN_DAYS)
    account.cooldown_days = window
    account.last_contacted_at = now
    account.suppressed_until = now + timedelta(days=window)
    if account.approval_status == "pending":
        account.approval_status = "approved"
    return actions


def evaluate_and_trigger_actions(
    account_id: str,
    org_id: str,
    session: Optional[Any] = None,
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

        org = session.get(Organization, org_id)
        cooldown_days = _org_cooldown_days(org, account)
        hitl_floor = _org_hitl_threshold(org)
        slack_url = (org.slack_webhook_url if org else None) or os.getenv("SLACK_WEBHOOK_URL") or os.getenv("WEBHOOK_URL")
        resend_key = (org.resend_api_key if org else None) or os.getenv("RESEND_API_KEY")
        if engine is None:
            engine, _ = load_or_train(tune=False, persist=True)
        scored = _score_account(session, account, engine)
        actions: list[DispatchedAction] = []
        probability = scored["probability"]
        triggered = probability >= AT_RISK_THRESHOLD or force
        now = utcnow()
        escalate = probability > ESCALATION_PROBABILITY
        cooldown = _in_cooldown(account, now, org=org) and not escalate and not force

        if triggered and cooldown:
            payload = {
                "trigger_reason": "cooldown_window",
                "churn_probability": probability,
                "risk_tier": scored["assessment"].risk_tier,
            }
            actions.append(_record_action(session, account, "system", payload, "suppressed_cooldown"))
            result_status = "suppressed_cooldown"
        elif triggered and float(account.mrr or 0) > hitl_floor and not force and not escalate:
            account.approval_status = "pending"
            payload = {
                "trigger_reason": scored["drivers"],
                "churn_probability": probability,
                "playbook": scored["playbook"],
                "queue": "Pending CSM Approval",
            }
            actions.append(_record_action(session, account, "approval_queue", payload, "pending_approval"))
            result_status = "pending_approval"
        elif triggered:
            actions.extend(
                _fire_channels(
                    session,
                    account,
                    scored,
                    slack_url=slack_url,
                    resend_api_key=resend_key,
                    cooldown_days=cooldown_days,
                )
            )
            result_status = "dispatched"
        else:
            result_status = "below_threshold"

        if owns_session:
            session.commit()
        else:
            session.flush()

        return {
            "triggered": triggered and result_status == "dispatched",
            "status": result_status,
            "probability": probability,
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


def approve_pending_dispatch(
    account_id: str,
    org_id: str,
    session: Optional[Any] = None,
    engine=None,
) -> dict[str, Any]:
    """Human-in-the-loop: release a pending high-MRR playbook."""
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
        org = session.get(Organization, org_id)
        if engine is None:
            engine, _ = load_or_train(tune=False, persist=True)
        scored = _score_account(session, account, engine)
        account.approval_status = "approved"
        actions = _fire_channels(
            session,
            account,
            scored,
            slack_url=(org.slack_webhook_url if org else None) or os.getenv("SLACK_WEBHOOK_URL"),
            resend_api_key=(org.resend_api_key if org else None) or os.getenv("RESEND_API_KEY"),
            cooldown_days=_org_cooldown_days(org, account),
        )
        from src.audit import record_audit

        record_audit(
            session,
            org_id,
            "csm",
            "hitl.approve",
            old_value={"approval_status": "pending", "account_id": account_id},
            new_value={"approval_status": "approved", "action_ids": [a.id for a in actions]},
        )
        if owns_session:
            session.commit()
        else:
            session.flush()
        return {"status": "approved", "action_ids": [a.id for a in actions], "probability": scored["probability"]}
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def dismiss_false_positive(
    account_id: str,
    org_id: str,
    session: Optional[Any] = None,
) -> dict[str, Any]:
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
        account.approval_status = "dismissed"
        now = utcnow()
        org = session.get(Organization, org_id)
        window = _org_cooldown_days(org, account)
        account.suppressed_until = now + timedelta(days=window)
        action = _record_action(
            session,
            account,
            "approval_queue",
            {"trigger_reason": "dismissed_false_positive"},
            "dismissed",
        )
        from src.audit import record_audit

        record_audit(
            session,
            org_id,
            "csm",
            "hitl.dismiss",
            old_value={"approval_status": "pending", "account_id": account_id},
            new_value={"approval_status": "dismissed", "action_id": action.id},
        )
        if owns_session:
            session.commit()
        else:
            session.flush()
        return {"status": "dismissed", "action_id": action.id}
    except Exception:
        if owns_session:
            session.rollback()
        raise
    finally:
        if owns_session:
            session.close()
