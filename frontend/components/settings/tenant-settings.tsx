"use client";

import { FormEvent, useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
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

export function TenantSettings() {
  const { data, error, loading, saving, save } = useTenantSettings();
  const [cooldown, setCooldown] = useState(14);
  const [hitl, setHitl] = useState(1000);
  const [salesforceInstance, setSalesforceInstance] = useState("");
  const [secrets, setSecrets] = useState(EMPTY_SECRETS);
  const [saved, setSaved] = useState(false);

  useEffect(() => {
    if (!data) {
      return;
    }
    setCooldown(data.tenant.alert_cooldown_days);
    setHitl(data.tenant.hitl_mrr_threshold);
    setSalesforceInstance(data.salesforce_instance_url || "");
  }, [data]);

  if (loading) {
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

  async function onSubmit(event: FormEvent) {
    event.preventDefault();
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

  const inputClass =
    "mt-1 w-full rounded-lg border border-input bg-background px-3 py-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header>
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">Tenant</p>
        <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">Settings & Integrations</h2>
        <p className="mt-2 text-[0.98rem] text-muted-foreground">
          Secrets stay encrypted at rest. Leave a field blank to keep the stored value.
        </p>
      </header>

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
            value={hitl}
            onChange={(event) => setHitl(Number(event.target.value))}
          />
        </label>
        <div className="md:col-span-2">
          <Button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save tenant settings"}
          </Button>
          {saved ? <span className="ml-3 text-sm text-primary">Saved.</span> : null}
          {error ? <p className="mt-2 text-sm text-destructive">{error}</p> : null}
        </div>
      </form>

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
        </CardContent>
      </Card>
    </div>
  );
}
