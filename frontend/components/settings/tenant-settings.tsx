"use client";

import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import { QuotaMeters } from "@/components/billing/quota-meters";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useOrgRole } from "@/hooks/use-org-role";
import { useTenantSettings } from "@/hooks/use-tenant-settings";
import { formatWhen } from "@/lib/command-center-data";

const EMPTY_SECRETS = {
  stripe_webhook_secret: "",
  slack_webhook_url: "",
  resend_api_key: "",
  hubspot_access_token: "",
  salesforce_access_token: "",
  instantly_api_key: "",
  apollo_api_key: "",
};

const ADMIN_REASON = "Admin access required";

export function TenantSettings() {
  const { data, error, loading, saving, backfilling, scoringCsv, save, runBackfill, uploadCsvBook, deleteCustomer, reload } =
    useTenantSettings();
  const { isAdmin, isLoaded: roleLoaded } = useOrgRole();
  const [cooldown, setCooldown] = useState(14);
  const [hitl, setHitl] = useState(1000);
  const [salesforceInstance, setSalesforceInstance] = useState("");
  const [secrets, setSecrets] = useState(EMPTY_SECRETS);
  const [stripeApiKey, setStripeApiKey] = useState("");
  const [segmentToken, setSegmentToken] = useState("");
  const [posthogKey, setPosthogKey] = useState("");
  const [gdprId, setGdprId] = useState("");
  const [saved, setSaved] = useState(false);
  const [scoring, setScoring] = useState(false);
  const [backfillMessage, setBackfillMessage] = useState<string | null>(null);
  const [csvMessage, setCsvMessage] = useState<string | null>(null);
  const [dragActive, setDragActive] = useState(false);

  useEffect(() => {
    if (!data) {
      return;
    }
    setCooldown(data.tenant.alert_cooldown_days);
    setHitl(data.tenant.hitl_mrr_threshold);
    setSalesforceInstance(data.salesforce_instance_url || "");
  }, [data]);

  if (loading || !roleLoaded) {
    return <p className="text-sm text-muted-foreground">Loading tenant settings…</p>;
  }
  if (!data && error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to configure integrations.</p>;
  }

  const configured = Object.entries(data.integrations)
    .filter(([, enabled]) => enabled)
    .map(([key]) => key);
  const emptyBook = (data.tenant.subscriber_count ?? 0) === 0;
  const inputsDisabled = !isAdmin || saving || scoring || scoringCsv || backfilling;

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
    if (!isAdmin) {
      return;
    }
    setSaved(false);
    const body: Record<string, unknown> = {
      alert_cooldown_days: cooldown,
      hitl_mrr_threshold: hitl,
      salesforce_instance_url: salesforceInstance,
    };
    for (const [key, value] of Object.entries(secrets)) {
      if (value.trim()) {
        body[key] = value.trim();
      }
    }
    await save(body);
    setSecrets(EMPTY_SECRETS);
    setSaved(true);
  }

  async function onBackfill() {
    if (!isAdmin) {
      return;
    }
    setBackfillMessage(null);
    await runBackfill({
      stripe_api_key: stripeApiKey.trim(),
      segment_access_token: segmentToken.trim() || undefined,
      posthog_api_key: posthogKey.trim() || undefined,
    });
    setStripeApiKey("");
    setScoring(true);
    try {
      for (let attempt = 0; attempt < 20; attempt += 1) {
        await new Promise((resolve) => setTimeout(resolve, 1500));
        const latest = await reload({ silent: true });
        if ((latest?.tenant.subscriber_count ?? 0) > 0) {
          setBackfillMessage("Historical book is scored. Open the Command Center to review accounts.");
          return;
        }
      }
      setBackfillMessage("Backfill queued. Scoring continues in the background — refresh the Command Center shortly.");
    } finally {
      setScoring(false);
    }
  }

  async function onCsvFile(file: File | undefined) {
    if (!isAdmin || !file) {
      return;
    }
    setCsvMessage(null);
    try {
      const result = await uploadCsvBook(file);
      if (result.status === "skipped") {
        setCsvMessage("This CSV was already scored for this workspace. Open the Command Center to review.");
        return;
      }
      setCsvMessage(
        `Scored ${result.assessments_written ?? 0} accounts. Open the Command Center to review the 48-hour diagnostic.`,
      );
    } catch {
      setCsvMessage(null);
    }
  }

  async function onDeleteCustomer() {
    if (!isAdmin) {
      return;
    }
    const target = gdprId.trim();
    if (!target) {
      return;
    }
    if (!window.confirm(`Permanently delete customer ${target} and related telemetry for this tenant?`)) {
      return;
    }
    await deleteCustomer(target);
    setGdprId("");
  }

  const inputClass =
    "mt-1 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60";

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header>
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">Tenant</p>
        <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">Settings & Integrations</h2>
        <p className="mt-2 text-[0.98rem] text-muted-foreground">
          Secrets stay encrypted at rest. Leave a field blank to keep the stored value.
        </p>
        {!isAdmin ? <p className="mt-2 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
      </header>

      <QuotaMeters quotas={data.quotas} />

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>First-run checklist</CardTitle>
          <CardDescription>Compact Day-1 path for a paying design partner.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-1 text-sm text-muted-foreground">
          <p>1. Score a combined CSV book (no Stripe secret required) or run Stripe historical backfill.</p>
          <p>2. Optional Segment / PostHog token if you have product analytics history.</p>
          <p>3. Slack incoming webhook and Resend API key for save motions.</p>
          <p>4. HITL MRR floor (accounts above it wait in Staging).</p>
        </CardContent>
      </Card>

      <Card id="csv-book" className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Score CSV Book</CardTitle>
          <CardDescription>
            Primary 48-hour diagnostic. Upload the combined billing + telemetry CSV (customer_id, channel, MRR, logins,
            adoption, tickets, inactivity). We upsert accounts, score churn, and skip duplicate files.
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          <label
            className={`relative flex min-h-32 cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed px-4 py-6 text-center text-sm ${
              dragActive ? "border-primary bg-primary/5" : "border-input bg-muted/30"
            } ${inputsDisabled ? "cursor-not-allowed opacity-60" : ""}`}
            onDragOver={(event) => {
              event.preventDefault();
              if (!inputsDisabled) {
                setDragActive(true);
              }
            }}
            onDragLeave={() => setDragActive(false)}
            onDrop={(event) => {
              event.preventDefault();
              setDragActive(false);
              if (inputsDisabled) {
                return;
              }
              void onCsvFile(event.dataTransfer.files?.[0]);
            }}
          >
            <span className="font-medium text-foreground">
              {scoringCsv ? "Scoring your CSV book…" : "Drop a .csv here or choose a file"}
            </span>
            <span className="mt-1 text-muted-foreground">Combined acquisition + telemetry extract</span>
            <input
              className="absolute h-0 w-0 overflow-hidden opacity-0"
              type="file"
              accept=".csv,text/csv"
              disabled={inputsDisabled}
              onChange={(event) => {
                const file = event.target.files?.[0];
                event.target.value = "";
                void onCsvFile(file);
              }}
            />
          </label>
          {!isAdmin ? <p className="text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
          {csvMessage ? <p className="text-sm text-primary">{csvMessage}</p> : null}
        </CardContent>
      </Card>

      {emptyBook ? (
        <Card id="historical-backfill" className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Day-1 empty book</CardTitle>
            <CardDescription>
              This workspace has no customers yet. Score a CSV book above, or paste a Stripe secret key and run a
              historical backfill.
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3">
            <label className="text-sm font-medium">
              Stripe API key
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={stripeApiKey}
                disabled={inputsDisabled}
                onChange={(event) => setStripeApiKey(event.target.value)}
                placeholder="sk_live_… or sk_test_…"
              />
            </label>
            <label className="text-sm font-medium">
              Segment access token (optional)
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={segmentToken}
                disabled={inputsDisabled}
                onChange={(event) => setSegmentToken(event.target.value)}
              />
            </label>
            <label className="text-sm font-medium">
              PostHog API key (optional)
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={posthogKey}
                disabled={inputsDisabled}
                onChange={(event) => setPosthogKey(event.target.value)}
              />
            </label>
            <div>
              <Button
                type="button"
                disabled={inputsDisabled || !stripeApiKey.trim()}
                onClick={() => void onBackfill()}
              >
                {scoring || backfilling ? "Scoring your book…" : "Run historical backfill"}
              </Button>
              {!isAdmin ? <p className="mt-2 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
              {backfillMessage ? <p className="mt-2 text-sm text-primary">{backfillMessage}</p> : null}
            </div>
          </CardContent>
        </Card>
      ) : (
        <Card id="historical-backfill" className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Historical backfill</CardTitle>
            <CardDescription>
              Re-run Stripe history if you need to refresh the scored book ({data.tenant.subscriber_count} accounts).
            </CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-end">
            <label className="flex-1 text-sm font-medium">
              Stripe API key
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={stripeApiKey}
                disabled={inputsDisabled}
                onChange={(event) => setStripeApiKey(event.target.value)}
                placeholder="sk_live_… or sk_test_…"
              />
            </label>
            <label className="flex-1 text-sm font-medium">
              Segment token (optional)
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={segmentToken}
                disabled={inputsDisabled}
                onChange={(event) => setSegmentToken(event.target.value)}
              />
            </label>
            <label className="flex-1 text-sm font-medium">
              PostHog key (optional)
              <input
                className={inputClass}
                type="password"
                autoComplete="off"
                value={posthogKey}
                disabled={inputsDisabled}
                onChange={(event) => setPosthogKey(event.target.value)}
              />
            </label>
            <Button
              type="button"
              disabled={inputsDisabled || !stripeApiKey.trim()}
              onClick={() => void onBackfill()}
            >
              {scoring || backfilling ? "Scoring your book…" : "Run historical backfill"}
            </Button>
          </CardContent>
          {!isAdmin ? <p className="px-6 pb-4 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
          {backfillMessage ? <p className="px-6 pb-4 text-sm text-primary">{backfillMessage}</p> : null}
        </Card>
      )}

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Configured integrations</CardTitle>
          <CardDescription>
            {configured.length ? configured.join(" · ") : "No integration secrets saved for this tenant yet."}
          </CardDescription>
        </CardHeader>
      </Card>

      <form onSubmit={(event) => void onSubmit(event)} className="grid gap-4 md:grid-cols-2">
        {(
          [
            ["stripe_webhook_secret", "Stripe webhook signing secret"],
            ["slack_webhook_url", "Slack incoming webhook URL"],
            ["resend_api_key", "Resend API key"],
            ["hubspot_access_token", "HubSpot access token"],
            ["salesforce_access_token", "Salesforce access token"],
            ["apollo_api_key", "Apollo API key"],
            ["instantly_api_key", "Instantly API key"],
          ] as const
        ).map(([key, label]) => (
          <label key={key} className="text-sm font-medium">
            {label}
            <input
              className={inputClass}
              type="password"
              autoComplete="off"
              disabled={inputsDisabled}
              value={secrets[key]}
              onChange={(event) => setSecrets((current) => ({ ...current, [key]: event.target.value }))}
            />
          </label>
        ))}
        <label className="text-sm font-medium md:col-span-2">
          Salesforce instance URL
          <input
            className={inputClass}
            value={salesforceInstance}
            disabled={inputsDisabled}
            onChange={(event) => setSalesforceInstance(event.target.value)}
            placeholder="https://yourorg.my.salesforce.com"
          />
        </label>
        <label className="text-sm font-medium">
          Alert cooldown (days)
          <input
            className={inputClass}
            type="number"
            min={7}
            max={30}
            disabled={inputsDisabled}
            value={cooldown}
            onChange={(event) => setCooldown(Number(event.target.value))}
          />
        </label>
        <label className="text-sm font-medium">
          HITL MRR threshold
          <input
            className={inputClass}
            type="number"
            min={0}
            step={100}
            disabled={inputsDisabled}
            value={hitl}
            onChange={(event) => setHitl(Number(event.target.value))}
          />
        </label>
        <div className="md:col-span-2">
          <Button type="submit" disabled={inputsDisabled}>
            {saving ? "Saving…" : "Save tenant settings"}
          </Button>
          {saved ? <span className="ml-3 text-sm text-primary">Saved.</span> : null}
          {!isAdmin ? <p className="mt-2 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
          {error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
        </div>
      </form>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Production webhook URLs</CardTitle>
          <CardDescription>Whitelist these in Stripe and your telemetry vendor. Webhooks use Stripe-Signature / org resolution, not Clerk JWT.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 text-sm">
          {(
            [
              ["Stripe", data.webhook_urls?.stripe || "http://localhost:3000/api/v1/webhooks/stripe"],
              ["Telemetry", data.webhook_urls?.telemetry || "http://localhost:3000/api/v1/webhooks/telemetry"],
            ] as const
          ).map(([label, url]) => (
            <div key={label} className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
              <p className="font-mono text-xs break-all">{label}: {url}</p>
              <Button type="button" variant="outline" onClick={() => void copyText(url)}>
                Copy
              </Button>
            </div>
          ))}
          <Link className="text-sm text-primary underline-offset-4 hover:underline" href="/billing">
            Open Billing for plan status and Stripe Customer Portal
          </Link>
        </CardContent>
      </Card>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>GDPR / CCPA delete</CardTitle>
          <CardDescription>Hard-delete a customer by external id (Stripe customer id or userId).</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3 sm:flex-row sm:items-end">
          <label className="flex-1 text-sm font-medium">
            Customer external id
            <input
              className={inputClass}
              value={gdprId}
              disabled={inputsDisabled}
              onChange={(event) => setGdprId(event.target.value)}
              placeholder="cus_…"
            />
          </label>
          <Button type="button" variant="outline" disabled={inputsDisabled || !gdprId.trim()} onClick={() => void onDeleteCustomer()}>
            Delete customer
          </Button>
        </CardContent>
        {!isAdmin ? <p className="px-6 pb-4 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
      </Card>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Recent audit log</CardTitle>
        </CardHeader>
        <CardContent className="space-y-2 text-sm">
          {data.audit_log.length === 0 ? (
            <p className="text-muted-foreground">No audit events yet.</p>
          ) : (
            data.audit_log.map((row, index) => (
              <p key={`${row.when}-${index}`} className="text-muted-foreground">
                <span className="font-medium text-foreground">{formatWhen(row.when)}</span> · {row.actor} · {row.action}
              </p>
            ))
          )}
          {emptyBook ? (
            <p className="text-muted-foreground">
              After scoring completes, open the{" "}
              <Link className="text-primary underline-offset-4 hover:underline" href="/">
                Command Center
              </Link>
              .
            </p>
          ) : null}
        </CardContent>
      </Card>
    </div>
  );
}

async function copyText(value: string) {
  try {
    await navigator.clipboard.writeText(value);
  } catch {
    // Clipboard can fail in insecure contexts; ignore.
  }
}
