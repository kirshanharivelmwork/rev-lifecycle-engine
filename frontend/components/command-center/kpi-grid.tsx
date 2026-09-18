import { KpiMetricCard } from "@/components/command-center/kpi-metric-card";
import { COMMAND_CENTER_KPIS } from "@/lib/command-center-data";

export function KpiGrid() {
  return (
    <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      {COMMAND_CENTER_KPIS.map((kpi) => (
        <KpiMetricCard key={kpi.id} label={kpi.label} value={kpi.value} delta={kpi.delta} />
      ))}
    </section>
  );
}
