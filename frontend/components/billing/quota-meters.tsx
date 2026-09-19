import type { PlanQuotas } from "@/lib/types";

const LABELS: Record<keyof PlanQuotas, string> = {
  customer_accounts: "Customer accounts",
  prospect_leads: "Prospect leads",
  ingest_runs_per_utc_day: "CSV + backfill runs today (UTC)",
};

export function QuotaMeters({ quotas }: { quotas?: PlanQuotas }) {
  if (!quotas) {
    return null;
  }
  return (
    <section className="grid gap-4 sm:grid-cols-3">
      {(Object.keys(LABELS) as (keyof PlanQuotas)[]).map((key) => {
        const meter = quotas[key];
        return (
          <div key={key} className="rounded-2xl border border-border bg-card p-4 shadow-card">
            <p className="text-xs font-medium uppercase tracking-[0.12em] text-muted-foreground">{LABELS[key]}</p>
            <p className="mt-2 text-2xl font-bold text-foreground">{meter.remaining.toLocaleString("en-US")}</p>
            <p className="mt-1 text-sm text-muted-foreground">
              remaining · {meter.used.toLocaleString("en-US")} / {meter.max.toLocaleString("en-US")} used
            </p>
          </div>
        );
      })}
    </section>
  );
}
