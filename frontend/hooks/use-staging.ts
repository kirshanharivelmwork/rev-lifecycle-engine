"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { StagingPayload } from "@/lib/types";

export function useStagingQueue() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<StagingPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [pendingId, setPendingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = await request<StagingPayload>("/api/v1/staging");
      setData(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load staging queue");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const approve = useCallback(
    async (accountId: string) => {
      setPendingId(accountId);
      try {
        await request(`/api/v1/staging/${accountId}/approve`, { method: "POST" });
        await reload();
      } finally {
        setPendingId(null);
      }
    },
    [reload, request],
  );

  const dismiss = useCallback(
    async (accountId: string) => {
      setPendingId(accountId);
      try {
        await request(`/api/v1/staging/${accountId}/dismiss`, { method: "POST" });
        await reload();
      } finally {
        setPendingId(null);
      }
    },
    [reload, request],
  );

  return { data, error, loading, isLoaded, isSignedIn, reload, approve, dismiss, pendingId };
}
