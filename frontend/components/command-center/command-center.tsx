"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { ActionStream } from "@/components/command-center/action-stream";
import { AtRiskBook } from "@/components/command-center/at-risk-book";
import { BookRiskChart } from "@/components/command-center/book-risk-chart";
import { KpiGrid } from "@/components/command-center/kpi-grid";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import { useCommandCenter } from "@/hooks/use-command-center";
import { formatUsd } from "@/lib/command-center-data";
import type { CheckoutConfirmResponse, TenantSummary } from "@/lib/types";

export function CommandCenter() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const sessionId = searchParams.get("session_id");
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const { data, error, loading, reload } = useCommandCenter();
  const [activating, setActivating] = useState(Boolean(sessionId));
  const [activationNote, setActivationNote] = useState<string | null>(null);

  useEffect(() => {
    if (!sessionId) {
      setActivating(false);
      return;
    }
    if (!isLoaded || !isSignedIn) {
      return;
    }
    let cancelled = false;

    async function activateFromCheckout() {
      setActivating(true);
      setActivationNote("Payment received. Activating your workspace…");
      try {
        for (let attempt = 0; attempt < 12; attempt += 1) {
          try {
            const confirmed = await request<CheckoutConfirmResponse>("/api/v1/billing/confirm-checkout", {
              method: "POST",
              body: JSON.stringify({ session_id: sessionId }),
            });
            if (confirmed.activated || confirmed.subscription_status === "active") {
              if (!cancelled) {
                setActivationNote("Subscription is active.");
                router.replace("/");
                await reload();
                setActivating(false);
              }
              return;
            }
          } catch {
            try {
              const tenant = await request<TenantSummary>("/api/v1/tenant");
              if (tenant.subscription_status === "active") {
                if (!cancelled) {
                  router.replace("/");
                  await reload();
                  setActivating(false);
                }
                return;
              }
            } catch {
              // Webhook may still be in flight; keep polling.
            }
          }
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
        if (!cancelled) {
          setActivationNote("Still waiting on Stripe. The Command Center will load as soon as billing is active.");
          setActivating(false);
        }
      } finally {
        if (!cancelled) {
          setActivating(false);
        }
      }
    }

    void activateFromCheckout();
    return () => {
      cancelled = true;
    };
  }, [sessionId, isLoaded, isSignedIn, request, router, reload]);

  if (activating) {
    return (
      <div className="mx-auto flex max-w-[1400px] flex-col gap-3">
        <p className="text-sm text-muted-foreground">{activationNote || "Activating your subscription…"}</p>
      </div>
    );
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading live command center…</p>;
  }
  if (error) {
    const billingBlocked = error.toLowerCase().includes("subscription inactive") || error.toLowerCase().includes("402");
    if (sessionId || billingBlocked) {
      return (
        <div className="mx-auto flex max-w-xl flex-col gap-3">
          <p className="text-sm text-muted-foreground">
            {activationNote || "Finishing Stripe Checkout. This page retries until your subscription is active."}
          </p>
          <p className="text-sm text-destructive">{error}</p>
          <Link className="text-sm text-primary underline-offset-4 hover:underline" href="/pricing">
            Return to pricing
          </Link>
        </div>
      );
    }
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to load tenant data.</p>;
  }

  const tenant = data.tenant;
  const emptyBook = (tenant.subscriber_count ?? 0) === 0;

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
        {activationNote ? <p className="mt-2 text-sm text-primary">{activationNote}</p> : null}
      </header>

      {emptyBook ? (
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Your book is empty</CardTitle>
            <CardDescription>
              A new Clerk workspace has no customers until you backfill Stripe history. Paste a Stripe API key on
              Settings and run historical backfill to score the book.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link className="text-sm font-medium text-primary underline-offset-4 hover:underline" href="/settings">
              Open Settings to run historical backfill
            </Link>
          </CardContent>
        </Card>
      ) : null}

      <KpiGrid kpis={data.kpis} subscriberCount={tenant.subscriber_count ?? 0} />

      <section className="grid gap-4 xl:grid-cols-[1.15fr_1fr]">
        <ActionStream rows={data.action_stream} />
        <BookRiskChart data={data.risk_mix} />
      </section>

      <AtRiskBook rows={data.at_risk_book} />
    </div>
  );
}
