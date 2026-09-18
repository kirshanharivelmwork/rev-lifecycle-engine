import { ActionStream } from "@/components/command-center/action-stream";
import { AtRiskBook } from "@/components/command-center/at-risk-book";
import { BookRiskChart } from "@/components/command-center/book-risk-chart";
import { KpiGrid } from "@/components/command-center/kpi-grid";
import { TENANT } from "@/lib/command-center-data";

export function CommandCenter() {
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
          {TENANT.name} · {TENANT.subscriberCount.toLocaleString("en-US")} monitored subscribers ·
          model {TENANT.modelVersion} · HITL threshold ${TENANT.hitlFloorUsd.toLocaleString("en-US")} ·
          cooldown {TENANT.cooldownDays}d
        </p>
      </header>

      <KpiGrid />

      <section className="grid gap-4 xl:grid-cols-[1.15fr_1fr]">
        <ActionStream />
        <BookRiskChart />
      </section>

      <AtRiskBook />
    </div>
  );
}
