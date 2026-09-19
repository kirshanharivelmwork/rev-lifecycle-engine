"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { CommandCenterPayload } from "@/lib/types";

export function useCommandCenter() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [data, setData] = useState<CommandCenterPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (!isLoaded || !isSignedIn) {
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const payload = await request<CommandCenterPayload>("/api/v1/command-center");
      setData(payload);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Failed to load command center");
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return { data, error, loading, isLoaded, isSignedIn, reload };
}
