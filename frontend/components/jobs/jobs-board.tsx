"use client";

import { PaymentCta } from "@/components/billing/payment-cta";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useJobs } from "@/hooks/use-jobs";
import { useOrgRole } from "@/hooks/use-org-role";
import { isBillingBlocked } from "@/lib/api";

const ADMIN_REASON = "Admin access required";

export function JobsBoard() {
  const { data, error, loading, pendingId, retryDeadLetter, resolveDeadLetter, retryIngestion } = useJobs();
  const { isAdmin } = useOrgRole();

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading failed jobs…</p>;
  }
  if (error && isBillingBlocked(error)) {
    return <PaymentCta message={error} />;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to inspect the dead-letter queue.</p>;
  }

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header>
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">Operations</p>
        <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">Failed jobs</h2>
        <p className="mt-2 text-[0.98rem] text-muted-foreground">
          Dead-letter rows and failed ingestion jobs for this Clerk organization.
        </p>
        {!isAdmin ? <p className="mt-2 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
      </header>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Dead letter</CardTitle>
          <CardDescription>Exhausted Celery / outbound work</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {data.dead_letter.length === 0 ? (
            <p className="text-sm text-muted-foreground">No dead-letter jobs.</p>
          ) : (
            data.dead_letter.map((row) => (
              <div key={row.id} className="flex flex-col gap-2 rounded-xl border border-border p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="font-medium">{row.task_name}</p>
                  <p className="text-sm text-muted-foreground">
                    {row.error_message || "No error message"} · retries {row.retry_count}
                    {row.resolved ? " · resolved" : ""}
                  </p>
                </div>
                <div className="flex gap-2">
                  <Button
                    disabled={!isAdmin || pendingId === row.id || row.resolved}
                    onClick={() => void retryDeadLetter(row.id)}
                  >
                    Retry
                  </Button>
                  <Button
                    variant="outline"
                    disabled={!isAdmin || pendingId === row.id || row.resolved}
                    onClick={() => void resolveDeadLetter(row.id)}
                  >
                    Mark resolved
                  </Button>
                </div>
              </div>
            ))
          )}
        </CardContent>
      </Card>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Ingestion failures</CardTitle>
          <CardDescription>Stripe / telemetry jobs that exhausted retries</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-col gap-3">
          {data.ingestion_failures.length === 0 ? (
            <p className="text-sm text-muted-foreground">No failed ingestion jobs.</p>
          ) : (
            data.ingestion_failures.map((row) => (
              <div key={row.id} className="flex flex-col gap-2 rounded-xl border border-border p-3 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="font-medium">{row.source}</p>
                  <p className="text-sm text-muted-foreground">{row.last_error || row.status}</p>
                </div>
                <Button disabled={!isAdmin || pendingId === row.id} onClick={() => void retryIngestion(row.id)}>
                  Retry
                </Button>
              </div>
            ))
          )}
        </CardContent>
      </Card>
    </div>
  );
}
