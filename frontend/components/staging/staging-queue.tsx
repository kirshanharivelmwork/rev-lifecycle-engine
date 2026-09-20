"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ProBadge, ProLockOverlay, useProWaitlist } from "@/components/ui/pro-waitlist-modal";
import { useOrgRole } from "@/hooks/use-org-role";
import { useStagingQueue } from "@/hooks/use-staging";
import { formatUsd } from "@/lib/command-center-data";

const ADMIN_REASON = "Admin access required";

export function StagingQueue() {
  const { data, error, loading, approve, dismiss, pendingId } = useStagingQueue();
  const { isAdmin } = useOrgRole();
  const { openWaitlist } = useProWaitlist();

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading approval queue…</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to review pending playbooks.</p>;
  }

  const lockPro = !Boolean(data.tenant.pro);

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header>
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">Human in the loop</p>
        <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">Staging & Approval Queue</h2>
        <p className="mt-2 text-[0.98rem] text-muted-foreground">
          Accounts with MRR above {formatUsd(data.tenant.hitl_mrr_threshold)} wait here before Slack/Resend fire.
        </p>
        {!isAdmin ? <p className="mt-2 text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
      </header>

      <section className="grid gap-4 sm:grid-cols-2">
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Verified ARR saved</CardTitle>
            <CardDescription>Empirical 30/60/90-day attribution</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-bold">{formatUsd(data.verified_arr_saved)}</CardContent>
        </Card>
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Prospective pipeline at risk</CardTitle>
            <CardDescription>Book with p ≥ 0.65</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-bold">{formatUsd(data.at_risk_arr)}</CardContent>
        </Card>
      </section>

      {data.pending.length === 0 ? (
        <Card className="rounded-2xl border-border shadow-card">
          <CardContent className="py-8 text-sm text-muted-foreground">No playbooks waiting on CSM approval.</CardContent>
        </Card>
      ) : (
        <div className="flex flex-col gap-3">
          {data.pending.map((account) => (
            <Card key={account.account_id} className="rounded-2xl border-border shadow-card">
              <CardContent className="flex flex-col gap-4 py-5 sm:flex-row sm:items-center sm:justify-between">
                <div>
                  <p className="font-semibold">{account.customer_id}</p>
                  <p className="text-sm text-muted-foreground">
                    {formatUsd(account.mrr)} MRR · {account.risk_tier} · {account.recommended_action || "retention playbook"}
                  </p>
                </div>
                <div className="relative flex flex-col items-start gap-2 sm:items-end">
                  <div className="flex items-center gap-2">
                    <div className="flex gap-2">
                    <Button
                      disabled={!isAdmin || lockPro || pendingId === account.account_id}
                      onClick={() => void approve(account.account_id)}
                    >
                      Approve Dispatch
                    </Button>
                    <Button
                      variant="outline"
                      disabled={!isAdmin || lockPro || pendingId === account.account_id}
                      onClick={() => void dismiss(account.account_id)}
                    >
                      Dismiss / False Positive
                    </Button>
                    </div>
                    {lockPro ? <ProBadge /> : null}
                  </div>
                  <ProLockOverlay locked={lockPro} onUnlock={() => openWaitlist()} />
                  {!isAdmin ? <p className="text-sm text-muted-foreground">{ADMIN_REASON}</p> : null}
                </div>
              </CardContent>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
