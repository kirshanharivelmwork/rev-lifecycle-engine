"use client";

import { useCallback, useEffect, useState } from "react";

import { useAuthedFetch } from "@/hooks/use-authed-fetch";
import type { TenantSummary } from "@/lib/types";

export function useTenant() {
  const { request, isLoaded, isSignedIn } = useAuthedFetch();
  const [tenant, setTenant] = useState<TenantSummary | null>(null);

  const reload = useCallback(async () => {
    if (!isLoaded || !isSignedIn) {
      return;
    }
    try {
      const payload = await request<TenantSummary>("/api/v1/tenant");
      setTenant(payload);
    } catch {
      setTenant(null);
    }
  }, [isLoaded, isSignedIn, request]);

  useEffect(() => {
    void reload();
  }, [reload]);

  return tenant;
}
