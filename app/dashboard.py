"""Executive Revenue Command Center — multi-tenant commercial dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy.orm import Session

from src.audit import update_tenant_policy
from src.database import get_session_factory, init_db
from src.dispatcher import approve_pending_dispatch, dismiss_false_positive
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DispatchedAction,
    Organization,
    OutboundCampaign,
    ProspectLead,
    SystemAuditLog,
    utcnow,
)
from src.outcome_tracker import audit_intervention_outcomes
from src.paths import AT_RISK_THRESHOLD, HITL_MRR_THRESHOLD, INTERVENTION_SUCCESS_RATE, MODEL_VERSION
from src.seed_commercial_demo import seed_commercial_demo

st.set_page_config(
    page_title="Rev Lifecycle Engine",
    page_icon="◈",
    layout="wide",
    initial_sidebar_state="expanded",
)

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="#F4F6F8",
    font=dict(color="#1F2937", family="IBM Plex Sans, sans-serif"),
    margin=dict(l=40, r=20, t=50, b=40),
    legend=dict(bgcolor="rgba(0,0,0,0)"),
)


def _inject_css() -> None:
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&display=swap');
        html, body, [class*="css"] { font-family: "IBM Plex Sans", sans-serif; }
        .stApp {
            background: #FFFFFF;
            color: #1F2937;
        }
        .block-container { padding-top: 1.2rem; max-width: 1400px; }
        h1, h2, h3 { letter-spacing: -0.03em; color: #1F2937; }
        .hero-kicker {
            font-size: 0.78rem; font-weight: 600; letter-spacing: 0.16em;
            text-transform: uppercase; color: #556B2F; margin-bottom: 0.2rem;
        }
        .hero-title { font-size: 2.05rem; font-weight: 700; margin: 0 0 0.35rem 0; color: #1F2937; }
        .hero-sub { color: #4B5563; font-size: 0.98rem; margin-bottom: 1.2rem; }
        div[data-testid="stMetric"] {
            background: #FFFFFF;
            border: 1px solid #E5E7EB;
            border-radius: 16px; padding: 14px 16px;
            box-shadow: 0 1px 2px rgba(31, 41, 55, 0.06);
        }
        div[data-testid="stMetric"] label { color: #4B5563 !important; }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: #1F2937 !important; font-weight: 700;
        }
        div[data-testid="stMetric"] [data-testid="stMetricDelta"] { color: #556B2F !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _fmt_money(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value/1_000:.1f}K"
    return f"${value:,.0f}"


def _session() -> Session:
    init_db()
    return get_session_factory()()


def _latest_assessments(session: Session, org_id: str) -> pd.DataFrame:
    accounts = session.query(CustomerAccount).filter(CustomerAccount.org_id == org_id).all()
    rows = []
    for account in accounts:
        latest = (
            session.query(ChurnAssessment)
            .filter(
                ChurnAssessment.org_id == org_id,
                ChurnAssessment.customer_account_id == account.id,
            )
            .order_by(ChurnAssessment.assessed_at.desc())
            .first()
        )
        rows.append(
            {
                "account_id": account.id,
                "customer_external_id": account.customer_external_id,
                "channel": account.channel,
                "mrr": account.mrr,
                "arr": account.mrr * 12.0,
                "contract_type": account.contract_type,
                "churn_probability": latest.churn_probability if latest else 0.0,
                "risk_tier": latest.risk_tier if latest else "Low",
                "recommended_action": latest.recommended_action if latest else "",
            }
        )
    return pd.DataFrame(rows)


def _month_actions(session: Session, org_id: str) -> list[DispatchedAction]:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return (
        session.query(DispatchedAction)
        .filter(DispatchedAction.org_id == org_id, DispatchedAction.created_at >= start)
        .order_by(DispatchedAction.created_at.desc())
        .all()
    )


def main() -> None:
    _inject_css()
    session = _session()
    try:
        orgs = session.query(Organization).order_by(Organization.name).all()
        if not orgs:
            st.markdown('<div class="hero-kicker">Rev Lifecycle Engine</div>', unsafe_allow_html=True)
            st.markdown('<p class="hero-title">Executive Revenue Command Center</p>', unsafe_allow_html=True)
            st.warning("No tenants in the database yet.")
            if st.button("Seed Acme SaaS demo organization"):
                seed_commercial_demo()
                st.rerun()
            st.caption("Or run `python3 -m src.seed_commercial_demo` from the repo root.")
            return

        names = {org.name: org for org in orgs}
        default_ix = 0
        if "Acme SaaS" in names:
            default_ix = list(names).index("Acme SaaS")
        selected_name = st.sidebar.selectbox("Organization", list(names), index=default_ix)
        org = names[selected_name]
        st.sidebar.caption(f"`{org.org_id}` · {org.plan_tier} plan")
        st.sidebar.caption("Configure Stripe, Slack, Resend, Apollo, Instantly, and HITL in Tenant Settings.")
        st.sidebar.divider()

        book = _latest_assessments(session, org.org_id)
        actions = _month_actions(session, org.org_id)
        n_subs = int(len(book))
        active_arr = float(book["arr"].sum()) if not book.empty else 0.0
        at_risk = book.loc[book["churn_probability"] >= AT_RISK_THRESHOLD] if not book.empty else book
        at_risk_arr = float(at_risk["arr"].sum()) if not at_risk.empty else 0.0
        audit = audit_intervention_outcomes(org.org_id, session=session)
        verified_arr = float(audit.get("verified_arr_saved") or 0.0)
        hitl_floor = float(org.hitl_mrr_threshold or HITL_MRR_THRESHOLD)
        cooldown_default = int(org.alert_cooldown_days or 14)
        dispatched_count = len(actions)

        st.markdown(
            '<div class="hero-kicker">Commercial · Multi-tenant revenue intelligence</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<p class="hero-title">Executive Revenue Command Center</p>', unsafe_allow_html=True)
        st.markdown(
            f'<p class="hero-sub">{selected_name} · {n_subs:,} monitored subscribers · '
            f"model {MODEL_VERSION} · HITL threshold ${hitl_floor:,.0f} · cooldown {cooldown_default}d</p>",
            unsafe_allow_html=True,
        )

        command, acquisition, staging, settings = st.tabs(
            [
                "Command Center",
                "Front Door: Acquisition",
                "Staging & Approval Queue",
                "Tenant Settings & Integrations",
            ]
        )

        with command:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Monitored subscribers & active ARR", _fmt_money(active_arr), f"{n_subs:,} accounts")
            c2.metric("Prospective ARR at risk", _fmt_money(at_risk_arr))
            c3.metric(
                "Verified ARR saved",
                _fmt_money(verified_arr),
                help="Empirical InterventionOutcome attribution (30/60/90-day audits)",
            )
            c4.metric("Dispatched interventions (this month)", f"{dispatched_count:,}")

            left, right = st.columns((1.15, 1))
            with left:
                st.markdown("### Live action stream")
                stream_rows = []
                for action in actions[:40]:
                    account = session.get(CustomerAccount, action.customer_account_id)
                    payload = action.payload or {}
                    stream_rows.append(
                        {
                            "when": action.created_at,
                            "customer_id": account.customer_external_id if account else action.customer_account_id,
                            "channel": action.channel,
                            "status": action.status,
                            "mrr_saved": payload.get("mrr_saved_est", (account.mrr * 12 * INTERVENTION_SUCCESS_RATE) if account else 0),
                            "trigger_reason": payload.get("trigger_reason", ""),
                        }
                    )
                stream = pd.DataFrame(stream_rows)
                if stream.empty:
                    st.info("No dispatched actions this month for this tenant.")
                else:
                    st.dataframe(
                        stream,
                        width="stretch",
                        hide_index=True,
                        column_config={
                            "when": st.column_config.DatetimeColumn("Execution", format="YYYY-MM-DD HH:mm"),
                            "mrr_saved": st.column_config.NumberColumn("Est. ARR saved", format="$%.0f"),
                        },
                    )
            with right:
                if not book.empty:
                    fig = px.histogram(
                        book,
                        x="churn_probability",
                        color="risk_tier",
                        nbins=20,
                        color_discrete_map={"Low": "#556B2F", "Medium": "#CA8A04", "Critical": "#B91C1C"},
                        title="Book risk mix",
                    )
                    fig.update_layout(**PLOTLY_LAYOUT, bargap=0.08)
                    st.plotly_chart(fig, width="stretch")

            st.markdown("### Interactive ROI calculator")
            p1, p2, p3 = st.columns(3)
            with p1:
                seat_price = st.number_input("Your subscription price (USD / month)", min_value=0, value=2500, step=100)
            with p2:
                save_rate = st.slider("CS intervention save rate", 0.05, 0.80, float(INTERVENTION_SUCCESS_RATE), 0.01)
            with p3:
                annual_saved = at_risk_arr * save_rate
                platform_cost = seat_price * 12
                net = annual_saved - platform_cost
                st.metric("Projected annual revenue saved", _fmt_money(annual_saved))
                st.metric("Net vs. platform cost", _fmt_money(net), f"{(annual_saved / platform_cost):.1f}× if cost>0" if platform_cost else None)

            st.markdown("### At-risk book")
            if book.empty:
                st.info("No customer accounts for this organization.")
            else:
                flagged = book.sort_values("churn_probability", ascending=False)
                st.dataframe(
                    flagged.head(40),
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "churn_probability": st.column_config.NumberColumn("Churn p", format="%.1%"),
                        "mrr": st.column_config.NumberColumn("MRR", format="$%.0f"),
                        "arr": st.column_config.NumberColumn("ARR", format="$%.0f"),
                    },
                )

        with acquisition:
            st.markdown("### Front Door: Acquisition")
            st.caption(
                "Apollo ICP ingest, heuristic conversion scoring, and Instantly sequence sync — "
                "isolated per tenant with the same RLS and DLQ path as retention jobs."
            )
            today_start = utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
            prospects = (
                session.query(ProspectLead)
                .filter(ProspectLead.org_id == org.org_id)
                .order_by(ProspectLead.conversion_score.desc(), ProspectLead.created_at.desc())
                .all()
            )
            sourced_today = [p for p in prospects if p.created_at and p.created_at >= today_start]
            high_intent = [p for p in prospects if (p.conversion_score or 0) > 80]
            dispatched_emails = (
                session.query(OutboundCampaign)
                .filter(OutboundCampaign.org_id == org.org_id)
                .count()
            )
            a1, a2, a3 = st.columns(3)
            a1.metric("New Leads Sourced", f"{len(sourced_today):,}", f"{len(prospects):,} in book")
            a2.metric("High-Intent Prospects", f"{len(high_intent):,}", "score > 80")
            a3.metric("Cold Emails Dispatched", f"{int(dispatched_emails):,}")
            ranked = sourced_today or prospects
            table_rows = []
            for lead in ranked[:40]:
                latest_campaign = (
                    session.query(OutboundCampaign)
                    .filter(
                        OutboundCampaign.org_id == org.org_id,
                        OutboundCampaign.prospect_lead_id == lead.id,
                    )
                    .order_by(OutboundCampaign.created_at.desc())
                    .first()
                )
                table_rows.append(
                    {
                        "company": lead.company_name,
                        "decision_maker": lead.decision_maker_name,
                        "email": lead.email,
                        "conversion_score": lead.conversion_score,
                        "status": lead.status,
                        "sync_status": latest_campaign.status if latest_campaign else "not_synced",
                        "campaign_id": latest_campaign.campaign_id if latest_campaign else "",
                    }
                )
            frame = pd.DataFrame(table_rows)
            st.markdown("### Today's highest-scored prospects")
            if frame.empty:
                st.info("No Front Door prospects for this tenant yet. Save Apollo credentials and run the daily outbound engine.")
            else:
                st.dataframe(
                    frame,
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "conversion_score": st.column_config.NumberColumn("Conversion score", format="%.0f"),
                    },
                )

        with staging:
            st.markdown("### Pending CSM approval")
            st.caption(
                f"Accounts with MRR > ${hitl_floor:,.0f} wait in this queue before Slack/Resend fire. "
                "Verified ARR saved is shown next to prospective at-risk ARR."
            )
            v1, v2 = st.columns(2)
            v1.metric("Verified ARR saved (empirical)", _fmt_money(verified_arr))
            v2.metric("Prospective pipeline at risk", _fmt_money(at_risk_arr))
            pending = (
                session.query(CustomerAccount)
                .filter(CustomerAccount.org_id == org.org_id, CustomerAccount.approval_status == "pending")
                .all()
            )
            if not pending:
                st.success("No playbooks waiting on CSM approval.")
            for account in pending:
                latest = (
                    session.query(ChurnAssessment)
                    .filter(ChurnAssessment.customer_account_id == account.id)
                    .order_by(ChurnAssessment.assessed_at.desc())
                    .first()
                )
                cols = st.columns((3, 1, 1))
                with cols[0]:
                    st.write(
                        f"**{account.customer_external_id}** · ${account.mrr:,.0f} MRR · "
                        f"{(latest.risk_tier if latest else 'n/a')} · "
                        f"{(latest.recommended_action if latest else 'retention playbook')}"
                    )
                with cols[1]:
                    if st.button("Approve Dispatch", key=f"approve_{account.id}"):
                        approve_pending_dispatch(account.id, org.org_id, session=session)
                        session.commit()
                        st.rerun()
                with cols[2]:
                    if st.button("Dismiss / False Positive", key=f"dismiss_{account.id}"):
                        dismiss_false_positive(account.id, org.org_id, session=session)
                        session.commit()
                        st.rerun()

        with settings:
            st.markdown("### Tenant Settings & Integrations")
            st.caption(
                "Secrets are stored on this organization row and never written back into the form. "
                "Leave a password field blank to keep the current value."
            )
            configured = []
            if org.stripe_webhook_secret:
                configured.append("Stripe signing secret")
            if org.slack_webhook_url:
                configured.append("Slack webhook")
            if org.resend_api_key:
                configured.append("Resend API key")
            if org.hubspot_access_token:
                configured.append("HubSpot")
            if org.salesforce_access_token:
                configured.append("Salesforce")
            if org.apollo_api_key:
                configured.append("Apollo")
            if org.instantly_api_key:
                configured.append("Instantly")
            if configured:
                st.success("Configured: " + " · ".join(configured))
            else:
                st.info("No integration secrets saved for this tenant yet.")

            stripe_secret = st.text_input(
                "Stripe webhook signing secret (`STRIPE_WEBHOOK_SECRET`)",
                value="",
                type="password",
                help="whsec_… from the Stripe Dashboard. Blank keeps the stored secret.",
            )
            slack_url = st.text_input(
                "Slack incoming webhook URL (`SLACK_WEBHOOK_URL`)",
                value="",
                type="password",
                help="https://hooks.slack.com/services/… Blank keeps the stored URL.",
            )
            resend_key = st.text_input(
                "Resend API key (`RESEND_API_KEY`)",
                value="",
                type="password",
                help="re_… key used for retention emails. Blank keeps the stored key.",
            )
            hubspot_token = st.text_input(
                "HubSpot access token",
                value="",
                type="password",
                help="Private app token. Blank keeps the stored token.",
            )
            salesforce_token = st.text_input(
                "Salesforce access token",
                value="",
                type="password",
                help="OAuth access token. Blank keeps the stored token.",
            )
            salesforce_instance = st.text_input(
                "Salesforce instance URL",
                value=org.salesforce_instance_url or "",
                help="e.g. https://yourorg.my.salesforce.com",
            )
            apollo_key = st.text_input(
                "Apollo API key",
                value="",
                type="password",
                help="Masked. Blank keeps the stored Apollo key.",
            )
            instantly_key = st.text_input(
                "Instantly API key",
                value="",
                type="password",
                help="Masked. Blank keeps the stored Instantly key.",
            )
            cooldown_days = st.slider(
                "Automated alert cooldown (days)",
                min_value=7,
                max_value=30,
                value=int(org.alert_cooldown_days or 14),
            )
            hitl_arr = st.number_input(
                "Minimum ARR threshold for human-in-the-loop review",
                min_value=0.0,
                value=float(org.hitl_mrr_threshold or HITL_MRR_THRESHOLD),
                step=100.0,
                help="Accounts above this MRR/ARR-proxy wait in Staging until a CSM approves.",
            )
            if st.button("Save tenant settings", type="primary"):
                old_cooldown = org.alert_cooldown_days
                old_hitl = org.hitl_mrr_threshold
                update_tenant_policy(
                    session,
                    org,
                    actor_id="dashboard",
                    alert_cooldown_days=int(cooldown_days),
                    hitl_mrr_threshold=float(hitl_arr),
                    stripe_webhook_secret=stripe_secret.strip() or None,
                    slack_webhook_url=slack_url.strip() or None,
                    resend_api_key=resend_key.strip() or None,
                    hubspot_access_token=hubspot_token.strip() or None,
                    salesforce_access_token=salesforce_token.strip() or None,
                    salesforce_instance_url=salesforce_instance.strip() or None,
                    apollo_api_key=apollo_key.strip() or None,
                    instantly_api_key=instantly_key.strip() or None,
                )
                session.query(CustomerAccount).filter(CustomerAccount.org_id == org.org_id).update(
                    {CustomerAccount.cooldown_days: int(cooldown_days)}
                )
                session.commit()
                st.success(
                    f"Saved integrations and policy (cooldown {old_cooldown}→{org.alert_cooldown_days}, "
                    f"HITL {old_hitl}→{org.hitl_mrr_threshold})."
                )
                st.rerun()
            st.markdown("#### Recent audit log")
            logs = (
                session.query(SystemAuditLog)
                .filter(SystemAuditLog.org_id == org.org_id)
                .order_by(SystemAuditLog.created_at.desc())
                .limit(12)
                .all()
            )
            if not logs:
                st.caption("No audit events yet.")
            else:
                st.dataframe(
                    [
                        {
                            "when": row.created_at,
                            "actor": row.actor_id,
                            "action": row.action,
                            "old": row.old_value,
                            "new": row.new_value,
                        }
                        for row in logs
                    ],
                    hide_index=True,
                    width="stretch",
                )
    finally:
        session.close()


main()
