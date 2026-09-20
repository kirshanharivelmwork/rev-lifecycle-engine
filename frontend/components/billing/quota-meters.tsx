import type { PlanQuotas } from "@/lib/types";

const LABELS: Record<string, string> = {
  customer_accounts: "Customer accounts",
  prospect_leads: "Prospect leads",
  ingest_runs_per_utc_day: "CSV + backfill runs today (UTC)",
  csv_ingests_per_calendar_month: "CSV diagnostics this month",
};

export function QuotaMeters({ quotas, freeTier }: { quotas?: PlanQuotas; freeTier?: boolean }) {
  if (!quotas) {
    return null;
  }
  const keys = freeTier
    ? (["customer_accounts", "csv_ingests_per_calendar_month"] as const)
    : (["customer_accounts", "prospect_leads", "ingest_runs_per_utc_day"] as const);
  return (
    <section className="grid gap-4 sm:grid-cols-3">
      {keys.map((key) => {
        const meter = quotas[key];
        if (!meter) {
          return null;
        }
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
