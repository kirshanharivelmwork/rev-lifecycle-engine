"use client";

import { SignInButton, useAuth } from "@clerk/nextjs";
import { useState } from "react";

import { LegalFooter } from "@/components/legal/legal-footer";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { apiFetch } from "@/lib/api";

export function PricingSubscribe() {
  const { getToken, isLoaded, isSignedIn } = useAuth();
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function startCheckout() {
    setError(null);
    setPending(true);
    try {
      const token = await getToken();
      if (!token) {
        throw new Error("Sign in to subscribe your organization.");
      }
      const body = await apiFetch<{ url?: string }>("/api/v1/billing/create-checkout-session", token, {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (!body.url) {
        throw new Error("Checkout session did not include a URL");
      }
      window.location.assign(body.url);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Checkout failed");
      setPending(false);
    }
  }

  if (!isLoaded) {
    return <p className="text-sm text-muted-foreground">Loading billing…</p>;
  }

  if (!isSignedIn) {
    return (
      <SignInButton mode="redirect" forceRedirectUrl="/pricing" signUpForceRedirectUrl="/pricing">
        <Button size="lg">Sign in to subscribe</Button>
      </SignInButton>
    );
  }

  return (
    <div className="flex flex-col items-start gap-3">
      <Button size="lg" disabled={pending} onClick={() => void startCheckout()}>
        {pending ? "Redirecting to Stripe…" : "Subscribe"}
      </Button>
      {error ? <p className="text-sm text-destructive">{error}</p> : null}
    </div>
  );
}

export function PricingPage() {
  return (
    <div className="min-h-screen bg-background px-6 py-16">
      <div className="mx-auto flex max-w-3xl flex-col gap-10">
        <header>
          <p className="text-[0.78rem] font-semibold uppercase tracking-[0.16em] text-primary">
            Self-serve onboarding
          </p>
          <h1 className="mt-2 text-4xl font-bold tracking-tight text-foreground">Growth plan</h1>
          <p className="mt-3 text-lg text-muted-foreground">
            Diagnose silent churn, dispatch save motions, and attribute verified ARR — billed monthly through Stripe.
          </p>
        </header>

        <Card className="rounded-2xl border-border shadow-card">
          <CardHeader>
            <CardTitle>Rev Lifecycle Engine</CardTitle>
            <CardDescription>One workspace. Command Center, HITL staging, and tenant integrations included.</CardDescription>
          </CardHeader>
          <CardContent className="flex flex-col gap-6">
            <p className="text-4xl font-bold">
              Custom<span className="text-lg font-medium text-muted-foreground"> / org / month</span>
            </p>
            <ul className="list-disc space-y-2 pl-5 text-sm text-muted-foreground">
              <li>Live churn scoring and at-risk book</li>
              <li>Slack + Resend interventions with 14-day cooldown</li>
              <li>Human-in-the-loop approval for high-MRR accounts</li>
            </ul>
            <PricingSubscribe />
          </CardContent>
        </Card>
        <LegalFooter />
      </div>
    </div>
  );
}
