"use client";

import { useCallback, useEffect, useState } from "react";

import { isBillingBlocked } from "@/lib/api";
import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { CommandCenterPayload } from "@/lib/types";

export function useCommandCenter(options?: { enabled?: boolean }) {
  const enabled = options?.enabled ?? true;
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<CommandCenterPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [billingBlocked, setBillingBlocked] = useState(false);

  const reload = useCallback(async () => {
    if (!enabled || !isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    setBillingBlocked(false);
    try {
      let lastError: unknown;
      for (let attempt = 0; attempt < 6; attempt += 1) {
        try {
          const payload = await request<CommandCenterPayload>("/api/v1/command-center");
          setData(payload);
          setBillingBlocked(false);
          setError(null);
          return;
        } catch (exc) {
          lastError = exc;
          if (!isBillingBlocked(exc) || attempt === 5) {
            break;
          }
          await new Promise((resolve) => setTimeout(resolve, 1000));
        }
      }
      setData(null);
      setBillingBlocked(isBillingBlocked(lastError));
      setError(lastError instanceof Error ? lastError.message : "Failed to load command center");
    } finally {
      setLoading(false);
    }
  }, [enabled, isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, billingBlocked, isLoaded, isSignedIn, reload };
}
