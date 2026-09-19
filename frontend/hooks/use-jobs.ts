"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { JobsPayload } from "@/lib/types";

export function useJobs() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<JobsPayload | null>(null);
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
      const payload = await request<JobsPayload>("/api/v1/jobs");
      setData(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load jobs");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const retryDeadLetter = useCallback(
    async (jobId: string) => {
      setPendingId(jobId);
      try {
        await request(`/api/v1/jobs/dead-letter/${jobId}/retry`, { method: "POST", body: JSON.stringify({}) });
        await reload();
      } finally {
        setPendingId(null);
      }
    },
    [reload, request],
  );

  const resolveDeadLetter = useCallback(
    async (jobId: string) => {
      setPendingId(jobId);
      try {
        await request(`/api/v1/jobs/dead-letter/${jobId}/resolve`, { method: "POST", body: JSON.stringify({}) });
        await reload();
      } finally {
        setPendingId(null);
      }
    },
    [reload, request],
  );

  const retryIngestion = useCallback(
    async (jobId: string) => {
      setPendingId(jobId);
      try {
        await request(`/api/v1/jobs/ingestion/${jobId}/retry`, { method: "POST", body: JSON.stringify({}) });
        await reload();
      } finally {
        setPendingId(null);
      }
    },
    [reload, request],
  );

  return {
    data,
    error,
    loading,
    pendingId,
    isLoaded,
    isSignedIn,
    reload,
    retryDeadLetter,
    resolveDeadLetter,
    retryIngestion,
  };
}
