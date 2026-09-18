"""Executive Revenue Command Center — Streamlit dashboard."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from src.churn_model import load_or_train
from src.paths import (
    AT_RISK_THRESHOLD,
    CHURN_ALERTS_PATH,
    FULL_FUNNEL_FEATURES_PATH,
    MODEL_VERSION,
)
from src.scoring import assign_risk_tier, recommend_playbook, risk_drivers

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

TIER_COLORS = {"Low": "#3ee0b1", "Medium": "#f5c542", "Critical": "#ff5c7a"}


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
        .stDataFrame { border-radius: 12px; overflow: hidden; }
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner="Loading production scoring engine…")
def _engine():
    engine, version = load_or_train(tune=False, persist=True)
    return engine, version


@st.cache_data(show_spinner="Scoring the live book of business…")
def _scored_book() -> pd.DataFrame:
    if not FULL_FUNNEL_FEATURES_PATH.exists():
        raise FileNotFoundError(
            "Missing data/processed/full_funnel_features.csv. Run `python -m src.data_pipeline` first."
        )
    engine, _ = _engine()
    df = pd.read_csv(FULL_FUNNEL_FEATURES_PATH)
    df["churn_probability"] = engine.predict_proba(df)
    df["arr"] = df["monthly_recurring_revenue"] * 12.0
    df["risk_tier"] = df["churn_probability"].map(assign_risk_tier)
    df["recommended_playbook"] = df.apply(recommend_playbook, axis=1)
    df["risk_drivers"] = df.apply(risk_drivers, axis=1)
    df["is_active"] = df["churned"].eq(0) if "churned" in df.columns else True
    return df


def _fmt_money(value: float) -> str:
    if abs(value) >= 1_000_000:
        return f"${value/1_000_000:.2f}M"
    if abs(value) >= 1_000:
        return f"${value/1_000:.1f}K"
    return f"${value:,.0f}"


def _kpi_row(active: pd.DataFrame) -> None:
    arr = float(active["arr"].sum())
    mrr = arr / 12.0
    at_risk = active.loc[active["churn_probability"] > AT_RISK_THRESHOLD, "arr"].sum()
    expected_retained = float((active["arr"] * (1.0 - active["churn_probability"])).sum())
    nrr = expected_retained / arr if arr else 0.0
    blended = float(active["ltv_est"].sum() / active["cac_usd"].clip(lower=1e-6).sum())

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Monthly Recurring Revenue", _fmt_money(mrr), help="Sum of MRR on currently active accounts")
    c1.caption(f"ARR {_fmt_money(arr)}")
    c2.metric("At-Risk ARR (p > 65%)", _fmt_money(at_risk), f"{100*at_risk/arr:.1f}% of ARR" if arr else None)
    c3.metric("NRR forecast", f"{100*nrr:.1f}%", help="Expected retained ARR / current ARR using model probabilities")
    c4.metric("Blended LTV:CAC", f"{blended:.1f}×", help="Book-level Σ LTV / Σ CAC")


def _charts(active: pd.DataFrame) -> None:
    left, right = st.columns((1.15, 1))
    with left:
        fig = px.histogram(
            active,
            x="churn_probability",
            color="risk_tier",
            nbins=40,
            color_discrete_map=TIER_COLORS,
            category_orders={"risk_tier": ["Low", "Medium", "Critical"]},
            title="Churn probability distribution vs. risk tiers",
        )
        fig.update_layout(**PLOTLY_LAYOUT, bargap=0.05)
        fig.update_xaxes(title="Churn probability", gridcolor="rgba(255,255,255,0.06)")
        fig.update_yaxes(title="Accounts", gridcolor="rgba(255,255,255,0.06)")
        st.plotly_chart(fig, width="stretch")

    with right:
        rate_col = "churned" if "churned" in active.columns else "churn_probability"
        channel = (
            active.groupby("acquisition_channel", as_index=False)
            .agg(
                median_cac=("cac_usd", "median"),
                churn_rate=(rate_col, "mean"),
                accounts=("customer_id", "count"),
                arr=("arr", "sum"),
                ltv_cac=("ltv_cac_ratio", "median"),
            )
        )
        channel["retention"] = 1.0 - channel["churn_rate"]
        fig2 = px.scatter(
            channel,
            x="median_cac",
            y="retention",
            size="arr",
            color="acquisition_channel",
            hover_data={"accounts": True, "ltv_cac": ":.1f", "arr": ":.0f"},
            title="Channel efficiency: CAC vs. 12-month retention",
        )
        fig2.update_traces(marker=dict(sizemode="area", sizeref=2.0 * channel["arr"].max() / 80**2, line=dict(width=1)))
        fig2.update_layout(**PLOTLY_LAYOUT)
        fig2.update_xaxes(title="Median CAC (USD)", gridcolor="rgba(255,255,255,0.06)")
        fig2.update_yaxes(title="12-month retention rate", tickformat=".0%", gridcolor="rgba(255,255,255,0.06)")
        st.plotly_chart(fig2, width="stretch")

    heat = active.copy()
    heat["inactivity_bin"] = pd.cut(
        heat["days_since_last_login"],
        bins=[-0.1, 7, 14, 21, 30, 60, 200],
        labels=["0–7d", "8–14d", "15–21d", "22–30d", "31–60d", "60d+"],
    )
    heat["adoption_bin"] = pd.cut(
        heat["feature_adoption_score"],
        bins=[-0.1, 2, 4, 6, 8, 10.1],
        labels=["0–2", "2–4", "4–6", "6–8", "8–10"],
    )
    pivot = heat.pivot_table(
        index="adoption_bin",
        columns="inactivity_bin",
        values="churn_probability",
        aggfunc="mean",
        observed=False,
    )
    fig3 = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=list(pivot.columns.astype(str)),
            y=list(pivot.index.astype(str)),
            colorscale=[
                [0.0, "#12352d"],
                [0.35, "#3ee0b1"],
                [0.55, "#f5c542"],
                [1.0, "#ff5c7a"],
            ],
            colorbar=dict(title="P(churn)", tickformat=".0%"),
            hovertemplate="Adoption %{y}<br>Inactivity %{x}<br>P(churn)=%{z:.1%}<extra></extra>",
        )
    )
    fig3.update_layout(
        **PLOTLY_LAYOUT,
        title='Inactivity vs. feature adoption — the "danger zone"',
        xaxis_title="Days since last login",
        yaxis_title="Feature adoption score",
        yaxis=dict(autorange="reversed"),
    )
    st.plotly_chart(fig3, width="stretch")


def _simulate(active: pd.DataFrame, lift_pct: float) -> tuple[pd.DataFrame, float]:
    engine, _ = _engine()
    lifted = active.copy()
    lifted["feature_adoption_score"] = (
        lifted["feature_adoption_score"] * (1.0 + lift_pct / 100.0)
    ).clip(upper=10.0)
    lifted["engagement_index"] = lifted["avg_weekly_logins"] * 0.4 + lifted["feature_adoption_score"] * 0.6
    lifted["churn_probability_lifted"] = engine.predict_proba(lifted)
    baseline_loss = float((active["arr"] * active["churn_probability"]).sum())
    lifted_loss = float((lifted["arr"] * lifted["churn_probability_lifted"]).sum())
    return lifted, max(baseline_loss - lifted_loss, 0.0)


def main() -> None:
    _inject_css()
    engine, version = _engine()
    book = _scored_book()
    active = book.loc[book["is_active"]].copy() if "is_active" in book.columns else book.copy()

    st.markdown('<div class="hero-kicker">Rev Lifecycle Engine · Front Door × Back Door</div>', unsafe_allow_html=True)
    st.markdown('<p class="hero-title">Executive Revenue Command Center</p>', unsafe_allow_html=True)
    st.markdown(
        f'<p class="hero-sub">Live scoring of {len(active):,} active accounts · '
        f"model {version} ({engine.production_name_}) · at-risk threshold {AT_RISK_THRESHOLD:.0%}</p>",
        unsafe_allow_html=True,
    )

    _kpi_row(active)
    st.markdown("### Portfolio diagnostics")
    _charts(active)

    st.markdown("### CSM early-warning queue")
    st.caption("Top flagged accounts with drivers and the recommended intervention playbook.")

    sidebar = st.sidebar
    sidebar.header("Filters")
    tiers = sidebar.multiselect("Risk tier", ["Low", "Medium", "Critical"], default=["Medium", "Critical"])
    channels = sorted(active["acquisition_channel"].dropna().unique().tolist())
    selected_channels = sidebar.multiselect("Acquisition channel", channels, default=channels)
    min_p = sidebar.slider("Minimum churn probability", 0.0, 1.0, AT_RISK_THRESHOLD, 0.01)
    lift = sidebar.slider(
        "Simulate retention impact: onboarding adoption lift",
        min_value=0,
        max_value=40,
        value=10,
        step=1,
        help="Re-scores the book after increasing feature_adoption_score by X%, capped at 10.",
        format="%d%%",
    )

    _, arr_saved = _simulate(active, float(lift))
    sidebar.metric("ARR saved at this lift", _fmt_money(arr_saved))
    sidebar.caption("Reduction in expected churned ARR if every active account gains the selected adoption lift.")

    queue = active.copy()
    if CHURN_ALERTS_PATH.exists() and queue.empty:
        queue = pd.read_csv(CHURN_ALERTS_PATH)
        queue["churn_probability"] = queue.get("churn_risk_score", queue.get("churn_probability"))
    queue = queue.loc[
        queue["risk_tier"].isin(tiers)
        & queue["acquisition_channel"].isin(selected_channels)
        & (queue["churn_probability"] >= min_p)
    ].sort_values("churn_probability", ascending=False)

    display_cols = [
        "customer_id",
        "acquisition_channel",
        "contract_type",
        "churn_probability",
        "risk_tier",
        "arr",
        "risk_drivers",
        "recommended_playbook",
        "days_since_last_login",
        "feature_adoption_score",
        "ltv_cac_ratio",
    ]
    show = queue.loc[:, [c for c in display_cols if c in queue.columns]].head(75)
    st.dataframe(
        show,
        width="stretch",
        hide_index=True,
        column_config={
            "churn_probability": st.column_config.NumberColumn("Churn risk", format="%.1%"),
            "arr": st.column_config.NumberColumn("ARR", format="$%.0f"),
            "ltv_cac_ratio": st.column_config.NumberColumn("LTV:CAC", format="%.1f×"),
            "feature_adoption_score": st.column_config.NumberColumn("Adoption", format="%.1f"),
        },
    )
    st.caption(f"Showing {len(show):,} of {len(queue):,} accounts in the current filter. Model version {MODEL_VERSION}.")


main()
