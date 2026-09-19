"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { AcquisitionPayload } from "@/lib/types";

export function useAcquisition() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<AcquisitionPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [running, setRunning] = useState(false);

  const reload = useCallback(async () => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = await request<AcquisitionPayload>("/api/v1/acquisition");
      setData(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load acquisition");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const runOutbound = useCallback(async () => {
    setRunning(true);
    setError(null);
    try {
      await request("/api/v1/acquisition/run", { method: "POST", body: JSON.stringify({}) });
      await reload();
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to run outbound");
      throw exc;
    } finally {
      setRunning(false);
    }
  }, [reload, request]);

  return { data, error, loading, running, isLoaded, isSignedIn, reload, runOutbound };
}
