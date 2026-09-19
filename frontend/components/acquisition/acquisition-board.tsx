"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useAcquisition } from "@/hooks/use-acquisition";
import { useOrgRole } from "@/hooks/use-org-role";
import { cn } from "@/lib/utils";

function labelStatus(value: string) {
  return value.replace(/_/g, " ");
}

export function AcquisitionBoard() {
  const { data, error, loading, running, runOutbound } = useAcquisition();
  const { isAdmin } = useOrgRole();

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading live prospects…</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to load Front Door prospects.</p>;
  }

  const sequenceEntries = Object.entries(data.sequence_status);
  const sequenceSummary = sequenceEntries.length
    ? sequenceEntries.map(([status, count]) => `${labelStatus(status)} ${count}`).join(" · ")
    : "No sequences yet";

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header className="flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
        <div>
          <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">Front Door</p>
          <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">Acquisition</h2>
          <p className="mt-2 text-[0.98rem] text-muted-foreground">
            {data.tenant.name} · Apollo ICP ingest, conversion scoring, and Instantly sequences
          </p>
        </div>
        <div className="flex flex-col items-start gap-2">
          <Button disabled={running || !isAdmin} onClick={() => void runOutbound()}>
            {running ? "Running outbound…" : "Run outbound"}
          </Button>
          {!isAdmin ? <p className="text-sm text-muted-foreground">Admin access required</p> : null}
        </div>
      </header>

      <section className="grid gap-4 sm:grid-cols-3">
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Live prospects</CardTitle>
            <CardDescription>Unique emails in this workspace</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-bold">{data.prospect_count}</CardContent>
        </Card>
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>High-intent</CardTitle>
            <CardDescription>Conversion score &gt; 80</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-bold">{data.high_intent_count}</CardContent>
        </Card>
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Sequence status</CardTitle>
            <CardDescription>Lead pipeline mix</CardDescription>
          </CardHeader>
          <CardContent className="text-sm font-medium leading-relaxed text-foreground">{sequenceSummary}</CardContent>
        </Card>
      </section>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader className="pb-2">
          <CardTitle className="text-lg">Prospect table</CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          {data.prospects.length === 0 ? (
            <p className="py-8 text-sm text-muted-foreground">
              No prospects yet. Configure Apollo and Instantly in Settings, then run outbound.
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Company</TableHead>
                  <TableHead>Decision maker</TableHead>
                  <TableHead>Email</TableHead>
                  <TableHead className="text-right">Score</TableHead>
                  <TableHead>Intent</TableHead>
                  <TableHead>Sequence</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.prospects.map((row) => (
                  <TableRow key={row.id}>
                    <TableCell className="font-medium">{row.company_name}</TableCell>
                    <TableCell>{row.decision_maker_name}</TableCell>
                    <TableCell className="font-mono text-xs">{row.email}</TableCell>
                    <TableCell className="text-right">{Math.round(row.conversion_score)}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={cn(
                          row.high_intent
                            ? "border-primary/30 bg-primary/10 text-primary"
                            : "border-border bg-muted text-muted-foreground",
                        )}
                      >
                        {row.high_intent ? "High" : "Watch"}
                      </Badge>
                    </TableCell>
                    <TableCell className="capitalize">{labelStatus(row.sequence_status)}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
