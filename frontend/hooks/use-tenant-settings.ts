"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { BackfillResponse, SettingsPayload } from "@/lib/types";

export function useTenantSettings() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [backfilling, setBackfilling] = useState(false);

  const reload = useCallback(async (options?: { silent?: boolean }) => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return null;
    }
    if (!options?.silent) {
      setLoading(true);
    }
    setError(null);
    try {
      const payload = await request<SettingsPayload>("/api/v1/settings");
      setData(payload);
      return payload;
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load settings");
      setData(null);
      return null;
    } finally {
      if (!options?.silent) {
        setLoading(false);
      }
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const save = useCallback(
    async (body: Record<string, unknown>) => {
      setSaving(true);
      setError(null);
      try {
        const payload = await request<SettingsPayload>("/api/v1/settings", {
          method: "PATCH",
          body: JSON.stringify(body),
        });
        setData(payload);
        return payload;
      } catch (exc) {
        const message = exc instanceof Error ? exc.message : "Failed to save settings";
        setError(message);
        throw exc;
      } finally {
        setSaving(false);
      }
    },
    [request],
  );

  const runBackfill = useCallback(
    async (stripeApiKey: string) => {
      setBackfilling(true);
      setError(null);
      try {
        const payload = await request<BackfillResponse>("/api/v1/backfill", {
          method: "POST",
          body: JSON.stringify({ stripe_api_key: stripeApiKey }),
        });
        await reload({ silent: true });
        return payload;
      } catch (exc) {
        const message = exc instanceof Error ? exc.message : "Failed to start historical backfill";
        setError(message);
        throw exc;
      } finally {
        setBackfilling(false);
      }
    },
    [reload, request],
  );

  return { data, error, loading, saving, backfilling, isLoaded, isSignedIn, reload, save, runBackfill };
}
