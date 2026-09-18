# Outbound sales & 48-hour retention audit kit

Commercial playbook for **Rev Lifecycle Engine**. Live Command Center: [https://rev-lifecycle-engine-production.up.railway.app](https://rev-lifecycle-engine-production.up.railway.app). Stripe ingest: `https://rev-lifecycle-engine-production.up.railway.app/api/v1/webhooks/stripe`.

Use this kit for founders in the **£1M–£5M ARR** band (B2B SaaS, CS-led or founder-led retention). Do not lead with a software demo. Lead with a **48-hour diagnostic on files they already export**.

Production proof points (seeded Acme tenant on Railway): **$416.6K** monitored ARR · **$100.7K** at risk · **$150.4K** verified ARR preserved · **3** HITL-queued interventions.

---

## The core wedge pitch

Cold “let me show you our churn model” loses to calendar. A **zero-integration 48-Hour Retention Audit** wins because it matches how this band actually buys:

1. **No engineering ticket.** They already dump Stripe or Chargebee and Mixpanel/PostHog/Amplitude. Two CSVs. No webhook until they have seen their own silent churn in a memo.
2. **The pain is annual leakage they cannot see.** Logos do not cancel in the CRM the week they go dark. They fail to renew 60–90 days later. Founders feel it as a surprise NRR print, not as a CS queue.
3. **Software demos imply a six-week integration.** An audit implies a weekend. You are selling a *decision* (where ARR is leaking), then offering the Railway-hosted engine as the way to keep the save motion on.
4. **Proof before secrets.** Stripe signing secrets and Slack webhooks stay off until the readout. That is the opposite of every other “connect your Stripe” PLG tool.

**One-sentence wedge:** “Send the last 12 months of billing + usage. In 48 hours we tell you which channels and which silent accounts are leaking ARR — without touching production.”

**Close:** If the memo is wrong, they owe you nothing. If it is right, they paste three secrets into [Tenant Settings](https://rev-lifecycle-engine-production.up.railway.app) and the same engine starts scoring live events.

---

## Ready-to-send cold outreach

Replace `[First name]`, `[Company]`, and `[specific observation]` (job post, funding, CS hire, churn tweet). Keep the Railway URL in the first or second touch so they can click without a meeting.

### Email 1 — Founder / CEO (silent annual churn)

**Subject A:** The churn you will not see until renewal  
**Subject B:** 48 hours, two CSVs, no engineers  
**Subject C:** `[Company]`’s quiet ARR leak

```
[First name] —

Most £1–5M B2B SaaS companies do not “have a churn problem.” They have a timing problem: accounts go dark months before the annual invoice, and the CRM still says Active.

We run a 48-Hour Retention Audit on historical exports only — billing + product usage. No Stripe webhook, no sandbox, no sprint. You get: which acquisition channels never pay back, which still-paying logos are already gone in the product, and how much ARR that represents.

Live book we operate on today (Railway): $416.6K monitored ARR, $100.7K at risk, $150.4K verified preserved after intervention audits.

Command center (no login theatre):
https://rev-lifecycle-engine-production.up.railway.app

If useful, reply with “audit” and we send the two-file column list. 48 hours after the CSVs land, you get the memo.

— [Your name]
```

### Email 2 — Head of Customer Success (fatigue, cooldown, HITL)

**Subject A:** Stop paging CSMs for the same Medium-risk account  
**Subject B:** 14-day cooldown + human approval for high-ACV  
**Subject C:** Your queue vs. actual saves

```
[First name] —

CS tools fail CS teams in a predictable way: they alert on every score tick. Medium stays Medium. Slack fills up. Playbooks get muted.

Rev Lifecycle Engine is built around that failure mode. Duplicate alerts inside a 14-day window are suppressed (`suppressed_cooldown`) unless risk escalates to Critical (p > 0.85). Accounts above a $1,000 MRR floor sit in a Staging & Approval Queue until a CSM hits Approve Dispatch or Dismiss / False Positive — Slack and Resend do not fire on a model hunch for enterprise logos.

I am not asking you to connect production. I am asking for a 48-hour pass on CSV exports so we can show *your* book: who would have been suppressed, who should have been queued for a human, and which playbooks actually match inactivity vs. adoption.

60-second look at the live queue:
https://rev-lifecycle-engine-production.up.railway.app
(Staging & Approval Queue tab — three HITL items on the demo tenant.)

Happy to run the audit against a redacted export this week.

— [Your name]
```

### Email 3 — Head of RevOps / Finance (empirical ARR, not 35%)

**Subject A:** Stop modelling saves at 35%  
**Subject B:** 30 / 60 / 90-day ARR that actually stayed  
**Subject C:** Verified vs. hypothetical retention ROI

```
[First name] —

Most retention ROI decks multiply “at-risk ARR × 35% save rate.” That number is a slide, not a ledger.

We attribute interventions empirically: each dispatch opens an InterventionOutcome, then a rolling audit stamps Active / Churned / Downgraded at 30, 60, and 90 days and writes verified ARR preserved. On the live Railway tenant that figure is $150.4K verified against $100.7K still at risk — not a hypothetical.

The commercial entry is a 48-Hour Retention Audit on historical CSVs (no live webhooks). You see channel-level leakage, calibrated risk on remaining actives, and a finance-readable memo before anyone grants a Stripe signing secret.

Live command center:
https://rev-lifecycle-engine-production.up.railway.app

Stripe endpoint for later, when you want ingestion rather than a file drop:
https://rev-lifecycle-engine-production.up.railway.app/api/v1/webhooks/stripe

If you send a billing extract + a usage extract, we return the diagnostic in two days.

— [Your name]
```

---

### LinkedIn — connection note

Stay under ~200 characters when possible; LinkedIn truncates aggressively.

```
[First name] — we diagnose silent SaaS churn from two CSVs in 48h (no webhooks). Live command center: rev-lifecycle-engine-production.up.railway.app — worth a look if NRR is a 2026 goal.
```

Longer variant if the UI allows:

```
[First name], most tools want Stripe connected before they prove anything. We run a 48-hour retention audit on historical billing + usage files, then show the live engine (Railway) if the memo is useful: https://rev-lifecycle-engine-production.up.railway.app
```

### LinkedIn — 2-step follow-up

**Day 2–3 (after they accept):**

```
Thanks for connecting, [First name].

The offer is deliberately small: two CSVs (customers/billing + product usage). In 48 hours we send (1) which channels leak vs. pay back CAC, (2) still-paying accounts that are already dark, (3) ARR at risk vs. what a 14-day alert cooldown and human approval queue would change.

Live demo of the operating view (Acme seed): $416.6K ARR monitored, $150.4K verified preserved, 3 high-ACV items waiting on CSM approval.
https://rev-lifecycle-engine-production.up.railway.app

If you would rather not export, I can walk the public tenant in 60 seconds on a call. Either works.
```

**Day 7–10 (if no reply):**

```
[First name] — last note from me.

If CS is already drowning in Medium-risk pings, the interesting part of the product is what it *does not* send: duplicate alerts are suppressed for 14 days unless the account goes Critical, and anything above ~$1k MRR waits for a human. Finance gets 30/60/90-day verified ARR, not a 35% save-rate cell.

URL again: https://rev-lifecycle-engine-production.up.railway.app

Happy to take a redacted CSV whenever. I’ll close the thread here.
```

---

## 48-hour audit execution checklist

Technical path for whoever receives the customer files. Repo root = working directory. Python 3.9+.

### Hour 0 — Receive and map files

- [ ] Create a working folder `audits/<customer_slug>/` (do not commit customer data).
- [ ] Confirm two extracts or split one wide table:

**Leads / billing → `data/raw/acquisition_leads.csv`**

| Column | Notes |
| --- | --- |
| `customer_id` | Stable join key (Stripe `cus_…` is fine) |
| `acquisition_channel` | Must map into: `Outbound Cold Email`, `Inbound Organic`, `Paid Search`, `Partner Referral` (relabel “Ads” → Paid Search, etc.) |
| `sales_touchpoints` | Integer; use `4` if unknown and footnote it |
| `cac_usd` | Float; use `500` if unknown and footnote it |
| `monthly_recurring_revenue` | Seat × price; annual contracts still as monthly ARR/12 |
| `contract_type` | `Monthly` or `Annual` only |

**Telemetry → `data/raw/user_telemetry_churn.csv`**

| Column | Notes |
| --- | --- |
| `customer_id` | Same key as leads |
| `avg_weekly_logins` | Float ≥ 0 |
| `feature_adoption_score` | 0–10 scale; rescale NPS-like scores and document |
| `support_tickets_raised` | Integer |
| `days_since_last_login` | Integer |
| `churned` | `1` if cancelled / unpaid / not renewed in the window; else `0` |

- [ ] Inner-join preview: every telemetry `customer_id` should exist on the lead file. Drop orphans or add stub lead rows with documented defaults.
- [ ] Copy mapped files over the repo’s `data/raw/` paths (or pass explicit paths if you wrap the pipeline). Backup any previous demo CSVs first.

### Hours 0–8 — Pipeline

```bash
python3 -m src.data_pipeline
```

- [ ] Confirm `data/processed/full_funnel_features.csv` exists and row count equals the inner join.
- [ ] Spot-check `ltv_est`, `ltv_cac_ratio`, `engagement_index`, `high_risk_inactivity`, `days_until_renewal`, `contract_renewal_urgency_ratio`.
- [ ] If `SchemaValidationError`, fix column names — do not edit `src/` for a one-off customer.

### Hours 8–24 — Stats + model

```bash
python3 -m src.stats_engine
python3 -m src.churn_model
```

- [ ] Capture the printed Welch t-test (adoption, churned vs retained) and chi-square (channel × churn) into the memo. These are the “this is not a vibe” exhibits.
- [ ] Confirm `data/processed/churn_risk_alerts.csv` and `models/churn_engine.pkl`.
- [ ] Pull the active, high-probability cohort (threshold **0.65**). Attach playbooks from the alert table (`recommended_action` / scoring playbooks).
- [ ] Translate model output to money: `ARR at risk ≈ Σ (MRR × 12)` for flagged *still-active* accounts.

Optional API check against a local or Railway scorer (customer row JSON):

```bash
curl -s -X POST https://rev-lifecycle-engine-production.up.railway.app/v1/predict \
  -H "Content-Type: application/json" \
  -H "X-API-Key: <tenant key if required>" \
  -d '{"customer_id":"cus_example","acquisition_channel":"Paid Search","contract_type":"Monthly","avg_weekly_logins":1.0,"feature_adoption_score":2.2,"support_tickets_raised":4,"days_since_last_login":30,"monthly_recurring_revenue":640}'
```

Do **not** POST customer CSVs to the public webhook during the audit.

### Hours 24–48 — Diagnostic summary report

Write a 2–4 page memo (PDF or Notion). Required sections:

1. **Scope & assumptions** — date range, join rate, any defaulted CAC/touches, channel relabels.
2. **Front Door** — churn rate and median LTV:CAC by `acquisition_channel`. Name the expensive leak (usually outbound).
3. **Back Door** — adoption gap (t-test), inactivity cohort (`days_since_last_login > 14` or `> 21`), ticket load.
4. **Still-active risk list** — top 15 accounts by churn probability with MRR, playbook, and whether they would be auto-dispatch vs HITL (`MRR > $1,000`) vs cooldown-suppressed if this were live.
5. **Money slide** — prospective ARR at risk vs a conservative save narrative. Contrast with *empirical* attribution (30/60/90) you will turn on *after* go-live — do not pretend the audit already verified saves.
6. **Go-live path** — paste secrets in Tenant Settings; whitelist  
   `https://rev-lifecycle-engine-production.up.railway.app/api/v1/webhooks/stripe`  
   and telemetry `POST /api/v1/webhooks/telemetry`. 14-day cooldown and HITL remain on.

Readout call: 25 minutes. Screen-share the [live Command Center](https://rev-lifecycle-engine-production.up.railway.app) for *product shape*, then the customer’s memo for *their numbers*. Ask for webhook enablement only if they accept the diagnostic.

### After they say yes

- [ ] Create or select their `Organization` (do not reuse Acme credentials in production).
- [ ] Customer pastes Stripe `whsec_`, Slack webhook, Resend key, cooldown, HITL floor on **Tenant Settings & Integrations**.
- [ ] Send a signed test event to `/api/v1/webhooks/stripe`; expect **HTTP 202** and a `job_id`.
- [ ] Confirm first `ChurnAssessment` / `DispatchedAction` (or `pending_approval` / `suppressed_cooldown`) in the dashboard.

---

## What not to do

- Do not send the Acme API key in outbound mail.
- Do not dump customer CSVs into git or the public Railway volume.
- Do not promise a 35% save rate; point at verified ARR after 30/60/90 days live.
- Do not skip the audit and “just connect Stripe” — that is the motion this wedge exists to replace.
