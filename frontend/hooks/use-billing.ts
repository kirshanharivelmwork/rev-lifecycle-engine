"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { BillingPayload } from "@/lib/types";

export function useBilling() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<BillingPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [openingPortal, setOpeningPortal] = useState(false);

  const reload = useCallback(async () => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = await request<BillingPayload>("/api/v1/billing");
      setData(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load billing");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const openPortal = useCallback(async () => {
    setOpeningPortal(true);
    setError(null);
    try {
      const payload = await request<{ url: string }>("/api/v1/billing/portal", {
        method: "POST",
        body: JSON.stringify({}),
      });
      if (payload.url) {
        window.location.assign(payload.url);
      }
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to open Stripe portal");
      throw exc;
    } finally {
      setOpeningPortal(false);
    }
  }, [request]);

  return { data, error, loading, openingPortal, isLoaded, isSignedIn, reload, openPortal };
}
