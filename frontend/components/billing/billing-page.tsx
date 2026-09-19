"use client";

import Link from "next/link";

import { PaymentCta } from "@/components/billing/payment-cta";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { useBilling } from "@/hooks/use-billing";

function statusCopy(status: string) {
  const normalized = status.toLowerCase();
  if (normalized === "active") {
    return "Your Growth subscription is current.";
  }
  if (normalized === "past_due") {
    return "Payment failed. Update the card on file to restore Command Center APIs.";
  }
  if (normalized === "canceled" || normalized === "cancelled") {
    return "This subscription is canceled. Resubscribe from pricing or reopen the portal if a customer still exists.";
  }
  if (normalized === "incomplete") {
    return "Checkout is not finished. Complete payment so this workspace can score the book.";
  }
  return `Subscription status: ${status}`;
}

export function BillingPage() {
  const { data, error, loading, openingPortal, openPortal } = useBilling();

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading billing…</p>;
  }
  if (error && !data) {
    return (
      <div className="mx-auto flex max-w-xl flex-col gap-3">
        <p className="text-sm text-destructive">{error}</p>
        <PaymentCta />
      </div>
    );
  }
  if (!data) {
    return <p className="text-sm text-muted-foreground">Sign in to view billing.</p>;
  }

  return (
    <div className="mx-auto flex max-w-[1400px] flex-col gap-6">
      <header>
        <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">SaaS</p>
        <h2 className="mt-1 text-[2.05rem] font-bold text-foreground">Billing</h2>
        <p className="mt-2 text-[0.98rem] text-muted-foreground">
          Plan, subscription status, invoices, and card updates via Stripe Customer Portal.
        </p>
      </header>

      {data.needs_payment ? <PaymentCta message={statusCopy(data.subscription_status)} /> : null}

      <section className="grid gap-4 sm:grid-cols-3">
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Plan</CardTitle>
            <CardDescription>Clerk organization workspace</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-bold capitalize">{data.plan_tier}</CardContent>
        </Card>
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Status</CardTitle>
            <CardDescription>{statusCopy(data.subscription_status)}</CardDescription>
          </CardHeader>
          <CardContent className="text-2xl font-bold">{data.subscription_status}</CardContent>
        </Card>
        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Stripe customer</CardTitle>
            <CardDescription>Linked after Checkout</CardDescription>
          </CardHeader>
          <CardContent className="font-mono text-sm">{data.stripe_customer_id || "Not linked yet"}</CardContent>
        </Card>
      </section>

      <Card className="rounded-2xl border-border shadow-card">
        <CardHeader>
          <CardTitle>Manage subscription</CardTitle>
          <CardDescription>Invoices, payment method, and cancel live in Stripe Customer Portal.</CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-3">
          <Button disabled={openingPortal || !data.portal_available} onClick={() => void openPortal()}>
            {openingPortal ? "Opening portal…" : "Open customer portal"}
          </Button>
          <Button variant="outline" asChild>
            <Link href="/pricing">Go to pricing</Link>
          </Button>
          {!data.portal_available ? (
            <p className="w-full text-sm text-muted-foreground">
              Portal is available after the first successful Checkout (stripe_customer_id on the organization).
            </p>
          ) : null}
          {error ? <p className="w-full text-sm text-destructive">{error}</p> : null}
        </CardContent>
      </Card>
    </div>
  );
}
