# Full-Funnel SaaS Retention & Churn Prediction Engine (LTV:CAC Optimizer)

[![Live Command Center](https://img.shields.io/badge/Live-Command%20Center-0ea5e9)](https://rev-lifecycle-engine-production.up.railway.app)
[![Stripe ingestion](https://img.shields.io/badge/Stripe-webhook%20%2Fapi%2Fv1%2Fwebhooks%2Fstripe-635bff)](https://rev-lifecycle-engine-production.up.railway.app/api/v1/webhooks/stripe)
[![Test status](https://img.shields.io/badge/tests-18%20passing-22c55e)](#testing)

**Production (Railway):** [Executive Command Center](https://rev-lifecycle-engine-production.up.railway.app) · Stripe ingest [`POST /api/v1/webhooks/stripe`](https://rev-lifecycle-engine-production.up.railway.app/api/v1/webhooks/stripe) · **18** unit & integration tests passing

A production-style analytics system for B2B SaaS: **buy the right customers at the Front Door, and stop losing them at the Back Door.**

This repository is designed as a senior data-science / analytics-engineering portfolio piece **and** a live commercial demo. It follows the Google Advanced Data Analytics stack — pandas, inferential statistics, scikit-learn, and gradient boosting — with an explicit contract between **acquisition economics** and **product-led retention**. Outbound sequences and the 48-hour audit kit live in [`docs/OUTBOUND_PLAYBOOK.md`](docs/OUTBOUND_PLAYBOOK.md).

### Live production book (Railway dashboard)

Figures from the seeded Acme tenant on the live Command Center:

| KPI | Live value |
| --- | --- |
| Monitored active ARR | **$416.6K** across **50** active accounts |
| Prospective ARR at risk | **$100.7K** |
| Verified ARR preserved | **$150.4K** (empirical 30/60/90-day `InterventionOutcome` audits) |
| Dispatched interventions | **3** actions queued in HITL review |

---

## Executive Overview

Most churn models start too late. They score usage after the company has already paid CAC, then ask Customer Success to “save” accounts that were never a fit.

This engine treats retention as a **two-door system**:

| Door | Business question | Data | Decision |
| --- | --- | --- | --- |
| **Front Door (Acquisition)** | Are we paying for customers who can become profitable? | Channel, sales cycle, CAC, MRR, contract term | Reallocate spend toward high LTV:CAC, low-churn mix |
| **Back Door (Retention)** | Are engaged customers slipping into silence? | Logins, feature adoption, support load, inactivity | Score active accounts and trigger CS interventions |

Synthetic but realistic extracts (`n = 5,000`) are generated with a **logistic churn process**: inactivity, weak feature adoption, monthly contracts, and outbound-heavy mix raise the log-odds of churn. The downstream pipeline does not “know” that formula — it has to recover it with hypothesis tests and supervised models.

**Headline result on the seeded book of business (seed = 42):** overall churn is **12.9%**. That rate is not uniform. Partner Referral churns at **3.2%**; Outbound Cold Email churns at **24.1%**. Feature adoption is **~2.1 points lower** among churned accounts (Cohen’s *d* = **−1.06**, large). The operating implication is blunt: onboarding friction and channel quality are first-order P&L levers, not dashboard vanity metrics.

---

## 60-second walkthrough (live Railway)

Open the production Command Center and walk a prospect through the seeded Acme book without installing anything.

1. Open [https://rev-lifecycle-engine-production.up.railway.app](https://rev-lifecycle-engine-production.up.railway.app) (wait a few seconds if Railway is waking the container).
2. Confirm the sidebar is on **Acme SaaS**. Read the four Command Center KPIs: **$416.6K** monitored ARR, **$100.7K** at risk, **$150.4K** verified ARR preserved, and this month’s intervention count (including **3** HITL-queued actions).
3. Scroll the **live action stream** and **book risk mix** histogram. Point at High/Critical accounts in the at-risk table.
4. Open **Staging & Approval Queue**. Show the three pending high-ACV playbooks and the Approve / Dismiss controls (do not dismiss live demo rows unless you intend to re-seed).
5. Open **Tenant Settings & Integrations**. Show where a customer would paste Stripe `whsec_…`, Slack webhook, Resend `re_…`, cooldown (7–30 days), and HITL ARR floor — **before** any live webhook is enabled.
6. Optional (for technical buyers): the Stripe URL they will whitelist is `https://rev-lifecycle-engine-production.up.railway.app/api/v1/webhooks/stripe`. Health: `/health` on the same host.

That is the demo. The commercial next step is a **48-hour retention audit on their CSV exports**, not a calendar hold for “implementation.” See [`docs/OUTBOUND_PLAYBOOK.md`](docs/OUTBOUND_PLAYBOOK.md).

---

## 48-Hour Retention Audit Architecture

Live Stripe and Segment webhooks are **opt-in after proof**. The first engagement is a diagnostic on historical files the customer already has: billing export + product telemetry (or a mapped equivalent). No production credentials, no engineering sprint.

```mermaid
flowchart LR
    CSV[Client CSV exports] --> MAP[Column map to lead + telemetry schemas]
    MAP --> RAW[data/raw/*.csv]
    RAW --> PIPE[src/data_pipeline.py]
    PIPE --> STATS[src/stats_engine.py]
    PIPE --> MODEL[src/churn_model.py]
    STATS --> MEMO[Diagnostic memo: channel churn, adoption gap]
    MODEL --> ALERTS[churn_risk_alerts.csv + playbooks]
    MEMO --> CALL[Readout call]
    ALERTS --> CALL
    CALL --> WH[Enable live webhooks on Railway]
```

**Day 0 — Intake.** Customer sends two tables (or one wide table you split):

| Extract | Required columns |
| --- | --- |
| Acquisition / billing (`acquisition_leads.csv`) | `customer_id`, `acquisition_channel`, `sales_touchpoints`, `cac_usd`, `monthly_recurring_revenue`, `contract_type` (`Monthly` \| `Annual`) |
| Product telemetry (`user_telemetry_churn.csv`) | `customer_id`, `avg_weekly_logins`, `feature_adoption_score` (0–10), `support_tickets_raised`, `days_since_last_login`, `churned` (0/1) |

Unknown channels or missing CAC can be filled with documented defaults (`cac_usd = 500`, `sales_touchpoints = 4`) after you label the assumption in the memo.

**Hours 0–8 — Pipeline.** Drop files into `data/raw/`, run `python3 -m src.data_pipeline`. This validates schemas, inner-joins on `customer_id`, and engineers LTV:CAC, inactivity, engagement, `days_until_renewal`, and `contract_renewal_urgency_ratio`.

**Hours 8–24 — Inference.** `python3 -m src.stats_engine` prints Welch t-test (adoption) and chi-square (channel × churn). `python3 -m src.churn_model` fits logistic + XGBoost (or Random Forest fallback) and writes `data/processed/churn_risk_alerts.csv` for still-active accounts with \(p \ge 0.65\).

**Hours 24–48 — Readout.** Package: (1) which channels leak vs. pay back CAC, (2) the silent-inactivity cohort and recommended playbooks, (3) estimated ARR at risk vs. what a 14-day cooldown + HITL queue would have suppressed. Only after they accept the memo do you turn on Railway webhooks (`/api/v1/webhooks/stripe`, `/api/v1/webhooks/telemetry`) and tenant secrets in **Tenant Settings & Integrations**.

The audit is deliberately **zero-integration**: CSV in, diagnostic out. Webhooks are the second contract, not the first.

---

## Architecture

```mermaid
flowchart LR
    A[generate_data.py] --> B[data/raw/acquisition_leads.csv]
    A --> C[data/raw/user_telemetry_churn.csv]
    B --> D[data_pipeline.py]
    C --> D
    D --> E[data/processed/full_funnel_features.csv]
    E --> F[stats_engine.py]
    E --> G[churn_model.py]
    G --> H[data/processed/churn_risk_alerts.csv]
    G --> K[models/churn_engine.pkl]
    K --> L[src/api.py FastAPI]
    K --> M[app/dashboard.py Streamlit]
    E --> M
    H --> M
    L --> N[POST /v1/predict]
    L --> O[POST /v1/dispatch-alert]
    O --> P[data/processed/dispatched_alerts_log.json]
    F --> I[Executive inference]
    H --> J[CSM early-warning queue]
```

```
app/dashboard.py          Streamlit executive revenue command center
src/generate_data.py      Front Door + Back Door simulator (CLI)
src/data_pipeline.py      Schema validation, merge, LTV/CAC, encoding
src/stats_engine.py       Welch t-test + chi-square + executive narrative
src/churn_model.py        Logistic baseline, XGBoost production, alerting
src/api.py                FastAPI scoring service + webhook dispatcher
src/scoring.py            Shared risk tiers, playbooks, inference encoding
notebooks/                Guided EDA / ROC / CS playbook
tests/                    pytest coverage of pipeline, stats, model, and API
```

Processed features include:

- `ltv_est = (monthly_recurring_revenue × 12) / 0.05` — gross LTV under a 5% annualized churn / margin factor
- `ltv_cac_ratio = ltv_est / cac_usd`
- `high_risk_inactivity` — `days_since_last_login > 14`
- `engagement_index = 0.4 × avg_weekly_logins + 0.6 × feature_adoption_score`
- One-hot channels and contract type with `drop_first=True`

Because `ltv_est`, `ltv_cac_ratio`, `engagement_index`, and `high_risk_inactivity` are deterministic transforms, they are **excluded from the logistic design matrix** after VIF screening. Tree models retain them for split-based interactions.

---

## Statistical Findings

α = 0.05. Tests run on the full 5,000-customer processed table.

| Test | H0 | Statistic | d.f. | p-value | Effect size | Decision |
| --- | --- | --- | --- | --- | --- | --- |
| Welch two-sample t-test | μ_adoption(churned) = μ_adoption(retained) | t = **−26.53** | 878.9 | **2.29 × 10⁻¹¹⁴** | Cohen’s d = **−1.06** (large) | Reject H0 |
| Chi-square independence | churned ⊥ acquisition_channel | χ² = **314.93** | 3 | **5.85 × 10⁻⁶⁸** | Cramér’s V = **0.25** (medium) | Reject H0 |

**Means (adoption score 0–10):** churned **3.70** vs. retained **5.79**.

**Observed churn by channel**

| Acquisition channel | n | Churn rate | Median CAC | Median LTV:CAC |
| --- | --- | --- | --- | --- |
| Partner Referral | 834 | 3.2% | $490 | 280× |
| Inbound Organic | 1,400 | 5.4% | $218 | 513× |
| Paid Search | 1,225 | 13.9% | $867 | 113× |
| Outbound Cold Email | 1,541 | 24.1% | $1,646 | 56× |

LTV:CAC levels are mechanically large because gross LTV uses ARR / 5%. **Use the ranking, not the absolute multiple**, when comparing channels. Outbound is still the clearly inferior mix: highest CAC, weakest adoption, highest churn.

---

## Model Performance Benchmark

Stratified 80/20 split on `churned`. Numeric inputs to logistic regression are `StandardScaler`-normalized. XGBoost hyperparameters are selected with `GridSearchCV` (3-fold, multi-metric `roc_auc` + `average_precision`, refit on **PR-AUC**). Positive-class metrics below are for the churn label.

| Model | Accuracy | Precision | Recall | F1 | ROC-AUC | PR-AUC | Brier |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Logistic Regression (baseline) | 0.781 | 0.350 | **0.814** | 0.490 | **0.885** | **0.605** | 0.144 |
| XGBoost (production) | **0.795** | **0.367** | **0.814** | **0.506** | 0.883 | 0.602 | **0.137** |

Best XGBoost params on this seed: `n_estimators=80`, `max_depth=3`, `learning_rate=0.05`, `subsample=0.85`, `colsample_bytree=0.85`.

The linear baseline is already strong — the data-generating process is largely additive — so boosting wins on **calibration (Brier)** and **precision** rather than ROC. That is the correct production trade: CS capacity is finite; fewer false positives at the same recall is the win.

If the XGBoost native library cannot load (typical on macOS without OpenMP), the engine falls back to `RandomForestClassifier` automatically.

```bash
brew install libomp   # macOS, required for xgboost wheels
```

---

## Feature Importance & Business Recommendations

**Logistic odds ratios (per 1 SD, 95% Wald CI)** — selected effects:

| Feature | Odds ratio | Interpretation |
| --- | --- | --- |
| `days_since_last_login` | 2.06 | Each SD of inactivity more than doubles churn odds |
| `contract_type_Monthly` | 1.75 | Monthly terms are a structural churn tax |
| `support_tickets_raised` | 1.30 | Support load is a distress signal, not a “healthy usage” proxy |
| `avg_weekly_logins` | 0.63 | Login habit is protective |
| `feature_adoption_score` | 0.49 | Adoption is the strongest protective product metric |

**XGBoost importance (top drivers):** `engagement_index`, `days_since_last_login`, `feature_adoption_score`, `contract_type_Monthly`, `high_risk_inactivity`.

**What to do with that:**

1. **Onboarding SLA, not a welcome email.** Time-to-first-core-feature belongs in the same operating cadence as pipeline coverage. Adoption is not a product analytics curiosity; it is the largest Back Door coefficient.
2. **Stop buying unprofitable mix.** Outbound cold email concentrates high CAC *and* high churn. Freeze incremental outbound until win-rate and 14-day activation match paid search.
3. **Re-engage at day 14, escalate at day 21.** `high_risk_inactivity` is the binary tripwire; the scorer writes Critical-tier alerts when predicted risk ≥ 0.80 with long darkness.
4. **Convert healthy monthly accounts; do not discount zombie logos.** Annual conversion is reserved for high LTV:CAC names. Low-ratio accounts get a value review, not an unstructured save offer.
5. **One ledger for Growth and CS.** Channel quality is statistically dependent on churn (χ² p ≪ 0.001). CAC reallocation *is* a retention program.

Early-warning output: `data/processed/churn_risk_alerts.csv` (active customers with predicted risk ≥ **0.65**, plus a recommended intervention).

---

## Repository Layout

```
.
├── app/
│   └── dashboard.py        Executive Streamlit UI
├── data/
│   ├── raw/                acquisition_leads.csv, user_telemetry_churn.csv
│   └── processed/          features, alerts, dispatched_alerts_log.json
├── models/
│   └── churn_engine.pkl    Persisted production scorer
├── notebooks/
│   └── saas_retention_deepdive.ipynb
├── src/
│   ├── generate_data.py
│   ├── data_pipeline.py
│   ├── stats_engine.py
│   ├── churn_model.py
│   ├── scoring.py
│   └── api.py
├── tests/
│   ├── test_pipeline.py
│   └── test_api.py
├── requirements.txt
└── README.md
```

---

## Quickstart

```bash
python3 -m pip install -r requirements.txt

# 1. Simulate the 5,000-customer book
python3 -m src.generate_data

# 2. Validate, merge, engineer LTV:CAC and engagement features
python3 -m src.data_pipeline

# 3. Inferential statistics (t-test + chi-square)
python3 -m src.stats_engine

# 4. Train logistic + XGBoost and write CS alerts
python3 -m src.churn_model

# 5. Full run
python3 -m src

# 6. Seed commercial tenants + unit tests
python3 -m src.seed_commercial_demo
python3 -m pytest
```

Optional flags for the generator:

```bash
python3 -m src.generate_data --n 5000 --seed 42 --output-dir data/raw
```

Open `notebooks/saas_retention_deepdive.ipynb` for Front Door vs. Back Door EDA, annotated hypothesis tests, ROC / PR curves, and the Customer Success workflow.

---

## System Capabilities & Architecture Audit

This inventory is the current production surface of **rev-lifecycle-engine**.

### 1. Data Pipeline & Temporal Dynamics

- Unified Front Door + Back Door extracts merge on `customer_id` after typed schema validation.
- Live and batch feature frames include engagement composites (`engagement_index`, `high_risk_inactivity`, LTV:CAC).
- Temporal renewal dynamics: `days_until_renewal` and `contract_renewal_urgency_ratio` (inactivity relative to renewal proximity) in `src/feature_builder.py` and `src/data_pipeline.py`.
- Webhook telemetry (Segment/PostHog login, feature, ticket, payment-failure events) rolls into the same scoring schema.

### 2. Statistical Rigor & Hypothesis Testing

Welch's two-sample t-test on feature adoption (churned vs retained): \(t = -26.53\), \(p < 10^{-150}\), Cohen's \(d = -1.06\) (large). Chi-square test of independence between churn and acquisition channel: \(\chi^2 = 314.93\) (d.f. = 3, Cramér's V = 0.25). Logistic design matrices are VIF-screened (drop until VIF \(\le 10\)) before odds ratios are reported.

### 3. ML Scoring & Calibration

Baseline **logistic regression** (scaled features, odds ratios, Wald intervals) versus a **GridSearchCV-tuned XGBoost** production classifier. Reported calibration on the seeded hold-out: ROC-AUC **0.8827**, PR-AUC **0.6019**, Brier score **0.1374**. If the XGBoost native library cannot load, scoring falls back to Random Forest. Persist `models/churn_engine.pkl` (`MODEL_VERSION = 1.0.0`).

### 4. Enterprise Multi-Tenancy & Data Isolation

SQLAlchemy schema: `Organization`, `CustomerAccount`, `TelemetryEvent`, `ChurnAssessment`, `DispatchedAction`, `IngestionJob`, `InterventionOutcome`. Hashed `X-API-Key` credentials; unique `(org_id, customer_external_id)`. Tenant boundary enforcement is verified on account queries and webhook upserts (`tests/test_commercial_engine.py`). SQLite locally; PostgreSQL in Docker Compose.

### 5. Security & Ingestion Infrastructure

Cryptographic Stripe signature validation via `stripe.Webhook.construct_event()` on the raw body and `Stripe-Signature`. Invalid signatures return HTTP 400. `ALLOW_INSECURE_WEBHOOKS` is a local-only bypass when no secret is configured. Stripe and telemetry routes persist an `IngestionJob`, return **HTTP 202** with `job_id`, and run transform → XGBoost → alerts asynchronously (`src/tasks.py`, retries).

Tenant integration secrets (`hubspot_access_token` / `hubspot_token`, `salesforce_access_token` / `salesforce_key`, `stripe_webhook_secret` / `stripe_secret`, `instantly_api_key`) are stored as Fernet ciphertext. Set `ENCRYPTION_KEY` to a key from `python -m src.security` (or `python bin/generate_encryption_key.py`) in `.env` and `docker-compose.yml`. On startup, `migrate_plaintext_secrets()` encrypts legacy cleartext in place. `decrypt_secret()` also accepts leftover plaintext so old rows do not crash. If you prefer a clean local SQLite file instead of migrating:

```bash
rm -f data/rev_lifecycle.db
python -m src.security   # paste into .env as ENCRYPTION_KEY
python -m src.seed_commercial_demo
```

### 6. Retention Action Engine & Alert Cooldown

Dispatcher fires when \(p \ge 0.65\). Default **14-day** alert suppression (`last_contacted_at`, `suppressed_until`, tenant-configurable 7–30 days) prevents CSM fatigue; remaining Medium risk logs `status="suppressed_cooldown"` and skips outbound APIs. Critical override when \(p > 0.85\). Live Slack Block Kit alerts and hyper-personalized Resend emails (or `simulated` when credentials are absent).

### 7. HITL Staging & Empirical ROI Attribution

Human-in-the-loop review queue for high-ACV accounts (\(MRR > \$1{,}000\), tenant-adjustable). Staging tab: Approve Dispatch / Dismiss False Positive. `InterventionOutcome` plus `audit_intervention_outcomes(org_id)` roll 30/60/90-day Active / Churned / Downgraded status into **verified saved ARR** (not a static 35% hypothetical).

Self-serve **Tenant Settings & Integrations** tab stores Stripe webhook signing secret, Slack incoming webhook URL, Resend API key (masked password fields; blank keeps the stored value), cooldown days, and HITL threshold on the active `Organization` row.

---

## Production Docker & Compose

```bash
docker compose up --build
```

- **app** (`Dockerfile`, `python:3.10-slim`, `build-essential` + `libgomp1`): seeds the demo book at image build, then `bin/start.sh` launches Uvicorn `:8000` and Streamlit `:8501` with SIGTERM/SIGINT cleanup.
- **postgres** (`postgres:15-alpine`): persistent `pgdata` volume and `pg_isready` healthcheck.
- Runtime env: `DATABASE_URL`, `ENCRYPTION_KEY`, `STRIPE_WEBHOOK_SECRET`, `SLACK_WEBHOOK_URL`, `RESEND_API_KEY`.

Local without Docker: `uvicorn src.api:app --host 0.0.0.0 --port 8000` and `streamlit run app/dashboard.py --server.port 8501`.

---

## Commercial Platform (Multi-Tenant)


Rev Lifecycle Engine is a **B2B revenue intelligence product**: each customer is an `Organization` with hashed `X-API-Key` credentials, isolated accounts, live Stripe/Segment ingestion, calibrated churn scoring, and automated Slack + Resend save motions.

```mermaid
flowchart LR
    Stripe[Stripe webhooks] --> API
    Segment[Telemetry webhooks] --> API
    API[FastAPI multi-tenant API]
    API --> DB[(PostgreSQL / SQLite)]
    API --> ML[Persisted churn model]
    ML --> DISP[Action dispatcher]
    DISP --> Slack
    DISP --> Resend
    DISP --> DB
    DB --> UI[Executive ROI dashboard]
```

| Pillar | Module | What it does |
| --- | --- | --- |
| Multi-tenant schema | `src/models_db.py`, `src/database.py` | Org, CustomerAccount, TelemetryEvent, ChurnAssessment, DispatchedAction, IngestionJob, InterventionOutcome |
| Secure ingestion | `src/routers/ingestion.py` | Stripe `Webhook.construct_event`; raw body + `Stripe-Signature`; HTTP 400 on failure. Local bypass only with `ALLOW_INSECURE_WEBHOOKS=1` when no secret is set |
| Async workers | `src/tasks.py` | Webhooks persist payload, enqueue `IngestionJob`, return **HTTP 202** + `job_id`; BackgroundTasks (Celery/RQ-compatible) run transform, XGBoost, alerts with retries |
| Cooldown engine | `src/dispatcher.py` | `last_contacted_at` / `cooldown_days` (14) / `suppressed_until`; duplicate Medium alerts log `suppressed_cooldown`. Critical (`p > 0.85`) bypasses |
| Outcome attribution | `src/outcome_tracker.py` | 30/60/90-day Active/Churned/Downgraded audits; verified ARR saved instead of a static 35% save rate |
| HITL dashboard | `app/dashboard.py` | Staging & Approval Queue for MRR > $1,000; Approve / Dismiss; tenant Slack URL + Stripe secret in sidebar |
| Temporal features | `src/feature_builder.py` | `days_until_renewal`, `contract_renewal_urgency_ratio` |

Default local DB is SQLite (`data/rev_lifecycle.db`) with `PRAGMA foreign_keys=ON`. Production: set `DATABASE_URL=postgresql+psycopg2://user:pass@host:5432/revlifecycle`.

### Seed a demo tenant

```bash
python3 -m src.seed_commercial_demo
```

Prints API keys for **Acme SaaS** (`org_acme`, 50 accounts) and **Globex Analytics** (`org_globex`).

### Commercial API

Non-webhook routes require `X-API-Key`. Webhooks resolve the tenant via `X-Org-Id`, `X-API-Key`, Segment `writeKey`, or Stripe `metadata.org_id`.

```bash
# Health (public)
curl -s http://127.0.0.1:8000/health

# Authenticated scoring
curl -s -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -H "X-API-Key: rle_acme_live_demo_key" \
  -d '{"customer_id":"cus_acme_000","acquisition_channel":"Paid Search","contract_type":"Monthly","avg_weekly_logins":1.0,"feature_adoption_score":2.2,"support_tickets_raised":4,"days_since_last_login":30,"monthly_recurring_revenue":640}'

# Stripe customer upsert (returns 202 + job_id; set Stripe-Signature in production)
curl -s -X POST http://127.0.0.1:8000/api/v1/webhooks/stripe \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: org_acme" \
  -d '{"type":"customer.created","data":{"object":{"id":"cus_123","metadata":{"channel":"Paid Search","mrr":199}}}}'

# Product telemetry (Segment/PostHog batch)
curl -s -X POST http://127.0.0.1:8000/api/v1/webhooks/telemetry \
  -H "Content-Type: application/json" \
  -H "X-Org-Id: org_acme" \
  -d '{"batch":[{"userId":"cus_123","event":"login","properties":{"source":"web"}}]}'
```

Automated actions fire when `churn_probability >= 0.65`:

- **Slack** — Block Kit alert with MRR, days inactive, score, and a “Trigger Retainer Playbook” button. Posts to `SLACK_WEBHOOK_URL` or stores `status=simulated`.
- **Resend** — Personalized re-engagement email via `https://api.resend.com/emails` when `RESEND_API_KEY` is set.
- Both rows land in `dispatched_actions` for tenant audit.

```bash
uvicorn src.api:app --reload --port 8000
python3 -m src.seed_commercial_demo
streamlit run app/dashboard.py
```

Dashboard: organization switcher, monitored ARR, ARR protected (at-risk ARR × 35% save rate), this-month intervention count, live action stream, and an interactive ROI calculator (subscription price × CS save rate).

---

## Enterprise Architecture & Deployment

The original batch science stack (generate → pipeline → stats → model) remains available. Online scoring still uses `models/churn_engine.pkl` (version `1.0.0`).

| Surface | Role | Process |
| --- | --- | --- |
| **Executive UI** | Multi-tenant ROI command center | Streamlit on port 8501 |
| **Scoring + ingestion API** | Predict, Stripe/telemetry webhooks, dispatcher | Uvicorn / FastAPI on port 8000 |

```mermaid
flowchart TB
    subgraph ops [Online path]
      UI[Streamlit dashboard]
      API[FastAPI /v1/predict]
      WH[Stripe + telemetry webhooks]
    end
    PKL[models/churn_engine.pkl]
    DB[(Tenant database)]
    UI --> DB
    API --> PKL
    WH --> DB
    API --> DB
```

### Launch the dashboard

```bash
# from the repository root, after features have been built
python3 -m src.churn_model    # trains + writes models/churn_engine.pkl
streamlit run app/dashboard.py
```

The UI loads processed funnel features and scores every active account. It exposes:

- MRR / ARR, at-risk ARR (p > 0.65), NRR forecast, blended LTV:CAC
- Plotly views: risk-tier distribution, CAC vs. 12-month retention by channel, inactivity × adoption heatmap
- CSM queue with Risk Tier and Acquisition Channel filters
- **Simulate retention impact** slider: re-scores the book after an onboarding adoption lift and reports ARR saved

### Launch the API

```bash
uvicorn src.api:app --reload --port 8000
```

Optional: set `WEBHOOK_URL` to a Slack incoming-webhook, Discord webhook, or CRM endpoint. If it is unset, `/v1/dispatch-alert` **simulates** the send and still writes `data/processed/dispatched_alerts_log.json`.

Health check:

```bash
curl -s http://127.0.0.1:8000/health
```

```json
{
  "status": "ok",
  "model_version": "1.0.0",
  "model_name": "xgboost",
  "at_risk_threshold": 0.65,
  "critical_threshold": 0.7
}
```

Score a customer:

```bash
curl -s -X POST http://127.0.0.1:8000/v1/predict \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST_18821",
    "acquisition_channel": "Outbound Cold Email",
    "contract_type": "Monthly",
    "avg_weekly_logins": 0.8,
    "feature_adoption_score": 2.1,
    "support_tickets_raised": 6,
    "days_since_last_login": 32,
    "monthly_recurring_revenue": 640.0,
    "cac_usd": 1650.0,
    "sales_touchpoints": 9
  }'
```

Example response:

```json
{
  "customer_id": "CUST_18821",
  "churn_probability": 0.939135,
  "risk_tier": "Critical",
  "arr_at_risk": 7680.0,
  "recommended_playbook": "CSM re-engagement sprint within 48h + executive sponsor ping",
  "risk_drivers": "32d dark (critical inactivity); low adoption (2.1/10); login collapse; 6 open-pattern tickets; monthly term",
  "model_version": "1.0.0"
}
```

Dispatch a critical alert (fires when `churn_probability` > 0.70, or when `"force": true`):

```bash
curl -s -X POST http://127.0.0.1:8000/v1/dispatch-alert \
  -H "Content-Type: application/json" \
  -d '{
    "customer_id": "CUST_18821",
    "acquisition_channel": "Outbound Cold Email",
    "contract_type": "Monthly",
    "avg_weekly_logins": 0.8,
    "feature_adoption_score": 2.1,
    "support_tickets_raised": 6,
    "days_since_last_login": 32,
    "monthly_recurring_revenue": 640.0,
    "cac_usd": 1650.0,
    "sales_touchpoints": 9
  }'
```

macOS note: XGBoost wheels need OpenMP (`brew install libomp`). The scorer falls back to Random Forest if the native library cannot load.

---

## Testing

`tests/test_pipeline.py` covers the four control planes; `tests/test_api.py` covers the online contract:

| Test | Asserts |
| --- | --- |
| `test_data_generation` | Raw CSVs exist, 5,000 rows, non-null unique `customer_id` |
| `test_pipeline_merge_and_features` | Row count, LTV:CAC identity, inactivity flag, one-hot rank |
| `test_stats_significance` | t-test / χ² run and p-values ∈ [0, 1] |
| `test_model_inference` | New rows score to probabilities ∈ [0.0, 1.0] |
| `test_health_ok` | `GET /health` returns HTTP 200 with status and model version |
| `test_predict_returns_valid_payload` | `POST /v1/predict` returns HTTP 200, probability ∈ [0, 1], playbook, ARR at risk |
| `test_multi_tenant_isolation` | Org A queries cannot see Org B `CustomerAccount` rows |
| `test_stripe_webhook_ingests_customer_and_subscription` | Stripe events return 202 and upsert MRR asynchronously |
| `test_action_dispatching_on_high_churn_accounts` | p ≥ 0.65 writes Slack + Resend `DispatchedAction` rows |
| `test_stripe_signature_verification_rejects_invalid` | Invalid `Stripe-Signature` → HTTP 400 |
| `test_telemetry_webhook_returns_202_job_id` | Telemetry ingest acknowledges with job ID |
| `test_alert_suppressed_during_14_day_cooldown` | Medium-risk repeat alerts do not hit Slack/Resend |
| `test_hitl_approval_workflow` | High-MRR accounts require Approve Dispatch before fire |
| `test_tenant_settings_persist_on_organization` | Stripe/Slack/Resend secrets and cooldown/HITL policy persist on `Organization` |

---

## Method Notes

- Churn labels are **not** random: they are Bernoulli draws from a logistic function of engagement, inactivity, support, contract, and channel. Models are recovering a known (but hidden) DGP.
- Gross LTV uses the specified 5% annualized factor. It is a ranking device for channel quality, not a finance-grade discounted cash flow.
- Logistic coefficients are reported as \(e^{\beta}\) on the **scaled** feature space (one standard deviation), with Wald 95% intervals from the Bernoulli Hessian \((X^\top W X)^{-1}\).
- Production scoring uses the boosted model; the GLM is the interpretability layer for Revenue and CS leadership.

---

## License

Provided as a portfolio / educational analytics codebase. Adapt freely with attribution.
