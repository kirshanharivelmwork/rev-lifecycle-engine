"use client";

import { ActionStream } from "@/components/command-center/action-stream";
import { AtRiskBook } from "@/components/command-center/at-risk-book";
import { BookRiskChart } from "@/components/command-center/book-risk-chart";
import { KpiGrid } from "@/components/command-center/kpi-grid";
import { useCommandCenter } from "@/hooks/use-command-center";
import { formatUsd } from "@/lib/command-center-data";

export function CommandCenter() {
  const { data, error, loading } = useCommandCenter();

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading live command center…</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to load tenant data.</p>;
  }

  const tenant = data.tenant;
  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header>
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">
          Commercial · Multi-tenant revenue intelligence
        </p>
        <h2 className="hero-title mt-1 text-[2.05rem] font-bold text-foreground">
          Executive Revenue Command Center
        </h2>
        <p className="mt-2 text-[0.98rem] text-muted-foreground">
          {tenant.name} · {(tenant.subscriber_count ?? 0).toLocaleString("en-US")} monitored subscribers ·
          model {tenant.model_version} · HITL threshold {formatUsd(tenant.hitl_mrr_threshold)} · cooldown{" "}
          {tenant.alert_cooldown_days}d
        </p>
      </header>

      <KpiGrid kpis={data.kpis} subscriberCount={tenant.subscriber_count ?? 0} />

      <section className="grid gap-4 xl:grid-cols-[1.15fr_1fr]">
        <ActionStream rows={data.action_stream} />
        <BookRiskChart data={data.risk_mix} />
      </section>

      <AtRiskBook rows={data.at_risk_book} />
    </div>
  );
}
