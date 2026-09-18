import { Card, CardContent } from "@/components/ui/card";

type KpiMetricCardProps = {
  label: string;
  value: string;
  delta?: string;
};

export function KpiMetricCard({ label, value, delta }: KpiMetricCardProps) {
  return (
    <Card className="rounded-2xl border-border shadow-card">
      <CardContent className="p-4">
        <p className="text-sm font-medium text-muted-foreground">{label}</p>
        <p className="mt-2 text-2xl font-bold tracking-tight text-foreground">{value}</p>
        {delta ? <p className="mt-1 text-sm font-medium text-primary">{delta}</p> : null}
      </CardContent>
    </Card>
  );
}
