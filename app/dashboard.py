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

from src.database import get_session_factory, init_db
from src.dispatcher import approve_pending_dispatch, dismiss_false_positive
from src.models_db import (
    ChurnAssessment,
    CustomerAccount,
    DispatchedAction,
    Organization,
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
    plot_bgcolor="rgba(11,18,32,0.35)",
    font=dict(color="#e8eefc", family="Inter, IBM Plex Sans, sans-serif"),
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
            background:
                radial-gradient(1200px 500px at 10% -10%, rgba(62,224,177,0.12), transparent 50%),
                radial-gradient(900px 400px at 100% 0%, rgba(90,130,255,0.14), transparent 45%),
                #070b14;
            color: #e8eefc;
        }
        .block-container { padding-top: 1.2rem; max-width: 1400px; }
        h1, h2, h3 { letter-spacing: -0.03em; }
        .hero-kicker {
            font-size: 0.78rem; font-weight: 600; letter-spacing: 0.16em;
            text-transform: uppercase; color: #3ee0b1; margin-bottom: 0.2rem;
        }
        .hero-title { font-size: 2.05rem; font-weight: 700; margin: 0 0 0.35rem 0; }
        .hero-sub { color: #9aa8c7; font-size: 0.98rem; margin-bottom: 1.2rem; }
        div[data-testid="stMetric"] {
            background: linear-gradient(180deg, rgba(18,26,43,0.95), rgba(12,18,32,0.92));
            border: 1px solid rgba(255,255,255,0.06);
            border-radius: 16px; padding: 14px 16px;
            box-shadow: 0 12px 40px rgba(0,0,0,0.25);
        }
        div[data-testid="stMetric"] label { color: #9aa8c7 !important; }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
            color: #f4f7ff !important; font-weight: 700;
        }
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
        st.sidebar.caption("Configure Stripe, Slack, Resend, and HITL in the Tenant Settings tab.")
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

        command, staging, settings = st.tabs(
            ["Command Center", "Staging & Approval Queue", "Tenant Settings & Integrations"]
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
                        color_discrete_map={"Low": "#3ee0b1", "Medium": "#f5c542", "Critical": "#ff5c7a"},
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
                if stripe_secret.strip():
                    org.stripe_webhook_secret = stripe_secret.strip()
                if slack_url.strip():
                    org.slack_webhook_url = slack_url.strip()
                if resend_key.strip():
                    org.resend_api_key = resend_key.strip()
                org.alert_cooldown_days = int(cooldown_days)
                org.hitl_mrr_threshold = float(hitl_arr)
                session.query(CustomerAccount).filter(CustomerAccount.org_id == org.org_id).update(
                    {CustomerAccount.cooldown_days: int(cooldown_days)}
                )
                session.commit()
                st.success("Saved integrations and policy for this organization.")
                st.rerun()
    finally:
        session.close()


main()
