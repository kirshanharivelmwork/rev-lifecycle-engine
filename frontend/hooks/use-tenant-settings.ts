"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { SettingsPayload } from "@/lib/types";

export function useTenantSettings() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<SettingsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);

  const reload = useCallback(async () => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = await request<SettingsPayload>("/api/v1/settings");
      setData(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load settings");
      setData(null);
    } finally {
      setLoading(false);
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

  return { data, error, loading, saving, isLoaded, isSignedIn, reload, save };
}
