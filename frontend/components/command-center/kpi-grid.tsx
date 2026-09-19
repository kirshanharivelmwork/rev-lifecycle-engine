"use client";

import { KpiMetricCard } from "@/components/command-center/kpi-metric-card";
import { formatUsd } from "@/lib/command-center-data";
import type { CommandCenterPayload } from "@/lib/types";

export function KpiGrid({ kpis, subscriberCount }: { kpis: CommandCenterPayload["kpis"]; subscriberCount: number }) {
  return (
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <KpiMetricCard
        label="Monitored subscribers & active ARR"
        value={formatUsd(kpis.active_arr)}
        delta={`${subscriberCount.toLocaleString("en-US")} accounts`}
      />
      <KpiMetricCard label="Prospective ARR at risk" value={formatUsd(kpis.at_risk_arr)} delta="p ≥ 0.65 churn" />
      <KpiMetricCard
        label="Verified ARR saved"
        value={formatUsd(kpis.verified_arr_saved)}
        delta="30 / 60 / 90-day audits"
      />
      <KpiMetricCard
        label="Dispatched interventions (this month)"
        value={String(kpis.dispatched_count)}
        delta="HITL queued"
      />
    </section>
  );
}
