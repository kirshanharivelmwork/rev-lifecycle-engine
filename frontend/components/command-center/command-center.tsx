"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";

import { PaymentCta } from "@/components/billing/payment-cta";
import { ActionStream } from "@/components/command-center/action-stream";
import { AtRiskBook } from "@/components/command-center/at-risk-book";
import { BookRiskChart } from "@/components/command-center/book-risk-chart";
import { KpiGrid } from "@/components/command-center/kpi-grid";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import { useCommandCenter } from "@/hooks/use-command-center";
import { formatUsd } from "@/lib/command-center-data";
import type { BillingPayload, CheckoutConfirmResponse } from "@/lib/types";

const CHECKOUT_POLL_MS = 2000;
const CHECKOUT_MAX_ATTEMPTS = 15;

export function CommandCenter() {
  const searchParams = useSearchParams();
  const router = useRouter();
  const sessionId = searchParams.get("session_id");
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [checkoutActive, setCheckoutActive] = useState(!sessionId);
  const [activating, setActivating] = useState(Boolean(sessionId));
  const [activationNote, setActivationNote] = useState<string | null>(null);
  const [activationTimedOut, setActivationTimedOut] = useState(false);
  const { data, error, loading, billingBlocked, reload } = useCommandCenter({
    enabled: checkoutActive,
  });

  useEffect(() => {
    if (!sessionId) {
      setCheckoutActive(true);
      setActivating(false);
      return;
    }
    if (!isLoaded || !isSignedIn) {
      return;
    }
    let cancelled = false;

    async function activateFromCheckout() {
      setActivating(true);
      setActivationTimedOut(false);
      setActivationNote("Payment received. Activating your workspace…");
      for (let attempt = 0; attempt < CHECKOUT_MAX_ATTEMPTS; attempt += 1) {
        if (cancelled) {
          return;
        }
        try {
          await request<CheckoutConfirmResponse>("/api/v1/billing/confirm-checkout", {
            method: "POST",
            body: JSON.stringify({ session_id: sessionId }),
          });
        } catch {
          // Webhook may still win; GET /billing does not 402 for unpaid tenants.
        }
        try {
          const billing = await request<BillingPayload>("/api/v1/billing");
          if (billing.subscription_status === "active") {
            if (!cancelled) {
              setActivationNote("Subscription is active.");
              setCheckoutActive(true);
              router.replace("/");
              await reload();
              setActivating(false);
            }
            return;
          }
        } catch {
          // Keep polling until active or timeout.
        }
        if (attempt < CHECKOUT_MAX_ATTEMPTS - 1) {
          await new Promise((resolve) => setTimeout(resolve, CHECKOUT_POLL_MS));
        }
      }
      if (!cancelled) {
        setActivationNote("Stripe billing is still settling. Update payment if this persists.");
        setActivationTimedOut(true);
        setActivating(false);
      }
    }

    void activateFromCheckout();
    return () => {
      cancelled = true;
    };
  }, [sessionId, isLoaded, isSignedIn, request, router, reload]);

  if (sessionId && (activating || !checkoutActive) && !activationTimedOut) {
    return (
      <div className="mx-auto flex max-w-[1400px] flex-col gap-3">
        <p className="text-sm text-muted-foreground">{activationNote || "Activating your subscription…"}</p>
      </div>
    );
  }

  if (sessionId && activationTimedOut) {
    return (
      <PaymentCta
        message={activationNote || "Timed out waiting for Stripe to mark this workspace active."}
      />
    );
  }

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading live command center…</p>;
  }
  if (error) {
    if (billingBlocked) {
      return (
        <PaymentCta
          message={
            activationNote ||
            "This workspace needs an active subscription before the Command Center will load."
          }
        />
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
              A new Clerk workspace has no customers until you score a CSV book or backfill Stripe history. Open Settings
              to upload a combined CSV (no Stripe secret required).
            </CardDescription>
          </CardHeader>
          <CardContent>
            <Link className="text-sm font-medium text-primary underline-offset-4 hover:underline" href="/settings">
              Open Settings to score a CSV book
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
